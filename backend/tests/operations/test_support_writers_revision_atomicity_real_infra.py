"""The last writers of plan sect. 6.5, against a real replica set (D22, D27, D28).

> Any write that can change the `CaseDetail` projection must, in the same
> transaction, bump `case.revision` and set `case.updatedAt`.

`test_case_revision_atomicity_real_infra.py` settled the four writers in
`case_repository.py` and named these as explicitly out of scope:

> Deliberately not in scope: the Support work-item writers in
> `operations/return_support/service.py`, and `update_return_item` /
> `assign_return_item_to_record`, which live in `operations/repository.py`. They
> do not hold the invariant yet.

They hold it now, and this is where that is proved. The two in
`operations/repository.py` are there and not in `case_repository.py` because
`OperationalRepository` shadows the mixin's item collection with the
session-scoped view, which is exactly why an audit scoped to the mixin's file
could not see them.

`test_every_projection_writer_contends_at_once_and_loses_nothing` below is the
one place all seven -- the mixin's four, the concrete class's two, and
`apply_action` -- are released into one contended window together, so "all six
writers hold it" is a single arithmetic assertion rather than six separate ones
that could each be true while the set was incomplete.

**Nothing here can be settled by a double.** The properties are the storage
engine's: a transaction that rolls a bump back when the child write is refused,
one that rolls a work item back when its opening message cannot be written, and
`$inc` under `with_transaction` arbitrating writers who arrive together. A fake
collection that mutated a dict would answer every one of them "yes" while the
production code answered "no" -- which is exactly how the invariant went missing
while looking covered.

`add_message` and `post_reminder` are deliberately absent, and their absence is
a decision rather than an oversight: nothing they write is on `SupportProjection`
today. They become required the moment it grows a message field.
`open_case_thread` is here for a different reason -- it writes no case field at
all, and what it needed was durability rather than a revision. See the tests
that say so.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from pydantic import SecretStr
from pymongo import AsyncMongoClient

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.settings import Settings
from return_platform.operations.errors import ConcurrencyConflictError
from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_support.service import (
    ReturnSupportService,
    SupportAction,
    SupportActionRequest,
    SupportWorkItemStatus,
)

# `loop_scope="module"` is required by the module-scoped fixtures below:
# `AsyncMongoClient` binds to the loop it was created on.
pytestmark = pytest.mark.asyncio(loop_scope="module")

TENANT = "tenant-support-revision"

#: How many writers enter the contended window together. Ten rather than two,
#: and spread across four child collections, so a lost `$inc` fails reliably
#: rather than once in a hundred runs. Ten because it is one per writer plan
#: sect. 6.5 binds, plus the duplicates that make two writers on the same
#: collection contend with each other as well as with the counter.
CONTENDERS = 10


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
async def platform() -> Any:
    """A repository and a Support service on one isolated database, dropped after.

    One client for both, because the transaction that carries the work-item
    write and the case bump has to span two collections the service and the
    repository each own a handle to. Two clients would be two sessions and no
    invariant at all -- which is itself worth pinning, and is why the service
    takes the client rather than a database.
    """
    database = f"support_revision_test_{uuid.uuid4().hex[:12]}"
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(_mongo_dsn())
    settings = Settings(
        environment="test",
        mongo_dsn=SecretStr(_mongo_dsn()),
        mongo_database=database,
        source_mongo_database=database,
    )
    repository = OperationalRepository(client, settings)
    await repository.ensure_indexes()
    service = ReturnSupportService(
        client=client,
        settings=settings,
        configuration=load_return_configuration(
            Path("config/returns/production.yaml")
        ).configuration,
        operational_repository=repository,
    )
    await service.ensure_indexes()
    try:
        yield repository, service
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


async def _seed_item(repository: OperationalRepository, case_id: str) -> str:
    item_id = str(uuid.uuid4())
    await repository.create_case_return_item(
        return_item_id=item_id,
        case_id=case_id,
        return_record_id=None,
        order_line_reference=f"L-{uuid.uuid4().hex[:6]}",
    )
    return item_id


async def _seed_thread(service: ReturnSupportService, case_id: str) -> str:
    return await service.open_case_thread(
        case_id=case_id,
        tenant_id=TENANT,
        principal_id="associate-1",
        support_draft="Please issue an RMA for this return.",
        idempotency_key=f"case-thread:{case_id}",
    )


def _action(action: SupportAction, version: int, **fields: Any) -> SupportActionRequest:
    return SupportActionRequest(
        action=action, expectedVersion=version, reason="because Support said so", **fields
    )


# ---------------------------------------------------------------------------
# Each newly-fixed writer moves the revision by exactly one
# ---------------------------------------------------------------------------


async def test_assigning_an_item_to_a_record_advances_the_revision(platform: Any) -> None:
    """The live one. `record_support_outcome` calls it on every outcome that maps
    order lines to an RMA, and `returnRecordId` is the attribution
    `selectedItems` renders."""
    repository, _service = platform
    case_id = await _seed_case(repository)
    item_id = await _seed_item(repository, case_id)
    before, before_at = await _revision(repository, case_id)

    await repository.assign_return_item_to_record(
        item_id, return_record_id=str(uuid.uuid4()), expected_version=0
    )

    after, after_at = await _revision(repository, case_id)
    assert after == before + 1
    assert after_at > before_at


async def test_updating_a_case_item_advances_the_revision(platform: Any) -> None:
    repository, _service = platform
    case_id = await _seed_case(repository)
    item_id = await _seed_item(repository, case_id)
    before, before_at = await _revision(repository, case_id)

    await repository.update_return_item(item_id, {"quantity": 3}, expected_version=0)

    after, after_at = await _revision(repository, case_id)
    assert after == before + 1
    assert after_at > before_at


async def test_a_support_action_advances_the_revision(platform: Any) -> None:
    """`SupportProjection` reads `status` off this document, so acknowledging a
    thread changes the projection a client is polling."""
    repository, service = platform
    case_id = await _seed_case(repository)
    work_item_id = await _seed_thread(service, case_id)
    before, before_at = await _revision(repository, case_id)

    item = await service.apply_action(
        work_item_id, _action(SupportAction.ACKNOWLEDGE, 0), actor_id="support-1"
    )

    assert item.status is SupportWorkItemStatus.ACKNOWLEDGED
    after, after_at = await _revision(repository, case_id)
    assert after == before + 1
    assert after_at > before_at


async def test_a_second_support_action_advances_it_again(platform: Any) -> None:
    """Two actions, two revisions. A client that saw the first must still be
    told about the second."""
    repository, service = platform
    case_id = await _seed_case(repository)
    work_item_id = await _seed_thread(service, case_id)
    before, _ = await _revision(repository, case_id)

    await service.apply_action(
        work_item_id, _action(SupportAction.ACKNOWLEDGE, 0), actor_id="support-1"
    )
    await service.apply_action(
        work_item_id,
        _action(SupportAction.ASSIGN, 1, assignee="support-2"),
        actor_id="support-1",
    )

    after, _ = await _revision(repository, case_id)
    assert after == before + 2


# ---------------------------------------------------------------------------
# A no-op must move nothing
# ---------------------------------------------------------------------------


async def test_a_replayed_item_assignment_advances_the_revision_by_zero(
    platform: Any,
) -> None:
    """The redelivery case, stated in the shape it actually arrives in.

    `_assign_items_to_record` re-reads the items before assigning and skips one
    that already has a `returnRecordId`, so the second delivery reaches this
    method with the version the first left behind and loses the compare-and-set.
    The refusal raises from inside the transaction, so the bump rolls back --
    and a redelivery of a message nobody acted on invalidates nobody's cache.
    """
    repository, _service = platform
    case_id = await _seed_case(repository)
    item_id = await _seed_item(repository, case_id)
    record_id = str(uuid.uuid4())
    await repository.assign_return_item_to_record(
        item_id, return_record_id=record_id, expected_version=0
    )
    before, before_at = await _revision(repository, case_id)

    with pytest.raises(ConcurrencyConflictError):
        await repository.assign_return_item_to_record(
            item_id, return_record_id=record_id, expected_version=0
        )

    after, after_at = await _revision(repository, case_id)
    assert after == before, "a replay that wrote nothing advanced the revision"
    assert after_at == before_at, "a replay that wrote nothing moved updatedAt"


async def test_a_replayed_support_action_advances_the_revision_by_zero(
    platform: Any,
) -> None:
    """The console pressing the same button twice.

    `find_one_and_update` matches on `(version, status)`, so the second attempt
    matches nothing, raises inside the transaction, and takes the bump with it.
    """
    repository, service = platform
    case_id = await _seed_case(repository)
    work_item_id = await _seed_thread(service, case_id)
    await service.apply_action(
        work_item_id, _action(SupportAction.ACKNOWLEDGE, 0), actor_id="support-1"
    )
    before, before_at = await _revision(repository, case_id)

    with pytest.raises(ConcurrencyConflictError):
        await service.apply_action(
            work_item_id, _action(SupportAction.ACKNOWLEDGE, 0), actor_id="support-1"
        )

    after, after_at = await _revision(repository, case_id)
    assert after == before
    assert after_at == before_at


async def test_a_session_item_advances_no_case_revision(platform: Any) -> None:
    """`operational_return_items` holds both shapes, and only one is projected.

    A session-scoped item has no case to invalidate. Written directly rather
    than through `create_case_return_item`, because there is no case-free
    creator -- which is itself the reason this collection is shared.
    """
    repository, _service = platform
    case_id = await _seed_case(repository)
    item_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    await repository.return_items.insert_one(
        {
            "returnItemId": item_id,
            "sessionId": f"ret-{uuid.uuid4().hex[:8]}",
            "orderLineId": "L-legacy",
            "quantity": 1,
            "version": 0,
            "createdAt": now,
            "updatedAt": now,
        }
    )
    before, before_at = await _revision(repository, case_id)

    await repository.update_return_item(item_id, {"quantity": 4}, expected_version=0)

    after, after_at = await _revision(repository, case_id)
    assert after == before
    assert after_at == before_at


# ---------------------------------------------------------------------------
# The concurrency test plan sect. 6.5 names by hand
# ---------------------------------------------------------------------------


async def _released_together(count: int, work: Any) -> list[Any]:
    """Run `work(index)` `count` times, all released from one barrier.

    `asyncio.gather` alone does not produce a race against a fast local
    datastore: the first task can run to completion inside the window the others
    are still being scheduled in. The barrier moves every task to the same
    starting line, so the contended interval is the operation itself.
    """
    barrier = asyncio.Barrier(count)

    async def _run(index: int) -> Any:
        await barrier.wait()
        return await work(index)

    return await asyncio.gather(*(_run(index) for index in range(count)), return_exceptions=True)


async def test_two_writers_on_different_collections_produce_two_revisions(
    platform: Any,
) -> None:
    """The plan's wording exactly, over the two newly-fixed collections.

    A Support action on `support_work_items` and an item assignment on
    `operational_return_items`, released together. Two distinct, monotonically
    increasing revisions and no lost update -- which for a blind `$inc` is the
    same statement as "the total moved by two".
    """
    repository, service = platform
    case_id = await _seed_case(repository)
    work_item_id = await _seed_thread(service, case_id)
    item_id = await _seed_item(repository, case_id)
    before, before_at = await _revision(repository, case_id)

    async def _write(index: int) -> None:
        if index == 0:
            await service.apply_action(
                work_item_id, _action(SupportAction.ACKNOWLEDGE, 0), actor_id="support-1"
            )
            return
        await repository.assign_return_item_to_record(
            item_id, return_record_id=str(uuid.uuid4()), expected_version=0
        )

    results = await _released_together(2, _write)
    assert not [result for result in results if isinstance(result, BaseException)], results

    after, after_at = await _revision(repository, case_id)
    assert after == before + 2, f"two concurrent writers advanced the revision by {after - before}"
    assert after_at > before_at


async def test_every_projection_writer_contends_at_once_and_loses_nothing(
    platform: Any,
) -> None:
    """The version that fails reliably rather than once in a hundred runs.

    **Every writer plan sect. 6.5 binds, in one contended window.** All six of
    the writers the invariant names -- `append_case_fact`, `create_return_record`,
    `update_return_record`, `create_case_return_item`, `update_return_item` and
    `assign_return_item_to_record` -- plus `apply_action` from D28, released
    together over four collections. Every one of them bumps the same one-document
    counter, so every pair of them contends. MongoDB aborts the loser as a
    transient error and `with_transaction` re-runs the whole callback, child
    write included.

    Two of the seven were unreachable from the file that proved the other four:
    `OperationalRepository` shadows `CaseRepository`'s item collection with the
    session-scoped view, so `update_return_item` and
    `assign_return_item_to_record` live in `operations/repository.py` and were
    excluded from that module's scope by name.

    The assertion is arithmetic and it is the strong one: `$inc` is atomic, so
    `CONTENDERS` successful writes can only produce that many distinct increasing
    revisions unless one was lost, in which case the total is short. A
    read-modify-write repair fails here two ways at once -- the losers raise
    `ConcurrencyConflictError` and the total lands under `CONTENDERS`.

    **Every writer gets its own child document.** Two writers compare-and-setting
    the *same* item is a genuine business conflict and one of them is supposed to
    lose -- which would make this module report a lost bump where there was only
    a correctly refused write. The contention under test is on the case counter
    they all share, and nothing else.
    """
    repository, service = platform
    case_id = await _seed_case(repository)
    thread_id = await _seed_thread(service, case_id)
    # Four items: three for the assignment writer, one for the direct update.
    items = [await _seed_item(repository, case_id) for _ in range(4)]
    # One record already in place, so `update_return_record` has something of its
    # own to move rather than racing the creator for it.
    standing = await repository.create_return_record(
        return_record_id=f"rr-standing-{case_id}",
        case_id=case_id,
        return_reference=f"RMA-{case_id[:8]}-standing",
        status="ISSUED",
    )
    before, before_at = await _revision(repository, case_id)

    async def _write(index: int) -> str:
        if index == 0:
            # One Channel B thread per case by construction, so there is exactly
            # one Support writer in the field.
            await service.apply_action(
                thread_id, _action(SupportAction.ACKNOWLEDGE, 0), actor_id="support-1"
            )
            return "apply_action"
        if index <= 3:
            await repository.assign_return_item_to_record(
                items[index - 1], return_record_id=f"rr-{index}", expected_version=0
            )
            return "assign_return_item_to_record"
        if index == 4:
            await repository.update_return_item(items[3], {"quantity": 4}, expected_version=0)
            return "update_return_item"
        if index <= 6:
            await repository.append_case_fact(
                fact_id=f"fact-{case_id}-{index}",
                case_id=case_id,
                fact_name=f"fact_{index}",
                value=index,
                agent_id=f"agent-{index}",
                channel=FactChannel.SYSTEM,
                acquisition_method=FactAcquisition.OBSERVED,
            )
            return "append_case_fact"
        if index == 7:
            await repository.update_return_record(
                str(standing["returnRecordId"]),
                {"trackingReference": "TRK-1"},
                expected_version=int(standing["version"]),
            )
            return "update_return_record"
        if index == 8:
            await repository.create_case_return_item(
                return_item_id=f"item-late-{case_id}",
                case_id=case_id,
                return_record_id=None,
                order_line_reference=f"L-late-{case_id[:6]}",
            )
            return "create_case_return_item"
        await repository.create_return_record(
            return_record_id=f"rr-record-{case_id}-{index}",
            case_id=case_id,
            return_reference=f"RMA-{case_id[:8]}-{index}",
            status="ISSUED",
        )
        return "create_return_record"

    results = await _released_together(CONTENDERS, _write)
    failures = [result for result in results if isinstance(result, BaseException)]
    assert not failures, f"a writer failed under contention: {failures}"
    # Named rather than counted, so a branch that silently stopped being reached
    # -- an index off by one after an edit here -- fails instead of quietly
    # narrowing what this test covers.
    assert set(results) == {
        "apply_action",
        "assign_return_item_to_record",
        "update_return_item",
        "append_case_fact",
        "update_return_record",
        "create_case_return_item",
        "create_return_record",
    }

    after, after_at = await _revision(repository, case_id)
    assert after == before + CONTENDERS, (
        f"{CONTENDERS} concurrent writes advanced the revision by {after - before}: a bump was lost"
    )
    assert after_at > before_at

    # And every child actually landed -- a total that matched because writes
    # were dropped in the same proportion would be the same number.
    assert len(await repository.list_case_facts(case_id)) == 2
    records = await repository.list_return_records(case_id)
    assert len(records) == 2, "the standing record and the one created in the window"
    assert [
        record["trackingReference"]
        for record in records
        if record["returnRecordId"] == standing["returnRecordId"]
    ] == ["TRK-1"]
    stored_items = await repository.list_case_return_items(case_id)
    assert len(stored_items) == 5, "four seeded plus the one created in the window"
    assert len([item for item in stored_items if item.get("returnRecordId")]) == 3
    assert [item["quantity"] for item in stored_items if item["returnItemId"] == items[3]] == [4]


# ---------------------------------------------------------------------------
# `open_case_thread`: assessed against the invariant, fixed for a worse reason
# ---------------------------------------------------------------------------


async def test_opening_a_thread_moves_no_case_revision(platform: Any) -> None:
    """Assessed and deliberately left alone (D28).

    `CaseDetail` reaches the work item only through `case.channelBWorkItemId`,
    which `open_support_work_item` sets afterwards with `update_case` -- and that
    writer bumps. Until the case is linked, `_load_support_work_item` reads
    nothing at all, so there is no projection state for a client to be stale
    about and a bump here would invalidate every cached projection for a
    document nobody can see.
    """
    repository, service = platform
    case_id = await _seed_case(repository)
    before, before_at = await _revision(repository, case_id)

    work_item_id = await _seed_thread(service, case_id)

    assert work_item_id
    after, after_at = await _revision(repository, case_id)
    assert after == before
    assert after_at == before_at
    # And it is genuinely invisible until the link is written.
    state = await repository.load_case_projection_state(case_id)
    assert state is not None
    assert state.support is None


async def test_opening_a_thread_writes_the_work_item_and_its_first_message_together(
    platform: Any,
) -> None:
    repository, service = platform
    case_id = await _seed_case(repository)

    work_item_id = await _seed_thread(service, case_id)

    item = await service.get_work_item(work_item_id)
    assert item is not None
    messages = await service.list_messages(item.threadId)
    assert [message.messageText for message in messages] == ["Please issue an RMA for this return."]


async def test_a_thread_whose_first_message_cannot_be_written_is_not_opened_at_all(
    platform: Any,
) -> None:
    """The durable defect the transaction closes, and the reason it is not about
    the revision.

    The two inserts used to run one after the other. A failure between them left
    a work item with no request text, and the idempotency check at the top of
    `open_case_thread` then returned that empty thread forever -- no retry could
    ever write the missing message. A human opened the conversation Support was
    meant to answer and found it blank.

    The fault is injected at the collection boundary and the rollback is
    MongoDB's: the work item is gone afterwards because the transaction aborted,
    not because anything here removed it.
    """
    repository, service = platform
    case_id = await _seed_case(repository)
    real_messages = service._messages

    class _RefusesToInsert:
        def __init__(self, wrapped: Any) -> None:
            self._wrapped = wrapped

        async def insert_one(self, *_: Any, **__: Any) -> Any:
            raise RuntimeError("the message collection is unavailable")

        def __getattr__(self, name: str) -> Any:
            return getattr(self._wrapped, name)

    service._messages = _RefusesToInsert(real_messages)
    try:
        with pytest.raises(RuntimeError):
            await _seed_thread(service, case_id)
    finally:
        service._messages = real_messages

    assert await service.get_work_item_for_session(case_id) is None
    assert (
        await repository.cases.database["support_work_items"].find_one({"caseId": case_id}) is None
    )

    # And the case can still be opened properly afterwards, which is the part
    # the old code made impossible.
    work_item_id = await _seed_thread(service, case_id)
    item = await service.get_work_item(work_item_id)
    assert item is not None
    assert len(await service.list_messages(item.threadId)) == 1


async def test_updatedat_moves_forward_across_every_writer(platform: Any) -> None:
    """`updatedAt` is half the invariant and it is the half a `$inc`-only repair
    would silently drop. Asserted as a strictly increasing sequence across the
    three writers, so a bump that moved the counter and left the timestamp
    behind fails here rather than in a client's cache."""
    repository, service = platform
    case_id = await _seed_case(repository)
    work_item_id = await _seed_thread(service, case_id)
    item_id = await _seed_item(repository, case_id)

    stamps = [(await _revision(repository, case_id))[1]]
    await service.apply_action(
        work_item_id, _action(SupportAction.ACKNOWLEDGE, 0), actor_id="support-1"
    )
    stamps.append((await _revision(repository, case_id))[1])
    await repository.assign_return_item_to_record(
        item_id, return_record_id=str(uuid.uuid4()), expected_version=0
    )
    stamps.append((await _revision(repository, case_id))[1])
    await repository.update_return_item(item_id, {"quantity": 5}, expected_version=1)
    stamps.append((await _revision(repository, case_id))[1])

    assert stamps == sorted(stamps), f"updatedAt did not move forward: {stamps}"
    assert len(set(stamps)) == len(stamps), f"a writer left updatedAt where it was: {stamps}"
    assert stamps[-1] - stamps[0] < timedelta(minutes=5)
