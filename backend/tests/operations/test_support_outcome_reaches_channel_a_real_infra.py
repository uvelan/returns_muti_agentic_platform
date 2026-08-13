"""W1.6: Support's answer arrives in the associate's original conversation.

The link this proves is the one the platform did not have. Support issued an
RMA on a work item; the copilot found it, when it found it at all, by fetching
every return session in the browser and matching an order reference. Two open
returns on one order showed the wrong RMA, and closing the tab lost the link.

The chain here is the replacement, end to end against real MongoDB:

    Support outcome -> `record_support_outcome` -> return record + case fact
                    -> `RepositoryCaseStore.case_facts`
                    -> `AgentTurnContext.case_facts` on the next turn

No new conversation, no poll, no client-side join. The final hop -- the fact
appearing in the next turn's context -- is covered by the discovery smoke net,
which owns the graph; this file owns everything up to and including the
projection that hop reads.
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from pydantic import SecretStr
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.integration.case_store import RepositoryCaseStore
from return_platform.dynamic_knowledge.order_agent.contracts import OrderConfirmation
from return_platform.operations.repository import OperationalRepository
from return_platform.workflows.return_case_activities import ReturnCaseActivities
from return_platform.workflows.return_case_workflow import (
    RecordSupportOutcomeInput,
    SupportReturnRecord,
    return_case_workflow_id,
)

pytestmark = pytest.mark.asyncio(loop_scope="module")

TENANT = "tenant-a"
PRINCIPAL = "associate-1"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    """`directConnection=true` -- see `test_return_record_sync_real_infra._mongo_dsn`."""
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return (
        f"mongodb://{username}:{password}@{host}:27017/"
        "return_platform?authSource=admin&directConnection=true"
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def context() -> Any:
    database = f"outcome_channel_a_{uuid.uuid4().hex[:12]}"
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    settings = Settings(
        environment="test",
        mongo_dsn=SecretStr(_mongo_dsn()),
        mongo_database=database,
        source_mongo_database=database,
    )
    repository = OperationalRepository(client, settings)
    await repository.ensure_indexes()

    # `support_service` is unused by `record_support_outcome`; the outcome path
    # writes to the case, not to the thread. Passing None rather than a stub
    # makes an accidental call a failure instead of a silent no-op.
    #
    # `return_store` is required now: `record_support_outcome` commits to the
    # authoritative SQL return store before it touches the case (T-14) and
    # raises if the port is absent. This test's subject is Channel A
    # propagation, not SQL, so the port is a recorder -- it keeps the new
    # contract visible here without dragging SQL Server into a MongoDB test.
    # The store's real behaviour is proven against real SQL Server in
    # `test_case_return_records_real_infra.py`.
    class _RecordingReturnStore:
        def __init__(self) -> None:
            self.writes: list[Any] = []

        async def persist_case_return_records(self, write: Any) -> tuple[str, ...]:
            self.writes.append(write)
            return tuple(record.return_record_id for record in write.records)

    activities = ReturnCaseActivities(
        repository=repository,
        support_service=None,
        return_store=_RecordingReturnStore(),
    )
    try:
        yield repository, activities, RepositoryCaseStore(repository)
    finally:
        await client.drop_database(database)
        await client.close()


async def _case(store: RepositoryCaseStore) -> str:
    """A case raised the way the platform raises one: an associate confirmed.

    Through the adapter rather than straight into the repository, so the
    confirmation fact exists and "the outcome adds to what the case already
    knows" is a claim about the real fact log.
    """
    conversation = f"conv-{uuid.uuid4().hex[:8]}"
    confirmed = await store.confirm_case(
        tenant_id=TENANT,
        principal_id=PRINCIPAL,
        branch_ids=("branch-1",),
        conversation_id=conversation,
        confirmation=OrderConfirmation(
            candidate_set_id="cs-1",
            candidate_id="cand-1",
            order_reference="CW273354",
        ),
        configuration_release_id="release-1",
        graph_generation_id="generation-1",
    )
    return confirmed.case_id


def _outcome(case_id: str, *records: SupportReturnRecord) -> RecordSupportOutcomeInput:
    return RecordSupportOutcomeInput(
        case_id=case_id,
        work_item_id=f"wi-{case_id}",
        records=tuple(records),
        rejected=False,
        reason=None,
        return_record_ids=tuple(f"rr-{case_id}-{index}" for index in range(len(records))),
    )


async def _facts_named(
    repository: OperationalRepository, case_id: str, name: str
) -> list[dict[str, Any]]:
    """The whole log for one fact, so "how many times" is answerable.

    The projection only ever shows the newest, which is exactly what an
    idempotency test must not rely on.
    """
    return [fact for fact in await repository.list_case_facts(case_id) if fact["factName"] == name]


def _record(reference: str, **extra: Any) -> SupportReturnRecord:
    return SupportReturnRecord(return_reference=reference, **extra)


async def test_an_rma_becomes_a_fact_the_agent_can_read(context: Any) -> None:
    _, activities, store = context
    case_id = await _case(store)

    await activities.record_support_outcome(
        _outcome(case_id, _record("RMA-1001", tracking_reference="1Z999", return_location="DC-7"))
    )

    facts = await store.case_facts(case_id)
    assert facts["return_reference"] == "RMA-1001"
    assert facts["tracking_reference"] == "1Z999"
    assert facts["return_location"] == "DC-7"
    # The confirmation the associate already made is still there: the outcome
    # adds to what the case knows, it does not replace it.
    assert facts["confirmed_order_reference"] == "CW273354"


async def test_the_return_record_and_the_fact_agree(context: Any) -> None:
    """Both exist, and they say the same thing.

    The return record is the first-class artifact (D6); the fact is what the
    conversation reads. Writing one without the other is how a console shows an
    RMA the agent will then ask Support to issue again.
    """
    repository, activities, store = context
    case_id = await _case(store)

    await activities.record_support_outcome(_outcome(case_id, _record("RMA-2002")))

    records = await repository.list_return_records(case_id)
    facts = await store.case_facts(case_id)
    assert [record["returnReference"] for record in records] == ["RMA-2002"]
    assert facts["return_reference"] == "RMA-2002"


async def test_a_replayed_outcome_does_not_double_the_rma(context: Any) -> None:
    """Temporal will re-run this activity. Twice must look like once.

    The ids come from the workflow, so the second run collides with the unique
    index and the fact id is the same string -- the append is a no-op rather
    than a second RMA in the associate's context.
    """
    repository, activities, store = context
    case_id = await _case(store)
    outcome = _outcome(case_id, _record("RMA-3003", tracking_reference="1Z777"))

    await activities.record_support_outcome(outcome)
    await activities.record_support_outcome(outcome)

    assert len(await repository.list_return_records(case_id)) == 1
    facts = await store.case_facts(case_id)
    assert facts["return_reference"] == "RMA-3003"
    assert len(await _facts_named(repository, case_id, "return_reference")) == 1


async def test_two_rmas_on_one_case_both_land(context: Any) -> None:
    """One reply, two RMAs -- the case the item-level model could not express.

    Both return records exist. The fact projection is last-write-wins by
    design, so it names one of them; the records are where "how many" lives,
    which is why the copilot reads records and not facts for that panel.
    """
    repository, activities, store = context
    case_id = await _case(store)

    await activities.record_support_outcome(
        _outcome(
            case_id,
            _record("RMA-4004", return_location="DC-7"),
            _record("RMA-4005", return_location="VENDOR-3"),
        )
    )

    references = {
        str(record["returnReference"]) for record in await repository.list_return_records(case_id)
    }
    assert references == {"RMA-4004", "RMA-4005"}
    facts = await store.case_facts(case_id)
    assert facts["return_reference"] in references


async def test_the_outcome_is_attributed_to_channel_b(context: Any) -> None:
    """Provenance survives the hop (D3: keep provenance, build no reconciler).

    Without this the associate's context would carry an RMA indistinguishable
    from something they said themselves, and nothing downstream could tell a
    fact Support observed from a fact a person asserted.
    """
    repository, activities, store = context
    case_id = await _case(store)

    await activities.record_support_outcome(_outcome(case_id, _record("RMA-5005")))

    fact = (await _facts_named(repository, case_id, "return_reference"))[0]
    assert fact["channel"] == "CHANNEL_B"
    assert fact["acquisitionMethod"] == "OBSERVED"
    assert fact["sourceSystem"] == "RETURN_SUPPORT"


async def test_absent_optional_references_are_not_recorded_as_facts(context: Any) -> None:
    """Support not knowing the tracking number yet is not a tracking number.

    A `None` written as a fact would show the agent an empty value it would
    then report to the associate as the answer.
    """
    _, activities, store = context
    case_id = await _case(store)

    await activities.record_support_outcome(_outcome(case_id, _record("RMA-6006")))

    facts = await store.case_facts(case_id)
    assert "tracking_reference" not in facts
    assert "label_reference" not in facts
    assert facts["return_reference"] == "RMA-6006"


async def test_the_workflow_id_is_derivable_from_the_case() -> None:
    """The Support console reaches the workflow with no lookup table.

    `submit_return_outcome` holds a work item, reads its case id, and signals
    `return-case-<case>`. If this were stored instead of derived, an outcome
    would depend on a second read that can be missing.
    """
    assert return_case_workflow_id("abc-123") == "return-case-abc-123"
