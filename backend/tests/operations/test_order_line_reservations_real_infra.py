"""The reservation lifecycle under real contention (plan sect. 12.3, 12.4).

**Nothing here can be settled by a double.** Every property under test is the
storage engine's: a transaction that re-evaluates availability and commits or
refuses atomically, a conditional update that lets exactly one of an
authorization and an expiry sweep win, a partial unique index that keeps one
`ACTIVE` hold per (case, line), and `$inc` under `with_transaction` moving the
case revision exactly once. A fake collection that mutated a dict would answer
"yes" to all of them while the production repository answered "no" -- which is
precisely how a fabricated item selection came to look covered.

The rules themselves -- which terms subtract, where the boundary between them
is, which transitions are legal -- are asserted without a database in
`test_order_line_availability.py`. This module asserts only what a database
decides.

Release-blocking, in the plan's own words:

* two concurrent requests for the full quantity -- **exactly one succeeds**, and
  the other is told the recomputed figure;
* the expiry worker and an RMA authorization firing together -- **exactly one
  transition wins**, and the total authorized never exceeds the quantity
  ordered;
* an abandoned reservation **releases rather than leaks**;
* a case editing its own reservation upward **does not reject itself**.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from pydantic import SecretStr
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.operations.models import CaseStatus
from return_platform.operations.order_lines import (
    LineSelection,
    QuantityReservationExpiredError,
    QuantityUnavailableError,
    ReservationRelease,
    ReservationState,
)
from return_platform.operations.repository import OperationalRepository

# `loop_scope="module"` is required by the module-scoped `repository` fixture:
# `AsyncMongoClient` binds to the loop it was created on.
pytestmark = pytest.mark.asyncio(loop_scope="module")

TENANT = "tenant-reservation"
LINE = "1"
TTL_SECONDS = 1_800


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required test environment variable is not set: {name}")
    return value


def _mongo_dsn() -> str:
    """`directConnection=true` -- the replica set advertises an internal hostname."""
    username = quote(_required_env("MONGO_ROOT_USERNAME"), safe="")
    password = quote(_required_env("MONGO_ROOT_PASSWORD"), safe="")
    host = os.getenv("PLATFORM_TEST_MONGO_HOST", "127.0.0.1")
    return (
        f"mongodb://{username}:{password}@{host}:27017/"
        "return_platform?authSource=admin&directConnection=true"
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def repository() -> Any:
    """A repository on an isolated database, with its real indexes, dropped after.

    `ensure_indexes` is not optional: the partial unique index over
    `(caseId, orderLineReference)` where `state` is `ACTIVE` is what makes "one
    hold per case per line" true. Without it the edit test would pass by
    accumulating holds, which is the opposite of the property.
    """
    database = f"reservation_test_{uuid.uuid4().hex[:12]}"
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


async def _seed_case(
    repository: OperationalRepository, *, order_reference: str, principal: str | None = None
) -> str:
    case_id = str(uuid.uuid4())
    await repository.create_case(
        case_id=case_id,
        tenant_id=TENANT,
        principal_id=principal or f"associate-{uuid.uuid4().hex[:8]}",
        channel_a_conversation_id=f"conv-{uuid.uuid4().hex[:12]}",
        confirmed_order_reference=order_reference,
        confirmation_key=f"{TENANT}|{uuid.uuid4().hex}|{order_reference}|",
    )
    return case_id


def _order() -> str:
    """A fresh order number per test, so the line ledger is never shared."""
    return f"CQ{uuid.uuid4().hex[:10].upper()}"


async def _revision(repository: OperationalRepository, case_id: str) -> int:
    case = await repository.get_case(case_id)
    assert case is not None
    return int(case["version"])


async def _select(
    repository: OperationalRepository,
    *,
    case_id: str,
    order_reference: str,
    quantity: int,
    ordered: int = 2,
    line: str = LINE,
) -> Any:
    return await repository.replace_case_line_selection(
        case_id=case_id,
        tenant_id=TENANT,
        order_reference=order_reference,
        selections=(LineSelection(order_line_reference=line, quantity=quantity),),
        ordered_by_line={line: ordered},
        ttl_seconds=TTL_SECONDS,
    )


async def _returnable(
    repository: OperationalRepository,
    *,
    order_reference: str,
    viewing_case_id: str | None,
    ordered: int = 2,
    line: str = LINE,
) -> int:
    availability = await repository.load_order_line_availability(
        tenant_id=TENANT,
        order_reference=order_reference,
        viewing_case_id=viewing_case_id,
        ordered_by_line={line: ordered},
    )
    return availability[line].returnable_quantity


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


# ---------------------------------------------------------------------------
# The write persists, and the read agrees with it
# ---------------------------------------------------------------------------


async def test_a_selection_holds_the_quantity_it_names(
    repository: OperationalRepository,
) -> None:
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)

    outcome = await _select(repository, case_id=case_id, order_reference=order, quantity=2)

    assert outcome.changed is True
    assert [item["orderLineId"] for item in outcome.items] == [LINE]
    assert [(view.quantity, view.state) for view in outcome.reservations] == [
        (2, ReservationState.ACTIVE)
    ]
    # Another case now sees nothing left.
    assert await _returnable(repository, order_reference=order, viewing_case_id="someone-else") == 0
    # And the holder still sees its own two, because its hold is excluded.
    assert await _returnable(repository, order_reference=order, viewing_case_id=case_id) == 2


# ---------------------------------------------------------------------------
# Two writers, one unit
# ---------------------------------------------------------------------------


async def test_two_concurrent_requests_for_the_full_quantity_leave_exactly_one_holder(
    repository: OperationalRepository,
) -> None:
    """The plan's first release-blocking test.

    Both associates read "2 returnable" and both submit 2. A transaction that
    only re-read would not be enough -- reads inside a transaction take no
    locks, so both snapshots are internally consistent and both commit. The
    per-line token they increment before reading is what makes them collide;
    the loser aborts as a transient error, `with_transaction` re-runs it, and
    the re-run sees the winner's committed hold and refuses.
    """
    order = _order()
    first = await _seed_case(repository, order_reference=order)
    second = await _seed_case(repository, order_reference=order)
    cases = (first, second)

    async def _submit(index: int) -> Any:
        return await _select(
            repository, case_id=cases[index], order_reference=order, quantity=2, ordered=2
        )

    results = await _released_together(2, _submit)

    refused = [result for result in results if isinstance(result, QuantityUnavailableError)]
    succeeded = [result for result in results if not isinstance(result, BaseException)]
    unexpected = [
        result
        for result in results
        if isinstance(result, BaseException) and not isinstance(result, QuantityUnavailableError)
    ]
    assert not unexpected, f"a writer failed for the wrong reason: {unexpected}"
    assert len(succeeded) == 1, f"the line was oversold: {results}"
    assert len(refused) == 1

    # The refusal carries the recomputed figure, so the client re-renders rather
    # than guessing (plan sect. 12.4).
    assert refused[0].unavailable == {LINE: 0}

    held = [
        view
        for case_id in cases
        for view in await repository.list_case_reservations(
            case_id, states=(ReservationState.ACTIVE,)
        )
    ]
    assert len(held) == 1
    assert sum(view.quantity for view in held) == 2


async def test_eight_writers_never_hold_more_than_the_line_carries(
    repository: OperationalRepository,
) -> None:
    """Two proves a race exists; eight makes a lost refusal fail reliably.

    Four units, eight cases, one unit each. Four must succeed and four must be
    refused, and the total held can only be four -- unless a re-evaluation was
    skipped, in which case the sum runs over.
    """
    order = _order()
    ordered = 4
    contenders = 8
    cases = [await _seed_case(repository, order_reference=order) for _ in range(contenders)]

    async def _submit(index: int) -> Any:
        return await _select(
            repository,
            case_id=cases[index],
            order_reference=order,
            quantity=1,
            ordered=ordered,
        )

    results = await _released_together(contenders, _submit)
    unexpected = [
        result
        for result in results
        if isinstance(result, BaseException) and not isinstance(result, QuantityUnavailableError)
    ]
    assert not unexpected, f"a writer failed for the wrong reason: {unexpected}"

    held = [
        view
        for case_id in cases
        for view in await repository.list_case_reservations(
            case_id, states=(ReservationState.ACTIVE,)
        )
    ]
    assert sum(view.quantity for view in held) == ordered, (
        f"{contenders} writers held {sum(view.quantity for view in held)} of {ordered} units"
    )
    assert (
        await _returnable(
            repository, order_reference=order, viewing_case_id="someone-else", ordered=ordered
        )
        == 0
    )


# ---------------------------------------------------------------------------
# Self-reservation exclusion (plan sect. 12.3)
# ---------------------------------------------------------------------------


async def test_a_case_raising_its_own_hold_from_one_to_two_does_not_reject_itself(
    repository: OperationalRepository,
) -> None:
    """The plan's named test, against the index that would otherwise refuse it.

    The edit releases the hold of one and takes a hold of two in the same
    transaction, so the partial unique index over `ACTIVE` never sees both.
    """
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=1)

    outcome = await _select(repository, case_id=case_id, order_reference=order, quantity=2)

    assert outcome.changed is True
    assert [(view.quantity, view.state) for view in outcome.reservations] == [
        (2, ReservationState.ACTIVE)
    ]
    settled = await repository.list_case_reservations(case_id)
    assert [view.state for view in settled] == [
        ReservationState.RELEASED,
        ReservationState.ACTIVE,
    ]
    assert await _returnable(repository, order_reference=order, viewing_case_id="someone-else") == 0


async def test_lowering_a_hold_gives_the_difference_back_to_everyone_else(
    repository: OperationalRepository,
) -> None:
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=2)

    await _select(repository, case_id=case_id, order_reference=order, quantity=1)

    assert await _returnable(repository, order_reference=order, viewing_case_id="someone-else") == 1


async def test_withdrawing_a_line_releases_its_hold_and_deletes_the_item(
    repository: OperationalRepository,
) -> None:
    """Replace-set: a line absent from the payload is a line given back."""
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=2)

    outcome = await repository.replace_case_line_selection(
        case_id=case_id,
        tenant_id=TENANT,
        order_reference=order,
        selections=(),
        ordered_by_line={LINE: 2},
        ttl_seconds=TTL_SECONDS,
    )

    assert outcome.changed is True
    assert outcome.items == ()
    assert await repository.list_case_return_items(case_id) == []
    assert [view.state for view in await repository.list_case_reservations(case_id)] == [
        ReservationState.RELEASED
    ]
    assert await _returnable(repository, order_reference=order, viewing_case_id="someone-else") == 2


# ---------------------------------------------------------------------------
# The revision invariant (plan sect. 6.5)
# ---------------------------------------------------------------------------


async def test_a_selection_advances_the_case_revision_by_exactly_one(
    repository: OperationalRepository,
) -> None:
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    before = await _revision(repository, case_id)

    outcome = await _select(repository, case_id=case_id, order_reference=order, quantity=2)

    after = await _revision(repository, case_id)
    assert after == before + 1
    assert outcome.revision == after


async def test_a_two_line_selection_still_advances_the_revision_by_exactly_one(
    repository: OperationalRepository,
) -> None:
    """One write, one revision. Not one per line."""
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    before = await _revision(repository, case_id)

    await repository.replace_case_line_selection(
        case_id=case_id,
        tenant_id=TENANT,
        order_reference=order,
        selections=(
            LineSelection(order_line_reference="1", quantity=1),
            LineSelection(order_line_reference="2", quantity=1),
        ),
        ordered_by_line={"1": 2, "2": 2},
        ttl_seconds=TTL_SECONDS,
    )

    assert await _revision(repository, case_id) == before + 1


async def test_an_identical_resubmission_advances_the_revision_by_zero(
    repository: OperationalRepository,
) -> None:
    """A no-op must invalidate nobody's cache."""
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=2)
    before = await _revision(repository, case_id)

    outcome = await _select(repository, case_id=case_id, order_reference=order, quantity=2)

    assert outcome.changed is False
    assert await _revision(repository, case_id) == before
    assert outcome.revision == before


