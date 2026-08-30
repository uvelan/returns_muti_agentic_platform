"""S2: review-plane commands are durable before any workflow hears of them.

Contracts.md sect. 7: command record + outbox row in one transaction, dedupe on
`signal_id`, the frozen approval CAS on `(case_id, review_id, draft_version,
canonical_edit_version)`, delivery through a closed kind->signal map.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
import pytest_asyncio
from pymongo.errors import DuplicateKeyError

from return_platform.configuration.settings import Settings
from return_platform.operations.case_commands import (
    CASE_COMMAND_RECORDS,
    CASE_COMMAND_REVIEW_CAS_INDEX,
    CASE_COMMAND_SIGNAL_INDEX,
    CASE_COMMAND_SIGNAL_TOPIC,
    SIGNAL_FOR_COMMAND_KIND,
    CaseCommandKind,
    CaseCommandSignalDispatcher,
    CommandIdempotencyConflictError,
    DurableCaseCommandStore,
    StaleReviewVersionError,
    ensure_case_command_indexes,
)
from return_platform.operations.integrations.outbox import (
    DispatchResult,
    IntegrationOutboxDispatcher,
    OutboxCommand,
    PermanentDeliveryFailure,
    TopicDispatcher,
)
from tests.operations.mongo_double import FakeClient, FakeCollection

CASE_ID = "case-8001"
WORKFLOW_ID = "return-case-case-8001"


class _RecordingWorkflow:
    """A workflow that keys business processing on `signal_id` -- what V1 will do."""

    def __init__(self) -> None:
        self.signals: list[tuple[str, dict[str, Any]]] = []
        self.applied_signal_ids: list[str] = []

    def receive(self, name: str, payload: dict[str, Any]) -> None:
        self.signals.append((name, payload))
        signal_id = str(payload.get("signal_id") or payload.get("review_id"))
        if signal_id in self.applied_signal_ids:
            return
        self.applied_signal_ids.append(signal_id)


class _FakeHandle:
    def __init__(self, workflow: _RecordingWorkflow) -> None:
        self._workflow = workflow

    async def signal(self, name: str, argument: Any) -> None:
        self._workflow.receive(name, argument)


class _FakeTemporalClient:
    def __init__(self, workflow: _RecordingWorkflow) -> None:
        self.workflow = workflow

    def get_workflow_handle(self, workflow_id: str) -> _FakeHandle:
        del workflow_id
        return _FakeHandle(self.workflow)


@pytest.fixture
def mongo() -> FakeClient:
    return FakeClient()


@pytest.fixture
def commands(mongo: FakeClient, test_settings: Settings) -> FakeCollection:
    return mongo[test_settings.mongo_database][CASE_COMMAND_RECORDS]


@pytest.fixture
def outbox(mongo: FakeClient, test_settings: Settings) -> FakeCollection:
    return mongo[test_settings.mongo_database]["integration_outbox"]


@pytest_asyncio.fixture
async def store(mongo: FakeClient, test_settings: Settings) -> DurableCaseCommandStore:
    await ensure_case_command_indexes(cast(Any, mongo[test_settings.mongo_database]))
    await mongo[test_settings.mongo_database]["integration_outbox"].create_index(
        "idempotencyKey", unique=True
    )
    return DurableCaseCommandStore(cast(Any, mongo), test_settings)


def _approval_kwargs(signal_id: str = "sig-1", **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "case_id": CASE_ID,
        "workflow_id": WORKFLOW_ID,
        "kind": CaseCommandKind.TEMPLATE_APPROVED,
        "signal_id": signal_id,
        "actor_id": "associate-1",
        "payload": {
            "review_id": "rev-1",
            "scope_id": "req-1",
            "canonical_approved_payload_hash": "a" * 64,
        },
        "review_id": "rev-1",
        "draft_version": 1,
        "canonical_edit_version": 1,
    }
    kwargs.update(overrides)
    return kwargs


# --------------------------------------------------------------------------- #
# Atomicity and identity
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_command_and_its_outbox_row_commit_together(
    store: DurableCaseCommandStore, commands: FakeCollection, outbox: FakeCollection
) -> None:
    receipt = await store.record_command(**_approval_kwargs())

    stored = await commands.find_one({"caseId": CASE_ID, "signalId": "sig-1"})
    assert stored is not None
    assert stored["kind"] == "template_approved"
    assert stored["actorId"] == "associate-1"
    assert stored["caseSequence"] == 1

    row = await outbox.find_one({"_id": receipt.outbox_command_id})
    assert row is not None
    assert row["topic"] == CASE_COMMAND_SIGNAL_TOPIC
    assert row["status"] == "PENDING"
    assert row["stream"] == "review_commands"
    assert row["payload"]["signal"]["review_id"] == "rev-1"
    assert row["payload"]["signal"]["scope_id"] == "req-1"


@pytest.mark.asyncio
async def test_a_failed_commit_leaves_neither_document(
    store: DurableCaseCommandStore, commands: FakeCollection, outbox: FakeCollection
) -> None:
    """Txn abort leaves neither -- the dual write, refused (definition of done)."""
    await outbox.insert_one(
        {
            "_id": "squatter",
            "idempotencyKey": DurableCaseCommandStore.outbox_idempotency_key(CASE_ID, "sig-torn"),
        }
    )
    await outbox.create_index("idempotencyKey", unique=True)

    with pytest.raises(DuplicateKeyError):
        await store.record_command(**_approval_kwargs(signal_id="sig-torn"))

    assert await commands.find_one({"signalId": "sig-torn"}) is None


@pytest.mark.asyncio
async def test_the_same_signal_id_with_the_same_payload_is_a_no_op(
    store: DurableCaseCommandStore, commands: FakeCollection, outbox: FakeCollection
) -> None:
    first = await store.record_command(**_approval_kwargs())
    second = await store.record_command(**_approval_kwargs())

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.outbox_command_id == first.outbox_command_id
    assert len(commands.documents) == 1
    assert len(outbox.documents) == 1


@pytest.mark.asyncio
async def test_the_same_signal_id_with_a_different_payload_is_refused(
    store: DurableCaseCommandStore,
) -> None:
    await store.record_command(**_approval_kwargs())
    with pytest.raises(CommandIdempotencyConflictError):
        await store.record_command(
            **_approval_kwargs(
                payload={
                    "review_id": "rev-1",
                    "scope_id": "req-1",
                    "canonical_approved_payload_hash": "b" * 64,
                }
            )
        )


@pytest.mark.asyncio
async def test_a_stale_version_pair_is_a_cas_conflict(
    store: DurableCaseCommandStore,
) -> None:
    """Two approvals of the same draft under different signal ids: the second
    hits the frozen CAS key and the API maps the error to 409."""
    await store.record_command(**_approval_kwargs(signal_id="sig-a"))
    with pytest.raises(StaleReviewVersionError):
        await store.record_command(**_approval_kwargs(signal_id="sig-b"))


@pytest.mark.asyncio
async def test_a_new_version_pair_is_a_new_command(
    store: DurableCaseCommandStore, commands: FakeCollection
) -> None:
    await store.record_command(**_approval_kwargs(signal_id="sig-a"))
    receipt = await store.record_command(
        **_approval_kwargs(signal_id="sig-b", draft_version=2, canonical_edit_version=2)
    )
    assert receipt.duplicate is False
    assert receipt.case_sequence == 2
    assert len(commands.documents) == 2


@pytest.mark.asyncio
async def test_the_identity_indexes_are_the_ones_the_store_depends_on(
    mongo: FakeClient,
) -> None:
    database = mongo["any"]
    await ensure_case_command_indexes(cast(Any, database))
    names = {
        options.get("name")
        for _keys, options in database[CASE_COMMAND_RECORDS].index_calls
        if options.get("unique")
    }
    assert names == {CASE_COMMAND_SIGNAL_INDEX, CASE_COMMAND_REVIEW_CAS_INDEX}


# --------------------------------------------------------------------------- #
# Payload validation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_review_scoped_kind_must_carry_review_and_scope_ids(
    store: DurableCaseCommandStore,
) -> None:
    with pytest.raises(ValueError, match="scope_id"):
        await store.record_command(**_approval_kwargs(payload={"review_id": "rev-1"}))


@pytest.mark.asyncio
async def test_half_a_cas_key_is_refused(store: DurableCaseCommandStore) -> None:
    with pytest.raises(ValueError, match="travel together"):
        await store.record_command(**_approval_kwargs(canonical_edit_version=None))


@pytest.mark.asyncio
async def test_a_clarification_answer_needs_no_review_id(
    store: DurableCaseCommandStore, outbox: FakeCollection
) -> None:
    receipt = await store.record_command(
        case_id=CASE_ID,
        workflow_id=WORKFLOW_ID,
        kind=CaseCommandKind.CLARIFICATION_ANSWERED,
        signal_id="sig-clar-1",
        actor_id="associate-1",
        payload={"clarification_id": "clar-1", "answer": "map it to RMA-2"},
    )
    assert receipt.duplicate is False
    row = await outbox.find_one({"_id": receipt.outbox_command_id})
    assert row is not None
    assert row["payload"]["kind"] == "clarification_answered"


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #


def _factory(client: _FakeTemporalClient) -> Any:
    async def factory() -> Any:
        return client

    return factory


@pytest.mark.asyncio
async def test_delivery_signals_the_mapped_name_with_review_and_scope_ids(
    store: DurableCaseCommandStore, mongo: FakeClient, test_settings: Settings
) -> None:
    await store.record_command(**_approval_kwargs())
    workflow = _RecordingWorkflow()
    dispatcher = CaseCommandSignalDispatcher(client_factory=_factory(_FakeTemporalClient(workflow)))
    worker = IntegrationOutboxDispatcher(
        cast(Any, mongo),
        test_settings,
        {CASE_COMMAND_SIGNAL_TOPIC: cast(TopicDispatcher, dispatcher)},
        worker_id="worker-a",
    )

    assert await worker.dispatch_once() is True
    assert len(workflow.signals) == 1
    name, payload = workflow.signals[0]
    assert name == "template_approved"
    assert payload["review_id"] == "rev-1"
    assert payload["scope_id"] == "req-1"


@pytest.mark.asyncio
async def test_a_forged_kind_dead_letters_as_permanent() -> None:
    dispatcher = CaseCommandSignalDispatcher(
        client_factory=_factory(_FakeTemporalClient(_RecordingWorkflow()))
    )
    with pytest.raises(PermanentDeliveryFailure):
        await dispatcher.dispatch(
            OutboxCommand(
                id="cmd-1",
                topic=CASE_COMMAND_SIGNAL_TOPIC,
                aggregate_type="RETURN_CASE",
                aggregate_id=CASE_ID,
                idempotency_key="k",
                payload={
                    "workflowId": WORKFLOW_ID,
                    "kind": "drop_all_tables",
                    "signal": {},
                },
                attempt_count=1,
            )
        )


def test_the_signal_map_is_closed_and_total() -> None:
    """Every kind has exactly one signal name; nothing else is reachable."""
    assert set(SIGNAL_FOR_COMMAND_KIND) == set(CaseCommandKind)
    assert SIGNAL_FOR_COMMAND_KIND[CaseCommandKind.CLARIFICATION_ANSWERED] == (
        "clarification_answered"
    )


@pytest.mark.asyncio
async def test_commands_list_in_case_sequence_order(
    store: DurableCaseCommandStore,
) -> None:
    await store.record_command(**_approval_kwargs(signal_id="sig-a"))
    await store.record_command(
        **_approval_kwargs(signal_id="sig-b", draft_version=2, canonical_edit_version=2)
    )
    listed = await store.list_commands(CASE_ID)
    assert [item["caseSequence"] for item in listed] == [1, 2]


def test_dispatch_result_shape_matches_the_outbox_contract() -> None:
    assert DispatchResult(external_reference=None, response_digest=None) is not None
