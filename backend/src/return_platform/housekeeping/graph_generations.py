"""Removing the nodes a RETIRED graph generation left behind.

Retirement is deliberately status-only. `GenerationLifecycleOrchestrator._retire`
moves the predecessor ACTIVE -> DRAINING -> RETIRED and stops; it does not
delete anything. That is correct and it is what makes cutover safe -- every
compiled read and write is scoped by `graph_generation_id`, so a retired
generation is *unreachable* rather than gone, and a reader that resolved the old
snapshot a moment before the swap still finds its data where it left it.

The cost is that nothing ever reclaims it. 212 `GraphGeneration` markers had
accumulated on this deployment, each with a full projection of the source data
behind it.

**The eligibility rule, all four parts positive.** A generation is reclaimable
only when:

1. its Neo4j marker status is exactly `RETIRED` -- not DRAINING, not FAILED, not
   ACTIVE. `DRAINING` is the state that says "no longer reachable, not yet safe
   to remove", and it is the one a stuck drain is left parked in for an operator;
2. it is not the generation named by the live `ActiveRuntimeSnapshot`. Redundant
   against (1) -- the active generation is ACTIVE by definition -- and kept
   because it is the one check that reads the authority every request resolves,
   so a marker that lied about its status still could not take the serving
   generation with it;
3. it holds no outstanding read lease or write reservation. `MongoGenerationLease
   Store.outstanding` counts by `expires_at`, so a crashed holder stops blocking
   on its own and a live one blocks for real;
4. it has been continuously eligible for longer than the retention window.

**Why the window is measured from first observation.** The marker records
`created_at` and nothing records `retired_at` -- `compile_generation_transition`
only writes `status`. A generation that served for a month and retired sixty
seconds ago therefore has a `created_at` a month old, and a creation-based window
would delete it on the very next pass. This module stamps
`reclaim_eligible_since` the first time it sees a generation satisfy (1)-(3) and
measures from that, which is a real quarantine rather than an accidental one. The
stamp is idempotent and crash-safe: a pass that dies after stamping simply finds
the stamp already there.

**Why deletion is batched.** A generation is a whole projection of the source
data. `DETACH DELETE` over all of it in one transaction is how a cleanup takes
Neo4j's heap with it. Nodes go in bounded batches, and the marker is removed last
-- so a pass interrupted half way leaves a still-RETIRED, still-ineligible-to-
serve generation with fewer nodes, which the next pass finishes. There is no
state in which a partial delete is observable as anything but a retired
generation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from return_platform.dynamic_knowledge.graph.generation import GraphGenerationStatus
from return_platform.dynamic_knowledge.lifecycle.lease_store import GenerationLeaseStore
from return_platform.housekeeping.reclamation import ReclamationOutcome

__all__ = [
    "RESOURCE_CLASS",
    "ActiveGenerationReader",
    "GenerationGraphPort",
    "GraphGenerationReclaimer",
    "ReclaimableGeneration",
]

logger = logging.getLogger("return_platform.housekeeping.graph_generations")

RESOURCE_CLASS = "graph-generation"

#: Statuses a generation may be reclaimed from, and what each additionally
#: requires beyond the shared gates.
#:
#: This was a frozenset of one -- `RETIRED` -- and that is why nothing was ever
#: reclaimed in practice. A live census reads 218 `PREPARING`, 45 `FAILED`,
#: 10 `ACTIVE`, 2 `RETIRED`: a perfectly healthy pass examined two markers and
#: left 263 of them, with roughly 13,000 orphaned nodes behind them, untouched
#: forever.
#:
#: `None` means the shared gates alone decide. A number names an additional age
#: floor in seconds, read from configuration, that the candidate must also clear.
#:
#: **`DRAINING` and `ACTIVE` are deliberately absent.** `DRAINING` means readers
#: that resolved a moment ago are still reading and on-demand sync may still be
#: writing -- it is the state that exists precisely to say "not yet safe to
#: remove". `ACTIVE` is never deleted at all; an orphaned one is reconciled onto
#: the retirement path instead, which is what `_reconcile_orphaned_active` does.
_ABANDONED_BUILD_STATUSES = frozenset(
    {
        GraphGenerationStatus.PREPARING,
        GraphGenerationStatus.BUILDING,
        GraphGenerationStatus.CATCHING_UP,
        GraphGenerationStatus.VALIDATING,
        GraphGenerationStatus.READY_FOR_ACTIVATION,
    }
)

#: Terminal, unreferenced, and safe on the shared gates alone.
#:
#: `FAILED` joins `RETIRED` because it is terminal and reachable from any
#: pre-ACTIVE state -- a rebuild that died leaves its candidate here, and nothing
#: else in the platform ever clears one.
_TERMINAL_RECLAIMABLE_STATUSES = frozenset(
    {
        GraphGenerationStatus.RETIRED,
        GraphGenerationStatus.FAILED,
    }
)

_RECLAIMABLE_STATUSES = _TERMINAL_RECLAIMABLE_STATUSES | _ABANDONED_BUILD_STATUSES


class ActiveGenerationReader(Protocol):
    """Every generation any live `ActiveRuntimeSnapshot` points at.

    Deliberately the whole set rather than `read(snapshot_name=...)` for a list
    of names this module was told about. Only `ORDER_DISCOVERY` exists today, and
    a hardcoded name is exactly how a second snapshot's active generation would
    become reclaimable the day someone added one -- the reclaimer would keep
    passing rule (2) for it and nothing would fail until its data was gone.
    """

    async def active_generation_ids(self) -> frozenset[str]: ...


class GenerationGraphPort(Protocol):
    """The Neo4j operations reclamation needs, as a seam.

    Structural so the eligibility rules can be exercised without a driver --
    they are the part that must not be got wrong, and a test that needs a live
    Neo4j to assert "DRAINING is not reclaimable" is a test nobody runs.
    """

    async def list_generations_by_status(
        self, *, status: str, limit: int
    ) -> tuple[dict[str, Any], ...]: ...

    async def mark_reclaim_eligible(
        self, *, graph_generation_id: str, status: str, observed_at: datetime
    ) -> datetime: ...

    async def transition_generation_status(
        self, *, graph_generation_id: str, expected_status: str, next_status: str
    ) -> bool: ...

    async def delete_generation_nodes(
        self, *, graph_generation_id: str, batch_size: int
    ) -> int: ...

    async def delete_generation_marker(self, *, graph_generation_id: str, status: str) -> bool: ...


class ReclaimableGeneration:
    """One candidate and the verdict on it, so a rejection can be logged."""

    __slots__ = ("eligible", "eligible_since", "graph_generation_id", "reason")

    def __init__(
        self,
        *,
        graph_generation_id: str,
        eligible: bool,
        reason: str,
        eligible_since: datetime | None = None,
    ) -> None:
        self.graph_generation_id = graph_generation_id
        self.eligible = eligible
        self.reason = reason
        self.eligible_since = eligible_since


class GraphGenerationReclaimer:
    """Deletes RETIRED, unreferenced, fully drained, quarantined generations."""

    def __init__(
        self,
        *,
        graph: GenerationGraphPort,
        lease_store: GenerationLeaseStore,
        active_generations: ActiveGenerationReader,
        retention_seconds: float,
        batch_limit: int,
        node_delete_batch_size: int,
        abandoned_build_seconds: float = 21_600,
        orphaned_active_seconds: float = 86_400,
    ) -> None:
        if retention_seconds < 0:
            raise ValueError("retention_seconds must not be negative")
        if batch_limit < 1:
            raise ValueError("batch_limit must be at least 1")
        if node_delete_batch_size < 1:
            raise ValueError("node_delete_batch_size must be at least 1")
        if abandoned_build_seconds < 0:
            raise ValueError("abandoned_build_seconds must not be negative")
        if orphaned_active_seconds < 0:
            raise ValueError("orphaned_active_seconds must not be negative")
        self._graph = graph
        self._lease_store = lease_store
        self._active_generations = active_generations
        self._retention_seconds = retention_seconds
        self._batch_limit = batch_limit
        self._node_delete_batch_size = node_delete_batch_size
        self._abandoned_build_seconds = abandoned_build_seconds
        self._orphaned_active_seconds = orphaned_active_seconds

    async def assess(
        self,
        *,
        graph_generation_id: str,
        status: str,
        active_generation_ids: frozenset[str],
        eligible_since: datetime | None,
        now: datetime,
    ) -> ReclaimableGeneration:
        """The complete eligibility rule, decided without touching the graph."""

        try:
            parsed = GraphGenerationStatus(status)
        except ValueError:
            return ReclaimableGeneration(
                graph_generation_id=graph_generation_id,
                eligible=False,
                reason=f"unrecognised status {status!r}",
            )
        if parsed not in _RECLAIMABLE_STATUSES:
            return ReclaimableGeneration(
                graph_generation_id=graph_generation_id,
                eligible=False,
                reason=f"status {parsed.value} is not reclaimable",
            )
        if graph_generation_id in active_generation_ids:
            return ReclaimableGeneration(
                graph_generation_id=graph_generation_id,
                eligible=False,
                reason="referenced by a live ActiveRuntimeSnapshot",
            )
        outstanding = await self._lease_store.outstanding(graph_generation_id=graph_generation_id)
        held = {lease_class.value: count for lease_class, count in outstanding.items() if count}
        if held:
            return ReclaimableGeneration(
                graph_generation_id=graph_generation_id,
                eligible=False,
                reason="outstanding " + ", ".join(f"{k}={v}" for k, v in sorted(held.items())),
            )
        if eligible_since is None:
            return ReclaimableGeneration(
                graph_generation_id=graph_generation_id,
                eligible=False,
                reason="quarantine starts now",
            )
        if eligible_since.tzinfo is None:
            eligible_since = eligible_since.replace(tzinfo=UTC)
        # The shared window, then the per-status one. A build status carries a
        # second, longer floor because a *live* rebuild sits in exactly those
        # states -- the floor is what excludes one arithmetically, in place of a
        # lock this pass has no safe way to take.
        window = timedelta(seconds=self._retention_seconds)
        if parsed in _ABANDONED_BUILD_STATUSES:
            window = max(window, timedelta(seconds=self._abandoned_build_seconds))
        if now - eligible_since < window:
            return ReclaimableGeneration(
                graph_generation_id=graph_generation_id,
                eligible=False,
                reason="inside the retention window",
                eligible_since=eligible_since,
            )
        return ReclaimableGeneration(
            graph_generation_id=graph_generation_id,
            eligible=True,
            reason=f"{parsed.value}, unreferenced, drained and past retention",
            eligible_since=eligible_since,
        )

    async def reclaim_once(self) -> ReclamationOutcome:
        """One pass. Reclaims at most `batch_limit` generations."""

        # Read before anything is examined, and allowed to raise: not knowing
        # which generation is serving is the one condition under which deleting a
        # generation is unacceptable, so an unreadable snapshot collection must
        # abandon the pass rather than be treated as an empty set of active
        # generations.
        active_generation_ids = await self._active_generations.active_generation_ids()
        now = datetime.now(UTC)
        # Read more than are reclaimed: the ones inside their quarantine are
        # candidates that still need their stamp written, and a limit equal to
        # the batch limit would keep re-reading the same head of the list and
        # never reach the rest.
        limit = max(self._batch_limit * 4, self._batch_limit)
        candidates: list[dict[str, Any]] = []
        for reclaimable in sorted(_RECLAIMABLE_STATUSES, key=lambda member: member.value):
            candidates.extend(
                await self._graph.list_generations_by_status(status=reclaimable.value, limit=limit)
            )

        # ACTIVE is listed too, and never deleted. An ACTIVE marker no snapshot
        # points at is the residue of a cutover that crashed between the
        # compare-and-swap and the retirement that follows it -- reconciled back
        # onto the retirement path, where the ordinary gates then apply.
        reconciled = await self._reconcile_orphaned_active(
            active_generation_ids=active_generation_ids, now=now, limit=limit
        )
        examined = 0
        quarantined = 0
        reclaimed: list[str] = []
        failed = 0
        blocked = 0

        for candidate in candidates:
            if len(reclaimed) >= self._batch_limit:
                break
            graph_generation_id = str(candidate.get("graph_generation_id") or "")
            if not graph_generation_id:
                continue
            examined += 1
            status = str(candidate.get("status") or "")
            eligible_since = candidate.get("reclaim_eligible_since")
            if eligible_since is not None and not isinstance(eligible_since, datetime):
                eligible_since = None
            verdict = await self.assess(
                graph_generation_id=graph_generation_id,
                status=status,
                active_generation_ids=active_generation_ids,
                eligible_since=eligible_since,
                now=now,
            )
            if not verdict.eligible:
                if verdict.reason == "quarantine starts now":
                    # Everything except the window is satisfied, so this is the
                    # moment the clock starts. Stamped rather than deleted.
                    # The candidate's own status, not a literal. Stamping a
                    # FAILED or PREPARING marker with `status: "RETIRED"` would
                    # match zero rows, the stamp would never land, and the
                    # verdict would be "quarantine starts now" on every pass
                    # forever -- a cleanup that runs eternally and frees nothing.
                    await self._graph.mark_reclaim_eligible(
                        graph_generation_id=graph_generation_id,
                        status=status,
                        observed_at=now,
                    )
                    quarantined += 1
                else:
                    blocked += 1
                    logger.debug(
                        "housekeeping_generation_not_reclaimable",
                        extra={
                            "graph_generation_id": graph_generation_id,
                            "reason": verdict.reason,
                        },
                    )
                continue

            try:
                deleted_nodes = await self._delete_generation(graph_generation_id, status=status)
            except Exception:  # noqa: BLE001 - one generation never stops the pass
                failed += 1
                logger.warning(
                    "housekeeping_generation_reclamation_failed",
                    extra={"graph_generation_id": graph_generation_id},
                    exc_info=True,
                )
                continue
            reclaimed.append(graph_generation_id)
            logger.info(
                "housekeeping_graph_generation_reclaimed",
                extra={
                    "graph_generation_id": graph_generation_id,
                    "deleted_nodes": deleted_nodes,
                },
            )

        return ReclamationOutcome(
            resource_class=RESOURCE_CLASS,
            examined=examined,
            reclaimed=len(reclaimed),
            reclaimed_ids=tuple(reclaimed),
            failed=failed,
            details={
                "quarantine_started": quarantined,
                "not_yet_reclaimable": blocked,
                "orphaned_active_reconciled": reconciled,
            },
        )

    async def _reconcile_orphaned_active(
        self,
        *,
        active_generation_ids: frozenset[str],
        now: datetime,
        limit: int,
    ) -> int:
        """Move stranded ACTIVE markers onto the retirement path. Never deletes.

        Activation performs the compare-and-swap and *then* retires the
        predecessor, and that retirement is documented never to raise. A crash
        between the two therefore leaves a marker ACTIVE with nothing in the
        platform that would ever correct it -- which is how ten of them
        accumulated here against one serving generation. Five more come from a
        sync path that mints its marker ACTIVE directly, bypassing the
        orchestrator entirely.

        **Transition, not deletion, and quarantined either way.** A marker
        legitimately carries ACTIVE for a moment *before* the compare-and-swap
        during a live cutover, so an immediate sweep would move a marker out
        from under a generation that is about to serve. The stamp is what tells
        a transient cutover from a stranded predecessor, and DRAINING is where
        the ordinary rules take over: it is the state that means "no longer
        reachable, not yet safe to remove".

        Deleting here would also skip the lease checks entirely. Sending it to
        DRAINING means the next pass has to satisfy every gate before anything
        is removed.
        """

        markers = await self._graph.list_generations_by_status(
            status=GraphGenerationStatus.ACTIVE.value, limit=limit
        )
        reconciled = 0
        for marker in markers:
            graph_generation_id = str(marker.get("graph_generation_id") or "")
            if not graph_generation_id or graph_generation_id in active_generation_ids:
                continue
            eligible_since = marker.get("reclaim_eligible_since")
            if not isinstance(eligible_since, datetime):
                await self._graph.mark_reclaim_eligible(
                    graph_generation_id=graph_generation_id,
                    status=GraphGenerationStatus.ACTIVE.value,
                    observed_at=now,
                )
                continue
            if eligible_since.tzinfo is None:
                eligible_since = eligible_since.replace(tzinfo=UTC)
            if now - eligible_since < timedelta(seconds=self._orphaned_active_seconds):
                continue
            try:
                moved = await self._graph.transition_generation_status(
                    graph_generation_id=graph_generation_id,
                    expected_status=GraphGenerationStatus.ACTIVE.value,
                    next_status=GraphGenerationStatus.DRAINING.value,
                )
            except Exception:  # noqa: BLE001 - one marker never stops the pass
                logger.warning(
                    "housekeeping_orphaned_active_reconciliation_failed",
                    extra={"graph_generation_id": graph_generation_id},
                    exc_info=True,
                )
                continue
            if moved:
                reconciled += 1
                logger.info(
                    "housekeeping_orphaned_active_generation_reconciled",
                    extra={"graph_generation_id": graph_generation_id},
                )
        return reconciled

    async def _delete_generation(self, graph_generation_id: str, *, status: str) -> int:
        """Nodes first, in batches; the marker last.

        Order matters and is the reverse of what looks natural. Removing the
        marker first would leave orphaned generation-scoped nodes with nothing
        left to identify them as reclaimable -- the marker is the only record
        that the generation was ever retired, so a pass that died between the
        two would strand its data permanently.
        """

        deleted = 0
        while True:
            batch = await self._graph.delete_generation_nodes(
                graph_generation_id=graph_generation_id,
                batch_size=self._node_delete_batch_size,
            )
            deleted += batch
            if batch < self._node_delete_batch_size:
                break
        # Guarded on the status this candidate was actually assessed under. A
        # hardcoded RETIRED here would match nothing for a FAILED generation --
        # nodes gone, marker orphaned, and the pass reporting success.
        await self._graph.delete_generation_marker(
            graph_generation_id=graph_generation_id,
            status=status,
        )
        return deleted
