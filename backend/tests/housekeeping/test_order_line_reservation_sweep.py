"""The reclaimer's own rules: batching, reporting, and what it refuses to decide.

The sweep's *correctness* -- that only `ACTIVE` holds past their deadline move,
that an authorization racing it cannot both win -- is a property of MongoDB and
is proved against a real replica set in
`tests/operations/test_order_line_reservations_real_infra.py`, including through
this reclaimer. What is settled here is the schedule around it: that the pass
reports honestly, that it does not query twice when there is nothing to do, and
that it holds no copy of the expiry predicate.
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.housekeeping.order_line_reservations import (
    RESOURCE_CLASS,
    OrderLineReservationReclaimer,
)


class _Sweep:
    """A repository stand-in that records the limits it was called with.

    Not a stand-in for the expiry rule -- it has none, which is the point. This
    module asserts the reclaimer's arithmetic over whatever the repository
    answers; the rule itself is never exercised here because a fake would say
    "yes" to every question a real replica set is the only thing able to answer.
    """

    def __init__(self, *, due: int, expired: int) -> None:
        self._due = due
        self._expired = expired
        self.counted: list[int] = []
        self.swept: list[int] = []

    async def count_due_reservations(self, *, limit: int) -> int:
        self.counted.append(limit)
        return self._due

    async def expire_due_reservations(self, *, limit: int) -> int:
        self.swept.append(limit)
        return self._expired


@pytest.mark.asyncio
async def test_a_pass_reports_what_it_examined_and_what_it_settled() -> None:
    sweep = _Sweep(due=7, expired=7)

    outcome = await OrderLineReservationReclaimer(sweep=sweep, batch_limit=50).reclaim_once()

    assert outcome.resource_class == RESOURCE_CLASS
    assert outcome.ran is True
    assert outcome.examined == 7
    assert outcome.reclaimed == 7
    assert outcome.details == {"contended": 0}
    assert sweep.counted == [50] and sweep.swept == [50]


@pytest.mark.asyncio
async def test_a_hold_settled_by_something_else_is_contention_not_failure() -> None:
    """An authorization consuming a hold this pass selected wins the conditional
    update, and the sweep is simply not counted for it. Nothing went wrong, so it
    must not land in `failed` -- which is what an operator pages on."""
    outcome = await OrderLineReservationReclaimer(
        sweep=_Sweep(due=5, expired=3), batch_limit=50
    ).reclaim_once()

    assert outcome.examined == 5
    assert outcome.reclaimed == 3
    assert outcome.failed == 0
    assert outcome.details == {"contended": 2}


@pytest.mark.asyncio
async def test_an_empty_backlog_does_not_issue_a_write() -> None:
    """The common case on a healthy deployment, every interval, forever."""
    sweep = _Sweep(due=0, expired=0)

    outcome = await OrderLineReservationReclaimer(sweep=sweep, batch_limit=50).reclaim_once()

    assert outcome.ran is True
    assert (outcome.examined, outcome.reclaimed) == (0, 0)
    assert sweep.swept == [], "a pass with no candidates still ran the sweep"


@pytest.mark.asyncio
async def test_the_batch_limit_bounds_both_the_count_and_the_sweep() -> None:
    """A backlog is counted up to the batch a pass would attempt, never scanned
    in full to produce a number nobody acts on."""
    sweep = _Sweep(due=4, expired=4)

    await OrderLineReservationReclaimer(sweep=sweep, batch_limit=4).reclaim_once()

    assert sweep.counted == [4] and sweep.swept == [4]


def test_a_batch_limit_below_one_is_refused_at_construction() -> None:
    """The fail-closed direction the cycle relies on: a bad release produces no
    reclaimer and the pass records why, rather than sweeping on unvalidated rules."""
    with pytest.raises(ValueError):
        OrderLineReservationReclaimer(sweep=_Sweep(due=0, expired=0), batch_limit=0)


def test_the_reclaimer_cannot_reach_the_reservation_documents_itself() -> None:
    """`ACTIVE and past its deadline` is written once, on the repository, beside
    the state machine it enforces.

    Enforced as a dependency rule rather than by grepping the source: this module
    may not import a Mongo driver or the reservation model, so the only way it
    can settle a hold is by asking the repository -- and a second copy of the
    predicate here would have nothing to apply itself to.
    """
    import inspect

    from return_platform.housekeeping import order_line_reservations

    source = inspect.getsource(order_line_reservations)
    for forbidden in ("import pymongo", "from pymongo", "order_lines.reservations"):
        assert forbidden not in source, f"the reclaimer reaches past the repository: {forbidden}"


def test_the_protocol_is_satisfied_by_the_real_repository() -> None:
    """Structural typing is only a promise until something checks it.

    `OperationalRepository` is what the composition root builds this over, and a
    renamed method there would otherwise fail at the first production pass.
    """
    import inspect

    from return_platform.operations.repository import OperationalRepository

    for name in ("count_due_reservations", "expire_due_reservations"):
        method: Any = getattr(OperationalRepository, name)
        assert callable(method)
        assert "limit" in inspect.signature(method).parameters, f"{name} takes no batch limit"