async def test_a_refused_selection_advances_the_revision_by_zero(
    repository: OperationalRepository,
) -> None:
    """The loser of the re-evaluation changed nothing and must move no revision."""
    order = _order()
    holder = await _seed_case(repository, order_reference=order)
    loser = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=holder, order_reference=order, quantity=2)
    before = await _revision(repository, loser)

    with pytest.raises(QuantityUnavailableError):
        await _select(repository, case_id=loser, order_reference=order, quantity=2)

    assert await _revision(repository, loser) == before
    assert await repository.list_case_return_items(loser) == []


async def test_authorizing_a_line_advances_the_revision_by_exactly_one(
    repository: OperationalRepository,
) -> None:
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=2)
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}"
    )
    before = await _revision(repository, case_id)

    await repository.authorize_reserved_line(
        case_id=case_id,
        order_line_reference=LINE,
        return_record_id=str(record["returnRecordId"]),
    )

    assert await _revision(repository, case_id) == before + 1


# ---------------------------------------------------------------------------
# Authorization consumes, and cannot be raced into overselling
# ---------------------------------------------------------------------------


async def test_authorization_consumes_the_hold_and_attaches_the_item(
    repository: OperationalRepository,
) -> None:
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=2)
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}"
    )

    item = await repository.authorize_reserved_line(
        case_id=case_id,
        order_line_reference=LINE,
        return_record_id=str(record["returnRecordId"]),
    )

    assert item["returnRecordId"] == record["returnRecordId"]
    assert [view.state for view in await repository.list_case_reservations(case_id)] == [
        ReservationState.CONSUMED
    ]
    # Still two units gone -- counted once, now through the item rather than
    # through the hold.
    assert await _returnable(repository, order_reference=order, viewing_case_id=case_id) == 0


