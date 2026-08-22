"""Terminalizing graph-sync runs whose process went away.

`GraphSyncService.sync` writes its ledger row `RUNNING`, then moves it to
COMPLETED on success or FAILED when an exception propagates. Both of those need
the process to still be there. A worker that is *killed* -- a restart, an OOM, a
host reboot mid-rebuild -- leaves a row that claims to be in progress for as long
as the collection survives.

The audit found one that had been RUNNING for fifteen hours with zero node
writes, and the screen presented it as `LATEST RUN -- Status RUNNING`. The
operator's only view of graph freshness was permanently wrong, and the service's
own comment said the ledger "is truthful either way" -- which held for raised
exceptions and for nothing else.

**Heartbeat, not age.** A long rebuild is legitimately RUNNING for a long time,
so elapsed time says nothing on its own. `sync` now refreshes `heartbeatAt`
while it works, and this reads the absence of recent beats rather than the
presence of a long run. A row with no heartbeat at all predates that and falls
back to `startedAt`, so old rows still terminalize instead of being immortal.

**STALLED, not FAILED.** FAILED means the sync ran and did not work; STALLED
means nobody knows, because whatever would have reported went away. An operator
reads them differently -- one points at the source data, the other at the
worker -- and collapsing them would lose the distinction the reaper exists to
draw.

Nothing is deleted. The row is the only record the run happened, and a rebuild
that died half way is exactly the thing somebody will want to read afterwards.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from return_platform.housekeeping.reclamation import ReclamationOutcome

__all__ = ["RESOURCE_CLASS", "StalledSyncRunReclaimer", "SyncRunLedgerPort"]

logger = logging.getLogger("return_platform.housekeeping.sync_runs")

RESOURCE_CLASS = "stalled-sync-run"


class SyncRunLedgerPort(Protocol):
    """The two ledger operations terminalizing needs, as a seam."""

    async def list_unconfirmed_running(
        self, *, cutoff: datetime, limit: int
    ) -> tuple[dict[str, Any], ...]: ...

    async def mark_stalled(self, *, run_id: str, observed_at: datetime) -> bool: ...


class StalledSyncRunReclaimer:
    """Moves unconfirmed RUNNING rows to STALLED."""

    def __init__(
        self,
        *,
        ledger: SyncRunLedgerPort,
        stall_seconds: float,
        batch_limit: int,
    ) -> None:
        if stall_seconds <= 0:
            raise ValueError("stall_seconds must be positive")
        if batch_limit < 1:
            raise ValueError("batch_limit must be at least 1")
        self._ledger = ledger
        self._stall_seconds = stall_seconds
        self._batch_limit = batch_limit

    async def reclaim_once(self) -> ReclamationOutcome:
        cutoff = datetime.now(UTC) - timedelta(seconds=self._stall_seconds)
        candidates = await self._ledger.list_unconfirmed_running(
            cutoff=cutoff, limit=self._batch_limit
        )

        stalled: list[str] = []
        failed = 0
        now = datetime.now(UTC)
        for candidate in candidates:
            run_id = str(candidate.get("_id") or candidate.get("id") or "")
            if not run_id:
                continue
            try:
                moved = await self._ledger.mark_stalled(run_id=run_id, observed_at=now)
            except Exception:  # noqa: BLE001 - one row never stops the pass
                failed += 1
                logger.warning(
                    "housekeeping_sync_run_stall_failed",
                    extra={"run_id": run_id},
                    exc_info=True,
                )
                continue
            if moved:
                stalled.append(run_id)
                logger.info(
                    "housekeeping_sync_run_marked_stalled",
                    extra={"run_id": run_id},
                )

        return ReclamationOutcome(
            resource_class=RESOURCE_CLASS,
            examined=len(candidates),
            reclaimed=len(stalled),
            reclaimed_ids=tuple(stalled),
            failed=failed,
        )
