"""Phase 3B: Support's reply is durable before anything is told about it.

The defect these cover was reproduced twice against live infrastructure. Support
pressed send, `submit_return_outcome` signalled Temporal straight from the
request handler, the case workflow had already closed, `handle.signal` raised a
raw `RPCError`, and the caller got an HTTP 500 with the RMA existing nowhere.
The shape underneath it was worse than the symptom: persist-then-signal is a
dual write, and a crash between the two left a stored event that the retry read
as a duplicate and reported as success while the workflow had never heard of it.

What is asserted here is the guarantee, stated the way it actually holds:

    transport (outbox -> Temporal):  AT LEAST ONCE
    business processing:             EFFECTIVELY ONCE, keyed on supportEventId

Not exactly-once, and the difference is the subject of
`test_redelivery_after_a_lost_acknowledgement_does_not_double_apply`: the
dispatcher there signals successfully and dies before it can acknowledge, the
signal arrives a second time -- and exactly one RMA exists at the end, because
the event id travelled with it.

MongoDB is a double rather than the real server. What is under test is the
control flow around a unique constraint and a lease, and the double enforces
both; the same constraint is created against the real server by
`OperationalRepository.ensure_indexes`, which
`test_the_identity_index_is_the_one_the_store_depends_on` pins by name.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError
from temporalio.service import RPCError, RPCStatusCode

from return_platform.api import return_support
from return_platform.api.return_support import router
from return_platform.configuration.settings import Settings
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.operations.integrations.outbox import (
    DEAD_LETTER_STATUS,
    REQUIRES_RECONCILIATION,
    DispatchResult,
    IntegrationOutboxDispatcher,
    OutboxCommand,
    PermanentDeliveryFailure,
    TopicDispatcher,
    TransientDeliveryFailure,
)
from return_platform.operations.integrations.temporal_signal import (
    TemporalSignalDispatcher,
    classify_rpc_error,
)
from return_platform.operations.models import CaseStatus
from return_platform.operations.support_events import (
    CASE_SUPPORT_EVENTS,
    SUPPORT_EVENT_IDENTITY_INDEX,
    SUPPORT_RESPONSE_SIGNAL_TOPIC,
    DurableSupportEventStore,
    IdempotencyConflictError,
    canonical_payload_digest,
    ensure_support_event_indexes,
    support_response_notice,
    support_return_record,
)
from return_platform.resources import RuntimeResources
from return_platform.security import roles as r
from return_platform.security.principal import Principal
from return_platform.workflows.return_case_workflow import (
    SupportResponseNotice,
    SupportReturnRecord,
)

CASE_ID = "case-9001"
WORKFLOW_ID = "return-case-case-9001"
WORK_ITEM_ID = "wi-9001"


# --------------------------------------------------------------------------- #
# A MongoDB double that enforces the two things this phase relies on:
# a unique index, and a transaction that rolls back.
# --------------------------------------------------------------------------- #


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(document, sub) for sub in condition):
                return False
            continue
        present = key in document
        actual = document.get(key)
        if (
            isinstance(condition, dict)
            and condition
            and all(str(k).startswith("$") for k in condition)
        ):
            for operator, operand in condition.items():
                if operator == "$exists":
                    if bool(operand) != present:
                        return False
                elif operator == "$in":
                    if actual not in operand:
                        return False
                elif operator == "$nin":
                    if actual in operand:
                        return False
                elif operator == "$lt":
                    if actual is None or not actual < operand:
                        return False
                elif operator == "$lte":
                    if actual is None or not actual <= operand:
                        return False
                elif operator == "$gt":
                    if actual is None or not actual > operand:
                        return False
                elif operator == "$type":
                    if operand != "string" or not isinstance(actual, str):
                        return False
                else:  # pragma: no cover - an operator the double has not met
                    raise NotImplementedError(operator)
        elif actual != condition:
            return False
    return True


class _FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, key: Any, direction: int = 1) -> _FakeCursor:
        if isinstance(key, str):
            self._documents.sort(key=lambda item: item.get(key), reverse=direction < 0)
        return self

    def limit(self, count: int) -> _FakeCursor:
        self._documents = self._documents[:count]
        return self

    def __aiter__(self) -> _FakeCursor:
        self._iterator = iter(self._documents)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iterator)
        except StopIteration:  # noqa: B904
            raise StopAsyncIteration from None


class _FakeCollection:
    """Enough of an `AsyncCollection` for the store, the outbox and the reads.

    Unique indexes are enforced rather than recorded. Without that the identity
    tests would be asserting on a `find_one` the production code only reaches
    when the index has already refused the write.
    """

    def __init__(self, name: str, database: _FakeDatabase) -> None:
        self.name = name
        self.database = database
        self.documents: dict[str, dict[str, Any]] = {}
        self.unique_indexes: list[tuple[tuple[str, ...], dict[str, Any] | None, str | None]] = []
        self.index_calls: list[tuple[Any, dict[str, Any]]] = []

    async def create_index(self, keys: Any, **options: Any) -> None:
        self.index_calls.append((keys, options))
        if not options.get("unique"):
            return
        fields = tuple(key for key, _ in keys) if isinstance(keys, list) else (str(keys),)
        self.unique_indexes.append(
            (fields, options.get("partialFilterExpression"), options.get("name"))
        )

    def _violates_unique(self, document: dict[str, Any]) -> bool:
        if str(document.get("_id")) in self.documents:
            return True
        for fields, partial, _name in self.unique_indexes:
            if partial is not None and not _matches(document, partial):
                continue
            candidate = tuple(document.get(field) for field in fields)
            for stored in self.documents.values():
                if partial is not None and not _matches(stored, partial):
                    continue
                if tuple(stored.get(field) for field in fields) == candidate:
                    return True
        return False

    async def insert_one(self, document: dict[str, Any], session: Any = None) -> None:
        del session
        if self._violates_unique(document):
            raise DuplicateKeyError(f"duplicate key on {self.name}")
        self.documents[str(document["_id"])] = copy.deepcopy(document)

    async def find_one(
        self, query: dict[str, Any], projection: Any = None, sort: Any = None, session: Any = None
    ) -> dict[str, Any] | None:
        del projection, sort, session
        for document in self.documents.values():
            if _matches(document, query):
                return copy.deepcopy(document)
        return None

    def find(self, query: dict[str, Any], projection: Any = None) -> _FakeCursor:
        del projection
        return _FakeCursor(
            [copy.deepcopy(item) for item in self.documents.values() if _matches(item, query)]
        )

    @staticmethod
    def _apply(document: dict[str, Any], update: dict[str, Any]) -> None:
        for field, value in update.get("$set", {}).items():
            document[field] = value
        for field, value in update.get("$inc", {}).items():
            document[field] = document.get(field, 0) + value

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        sort: Any = None,
        return_document: Any = None,
        session: Any = None,
    ) -> dict[str, Any] | None:
        del return_document, session
        candidates = [item for item in self.documents.values() if _matches(item, query)]
        if sort:
            for field, direction in reversed(list(sort)):
                candidates.sort(key=lambda item: item.get(field), reverse=direction < 0)
        if not candidates:
            return None
        self._apply(candidates[0], update)
        return copy.deepcopy(candidates[0])

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any], session: Any = None
    ) -> Any:
        del session
        for document in self.documents.values():
            if _matches(document, query):
                self._apply(document, update)
                return type("_Result", (), {"modified_count": 1})()
        return type("_Result", (), {"modified_count": 0})()


class _FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        if name not in self.collections:
            self.collections[name] = _FakeCollection(name, self)
        return self.collections[name]


class _FakeSession:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    async def with_transaction(self, callback: Any) -> None:
        """All of it or none of it.

        The snapshot is the whole point of the double. `record_support_response`
        writes the Support event and the outbox command as one act, and a test
        that could not observe the rollback would pass just as happily against
        the dual write this phase removes.
        """
        snapshot = self._client.snapshot()
        try:
            await callback(self)
        except BaseException:
            self._client.restore(snapshot)
            raise


class _FakeClient:
    def __init__(self) -> None:
        self.databases: dict[str, _FakeDatabase] = {}

    def __getitem__(self, name: str) -> _FakeDatabase:
        if name not in self.databases:
            self.databases[name] = _FakeDatabase()
        return self.databases[name]

    def snapshot(self) -> dict[tuple[str, str], dict[str, dict[str, Any]]]:
        return {
            (database_name, collection_name): copy.deepcopy(collection.documents)
            for database_name, database in self.databases.items()
            for collection_name, collection in database.collections.items()
        }

    def restore(self, snapshot: dict[tuple[str, str], dict[str, dict[str, Any]]]) -> None:
        for (database_name, collection_name), documents in snapshot.items():
            self.databases[database_name].collections[collection_name].documents = documents

    def start_session(self) -> Any:
        session = _FakeSession(self)

        class _Context:
            async def __aenter__(self) -> _FakeSession:
                return session

            async def __aexit__(self, *_: Any) -> bool:
                return False

        return _Context()


# --------------------------------------------------------------------------- #
# Doubles for the two ends of the delivery path
# --------------------------------------------------------------------------- #


class _RecordingWorkflow:
    """A case workflow that keys its business mutation on the event id.

    Stands in for what Phase 4 makes `ReturnCaseWorkflow` do. Modelled here
    rather than assumed, because "redelivery does not double-apply" is a claim
    about the *pair* -- an at-least-once transport and a receiver that keys on
    the id -- and testing only the transport half would prove nothing.
    """

    def __init__(self) -> None:
        self.signals: list[dict[str, Any]] = []
        self.applied_event_ids: list[str] = []
        self.rma_references: list[str] = []
        self.revision = 0

    def receive(self, notice: dict[str, Any]) -> None:
        self.signals.append(notice)
        event_id = str(notice.get("support_event_id"))
        if event_id in self.applied_event_ids:
            return
        self.applied_event_ids.append(event_id)
        self.rma_references.extend(
            str(record["return_reference"]) for record in notice.get("records", [])
        )
        self.revision += 1


class _FakeHandle:
    def __init__(self, workflow: _RecordingWorkflow, failure: BaseException | None) -> None:
        self._workflow = workflow
        self._failure = failure

    async def signal(self, name: str, argument: Any) -> None:
        del name
        if self._failure is not None:
            raise self._failure
        self._workflow.receive(argument)


class _FakeTemporalClient:
    def __init__(self, workflow: _RecordingWorkflow) -> None:
        self.workflow = workflow
        self.failure: BaseException | None = None
        self.handles_requested: list[str] = []

    def get_workflow_handle(self, workflow_id: str) -> _FakeHandle:
        self.handles_requested.append(workflow_id)
        return _FakeHandle(self.workflow, self.failure)


class _ScriptedDispatcher:
    """Raises what it is told to, then succeeds. For the outbox-side tests."""

    def __init__(self, failures: list[BaseException]) -> None:
        self._failures = failures
        self.dispatched = 0

    async def dispatch(self, command: OutboxCommand) -> DispatchResult:
        self.dispatched += 1
        if self._failures:
            raise self._failures.pop(0)
        return DispatchResult(external_reference=command.aggregate_id, response_digest=None)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def mongo() -> _FakeClient:
    return _FakeClient()


@pytest_asyncio.fixture
async def indexed_store(mongo: _FakeClient, test_settings: Settings) -> DurableSupportEventStore:
    """A store over a database that has the constraints production has.

    The indexes are created through `ensure_support_event_indexes` -- the same
    definition `OperationalRepository.ensure_indexes` calls -- so an index this
    code depends on but nobody creates would fail here rather than in
    production. Without the unique index the double would accept a second event
    under the same id and every idempotency assertion below would be vacuous.
    """
    await ensure_support_event_indexes(cast(Any, mongo[test_settings.mongo_database]))
    await mongo[test_settings.mongo_database]["integration_outbox"].create_index(
        "idempotencyKey", unique=True
    )
    return DurableSupportEventStore(cast(Any, mongo), test_settings)


def _records(*references: str) -> list[dict[str, Any]]:
    return [
        support_return_record(return_reference=reference, tracking_reference=f"1Z{reference}")
        for reference in references
    ]


async def _record_outcome(
    store: DurableSupportEventStore,
    *,
    support_event_id: str,
    references: tuple[str, ...] = ("RMA-1",),
    reason: str | None = None,
) -> Any:
    return await store.record_support_response(
        case_id=CASE_ID,
        work_item_id=WORK_ITEM_ID,
        support_event_id=support_event_id,
        records=_records(*references),
        rejected=False,
        reason=reason,
        workflow_id=WORKFLOW_ID,
        actor_id="support-1",
    )


def _outbox(mongo: _FakeClient, settings: Settings) -> _FakeCollection:
    return mongo[settings.mongo_database]["integration_outbox"]


def _events(mongo: _FakeClient, settings: Settings) -> _FakeCollection:
    return mongo[settings.mongo_database][CASE_SUPPORT_EVENTS]


# --------------------------------------------------------------------------- #
# 1. The commit survives Temporal being unreachable
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_event_is_durable_before_temporal_is_ever_contacted(
    indexed_store: DurableSupportEventStore, mongo: _FakeClient, test_settings: Settings
) -> None:
    """Gate 3B, first line. Nothing here has a Temporal client at all."""
    receipt = await _record_outcome(indexed_store, support_event_id="evt-1")

    assert receipt.duplicate is False
    stored = await _events(mongo, test_settings).find_one({"supportEventId": "evt-1"})
    assert stored is not None
    assert stored["caseId"] == CASE_ID
    assert stored["workflowId"] == WORKFLOW_ID

    command = await _outbox(mongo, test_settings).find_one({"_id": receipt.outbox_command_id})
    assert command is not None
    assert command["status"] == "PENDING"
    assert command["topic"] == SUPPORT_RESPONSE_SIGNAL_TOPIC
    assert command["payload"]["supportEventId"] == "evt-1"


@pytest.mark.asyncio
async def test_a_temporal_outage_delays_delivery_and_never_loses_it(
    indexed_store: DurableSupportEventStore, mongo: _FakeClient, test_settings: Settings
) -> None:
    """Down, then up. The event waits; it does not evaporate and it does not
    dead-letter."""
    await _record_outcome(indexed_store, support_event_id="evt-2")
    workflow = _RecordingWorkflow()
    temporal = _FakeTemporalClient(workflow)
    temporal.failure = RPCError("cluster is unavailable", RPCStatusCode.UNAVAILABLE, b"")
    dispatcher = TemporalSignalDispatcher(client_factory=_factory(temporal))
    worker = IntegrationOutboxDispatcher(
        cast(Any, mongo),
        test_settings,
        {SUPPORT_RESPONSE_SIGNAL_TOPIC: cast(TopicDispatcher, dispatcher)},
        worker_id="worker-a",
    )

    assert await worker.dispatch_once() is True
    command = await _outbox(mongo, test_settings).find_one({"aggregateId": CASE_ID})
    assert command is not None
    assert command["status"] == "RETRY"
    assert command["lastErrorCode"] == "TEMPORAL_SIGNAL_UNAVAILABLE"
    assert workflow.signals == []

    # Temporal comes back, and the command that was waiting is delivered.
    temporal.failure = None
    _make_claimable(command_id=str(command["_id"]), mongo=mongo, settings=test_settings)
    assert await worker.dispatch_once() is True
    delivered = await _outbox(mongo, test_settings).find_one({"aggregateId": CASE_ID})
    assert delivered is not None
    assert delivered["status"] == "DELIVERED"
    assert delivered["externalReference"] == WORKFLOW_ID
    assert workflow.rma_references == ["RMA-1"]


def _factory(client: _FakeTemporalClient) -> Any:
    async def factory() -> Any:
        return client

    return factory


def _make_claimable(*, command_id: str, mongo: _FakeClient, settings: Settings) -> None:
    """Bring the backoff forward. The outbox schedules the retry minutes out and
    a test that slept for it would be testing `asyncio.sleep`."""
    document = _outbox(mongo, settings).documents[command_id]
    document["nextAttemptAt"] = datetime.now(UTC) - timedelta(seconds=1)


# --------------------------------------------------------------------------- #
# 2. Redelivery: at-least-once transport, effectively-once processing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_redelivery_after_a_lost_acknowledgement_does_not_double_apply(
    indexed_store: DurableSupportEventStore, mongo: _FakeClient, test_settings: Settings
) -> None:
    """The scenario no transport can prevent, and the one the id exists for.

    The dispatcher signals successfully and the process dies before it can mark
    the command delivered. The lease expires, another worker claims the same
    command, and the same signal arrives again. Two signals is *correct* -- that
    is what at-least-once means. One RMA and one revision is what has to come
    out the other side.
    """
    await _record_outcome(indexed_store, support_event_id="evt-3", references=("RMA-7",))
    workflow = _RecordingWorkflow()
    temporal = _FakeTemporalClient(workflow)
    dispatcher = TemporalSignalDispatcher(client_factory=_factory(temporal))

    dying = IntegrationOutboxDispatcher(
        cast(Any, mongo),
        test_settings,
        {SUPPORT_RESPONSE_SIGNAL_TOPIC: cast(TopicDispatcher, dispatcher)},
        worker_id="worker-that-dies",
    )
    command = await dying.claim()
    assert command is not None
    await dispatcher.dispatch(command)
    # ...and here the process is gone. Nothing marked the command delivered.

    stranded = _outbox(mongo, test_settings).documents[command.id]
    assert stranded["status"] == "DISPATCHING"
    stranded["leaseUntil"] = datetime.now(UTC) - timedelta(seconds=1)
    stranded["status"] = "RETRY"

    survivor = IntegrationOutboxDispatcher(
        cast(Any, mongo),
        test_settings,
        {SUPPORT_RESPONSE_SIGNAL_TOPIC: cast(TopicDispatcher, dispatcher)},
        worker_id="worker-that-survives",
    )
    assert await survivor.dispatch_once() is True

    assert len(workflow.signals) == 2, "at-least-once transport: the second delivery is expected"
    assert workflow.rma_references == ["RMA-7"], "effectively-once processing, keyed on the id"
    assert workflow.revision == 1
    assert workflow.applied_event_ids == ["evt-3"]
    final = _outbox(mongo, test_settings).documents[command.id]
    assert final["status"] == "DELIVERED"


# --------------------------------------------------------------------------- #
# 3. Identity: same id, same payload / same id, different payload
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_same_event_id_with_the_same_payload_mutates_nothing_twice(
    indexed_store: DurableSupportEventStore, mongo: _FakeClient, test_settings: Settings
) -> None:
    first = await _record_outcome(indexed_store, support_event_id="evt-4")
    second = await _record_outcome(indexed_store, support_event_id="evt-4")

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.outbox_command_id == first.outbox_command_id
    assert len(_events(mongo, test_settings).documents) == 1
    assert len(_outbox(mongo, test_settings).documents) == 1


@pytest.mark.asyncio
async def test_a_reused_event_id_carrying_a_different_reply_is_refused(
    indexed_store: DurableSupportEventStore, mongo: _FakeClient, test_settings: Settings
) -> None:
    """The one case where accepting either answer loses data."""
    await _record_outcome(indexed_store, support_event_id="evt-5", references=("RMA-A",))

    with pytest.raises(IdempotencyConflictError):
        await _record_outcome(indexed_store, support_event_id="evt-5", references=("RMA-B",))

    assert len(_outbox(mongo, test_settings).documents) == 1


@pytest.mark.asyncio
async def test_the_unique_index_and_not_the_read_is_what_enforces_identity(
    indexed_store: DurableSupportEventStore, mongo: _FakeClient, test_settings: Settings
) -> None:
    """Two concurrent sends of the same id: the read cannot separate them.

    Simulated by inserting the winner behind the store's back, after the point
    where its own `find_one` would have seen nothing. Without the index the
    second write would land and the case would get two RMAs.
    """
    collection = _events(mongo, test_settings)
    assert any(
        name == SUPPORT_EVENT_IDENTITY_INDEX
        for _fields, _partial, name in collection.unique_indexes
    )
    collection.documents["intruder"] = {
        "_id": "intruder",
        "caseId": CASE_ID,
        "supportEventId": "evt-6",
        "payloadDigest": canonical_payload_digest(
            support_response_notice(
                work_item_id=WORK_ITEM_ID,
                support_event_id="evt-6",
                records=_records("RMA-1"),
                rejected=False,
                reason=None,
            )
        ),
        "outboxCommandId": "cmd-intruder",
    }

    receipt = await _record_outcome(indexed_store, support_event_id="evt-6")
    assert receipt.duplicate is True
    assert receipt.outbox_command_id == "cmd-intruder"
    assert len(collection.documents) == 1


@pytest.mark.asyncio
async def test_a_failed_commit_leaves_neither_the_event_nor_the_command(
    indexed_store: DurableSupportEventStore, mongo: _FakeClient, test_settings: Settings
) -> None:
    """The dual write, refused. Either both land or neither does."""
    await _outbox(mongo, test_settings).insert_one(
        {
            "_id": "squatter",
            "idempotencyKey": DurableSupportEventStore.outbox_idempotency_key(CASE_ID, "evt-7"),
        }
    )

    with pytest.raises(DuplicateKeyError):
        await _record_outcome(indexed_store, support_event_id="evt-7")

    assert await _events(mongo, test_settings).find_one({"supportEventId": "evt-7"}) is None


# --------------------------------------------------------------------------- #
# The digest detects a changed payload, and nothing else
# --------------------------------------------------------------------------- #


def test_property_order_does_not_change_the_digest() -> None:
    assert canonical_payload_digest({"a": 1, "b": 2}) == canonical_payload_digest({"b": 2, "a": 1})


def test_an_explicit_null_is_the_same_statement_as_an_omitted_field() -> None:
    """A client that starts sending `"reason": null` must not turn every resend
    into a 409."""
    assert canonical_payload_digest({"records": [], "reason": None}) == canonical_payload_digest(
        {"records": []}
    )


def test_the_order_lines_on_a_record_are_a_set_not_a_sequence() -> None:
    left = support_return_record(return_reference="RMA-1", order_line_references=("L2", "L1"))
    right = support_return_record(return_reference="RMA-1", order_line_references=("L1", "L2"))
    assert canonical_payload_digest(left) == canonical_payload_digest(right)


def test_a_different_rma_is_a_different_digest() -> None:
    """The one thing the digest is actually for."""
    assert canonical_payload_digest(
        support_return_record(return_reference="RMA-1")
    ) != canonical_payload_digest(support_return_record(return_reference="RMA-2"))


# --------------------------------------------------------------------------- #
# 4. Failure classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "code",
    [
        RPCStatusCode.NOT_FOUND,
        RPCStatusCode.FAILED_PRECONDITION,
        RPCStatusCode.INVALID_ARGUMENT,
        RPCStatusCode.PERMISSION_DENIED,
        RPCStatusCode.UNAUTHENTICATED,
        RPCStatusCode.UNIMPLEMENTED,
    ],
)
def test_a_workflow_that_is_gone_is_classified_permanent(code: RPCStatusCode) -> None:
    assert classify_rpc_error(RPCError("gone", code, b"")) is True


@pytest.mark.parametrize(
    "code",
    [
        RPCStatusCode.UNAVAILABLE,
        RPCStatusCode.DEADLINE_EXCEEDED,
        RPCStatusCode.RESOURCE_EXHAUSTED,
        RPCStatusCode.ABORTED,
        RPCStatusCode.INTERNAL,
        RPCStatusCode.UNKNOWN,
        RPCStatusCode.CANCELLED,
        RPCStatusCode.DATA_LOSS,
    ],
)
def test_a_cluster_that_is_merely_unreachable_is_classified_transient(code: RPCStatusCode) -> None:
    """Classified by exclusion, so a status a future Temporal release adds
    retries and stays recoverable rather than dead-lettering a live event."""
    assert classify_rpc_error(RPCError("later", code, b"")) is False


@pytest.mark.asyncio
async def test_a_closed_workflow_dead_letters_and_the_retrying_stops(
    indexed_store: DurableSupportEventStore, mongo: _FakeClient, test_settings: Settings
) -> None:
    """The runtime defect, in its new form.

    `NOT_FOUND` on a case workflow that has completed used to reach the API
    handler as an HTTP 500 with the RMA lost. Now the event is already on disk
    and the command comes to rest somewhere an operator can list it.
    """
    await _record_outcome(indexed_store, support_event_id="evt-8")
    workflow = _RecordingWorkflow()
    temporal = _FakeTemporalClient(workflow)
    temporal.failure = RPCError(
        "workflow execution already completed", RPCStatusCode.NOT_FOUND, b""
    )
    dispatcher = TemporalSignalDispatcher(client_factory=_factory(temporal))
    worker = IntegrationOutboxDispatcher(
        cast(Any, mongo),
        test_settings,
        {SUPPORT_RESPONSE_SIGNAL_TOPIC: cast(TopicDispatcher, dispatcher)},
        worker_id="worker-a",
    )

    assert await worker.dispatch_once() is True

    command = await _outbox(mongo, test_settings).find_one({"aggregateId": CASE_ID})
    assert command is not None
    assert command["status"] == DEAD_LETTER_STATUS
    assert command["reconciliationState"] == REQUIRES_RECONCILIATION
    assert command["lastErrorCode"] == "TEMPORAL_SIGNAL_NOT_FOUND"
    assert command["deadLetteredAt"] is not None

    # And it stays there. Nothing claimable means no infinite retry loop.
    assert await worker.dispatch_once() is False
    # The Support event itself is untouched and still reconcilable.
    assert await _events(mongo, test_settings).find_one({"supportEventId": "evt-8"}) is not None


@pytest.mark.asyncio
async def test_a_transient_failure_backs_off_within_a_bound_and_never_dead_letters(
    indexed_store: DurableSupportEventStore, mongo: _FakeClient, test_settings: Settings
) -> None:
    """Bounded exponential backoff: it grows, it caps, it stays retryable."""
    await _record_outcome(indexed_store, support_event_id="evt-9")
    dispatcher = _ScriptedDispatcher([TransientDeliveryFailure("TEMPORAL_SIGNAL_UNAVAILABLE")] * 6)
    worker = IntegrationOutboxDispatcher(
        cast(Any, mongo),
        test_settings,
        {SUPPORT_RESPONSE_SIGNAL_TOPIC: cast(TopicDispatcher, dispatcher)},
        worker_id="worker-a",
    )
    command_id = next(iter(_outbox(mongo, test_settings).documents))

    delays: list[float] = []
    for _ in range(6):
        _make_claimable(command_id=command_id, mongo=mongo, settings=test_settings)
        before = datetime.now(UTC)
        assert await worker.dispatch_once() is True
        document = _outbox(mongo, test_settings).documents[command_id]
        assert document["status"] == "RETRY", "a reachable cluster must never be dead-lettered"
        delays.append((document["nextAttemptAt"] - before).total_seconds())

    assert delays == sorted(delays), "the backoff grows"
    assert max(delays) <= 3_600, "and it is bounded -- an unbounded one never retries again"
    assert "reconciliationState" not in _outbox(mongo, test_settings).documents[command_id]


@pytest.mark.asyncio
async def test_a_command_with_no_workflow_id_is_permanent_not_a_retry_loop(
    mongo: _FakeClient, test_settings: Settings
) -> None:
    """A malformed command cannot be fixed by waiting, so waiting is not the
    answer."""
    dispatcher = TemporalSignalDispatcher(
        client_factory=_factory(_FakeTemporalClient(_RecordingWorkflow()))
    )
    with pytest.raises(PermanentDeliveryFailure):
        await dispatcher.dispatch(
            OutboxCommand(
                id="cmd-1",
                topic=SUPPORT_RESPONSE_SIGNAL_TOPIC,
                aggregate_type="RETURN_CASE",
                aggregate_id=CASE_ID,
                idempotency_key="k",
                payload={"notice": {}},
                attempt_count=1,
            )
        )


@pytest.mark.asyncio
async def test_a_refused_connection_is_transient_and_does_not_kill_the_worker() -> None:
    """The worker has to start while Temporal is down -- that is the outage the
    outbox exists for -- so a connect failure is a retry, not a crash."""

    async def factory() -> Any:
        raise ConnectionRefusedError("temporal is not listening")

    dispatcher = TemporalSignalDispatcher(client_factory=factory)
    with pytest.raises(TransientDeliveryFailure):
        await dispatcher.dispatch(
            OutboxCommand(
                id="cmd-1",
                topic=SUPPORT_RESPONSE_SIGNAL_TOPIC,
                aggregate_type="RETURN_CASE",
                aggregate_id=CASE_ID,
                idempotency_key="k",
                payload={"workflowId": WORKFLOW_ID, "notice": {"records": []}},
                attempt_count=1,
            )
        )


# --------------------------------------------------------------------------- #
# The seam with Phase 4
# --------------------------------------------------------------------------- #


def test_the_signal_envelope_fits_the_workflow_dataclass() -> None:
    """The notice is a dict, and Temporal converts it by field name.

    The direction that matters is **every key the envelope sends must be a field
    the dataclass has**. A key with no field is silently dropped by the
    converter, and a dropped key is a value Support stated that the case never
    records -- a failure with no error anywhere.

    The other direction was one-way while `return_method` was still outstanding:
    a dataclass field the envelope does not send simply takes its default, which
    is what let `support_event_id` ride along ahead of Phase 4. Nothing is
    outstanding now -- D23 closed the last gap -- so the assertion is equality in
    both directions, and a new field on either side has to be carried through
    consciously rather than appearing as a silent default.
    """
    notice = support_response_notice(
        work_item_id=WORK_ITEM_ID,
        support_event_id="evt-1",
        records=_records("RMA-1"),
    )
    workflow_fields = {field.name for field in SupportResponseNotice.__dataclass_fields__.values()}
    assert set(notice) <= workflow_fields, "the envelope sends a key the workflow would drop"
    assert "support_event_id" in workflow_fields, "Phase 4 reads the event id; it must be a field"
    assert not workflow_fields - set(notice), "the notice does not carry every field yet"

    record_fields = {field.name for field in SupportReturnRecord.__dataclass_fields__.values()}
    sent = set(notice["records"][0])
    assert sent <= record_fields, "the envelope sends a record key the workflow would drop"
    #: `return_method` was the one field the dataclass carried and the envelope
    #: did not (D23). Until it travelled, `record_support_outcome` wrote nothing
    #: for it, the completion profile never resolved, and `businessComplete`
    #: could not become true for any return. It travels now.
    assert "return_method" in sent, "the method Support decided does not reach the workflow"
    assert record_fields - sent == set(), "the envelope leaves a record field unsent"


@pytest.mark.asyncio
async def test_the_identity_index_is_the_one_the_store_depends_on(mongo: _FakeClient) -> None:
    """Named, unique, and on the pair. Asserted against the definition that
    `OperationalRepository.ensure_indexes` calls, so a rename cannot leave the
    store keying on an index nobody creates."""
    database = mongo["any"]
    await ensure_support_event_indexes(cast(Any, database))

    keys, options = database[CASE_SUPPORT_EVENTS].index_calls[0]
    assert [field for field, _ in keys] == ["caseId", "supportEventId"]
    assert options["unique"] is True
    assert options["name"] == SUPPORT_EVENT_IDENTITY_INDEX


def test_no_module_in_this_phase_claims_exactly_once() -> None:
    """The guarantee is at-least-once transport with effectively-once
    processing. A comment that said otherwise would be the one thing a future
    reader trusts over the code."""
    from pathlib import Path

    import return_platform

    root = Path(return_platform.__file__).parent
    for relative in (
        "operations/support_events.py",
        "operations/integrations/temporal_signal.py",
        "operations/integrations/outbox.py",
        "api/return_support.py",
    ):
        text = (root / relative).read_text(encoding="utf-8").lower()
        assert "exactly-once" not in text, relative
        assert "exactly once" not in text, relative


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


class _StubSupportService:
    def __init__(self, case_id: str | None) -> None:
        self._case_id = case_id

    async def get_work_item(self, work_item_id: str) -> Any:
        if work_item_id != WORK_ITEM_ID:
            return None
        return type("_Item", (), {"caseId": self._case_id})()


class _StubRepository:
    def __init__(self, case: dict[str, Any] | None) -> None:
        self._case = case

    async def get_case(self, case_id: str) -> dict[str, Any] | None:
        del case_id
        return self._case


def _client(
    *,
    mongo: _FakeClient,
    settings: Settings,
    catalog: LoadedAssetCatalog,
    monkeypatch: pytest.MonkeyPatch,
    case_id: str | None = CASE_ID,
    case: dict[str, Any] | None = None,
) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(
            subject="support-1", roles=frozenset({r.RETURN_SUPPORT})
        )
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    monkeypatch.setattr(return_support, "_service", lambda request: _StubSupportService(case_id))
    monkeypatch.setattr(
        return_support,
        "OperationalRepository",
        lambda *args, **kwargs: _StubRepository(case),
    )
    app.state.resources = RuntimeResources(
        settings=settings, catalog=catalog, mongo=cast(Any, mongo)
    )
    app.include_router(router)
    return TestClient(app)


_URL = f"/api/v1/return-support/work-items/{WORK_ITEM_ID}/return-outcome"
_BODY: dict[str, Any] = {"records": [{"returnReference": "RMA-1"}], "rejected": False}


@pytest.fixture
def http(
    mongo: _FakeClient,
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    def build(**kwargs: Any) -> TestClient:
        return _client(
            mongo=mongo,
            settings=test_settings,
            catalog=loaded_empty_catalog,
            monkeypatch=monkeypatch,
            **kwargs,
        )

    return build


def test_the_handler_accepts_the_reply_with_no_temporal_connection_at_all(
    http: Any, mongo: _FakeClient, test_settings: Settings
) -> None:
    """`resources.temporal` is None here. Support must still be able to file a
    reply during a Temporal outage -- that outage is the entire reason the
    outbox is in the path."""
    with http() as client:
        response = client.post(_URL, json={**_BODY, "supportEventId": "http-1"})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data == {
        "caseId": CASE_ID,
        "supportEventId": "http-1",
        "disposition": "RECORDED",
        "outboxCommandId": data["outboxCommandId"],
    }
    assert len(_outbox(mongo, test_settings).documents) == 1


def test_the_idempotency_key_header_is_accepted_in_place_of_the_body_field(http: Any) -> None:
    with http() as client:
        response = client.post(_URL, json=_BODY, headers={"Idempotency-Key": "http-2"})

    assert response.status_code == 200, response.text
    assert response.json()["data"]["supportEventId"] == "http-2"


def test_a_reply_with_no_event_id_is_refused_rather_than_given_one(http: Any) -> None:
    """A server-minted id is a new id on every retry, which is no idempotency at
    all -- the console that lost its response and pressed send again would issue
    a second RMA."""
    with http() as client:
        response = client.post(_URL, json=_BODY)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "SUPPORT_EVENT_ID_REQUIRED"


def test_sending_the_same_reply_twice_is_a_success_and_one_mutation(
    http: Any, mongo: _FakeClient, test_settings: Settings
) -> None:
    with http() as client:
        first = client.post(_URL, json={**_BODY, "supportEventId": "http-3"})
        second = client.post(_URL, json={**_BODY, "supportEventId": "http-3"})

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json()["data"]["disposition"] == "RECORDED"
    assert second.json()["data"]["disposition"] == "DUPLICATE"
    assert len(_outbox(mongo, test_settings).documents) == 1
    assert len(_events(mongo, test_settings).documents) == 1


def test_the_same_id_carrying_a_different_reply_is_a_409(
    http: Any, mongo: _FakeClient, test_settings: Settings
) -> None:
    with http() as client:
        client.post(_URL, json={**_BODY, "supportEventId": "http-4"})
        conflict = client.post(
            _URL,
            json={
                "records": [{"returnReference": "RMA-DIFFERENT"}],
                "rejected": False,
                "supportEventId": "http-4",
            },
        )

    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(_outbox(mongo, test_settings).documents) == 1


def test_a_closed_case_is_refused_with_a_reason_rather_than_a_500(
    http: Any, mongo: _FakeClient, test_settings: Settings
) -> None:
    """The refusal that is still reachable from the handler.

    A workflow that closed without the case knowing is no longer detected here
    -- it dead-letters in the dispatcher instead. A case the platform already
    knows is terminal is worth refusing, because queueing a reply nobody will
    ever act on is worse than telling Support now.
    """
    with http(case={"caseId": CASE_ID, "status": CaseStatus.CLOSED.value}) as client:
        response = client.post(_URL, json={**_BODY, "supportEventId": "http-5"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "CASE_WORKFLOW_CLOSED"
    assert _outbox(mongo, test_settings).documents == {}


def test_a_work_item_with_no_case_is_still_refused(http: Any) -> None:
    with http(case_id=None) as client:
        response = client.post(_URL, json={**_BODY, "supportEventId": "http-6"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "WORK_ITEM_HAS_NO_CASE"


def test_an_unknown_work_item_is_a_404(http: Any) -> None:
    with http() as client:
        response = client.post(
            "/api/v1/return-support/work-items/nope/return-outcome",
            json={**_BODY, "supportEventId": "http-7"},
        )

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "WORK_ITEM_NOT_FOUND"