async def test_a_second_authorization_of_the_same_line_loses_and_does_not_authorize(
    repository: OperationalRepository,
) -> None:
    """`CONSUMED -> CONSUMED` is not an edge, so the second RMA gets nothing."""
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=2)
    first = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}-A"
    )
    second = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}-B"
    )
    await repository.authorize_reserved_line(
        case_id=case_id,
        order_line_reference=LINE,
        return_record_id=str(first["returnRecordId"]),
    )
    before = await _revision(repository, case_id)

    with pytest.raises(QuantityReservationExpiredError) as lost:
        await repository.authorize_reserved_line(
            case_id=case_id,
            order_line_reference=LINE,
            return_record_id=str(second["returnRecordId"]),
        )

    assert lost.value.state is ReservationState.CONSUMED
    assert await _revision(repository, case_id) == before
    items = await repository.list_case_return_items(case_id)
    assert [item["returnRecordId"] for item in items] == [first["returnRecordId"]]


async def test_the_expiry_sweep_and_an_authorization_cannot_both_win(
    repository: OperationalRepository,
) -> None:
    """The plan's second release-blocking test.

    Both filter on `state: ACTIVE`, so they contend on one document and MongoDB
    settles it. Whichever loses must leave the world consistent: if the
    authorization won, the hold is `CONSUMED` and the item carries the RMA; if
    the sweep won, the hold is `EXPIRED`, the authorization raised, and **no
    item was attached** -- because the assignment shares the consume's
    transaction and rolled back with it.

    Run repeatedly: the interleaving that matters is not guaranteed on any one
    attempt, and a single pass would report green on the uncontended path.
    """
    for attempt in range(12):
        order = _order()
        case_id = await _seed_case(repository, order_reference=order)
        # A hold that is already at its deadline, so the sweep has work to do at
        # the same instant the authorization does.
        await repository.replace_case_line_selection(
            case_id=case_id,
            tenant_id=TENANT,
            order_reference=order,
            selections=(LineSelection(order_line_reference=LINE, quantity=2),),
            ordered_by_line={LINE: 2},
            ttl_seconds=60,
            now=datetime.now(UTC) - timedelta(seconds=59),
        )
        record = await repository.create_return_record(
            return_record_id=str(uuid.uuid4()),
            case_id=case_id,
            return_reference=f"RMA-{order}",
        )

        async def _race(index: int, case_id: str = case_id, record: Any = record) -> str:
            if index == 0:
                await repository.authorize_reserved_line(
                    case_id=case_id,
                    order_line_reference=LINE,
                    return_record_id=str(record["returnRecordId"]),
                )
                return "AUTHORIZED"
            return f"EXPIRED:{await repository.expire_due_reservations(limit=50)}"

        results = await _released_together(2, _race)
        unexpected = [
            result
            for result in results
            if isinstance(result, BaseException)
            and not isinstance(result, QuantityReservationExpiredError)
        ]
        assert not unexpected, f"attempt {attempt}: an actor failed unexpectedly: {unexpected}"

        states = [view.state for view in await repository.list_case_reservations(case_id)]
        assert len(states) == 1
        assert states[0] in {ReservationState.CONSUMED, ReservationState.EXPIRED}, (
            f"attempt {attempt}: the hold ended in {states[0]}"
        )

        items = await repository.list_case_return_items(case_id)
        authorized_quantity = sum(
            int(item["quantity"]) for item in items if item.get("returnRecordId") is not None
        )
        if states[0] is ReservationState.CONSUMED:
            assert authorized_quantity == 2
        else:
            # The sweep won. Nothing may have been authorized against a hold
            # that no longer existed.
            assert authorized_quantity == 0, (
                f"attempt {attempt}: an expired hold was authorized anyway"
            )
        assert authorized_quantity <= 2, "the total authorized exceeded the quantity ordered"


