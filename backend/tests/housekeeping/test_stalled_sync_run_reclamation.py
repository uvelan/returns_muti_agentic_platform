"""A graph-sync run whose process died must not stay RUNNING forever.

`GraphSyncService.sync` records COMPLETED on success and FAILED when an exception
propagates. Both need the process to still be there, so a worker that is *killed*
mid-rebuild leaves a row that claims to be in progress for as long as the
collection survives. The audit found one that had been RUNNING for fifteen hours
with zero node writes, presented by the console as the latest run -- the
operator's only view of graph freshness, permanently wrong.

The service's own comment said the ledger "is truthful either way". That held for
raised exceptions and for nothing else.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from return_platform.housekeeping.sync_runs import StalledSyncRunReclaimer

_STALL_SECONDS = 150


class _Ledger:
    """An in-memory `graph_sync_runs`, recording what was terminalized."""

    def __init__(self, runs: list[dict[str, Any]], *, fail_on: str | None = None) -> None:
        self._runs = runs
        self._fail_on = fail_on
        self.stalled: list[str] = []
        self.listed_cutoffs: list[datetime] = []

    async def list_unconfirmed_running(
        self, *, cutoff: datetime, limit: int
    ) -> tuple[dict[str, Any], ...]:
        self.listed_cutoffs.append(cutoff)
        matched = [
            run
            for run in self._runs
            if run["status"] == "RUNNING" and (run.get("heartbeatAt") or run["startedAt"]) <= cutoff
        ]
        return tuple(matched[:limit])

    async def mark_stalled(self, *, run_id: str, observed_at: datetime) -> bool:
        del observed_at
        if run_id == self._fail_on:
            raise RuntimeError("mongo unavailable")
        for run in self._runs:
            if run["_id"] == run_id and run["status"] == "RUNNING":
                run["status"] = "STALLED"
                self.stalled.append(run_id)
                return True
        return False


def _run(
    run_id: str,
    *,
    status: str = "RUNNING",
    heartbeat_age: timedelta | None = None,
    started_age: timedelta = timedelta(minutes=1),
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "_id": run_id,
        "status": status,
        "startedAt": now - started_age,
        "heartbeatAt": None if heartbeat_age is None else now - heartbeat_age,
    }


def _reclaimer(ledger: _Ledger, *, batch_limit: int = 20) -> StalledSyncRunReclaimer:
    return StalledSyncRunReclaimer(
        ledger=ledger, stall_seconds=_STALL_SECONDS, batch_limit=batch_limit
    )


@pytest.mark.asyncio
async def test_a_run_that_stopped_beating_is_terminalized() -> None:
    """The fifteen-hour row, as a test."""
    ledger = _Ledger([_run("dead", heartbeat_age=timedelta(hours=15))])

    outcome = await _reclaimer(ledger).reclaim_once()

    assert ledger.stalled == ["dead"]
    assert outcome.reclaimed == 1
    assert outcome.reclaimed_ids == ("dead",)


@pytest.mark.asyncio
async def test_a_run_still_beating_is_left_alone() -> None:
    """A full rebuild is legitimately RUNNING for a long time.

    Age says nothing on its own, which is why the rule reads the heartbeat: this
    run started hours ago and confirmed itself a moment ago.
    """
    ledger = _Ledger(
        [_run("alive", heartbeat_age=timedelta(seconds=5), started_age=timedelta(hours=4))]
    )

    outcome = await _reclaimer(ledger).reclaim_once()

    assert ledger.stalled == []
    assert outcome.reclaimed == 0


@pytest.mark.asyncio
async def test_a_row_with_no_heartbeat_falls_back_to_when_it_started() -> None:
    """Rows written before heartbeating must still terminalize.

    Without the fallback they would match nothing and stay RUNNING forever --
    which is the exact condition being fixed, preserved for every row that
    already has it.
    """
    ledger = _Ledger([_run("legacy", heartbeat_age=None, started_age=timedelta(hours=15))])

    outcome = await _reclaimer(ledger).reclaim_once()

    assert ledger.stalled == ["legacy"]
    assert outcome.reclaimed == 1


@pytest.mark.asyncio
async def test_a_finished_run_is_never_touched() -> None:
    """A run that recorded its own outcome keeps it.

    Overwriting a real COMPLETED or FAILED with a guess is worse than leaving a
    stale row: one is missing information, the other is wrong information.
    """
    ledger = _Ledger(
        [
            _run("done", status="COMPLETED", started_age=timedelta(hours=15)),
            _run("broke", status="FAILED", started_age=timedelta(hours=15)),
        ]
    )

    outcome = await _reclaimer(ledger).reclaim_once()

    assert ledger.stalled == []
    assert outcome.examined == 0


@pytest.mark.asyncio
async def test_one_unwritable_row_never_stops_the_pass() -> None:
    ledger = _Ledger(
        [
            _run("first", heartbeat_age=timedelta(hours=15)),
            _run("second", heartbeat_age=timedelta(hours=15)),
        ],
        fail_on="first",
    )

    outcome = await _reclaimer(ledger).reclaim_once()

    assert ledger.stalled == ["second"]
    assert outcome.failed == 1
    assert outcome.reclaimed == 1


@pytest.mark.asyncio
async def test_the_cutoff_is_the_stall_window_behind_now() -> None:
    """The window is what separates a slow run from a dead one."""
    ledger = _Ledger([])

    await _reclaimer(ledger).reclaim_once()

    (cutoff,) = ledger.listed_cutoffs
    elapsed = (datetime.now(UTC) - cutoff).total_seconds()
    assert _STALL_SECONDS - 5 <= elapsed <= _STALL_SECONDS + 5
