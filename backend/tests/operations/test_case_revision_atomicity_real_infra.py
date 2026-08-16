"""Plan sect. 6.5's revision invariant, against a real replica set.

> Any write that can change the `CaseDetail` projection must, in the same
> transaction, bump `case.revision` and set `case.updatedAt`.

Before this, `cases.version` moved only when the *case document* was written.
Every child write -- a fact, an RMA, an RMA's label, a selected item -- changed
the `CaseDetail` projection and left the revision alone, which made Phase 8's
stale-response guard ("client holds 18, response carries 17 -> ignore")
decorative for everything except an explicit case update. A Support reply
writing an RMA is the case that mattered: the projection changed completely and
the client had no way to tell it apart from a stale one.

**Nothing here can be settled by a double.** The properties under test are the
storage engine's: a transaction that rolls a bump back when the child write
fails, a unique index that turns a replay into a no-op, and `$inc` under
`with_transaction` arbitrating writers who arrive together. A fake collection
that increments a dict would answer every one of them "yes" while the
production repository answered "no", which is exactly how the invariant went
missing while looking covered.

Deliberately not in scope: the Support work-item writers in
`operations/return_support/service.py`, and `update_return_item` /
`assign_return_item_to_record`, which live in `operations/repository.py` --
`OperationalRepository` shadows this mixin's item collection with the
session-scoped view, so they are not reachable from here. They hold the
invariant now, and `test_support_writers_revision_atomicity_real_infra.py`
proves it; that module also runs the one contended window all seven writers
enter together.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from pydantic import SecretStr
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from return_platform.configuration.settings import Settings
from return_platform.operations.errors import ConcurrencyConflictError
from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.repository import OperationalRepository

# `loop_scope="module"` is required by the module-scoped `repository` fixture:
# `AsyncMongoClient` binds to the loop it was created on.
pytestmark = pytest.mark.asyncio(loop_scope="module")

TENANT = "tenant-revision"

#: How many writers enter the contended window together.
#:
#: Eight rather than two, and spread across three different child collections.
#: Two proves a race exists; eight makes a lost `$inc` fail reliably rather
#: than once in a hundred runs.
CONTENDERS = 8


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    """`directConnection=true` -- the replica set advertises an internal hostname."""
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    return (
        f"mongodb://{username}:{password}@{host}:27017/"
        "return_platform?authSource=admin&directConnection=true"
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def repository() -> Any:
    """A repository on an isolated database, with its real indexes, dropped after.

    `ensure_indexes` is not optional here: the replay assertion depends on the
    unique partial index over `(caseId, returnReference)` being the thing that
    refuses the second write. Without it every replay would succeed and this
    module would report the opposite of the truth.

    Module-scoped because those indexes cost seconds; isolation comes from a
    fresh case per test instead.
    """
    database = f"case_revision_test_{uuid.uuid4().hex[:12]}"
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


async def _seed_case(repository: OperationalRepository) -> str:
    case_id = str(uuid.uuid4())
    await repository.create_case(
        case_id=case_id,
        tenant_id=TENANT,
        principal_id=f"associate-{uuid.uuid4().hex[:8]}",
        channel_a_conversation_id=f"conv-{uuid.uuid4().hex[:12]}",
        confirmed_order_reference="CW273354",
        confirmation_key=f"{TENANT}|{uuid.uuid4().hex}|CW273354|",
    )
    return case_id


async def _revision(repository: OperationalRepository, case_id: str) -> tuple[int, datetime]:
    case = await repository.get_case(case_id)
    assert case is not None
    updated_at = case["updatedAt"]
    assert isinstance(updated_at, datetime)
    return int(case["version"]), updated_at


# ---------------------------------------------------------------------------
# Each writer moves the revision by exactly one
# ---------------------------------------------------------------------------


async def test_appending_a_fact_advances_the_revision_and_updatedat(
    repository: OperationalRepository,
) -> None:
    """A fact is on the projection, so a client holding the old revision is stale."""
    case_id = await _seed_case(repository)
    before, before_at = await _revision(repository, case_id)

    await repository.append_case_fact(
        fact_id=str(uuid.uuid4()),
        case_id=case_id,
        fact_name="return_reason",
        value="damaged",
        agent_id="order-discovery",
        channel=FactChannel.CHANNEL_A,
        acquisition_method=FactAcquisition.STATED,
    )

    after, after_at = await _revision(repository, case_id)
    assert after == before + 1
    assert after_at > before_at


async def test_creating_a_return_record_advances_the_revision(
    repository: OperationalRepository,
) -> None:
    """The Support reply that writes an RMA. The case the guard exists for."""
    case_id = await _seed_case(repository)
    before, before_at = await _revision(repository, case_id)

    await repository.create_return_record(
        return_record_id=str(uuid.uuid4()),
        case_id=case_id,
        return_reference=f"RMA-{uuid.uuid4().hex[:8]}",
        status="ISSUED",
        source_system="RETURN_SUPPORT",
    )

    after, after_at = await _revision(repository, case_id)
    assert after == before + 1
    assert after_at > before_at


async def test_updating_a_return_record_advances_the_revision(
    repository: OperationalRepository,
) -> None:
    """The label and the tracking reference arrive here, one write after the RMA."""
    case_id = await _seed_case(repository)
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()),
        case_id=case_id,
        return_reference=f"RMA-{uuid.uuid4().hex[:8]}",
    )
    before, before_at = await _revision(repository, case_id)

    await repository.update_return_record(
        str(record["returnRecordId"]),
        {"labelReference": "LBL-1", "trackingReference": "TRK-1"},
        expected_version=0,
    )

    after, after_at = await _revision(repository, case_id)
    assert after == before + 1
    assert after_at > before_at


async def test_creating_a_case_return_item_advances_the_revision(
    repository: OperationalRepository,
) -> None:
    case_id = await _seed_case(repository)
    before, before_at = await _revision(repository, case_id)

    await repository.create_case_return_item(
        return_item_id=str(uuid.uuid4()),
        case_id=case_id,
        return_record_id=None,
        order_line_reference="L1",
        quantity=2,
    )

    after, after_at = await _revision(repository, case_id)
    assert after == before + 1
    assert after_at > before_at


# ---------------------------------------------------------------------------
# A no-op must move nothing
# ---------------------------------------------------------------------------


async def test_a_replayed_return_record_advances_the_revision_by_zero(
    repository: OperationalRepository,
) -> None:
    """Temporal replays `record_support_outcome` with pre-minted ids.

    The unique partial index on `(caseId, returnReference)` is what turns the
    second attempt into a no-op, and the activity absorbs the error. If that
    path bumped, every redelivery of a message nobody acted on would invalidate
    every client's cache -- and the guard would be back to noise, from the other
    direction.
    """
    case_id = await _seed_case(repository)
    reference = f"RMA-{uuid.uuid4().hex[:8]}"
    await repository.create_return_record(
        return_record_id=str(uuid.uuid4()),
        case_id=case_id,
        return_reference=reference,
        status="ISSUED",
    )
    before, before_at = await _revision(repository, case_id)

    with pytest.raises(DuplicateKeyError):
        await repository.create_return_record(
            # A fresh record id, as the workflow's own concurrency test uses:
            # the rule under test is that one RMA cannot be recorded twice on
            # one case however the record is identified.
            return_record_id=str(uuid.uuid4()),
            case_id=case_id,
            return_reference=reference,
            status="ISSUED",
        )

    after, after_at = await _revision(repository, case_id)
    assert after == before, "a replay that wrote nothing advanced the revision"
    assert after_at == before_at, "a replay that wrote nothing moved updatedAt"
    assert len(await repository.list_return_records(case_id)) == 1


async def test_a_replayed_fact_advances_the_revision_by_zero(
    repository: OperationalRepository,
) -> None:
    """Derived `factId`s are the same shape of replay, one collection over.

    `_append_fact_once` absorbs the duplicate so a Temporal retry can get past
    a marker its previous attempt already wrote; the retry learned nothing new
    and must therefore invalidate nothing.
    """
    case_id = await _seed_case(repository)
    fact_id = f"rma-{case_id}"
    fact = {
        "fact_id": fact_id,
        "case_id": case_id,
        "fact_name": "return_reference",
        "value": "RMA-REPLAY",
        "agent_id": "return-support",
        "channel": FactChannel.CHANNEL_B,
        "acquisition_method": FactAcquisition.OBSERVED,
    }
    await repository.append_case_fact(**fact)  # type: ignore[arg-type]
    before, before_at = await _revision(repository, case_id)

    with pytest.raises(DuplicateKeyError):
        await repository.append_case_fact(**fact)  # type: ignore[arg-type]

    after, after_at = await _revision(repository, case_id)
    assert after == before
    assert after_at == before_at
    assert len(await repository.list_case_facts(case_id)) == 1


async def test_a_failed_child_write_leaves_the_revision_where_it_was(
    repository: OperationalRepository,
) -> None:
    """Atomicity, not best effort.

    The record's own compare-and-set rejects this write, and the rejection is
    raised from inside the transaction. A repair that bumped the case first and
    updated the record second would leave a revision advertising a change that
    never happened, and every client would discard a projection that was in
    fact current.
    """
    case_id = await _seed_case(repository)
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id
    )
    await repository.update_return_record(
        str(record["returnRecordId"]), {"status": "ISSUED"}, expected_version=0
    )
    before, before_at = await _revision(repository, case_id)

    with pytest.raises(ConcurrencyConflictError):
        await repository.update_return_record(
            str(record["returnRecordId"]), {"status": "CANCELLED"}, expected_version=0
        )

    after, after_at = await _revision(repository, case_id)
    assert after == before
    assert after_at == before_at


# ---------------------------------------------------------------------------
# The concurrency test plan sect. 6.5 names by hand
# ---------------------------------------------------------------------------


async def _released_together(count: int, work: Any) -> list[Any]:
    """Run `work(index)` `count` times, all released from one barrier.

    `asyncio.gather` alone does not produce a race against a fast local
    datastore: the first task can run to completion inside the window the
    others are still being scheduled in. The barrier moves every task to the
    same starting line, so the contended interval is the operation itself.
    """
    barrier = asyncio.Barrier(count)

    async def _run(index: int) -> Any:
        await barrier.wait()
        return await work(index)

    return await asyncio.gather(*(_run(index) for index in range(count)), return_exceptions=True)


async def test_concurrent_child_writes_produce_distinct_increasing_revisions(
    repository: OperationalRepository,
) -> None:
    """Plan sect. 6.5's named test: no lost update, across three collections.

    Eight writers on three *different* child collections, released together.
    Each bumps the same one-document counter, so every pair of them contends;
    MongoDB aborts the loser as a transient error and `with_transaction`
    re-runs the whole callback, child write included.

    The assertion is arithmetic and it is the strong one. `$inc` is atomic, so
    eight successful writes can only produce eight distinct, strictly
    increasing revisions -- unless one was lost, in which case the total is
    short. A read-modify-write repair (`get_case` then
    `update_case(expected_version=...)`) fails here two ways at once: the
    losers raise `ConcurrencyConflictError` and the total lands under eight.
    """
    case_id = await _seed_case(repository)
    before, before_at = await _revision(repository, case_id)

    async def _write(index: int) -> str:
        collection = index % 3
        if collection == 0:
            await repository.append_case_fact(
                fact_id=f"fact-{case_id}-{index}",
                case_id=case_id,
                fact_name=f"fact_{index}",
                value=index,
                agent_id=f"agent-{index}",
                channel=FactChannel.SYSTEM,
                acquisition_method=FactAcquisition.OBSERVED,
            )
            return "fact"
        if collection == 1:
            await repository.create_return_record(
                return_record_id=f"rr-{case_id}-{index}",
                case_id=case_id,
                return_reference=f"RMA-{case_id[:8]}-{index}",
                status="ISSUED",
            )
            return "record"
        await repository.create_case_return_item(
            return_item_id=f"item-{case_id}-{index}",
            case_id=case_id,
            return_record_id=None,
            order_line_reference=f"L{index}",
        )
        return "item"

    results = await _released_together(CONTENDERS, _write)
    failures = [result for result in results if isinstance(result, BaseException)]
    assert not failures, f"a child writer failed under contention: {failures}"

    after, after_at = await _revision(repository, case_id)
    assert after == before + CONTENDERS, (
        f"{CONTENDERS} concurrent child writes advanced the revision by "
        f"{after - before}: a bump was lost"
    )
    assert after_at > before_at

    # And every child actually landed -- a total that matched because writes
    # were dropped in the same proportion would be the same number.
    assert len(await repository.list_case_facts(case_id)) == 3
    assert len(await repository.list_return_records(case_id)) == 3
    assert len(await repository.list_case_return_items(case_id)) == 2


async def test_two_writers_on_different_collections_both_leave_a_revision(
    repository: OperationalRepository,
) -> None:
    """The plan's wording, exactly: *two* writers, two collections, two revisions.

    The eight-way test above is the one that fails reliably; this is the shape
    the invariant is stated in, kept separately so a regression reads as the
    sentence it broke.
    """
    case_id = await _seed_case(repository)
    before, _ = await _revision(repository, case_id)

    async def _write(index: int) -> None:
        if index == 0:
            await repository.append_case_fact(
                fact_id=f"fact-{case_id}-a",
                case_id=case_id,
                fact_name="return_location",
                value="BAY-12",
                agent_id="bay",
                channel=FactChannel.SYSTEM,
                acquisition_method=FactAcquisition.OBSERVED,
            )
            return
        await repository.create_return_record(
            return_record_id=f"rr-{case_id}-b", case_id=case_id, return_reference="RMA-PAIR"
        )

    results = await _released_together(2, _write)
    assert not [result for result in results if isinstance(result, BaseException)]

    after, _ = await _revision(repository, case_id)
    assert after == before + 2


# ---------------------------------------------------------------------------
# The read ordering the invariant does not replace
# ---------------------------------------------------------------------------


async def test_the_projection_never_reports_a_revision_newer_than_its_children(
    repository: OperationalRepository,
) -> None:
    """`load_case_projection_state` reads the case first, and still must.

    Four separate reads are not a snapshot, so a concurrent writer can land
    between any two of them whatever the revision does. Reading the case first
    bounds the error in the safe direction: the reported revision can only be
    *older* than the children, so a client comparing revisions discards the
    response and polls again. The other order reports a fresh revision over
    stale children, and the client accepts it and stops looking.

    Asserted as arithmetic rather than by inspecting the source. Every child
    write moves the revision by exactly one, so a projection that returned `k`
    children can only be honest if the revision it reported is no greater than
    the seed revision plus `k`. A reordered read would exceed that as soon as a
    write landed mid-sequence, which is what the writer beside it arranges.
    """
    case_id = await _seed_case(repository)
    seed, _ = await _revision(repository, case_id)

    total = 24
    observations: list[tuple[int, int]] = []
    stop = asyncio.Event()

    async def _write() -> None:
        for index in range(total):
            await repository.append_case_fact(
                fact_id=f"fact-{case_id}-{index}",
                case_id=case_id,
                fact_name=f"fact_{index}",
                value=index,
                agent_id="writer",
                channel=FactChannel.SYSTEM,
                acquisition_method=FactAcquisition.OBSERVED,
            )
        stop.set()

    async def _read() -> None:
        while not stop.is_set():
            state = await repository.load_case_projection_state(case_id)
            assert state is not None
            observations.append((state.revision, len(state.facts or ())))

    await asyncio.gather(_write(), _read())

    assert observations, "the reader never observed the case"
    for revision, children in observations:
        assert revision <= seed + children, (
            f"the projection reported revision {revision} over {children} children "
            f"(seed {seed}): the case was read after them"
        )