async def test_an_expired_hold_can_never_be_authorized(
    repository: OperationalRepository,
) -> None:
    """`EXPIRED -> CONSUMED` is forbidden, and the conditional update enforces it."""
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await repository.replace_case_line_selection(
        case_id=case_id,
        tenant_id=TENANT,
        order_reference=order,
        selections=(LineSelection(order_line_reference=LINE, quantity=2),),
        ordered_by_line={LINE: 2},
        ttl_seconds=60,
        now=datetime.now(UTC) - timedelta(seconds=120),
    )
    await repository.expire_due_reservations(limit=50)
    assert [view.state for view in await repository.list_case_reservations(case_id)] == [
        ReservationState.EXPIRED
    ]
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}"
    )

    with pytest.raises(QuantityReservationExpiredError) as lost:
        await repository.authorize_reserved_line(
            case_id=case_id,
            order_line_reference=LINE,
            return_record_id=str(record["returnRecordId"]),
        )

    assert lost.value.state is ReservationState.EXPIRED
    items = await repository.list_case_return_items(case_id)
    assert [item.get("returnRecordId") for item in items] == [None]


# ---------------------------------------------------------------------------
# Abandonment releases; it does not leak
# ---------------------------------------------------------------------------


async def test_an_abandoned_hold_stops_blocking_the_line_the_moment_it_expires(
    repository: OperationalRepository,
) -> None:
    """The arithmetic reads the deadline, not the sweep.

    A sweep that was down would otherwise leak the quantity for as long as it
    stayed down, which is the failure mode the plan calls a permanent leak.
    """
    order = _order()
    abandoned = await _seed_case(repository, order_reference=order)
    await repository.replace_case_line_selection(
        case_id=abandoned,
        tenant_id=TENANT,
        order_reference=order,
        selections=(LineSelection(order_line_reference=LINE, quantity=2),),
        ordered_by_line={LINE: 2},
        ttl_seconds=60,
        now=datetime.now(UTC) - timedelta(seconds=120),
    )

    # No sweep has run: the document is still ACTIVE.
    assert [view.state for view in await repository.list_case_reservations(abandoned)] == [
        ReservationState.ACTIVE
    ]
    assert await _returnable(repository, order_reference=order, viewing_case_id="someone-else") == 2

    # And another case can take the units for real.
    taker = await _seed_case(repository, order_reference=order)
    outcome = await _select(repository, case_id=taker, order_reference=order, quantity=2)
    assert outcome.changed is True


