"""The case aggregate: one case per return, facts that cannot be clobbered.

No case entity existed. A discovery conversation and a return session were
unrelated documents, and the only link between a support outcome and the
associate's chat was the browser matching an order reference client-side -- so
if two open orders shared a reference, or the tab closed, the link was gone.

Two properties are load-bearing and both are asserted against real MongoDB:

* **Facts are append-only.** Bay, Support, Fulfillment and Channel A write
  concurrently. A single mutable `facts` sub-document would make the last
  writer win and silently drop what the others learned.
* **Both channels resolve to the same case.** That is the whole mechanism by
  which a support outcome reaches the original conversation.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from pydantic import SecretStr
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.operations.models import (
    CaseFactView,
    CaseStatus,
    CaseView,
    FactAcquisition,
    FactChannel,
    ReturnRecordView,
)
from return_platform.operations.repository import ConcurrencyConflictError, OperationalRepository

# `loop_scope="module"` is required by the module-scoped `repository` fixture
# below: `AsyncMongoClient` binds to the loop it was created on, so a
# function-scoped loop would make every test a different loop from the client's.
pytestmark = pytest.mark.asyncio(loop_scope="module")


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return f"mongodb://{username}:{password}@{host}:27017/return_platform?authSource=admin"


TENANT = "tenant-a"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def repository() -> Any:
    """A repository on an isolated database, dropped after the module.

    A dedicated database rather than a collection prefix: `OperationalRepository`
    owns twenty-odd collections and builds their handles in `__init__`, so
    isolating one of them would be a fiction.

    Module-scoped because `ensure_indexes` creates roughly fifty indexes, and
    paying that per test made this file take six minutes. Isolation comes from
    `principal` below instead -- every test gets its own principal id, so no
    test can see another's cases. That is also closer to how the collection is
    actually used than a database nobody else is writing to.
    """
    database = f"case_aggregate_test_{uuid.uuid4().hex[:12]}"
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    settings = Settings(
        environment="test",
        mongo_dsn=SecretStr(_mongo_dsn()),
        mongo_database=database,
        source_mongo_database=database,
    )
    made = OperationalRepository(client, settings)
    await made.ensure_indexes()
    try:
        yield made
    finally:
        await client.drop_database(database)
        await client.close()


@pytest.fixture
def principal() -> str:
    """One principal per test, so a shared database still isolates."""
    return f"associate-{uuid.uuid4().hex[:8]}"


def _stored(document: dict[str, Any]) -> dict[str, Any]:
    """A read-back document without Mongo's `_id`.

    The repository returns raw documents by design and the API layer strips
    `_id` on the way out (`api/canonical_returns._without_mongo_id`). The
    contracts are `extra="forbid"`, so validating a raw read-back would fail on
    the ObjectId rather than on anything about the case.
    """
    return {key: value for key, value in document.items() if key != "_id"}


async def _case(
    repository: OperationalRepository, principal: str, **overrides: Any
) -> dict[str, Any]:
    return await repository.create_case(
        case_id=str(uuid.uuid4()),
        tenant_id=TENANT,
        principal_id=principal,
        **overrides,
    )


# ---------------------------------------------------------------------------
# Concurrency: the property the append-only design exists for
# ---------------------------------------------------------------------------


async def test_concurrent_fact_writers_all_survive(
    repository: OperationalRepository, principal: str
) -> None:
    """Twelve writers, twelve facts. None lost.

    This is what a nested mutable `facts` document could not give: each of
    these would read, modify and write the same field, and eleven of them would
    be overwritten by whichever finished last.
    """
    case = await _case(repository, principal)

    async def write(index: int) -> None:
        await repository.append_case_fact(
            fact_id=str(uuid.uuid4()),
            case_id=case["caseId"],
            fact_name=f"fact_{index}",
            value=index,
            agent_id=f"agent-{index}",
            channel=FactChannel.SYSTEM,
            acquisition_method=FactAcquisition.DERIVED,
        )

    await asyncio.gather(*(write(index) for index in range(12)))

    facts = await repository.list_case_facts(case["caseId"])
    assert len(facts) == 12
    assert {fact["factName"] for fact in facts} == {f"fact_{index}" for index in range(12)}


async def test_concurrent_writers_of_the_same_fact_name_all_survive(
    repository: OperationalRepository, principal: str
) -> None:
    """Two agents observing the same thing at once is not a conflict.

    Bay and Support can both report a return location. Both observations are
    kept; the projection decides which is current, and neither is destroyed to
    record the other.
    """
    case = await _case(repository, principal)

    async def write(agent: str, value: str) -> None:
        await repository.append_case_fact(
            fact_id=str(uuid.uuid4()),
            case_id=case["caseId"],
            fact_name="return_location",
            value=value,
            agent_id=agent,
            channel=FactChannel.SYSTEM,
            acquisition_method=FactAcquisition.OBSERVED,
        )

    await asyncio.gather(write("bay", "BAY-12"), write("support", "DOCK-3"))

    facts = await repository.list_case_facts(case["caseId"])
    assert len(facts) == 2
    assert {fact["value"] for fact in facts} == {"BAY-12", "DOCK-3"}

    latest = await repository.latest_case_facts(case["caseId"])
    assert set(latest) == {"return_location"}
    assert latest["return_location"]["value"] in {"BAY-12", "DOCK-3"}


async def test_the_projection_returns_the_newest_value_per_name(
    repository: OperationalRepository, principal: str
) -> None:
    case = await _case(repository, principal)
    for value in ("first", "second", "third"):
        await repository.append_case_fact(
            fact_id=str(uuid.uuid4()),
            case_id=case["caseId"],
            fact_name="return_reason",
            value=value,
            agent_id="order-discovery",
            channel=FactChannel.CHANNEL_A,
            acquisition_method=FactAcquisition.STATED,
        )

    latest = await repository.latest_case_facts(case["caseId"])

    assert latest["return_reason"]["value"] == "third"
    # And the earlier ones are still there to explain how it got that way.
    assert len(await repository.list_case_facts(case["caseId"])) == 3


async def test_a_fact_carries_its_full_provenance(
    repository: OperationalRepository, principal: str
) -> None:
    """Provenance is the point of the log, not decoration.

    An agent deciding whether to re-ask needs to know a value was STATED by a
    person and never verified, rather than OBSERVED from a source system.
    """
    case = await _case(repository, principal)
    superseded = await repository.append_case_fact(
        fact_id=str(uuid.uuid4()),
        case_id=case["caseId"],
        fact_name="order_reference",
        value="CW273354",
        agent_id="order-discovery-agent",
        channel=FactChannel.CHANNEL_A,
        acquisition_method=FactAcquisition.STATED,
        turn_id="turn-7",
        correlation_id="corr-1",
    )
    corrected = await repository.append_case_fact(
        fact_id=str(uuid.uuid4()),
        case_id=case["caseId"],
        fact_name="order_reference",
        value="CW273355",
        agent_id="order-discovery-agent",
        channel=FactChannel.CHANNEL_A,
        acquisition_method=FactAcquisition.OBSERVED,
        source_system="salesInv",
        source_path="ORDER_SEARCH",
        supersedes_fact_id=superseded["factId"],
    )

    # Validated back through its contract: the repository writes plain dicts by
    # design, so a stored shape the model refuses is drift nothing else catches.
    validated = CaseFactView.model_validate(corrected)
    assert validated.acquisitionMethod is FactAcquisition.OBSERVED
    assert validated.sourceSystem == "salesInv"
    assert validated.supersedesFactId == superseded["factId"]
    # The superseded observation is retained, not deleted.
    assert len(await repository.list_case_facts(case["caseId"])) == 2


# ---------------------------------------------------------------------------
# Channel linkage: why the case exists at all
# ---------------------------------------------------------------------------


async def test_a_case_is_reachable_from_both_channels(
    repository: OperationalRepository, principal: str
) -> None:
    """Channel B -> case -> Channel A, server-side.

    Previously impossible: the only bridge was the browser matching an order
    reference across two unrelated collections.
    """
    conversation_id = str(uuid.uuid4())
    case = await _case(repository, principal, channel_a_conversation_id=conversation_id)

    work_item_id = str(uuid.uuid4())
    await repository.update_case(
        case["caseId"],
        {"channelBWorkItemId": work_item_id, "status": CaseStatus.AWAITING_SUPPORT.value},
        expected_version=0,
    )

    from_a = await repository.get_case_by_conversation(conversation_id)
    from_b = await repository.get_case_by_work_item(work_item_id)

    assert from_a is not None and from_b is not None
    assert from_a["caseId"] == from_b["caseId"] == case["caseId"]
    assert from_b["channelAConversationId"] == conversation_id


async def test_creating_a_case_twice_for_one_conversation_yields_one_case(
    repository: OperationalRepository, principal: str
) -> None:
    """A confirmation turn can be retried by Temporal. It must not fork the case."""
    conversation_id = str(uuid.uuid4())

    first = await _case(repository, principal, channel_a_conversation_id=conversation_id)
    second = await _case(repository, principal, channel_a_conversation_id=conversation_id)

    assert first["caseId"] == second["caseId"]
    assert (
        len(await repository.list_cases_for_principal(tenant_id=TENANT, principal_id=principal))
        == 1
    )


async def test_a_case_survives_and_round_trips_through_its_contract(
    repository: OperationalRepository, principal: str
) -> None:
    case = await _case(
        repository, principal, branch_id="CHARLOTTE", confirmed_order_reference="CW273354"
    )

    reloaded = await repository.get_case(case["caseId"])

    assert reloaded is not None
    validated = CaseView.model_validate(_stored(reloaded))
    assert validated.status is CaseStatus.GATHERING_INFO
    assert validated.branchId == "CHARLOTTE"
    assert validated.confirmedOrderReference == "CW273354"


async def test_case_updates_are_optimistically_concurrent(
    repository: OperationalRepository, principal: str
) -> None:
    """Two writers on the *case* do conflict -- unlike facts, which do not.

    The case row holds a small set of mutually-dependent fields (status and the
    channel pointers), so a lost update there is a real inconsistency rather
    than a dropped observation.
    """
    case = await _case(repository, principal)
    await repository.update_case(case["caseId"], {"branchId": "A"}, expected_version=0)

    with pytest.raises(ConcurrencyConflictError):
        await repository.update_case(case["caseId"], {"branchId": "B"}, expected_version=0)


async def test_cases_are_listed_per_principal(
    repository: OperationalRepository, principal: str
) -> None:
    await _case(repository, principal)
    await repository.create_case(
        case_id=str(uuid.uuid4()), tenant_id=TENANT, principal_id="other-associate"
    )

    mine = await repository.list_cases_for_principal(tenant_id=TENANT, principal_id=principal)

    assert len(mine) == 1
    assert mine[0]["principalId"] == principal


# ---------------------------------------------------------------------------
# Multi-RMA: one case, N return records
# ---------------------------------------------------------------------------


async def test_one_case_carries_several_return_records(
    repository: OperationalRepository, principal: str
) -> None:
    """The shape `ReturnSessionView`'s singular scalars cannot express.

    A multi-item return can produce two RMAs with different labels and
    different return locations. Neither belongs on the case (one each) nor on
    the item (one per item).
    """
    case = await _case(repository, principal)

    first = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case["caseId"], return_reference="RMA-1"
    )
    second = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case["caseId"], return_reference="RMA-2"
    )

    await repository.update_return_record(
        first["returnRecordId"],
        {"labelReference": "LBL-1", "returnLocation": "DOCK-3", "trackingReference": "TRK-1"},
        expected_version=0,
    )
    await repository.update_return_record(
        second["returnRecordId"],
        {"labelReference": "LBL-2", "returnLocation": "BAY-12", "trackingReference": "TRK-2"},
        expected_version=0,
    )

    records = await repository.list_return_records(case["caseId"])

    assert len(records) == 2
    by_reference = {record["returnReference"]: record for record in records}
    # The assertion that matters: artifacts never cross between records.
    assert by_reference["RMA-1"]["labelReference"] == "LBL-1"
    assert by_reference["RMA-1"]["returnLocation"] == "DOCK-3"
    assert by_reference["RMA-2"]["labelReference"] == "LBL-2"
    assert by_reference["RMA-2"]["returnLocation"] == "BAY-12"
    for record in records:
        ReturnRecordView.model_validate(_stored(record))


async def test_the_same_rma_cannot_be_recorded_twice_on_one_case(
    repository: OperationalRepository, principal: str
) -> None:
    """Support replying twice must not create a second record for one RMA."""
    from pymongo.errors import DuplicateKeyError

    case = await _case(repository, principal)
    await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case["caseId"], return_reference="RMA-1"
    )

    with pytest.raises(DuplicateKeyError):
        await repository.create_return_record(
            return_record_id=str(uuid.uuid4()), case_id=case["caseId"], return_reference="RMA-1"
        )


async def test_return_records_without_an_rma_yet_do_not_collide(
    repository: OperationalRepository, principal: str
) -> None:
    """A record exists from the moment the case decides to raise it.

    Its RMA arrives later from Support, so several records on one case can sit
    with a null reference at the same time -- which a non-sparse unique index
    would reject.
    """
    case = await _case(repository, principal)

    await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case["caseId"]
    )
    await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case["caseId"]
    )

    assert len(await repository.list_return_records(case["caseId"])) == 2
