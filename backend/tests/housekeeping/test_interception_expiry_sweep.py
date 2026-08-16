"""The reclaimer's own rules: batching, reporting, and what it refuses to decide.

The sweep's *correctness* -- that only `PENDING` records past their deadline
move, and that an operator answering one this pass had selected wins the
conditional update -- belongs to the store and to MongoDB, and is settled in
`tests/test_interception_expiry.py` (the predicate and the compare-and-set) and
against a real replica set in `tests/test_durable_interception_real_infra.py`.
What is settled here is the schedule around it: that the pass reports honestly,
that it does not query twice when there is nothing to do, and that it holds no
copy of the expiry predicate.
"""

from __future__ import annotations

import pytest

from return_platform.housekeeping.interception_expiry import (
    RESOURCE_CLASS,
    InterceptionExpiryReclaimer,
)


class _Sweep:
    """A store stand-in that records the limits it was called with.

    Not a stand-in for the expiry rule -- it has none, which is the point. This
    module asserts the reclaimer's arithmetic over whatever the store answers.
    """

    def __init__(self, *, lapsed: int, expired: int) -> None:
        self._lapsed = lapsed
        self._expired = expired
        self.counted: list[int] = []
        self.swept: list[int] = []

    async def count_lapsed(self, *, limit: int) -> int:
        self.counted.append(limit)
        return self._lapsed

    async def expire_lapsed(self, *, limit: int) -> int:
        self.swept.append(limit)
        return self._expired


@pytest.mark.asyncio
async def test_a_pass_reports_what_it_examined_and_what_it_settled() -> None:
    sweep = _Sweep(lapsed=9, expired=9)

    outcome = await InterceptionExpiryReclaimer(sweep=sweep, batch_limit=200).reclaim_once()

    assert outcome.resource_class == RESOURCE_CLASS
    assert outcome.ran is True
    assert outcome.examined == 9
    assert outcome.reclaimed == 9
    assert outcome.details == {"contended": 0}
    assert sweep.counted == [200] and sweep.swept == [200]


@pytest.mark.asyncio
async def test_an_empty_backlog_costs_one_read_and_no_write() -> None:
    """The common case on a healthy deployment, and it runs every interval.

    A pass that issued the update anyway would take a write lock on a collection
    an operator console is reading, forever, to change nothing.
    """
    sweep = _Sweep(lapsed=0, expired=0)

    outcome = await InterceptionExpiryReclaimer(sweep=sweep, batch_limit=200).reclaim_once()

    assert outcome.examined == 0
    assert outcome.reclaimed == 0
    assert outcome.ran is True
    assert sweep.counted == [200]
    assert sweep.swept == [], "nothing was due; nothing should have been written"


@pytest.mark.asyncio
async def test_an_operator_winning_the_race_is_contention_not_failure() -> None:
    """Somebody answered one of the very records this pass selected.

    The conditional update loses, and that is the mechanism working: the human's
    text is kept. Reporting it as `failed` would put a permanent error on a
    healthy deployment's dashboard for doing the right thing.
    """
    sweep = _Sweep(lapsed=5, expired=3)

    outcome = await InterceptionExpiryReclaimer(sweep=sweep, batch_limit=200).reclaim_once()

    assert outcome.examined == 5
    assert outcome.reclaimed == 3
    assert outcome.failed == 0
    assert outcome.details == {"contended": 2}


@pytest.mark.asyncio
async def test_the_batch_limit_is_passed_through_to_both_reads() -> None:
    """Both must see the same ceiling, or `examined` and `reclaimed` describe
    different populations and `contended` becomes noise."""
    sweep = _Sweep(lapsed=4, expired=4)

    await InterceptionExpiryReclaimer(sweep=sweep, batch_limit=7).reclaim_once()

    assert sweep.counted == [7] and sweep.swept == [7]


def test_a_batch_limit_below_one_is_refused_at_construction() -> None:
    """A zero batch is a reclaimer that runs every interval and reaps nothing,
    which reads exactly like a healthy one."""
    with pytest.raises(ValueError, match="batch_limit"):
        InterceptionExpiryReclaimer(sweep=_Sweep(lapsed=0, expired=0), batch_limit=0)