async def test_the_sweep_settles_an_abandoned_hold_as_expired(
    repository: OperationalRepository,
) -> None:
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await repository.replace_case_line_selection(
        case_id=case_id,
        tenant_id=TENANT,
        order_reference=order,
        selections=(LineSelection(order_line_reference=LINE, quantity=2),),
        ordered_by_line={LINE: 2},
        ttl_seconds=60,
        now=datetime.now(UTC) - timedelta(seconds=120),
    )

    assert await repository.expire_due_reservations(limit=50) >= 1
    assert [view.state for view in await repository.list_case_reservations(case_id)] == [
        ReservationState.EXPIRED
    ]
    # Idempotent: a second pass finds nothing left to settle. Asserted as an
    # absolute zero rather than "one fewer" -- the sweep is collection-wide, so
    # by this point every earlier test's abandoned hold has been settled too.
    assert await repository.expire_due_reservations(limit=50) == 0


async def test_the_sweep_leaves_a_live_hold_alone(
    repository: OperationalRepository,
) -> None:
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=2)

    await repository.expire_due_reservations(limit=50)
    assert [view.state for view in await repository.list_case_reservations(case_id)] == [
        ReservationState.ACTIVE
    ]


async def test_closing_a_case_gives_its_holds_back(
    repository: OperationalRepository,
) -> None:
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=2)

    assert await repository.release_case_reservations(case_id) == 1

    assert [view.state for view in await repository.list_case_reservations(case_id)] == [
        ReservationState.RELEASED
    ]
    assert await _returnable(repository, order_reference=order, viewing_case_id="someone-else") == 2


async def test_closing_a_case_does_not_take_back_a_consumed_hold(
    repository: OperationalRepository,
) -> None:
    """`CONSUMED -> RELEASED` is not an edge. Authorized units stay gone."""
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=2)
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}"
    )
    await repository.authorize_reserved_line(
        case_id=case_id,
        order_line_reference=LINE,
        return_record_id=str(record["returnRecordId"]),
    )

    assert (
        await repository.release_case_reservations(case_id, reason=ReservationRelease.CASE_CLOSED)
        == 0
    )
    assert [view.state for view in await repository.list_case_reservations(case_id)] == [
        ReservationState.CONSUMED
    ]


# ---------------------------------------------------------------------------
# The read, across cases
# ---------------------------------------------------------------------------


async def test_a_cancelled_case_returns_its_authorized_quantity_to_the_line(
    repository: OperationalRepository,
) -> None:
    """Read against Mongo, because the case status comes off the stored document."""
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=2)
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}"
    )
    await repository.authorize_reserved_line(
        case_id=case_id,
        order_line_reference=LINE,
        return_record_id=str(record["returnRecordId"]),
    )
    assert await _returnable(repository, order_reference=order, viewing_case_id=None) == 0

    case = await repository.get_case(case_id)
    assert case is not None
    await repository.update_case(
        case_id,
        {"status": CaseStatus.CANCELLED.value},
        expected_version=int(case["version"]),
    )

    assert await _returnable(repository, order_reference=order, viewing_case_id=None) == 2


async def test_a_completed_case_keeps_the_units_off_the_line(
    repository: OperationalRepository,
) -> None:
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=1)
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}"
    )
    await repository.authorize_reserved_line(
        case_id=case_id,
        order_line_reference=LINE,
        return_record_id=str(record["returnRecordId"]),
    )
    case = await repository.get_case(case_id)
    assert case is not None
    await repository.update_case(
        case_id, {"status": CaseStatus.CLOSED.value}, expected_version=int(case["version"])
    )

    availability = await repository.load_order_line_availability(
        tenant_id=TENANT,
        order_reference=order,
        viewing_case_id=None,
        ordered_by_line={LINE: 2},
    )
    assert availability[LINE].completed_return_quantity == 1
    assert availability[LINE].open_authorized_quantity == 0
    assert availability[LINE].returnable_quantity == 1


async def test_another_tenants_order_of_the_same_number_is_a_different_order(
    repository: OperationalRepository,
) -> None:
    """Order numbers are not globally unique; the ledger and the read are scoped."""
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=2)

    availability = await repository.load_order_line_availability(
        tenant_id="tenant-somebody-else",
        order_reference=order,
        viewing_case_id=None,
        ordered_by_line={LINE: 2},
    )
    assert availability[LINE].returnable_quantity == 2


