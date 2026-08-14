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

#: The one status a generation may be reclaimed from. A frozenset of one, rather
#: than an equality check, so that widening it is a deliberate edit at a named
#: constant instead of an inline `in {...}` somewhere in a loop.
_RECLAIMABLE_STATUSES = frozenset({GraphGenerationStatus.RETIRED})


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
        self, *, graph_generation_id: str, observed_at: datetime
    ) -> datetime: ...

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
    ) -> None:
        if retention_seconds < 0:
            raise ValueError("retention_seconds must not be negative")
        if batch_limit < 1:
            raise ValueError("batch_limit must be at least 1")
        if node_delete_batch_size < 1:
            raise ValueError("node_delete_batch_size must be at least 1")
        self._graph = graph
        self._lease_store = lease_store
        self._active_generations = active_generations
        self._retention_seconds = retention_seconds
        self._batch_limit = batch_limit
        self._node_delete_batch_size = node_delete_batch_size

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
                reason=f"status is {parsed.value}, not RETIRED",
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
        if now - eligible_since < timedelta(seconds=self._retention_seconds):
            return ReclaimableGeneration(
                graph_generation_id=graph_generation_id,
                eligible=False,
                reason="inside the retention window",
                eligible_since=eligible_since,
            )
        return ReclaimableGeneration(
            graph_generation_id=graph_generation_id,
            eligible=True,
            reason="RETIRED, unreferenced, drained and past retention",
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
        candidates = await self._graph.list_generations_by_status(
            status=GraphGenerationStatus.RETIRED.value,
            # Read more than are reclaimed: the ones inside their quarantine are
            # candidates that still need their stamp written, and a limit equal
            # to the batch limit would keep re-reading the same head of the list
            # and never reach the rest.
            limit=max(self._batch_limit * 4, self._batch_limit),
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
                    await self._graph.mark_reclaim_eligible(
                        graph_generation_id=graph_generation_id, observed_at=now
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
                deleted_nodes = await self._delete_generation(graph_generation_id)
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
            details={"quarantine_started": quarantined, "not_yet_reclaimable": blocked},
        )

    async def _delete_generation(self, graph_generation_id: str) -> int:
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
        await self._graph.delete_generation_marker(
            graph_generation_id=graph_generation_id,
            status=GraphGenerationStatus.RETIRED.value,
        )
        return deleted