async def test_a_case_may_not_reselect_a_line_an_rma_already_covers(
    repository: OperationalRepository,
) -> None:
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=1)
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}"
    )
    await repository.authorize_reserved_line(
        case_id=case_id,
        order_line_reference=LINE,
        return_record_id=str(record["returnRecordId"]),
    )
    before = await _revision(repository, case_id)

    from return_platform.operations.order_lines import LineAlreadyAuthorizedError

    with pytest.raises(LineAlreadyAuthorizedError):
        await _select(repository, case_id=case_id, order_reference=order, quantity=1)

    assert await _revision(repository, case_id) == before


# ---------------------------------------------------------------------------
# The Support outcome is what authorizes a hold (plan sect. 12.3)
# ---------------------------------------------------------------------------
#
# `record_support_outcome` is the only production caller of
# `authorize_reserved_line`, and until this it did not call it at all: it
# assigned the item straight through `assign_return_item_to_record`, leaving the
# hold `ACTIVE` beside an item an RMA already covered. The units were then
# subtracted twice by every other case's availability read -- once through the
# item, once through the hold -- until the TTL lapsed.


def _outcome_activities(repository: OperationalRepository) -> Any:
    """The real activity set over the real repository.

    Only `_assign_items_to_record` is exercised, and it touches nothing but the
    repository, so the support service is a placeholder object rather than a
    stand-in for anything: a double that answered questions would be a double of
    a collaborator this path never speaks to.
    """
    from return_platform.workflows.return_case_activities import ReturnCaseActivities

    return ReturnCaseActivities(repository=repository, support_service=object())


def _outcome(case_id: str, *, record_id: str, reference: str, line: str = LINE) -> Any:
    from return_platform.workflows.return_case_activities import _RecordPlan
    from return_platform.workflows.return_case_workflow import (
        RecordSupportOutcomeInput,
        SupportReturnRecord,
    )

    incoming = SupportReturnRecord(return_reference=reference, order_line_references=(line,))
    request = RecordSupportOutcomeInput(
        case_id=case_id,
        work_item_id=f"wi-{case_id}",
        records=(incoming,),
        rejected=False,
        reason=None,
        return_record_ids=(record_id,),
    )
    plan = _RecordPlan(record_id=record_id, incoming=incoming, existing=None, merged={}, changed={})
    return request, plan


async def test_a_support_outcome_consumes_the_hold_it_authorizes(
    repository: OperationalRepository,
) -> None:
    """The wiring, end to end, against real documents.

    Before this the hold stayed `ACTIVE` and the line was subtracted twice.
    `_returnable` is the assertion that would catch a regression from an
    associate's seat: two units ordered, two authorized, none left -- not the
    minus-two a double count produces and `compute_line_availability` then
    reports as `0` with a `COMMITMENTS_EXCEED_ORDERED_QUANTITY` flag.
    """
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=2)
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}"
    )
    record_id = str(record["returnRecordId"])
    request, plan = _outcome(case_id, record_id=record_id, reference=f"RMA-{order}")

    await _outcome_activities(repository)._assign_items_to_record(request, plan)

    assert [view.state for view in await repository.list_case_reservations(case_id)] == [
        ReservationState.CONSUMED
    ]
    items = await repository.list_case_return_items(case_id)
    assert [item["returnRecordId"] for item in items] == [record_id]
    availability = await repository.load_order_line_availability(
        tenant_id=TENANT,
        order_reference=order,
        viewing_case_id=None,
        ordered_by_line={LINE: 2},
    )
    assert availability[LINE].open_authorized_quantity == 2
    assert availability[LINE].active_reservation_quantity == 0
    assert availability[LINE].returnable_quantity == 0
    assert availability[LINE].data_inconsistency is None


async def test_a_support_outcome_advances_the_revision_exactly_once_per_line(
    repository: OperationalRepository,
) -> None:
    """The consume and the assignment share one transaction, so they share one
    bump. Two would invalidate the client's cache twice for one change."""
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=1)
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}"
    )
    request, plan = _outcome(
        case_id, record_id=str(record["returnRecordId"]), reference=f"RMA-{order}"
    )
    before = await _revision(repository, case_id)

    await _outcome_activities(repository)._assign_items_to_record(request, plan)

    assert await _revision(repository, case_id) == before + 1


async def test_a_replayed_support_outcome_writes_nothing_and_bumps_nothing(
    repository: OperationalRepository,
) -> None:
    """Temporal retries this activity with identical input.

    The second delivery finds the item already carrying a `returnRecordId` and
    skips it before either writer is reached, so the settled `CONSUMED` hold is
    never offered to a transition that has no edge for it.
    """
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=1)
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}"
    )
    request, plan = _outcome(
        case_id, record_id=str(record["returnRecordId"]), reference=f"RMA-{order}"
    )
    activities = _outcome_activities(repository)
    await activities._assign_items_to_record(request, plan)
    before = await _revision(repository, case_id)

    await activities._assign_items_to_record(request, plan)

    assert await _revision(repository, case_id) == before
    assert [view.state for view in await repository.list_case_reservations(case_id)] == [
        ReservationState.CONSUMED
    ]


async def test_a_lapsed_hold_does_not_block_an_rma_support_has_already_issued(
    repository: OperationalRepository,
) -> None:
    """The case the TTL makes ordinary rather than exceptional.

    `item_reservation_ttl_seconds` is thirty minutes and
    `support_response_wait_seconds` is eight *working* hours, so by the time a
    reply lands the hold has usually lapsed. Refusing the assignment then would
    refuse an RMA the authoritative SQL return store already carries -- the
    activity commits there first -- and `_PERSIST_RETRY` would fail the workflow
    after five attempts over a hold that expired hours earlier.

    Nothing is double counted on this path: the lapsed hold already holds
    nothing, so the item is the only claim on the units. The assertion that
    proves it is the availability read, not the reservation state.
    """
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await repository.replace_case_line_selection(
        case_id=case_id,
        tenant_id=TENANT,
        order_reference=order,
        selections=(LineSelection(order_line_reference=LINE, quantity=2),),
        ordered_by_line={LINE: 2},
        ttl_seconds=60,
        now=datetime.now(UTC) - timedelta(seconds=120),
    )
    await repository.expire_due_reservations(limit=50)
    assert [view.state for view in await repository.list_case_reservations(case_id)] == [
        ReservationState.EXPIRED
    ]
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}"
    )
    record_id = str(record["returnRecordId"])
    request, plan = _outcome(case_id, record_id=record_id, reference=f"RMA-{order}")
    before = await _revision(repository, case_id)

    await _outcome_activities(repository)._assign_items_to_record(request, plan)

    items = await repository.list_case_return_items(case_id)
    assert [item["returnRecordId"] for item in items] == [record_id]
    assert await _revision(repository, case_id) == before + 1
    # The settled hold is left where the sweep put it. `EXPIRED -> CONSUMED` is
    # not an edge, and inventing one here would be the transition the plan
    # forbids by name.
    assert [view.state for view in await repository.list_case_reservations(case_id)] == [
        ReservationState.EXPIRED
    ]
    availability = await repository.load_order_line_availability(
        tenant_id=TENANT,
        order_reference=order,
        viewing_case_id=None,
        ordered_by_line={LINE: 2},
    )
    assert availability[LINE].open_authorized_quantity == 2
    assert availability[LINE].returnable_quantity == 0
    assert availability[LINE].data_inconsistency is None


async def test_a_line_that_was_never_selected_is_still_assigned(
    repository: OperationalRepository,
) -> None:
    """Support may authorize a line no reservation was ever taken on -- a case
    created before the selection route existed, or an item written by the
    legacy path. There is nothing to consume, and refusing would strand it."""
    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    item_id = str(uuid.uuid4())
    await repository.create_case_return_item(
        return_item_id=item_id,
        case_id=case_id,
        return_record_id=None,
        order_line_reference=LINE,
    )
    record = await repository.create_return_record(
        return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}"
    )
    record_id = str(record["returnRecordId"])
    request, plan = _outcome(case_id, record_id=record_id, reference=f"RMA-{order}")

    await _outcome_activities(repository)._assign_items_to_record(request, plan)

    assert await repository.list_case_reservations(case_id) == ()
    items = await repository.list_case_return_items(case_id)
    assert [item["returnRecordId"] for item in items] == [record_id]


async def test_the_authorization_and_the_sweep_still_cannot_both_win_through_the_activity(
    repository: OperationalRepository,
) -> None:
    """The plan's second release-blocking race, driven from the caller that runs
    it in production rather than from the repository method directly.

    Either the activity authorized -- in which case the hold is `CONSUMED` and
    the item carries the RMA -- or the sweep won and the activity fell through to
    the plain assignment, in which case the hold is `EXPIRED` and the item still
    carries the RMA. What may never happen is the units being subtracted twice,
    which is what the availability assertion checks on every attempt.
    """
    for attempt in range(12):
        order = _order()
        case_id = await _seed_case(repository, order_reference=order)
        await repository.replace_case_line_selection(
            case_id=case_id,
            tenant_id=TENANT,
            order_reference=order,
            selections=(LineSelection(order_line_reference=LINE, quantity=2),),
            ordered_by_line={LINE: 2},
            ttl_seconds=60,
            now=datetime.now(UTC) - timedelta(seconds=59),
        )
        record = await repository.create_return_record(
            return_record_id=str(uuid.uuid4()), case_id=case_id, return_reference=f"RMA-{order}"
        )
        record_id = str(record["returnRecordId"])
        request, plan = _outcome(case_id, record_id=record_id, reference=f"RMA-{order}")
        activities = _outcome_activities(repository)

        async def _race(
            index: int, request: Any = request, plan: Any = plan, acting: Any = activities
        ) -> str:
            if index == 0:
                await acting._assign_items_to_record(request, plan)
                return "ASSIGNED"
            return f"EXPIRED:{await repository.expire_due_reservations(limit=50)}"

        results = await _released_together(2, _race)
        failures = [result for result in results if isinstance(result, BaseException)]
        assert not failures, f"attempt {attempt}: an actor failed: {failures}"

        states = [view.state for view in await repository.list_case_reservations(case_id)]
        assert states in ([ReservationState.CONSUMED], [ReservationState.EXPIRED]), (
            f"attempt {attempt}: the hold ended in {states}"
        )
        items = await repository.list_case_return_items(case_id)
        assert [item["returnRecordId"] for item in items] == [record_id]
        availability = await repository.load_order_line_availability(
            tenant_id=TENANT,
            order_reference=order,
            viewing_case_id=None,
            ordered_by_line={LINE: 2},
        )
        assert availability[LINE].returnable_quantity == 0
        assert availability[LINE].data_inconsistency is None, (
            f"attempt {attempt}: the units were counted twice"
        )


# ---------------------------------------------------------------------------
# The sweep is scheduled, not merely available
# ---------------------------------------------------------------------------


async def test_the_housekeeping_cycle_settles_lapsed_holds(
    repository: OperationalRepository,
) -> None:
    """`expire_due_reservations` existed and nothing called it.

    Driven through the real `HousekeepingCycle` and the real composition
    factory, so a reclaimer that was written but never registered fails here --
    which is the defect this closes, not the sweep itself.
    """
    from return_platform.configuration.return_configuration import HousekeepingConfiguration
    from return_platform.housekeeping.composition import build_housekeeping_cycle

    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await repository.replace_case_line_selection(
        case_id=case_id,
        tenant_id=TENANT,
        order_reference=order,
        selections=(LineSelection(order_line_reference=LINE, quantity=2),),
        ordered_by_line={LINE: 2},
        ttl_seconds=60,
        now=datetime.now(UTC) - timedelta(seconds=120),
    )

    cycle = build_housekeeping_cycle(
        # The fixture's own `Settings`, so the factory builds its repository
        # against the same throwaway database this test wrote into. Reaching for
        # it is deliberate: rebuilding one here would be a second answer to
        # "which database", and the whole point is that the composition root
        # resolves it from settings rather than from an injected handle.
        settings_provider=lambda: repository._settings,
        configuration_provider=HousekeepingConfiguration,
        temporal_client=None,
        mongo=repository.platform_client,
        neo4j_driver=None,
    )
    result = await cycle.run_once()

    sweep = next(
        outcome for outcome in result.outcomes if outcome.resource_class == "order-line-reservation"
    )
    assert sweep.ran is True, sweep.skipped_reason
    assert sweep.reclaimed >= 1
    assert [view.state for view in await repository.list_case_reservations(case_id)] == [
        ReservationState.EXPIRED
    ]


async def test_the_scheduled_sweep_leaves_a_live_hold_alone(
    repository: OperationalRepository,
) -> None:
    """A pass that settled a hold an associate is still working against would
    hand the units to the next case mid-conversation."""
    from return_platform.configuration.return_configuration import (
        HousekeepingConfiguration,
        OrderLineReservationReclamationConfiguration,
    )
    from return_platform.housekeeping.order_line_reservations import (
        OrderLineReservationReclaimer,
    )

    order = _order()
    case_id = await _seed_case(repository, order_reference=order)
    await _select(repository, case_id=case_id, order_reference=order, quantity=2)

    batch = HousekeepingConfiguration().order_line_reservations.batch_limit
    assert batch == OrderLineReservationReclamationConfiguration().batch_limit
    outcome = await OrderLineReservationReclaimer(
        sweep=repository, batch_limit=batch
    ).reclaim_once()

    assert outcome.ran is True
    # Case-scoped rather than collection-wide: the sweep runs over every hold in
    # the database, and asserting a total here would make this test depend on
    # what every earlier test in the module left behind.
    assert [view.state for view in await repository.list_case_reservations(case_id)] == [
        ReservationState.ACTIVE
    ]


async def test_the_sweep_reports_a_backlog_it_did_not_clear(
    repository: OperationalRepository,
) -> None:
    """`examined` is the candidate count, not a restatement of `reclaimed`.

    A batch limit below the backlog is the shape an operator has to be able to
    read: the pass is working, it is behind, and the next one continues.
    """
    from return_platform.housekeeping.order_line_reservations import (
        OrderLineReservationReclaimer,
    )

    stale = datetime.now(UTC) - timedelta(seconds=120)
    for _ in range(3):
        # A fresh order each time: three cases holding the same two units of one
        # line is a genuine shortage, and the second selection would be refused.
        order = _order()
        case_id = await _seed_case(repository, order_reference=order)
        await repository.replace_case_line_selection(
            case_id=case_id,
            tenant_id=TENANT,
            order_reference=order,
            selections=(LineSelection(order_line_reference=LINE, quantity=2),),
            ordered_by_line={LINE: 2},
            ttl_seconds=60,
            now=stale,
        )

    outcome = await OrderLineReservationReclaimer(sweep=repository, batch_limit=2).reclaim_once()

    assert outcome.examined == 2
    assert outcome.reclaimed == 2
    assert outcome.failed == 0
    # And the rest is still there for the next pass.
    assert await repository.count_due_reservations(limit=50) >= 1
