"""A generation is reclaimed only when all four positive rules hold.

The rules restate Track G's design: retirement drains, and cleanup of a RETIRED
generation waits for every read lease and write reservation naming it. Anything
still draining, still leased, still active, or still inside its quarantine must be
untouched -- and the *active* and *target* generations must be untouchable however
the rest of the pass goes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from return_platform.dynamic_knowledge.graph.generation import GraphGenerationStatus
from return_platform.dynamic_knowledge.lifecycle.lease_store import LeaseClass
from return_platform.housekeeping.graph_generations import GraphGenerationReclaimer

_ACTIVE_ID = "generation-serving-traffic"


class _Graph:
    """An in-memory `GenerationGraphPort` that records what was deleted."""

    def __init__(
        self, generations: list[dict[str, Any]], *, nodes: dict[str, int] | None = None
    ) -> None:
        self._generations = generations
        self._nodes = dict(nodes or {})
        self.stamped: list[str] = []
        self.deleted_markers: list[str] = []
        self.node_delete_calls: list[tuple[str, int]] = []

    async def list_generations_by_status(
        self, *, status: str, limit: int
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            dict(generation) for generation in self._generations if generation["status"] == status
        )[:limit]

    async def mark_reclaim_eligible(
        self, *, graph_generation_id: str, observed_at: datetime
    ) -> datetime:
        self.stamped.append(graph_generation_id)
        for generation in self._generations:
            if generation["graph_generation_id"] == graph_generation_id:
                generation.setdefault("reclaim_eligible_since", observed_at)
        return observed_at

    async def delete_generation_nodes(self, *, graph_generation_id: str, batch_size: int) -> int:
        self.node_delete_calls.append((graph_generation_id, batch_size))
        remaining = self._nodes.get(graph_generation_id, 0)
        taken = min(remaining, batch_size)
        self._nodes[graph_generation_id] = remaining - taken
        return taken

    async def delete_generation_marker(self, *, graph_generation_id: str, status: str) -> bool:
        self.deleted_markers.append(graph_generation_id)
        return True


class _LeaseStore:
    def __init__(self, outstanding: dict[str, dict[LeaseClass, int]] | None = None) -> None:
        self._outstanding = outstanding or {}

    async def acquire_read_lease(self, **_: Any) -> None:  # pragma: no cover - unused
        return None

    async def acquire_write_reservation(self, **_: Any) -> None:  # pragma: no cover - unused
        return None

    async def release(self, **_: Any) -> None:  # pragma: no cover - unused
        return None

    async def begin_drain(self, **_: Any) -> None:  # pragma: no cover - unused
        return None

    async def outstanding(self, *, graph_generation_id: str) -> dict[LeaseClass, int]:
        return self._outstanding.get(graph_generation_id, {LeaseClass.READ: 0, LeaseClass.WRITE: 0})


class _ActiveGenerations:
    def __init__(self, ids: frozenset[str] = frozenset({_ACTIVE_ID})) -> None:
        self._ids = ids

    async def active_generation_ids(self) -> frozenset[str]:
        return self._ids


class _UnreadableActiveGenerations:
    async def active_generation_ids(self) -> frozenset[str]:
        raise RuntimeError("platform mongo is unreachable")


def _generation(
    graph_generation_id: str,
    *,
    status: GraphGenerationStatus = GraphGenerationStatus.RETIRED,
    eligible_since: datetime | None = None,
) -> dict[str, Any]:
    return {
        "graph_generation_id": graph_generation_id,
        "status": status.value,
        "reclaim_eligible_since": eligible_since,
    }


def _reclaimer(
    graph: _Graph,
    *,
    lease_store: _LeaseStore | None = None,
    active: Any = None,
    retention_seconds: float = 3_600,
    node_delete_batch_size: int = 1_000,
    batch_limit: int = 5,
) -> GraphGenerationReclaimer:
    return GraphGenerationReclaimer(
        graph=graph,
        lease_store=lease_store or _LeaseStore(),
        active_generations=active if active is not None else _ActiveGenerations(),
        retention_seconds=retention_seconds,
        batch_limit=batch_limit,
        node_delete_batch_size=node_delete_batch_size,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        GraphGenerationStatus.PREPARING,
        GraphGenerationStatus.BUILDING,
        GraphGenerationStatus.CATCHING_UP,
        GraphGenerationStatus.VALIDATING,
        GraphGenerationStatus.READY_FOR_ACTIVATION,
        GraphGenerationStatus.ACTIVE,
        GraphGenerationStatus.DRAINING,
        GraphGenerationStatus.FAILED,
    ],
)
async def test_only_retired_generations_are_even_listed(
    status: GraphGenerationStatus,
) -> None:
    """DRAINING is the one this matters most for.

    `_retire` leaves a generation parked in DRAINING when its drain times out --
    "still reachable by someone, left for operator review". Reclaiming it would
    delete data out from under exactly the reader that caused the timeout.
    """
    graph = _Graph([_generation("g1", status=status)])
    outcome = await _reclaimer(graph).reclaim_once()

    assert graph.deleted_markers == []
    assert outcome.reclaimed == 0
    if status is not GraphGenerationStatus.RETIRED:
        assert outcome.examined == 0


@pytest.mark.asyncio
async def test_the_active_generation_is_never_reclaimed_even_if_its_marker_says_retired() -> None:
    """Rule (2) reads the authority every request resolves.

    A marker that lied about its status -- a botched manual edit, a half-applied
    transition -- must not be able to take the serving generation with it.
    """
    graph = _Graph(
        [_generation(_ACTIVE_ID, eligible_since=datetime.now(UTC) - timedelta(days=30))],
        nodes={_ACTIVE_ID: 5_000},
    )
    outcome = await _reclaimer(graph).reclaim_once()

    assert graph.deleted_markers == []
    assert graph.node_delete_calls == []
    assert outcome.reclaimed == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outstanding",
    [
        {LeaseClass.READ: 1, LeaseClass.WRITE: 0},
        {LeaseClass.READ: 0, LeaseClass.WRITE: 1},
        {LeaseClass.READ: 3, LeaseClass.WRITE: 2},
    ],
)
async def test_an_outstanding_lease_blocks_reclamation(
    outstanding: dict[LeaseClass, int],
) -> None:
    graph = _Graph(
        [_generation("g1", eligible_since=datetime.now(UTC) - timedelta(days=30))],
        nodes={"g1": 100},
    )
    outcome = await _reclaimer(graph, lease_store=_LeaseStore({"g1": outstanding})).reclaim_once()

    assert graph.deleted_markers == []
    assert outcome.reclaimed == 0
    assert outcome.details["not_yet_reclaimable"] == 1


@pytest.mark.asyncio
async def test_a_newly_eligible_generation_starts_its_quarantine_rather_than_being_deleted() -> (
    None
):
    """The window runs from first observation, not from `created_at`.

    Nothing writes `retired_at` -- `compile_generation_transition` sets only
    `status` -- so a generation that served for a month has a month-old
    `created_at` and a creation-based window would delete it immediately.
    """
    graph = _Graph([_generation("g1", eligible_since=None)], nodes={"g1": 10})
    outcome = await _reclaimer(graph).reclaim_once()

    assert graph.stamped == ["g1"]
    assert graph.deleted_markers == []
    assert outcome.reclaimed == 0
    assert outcome.details["quarantine_started"] == 1


@pytest.mark.asyncio
async def test_a_generation_inside_its_quarantine_is_not_reclaimed() -> None:
    graph = _Graph(
        [_generation("g1", eligible_since=datetime.now(UTC) - timedelta(minutes=5))],
        nodes={"g1": 10},
    )
    outcome = await _reclaimer(graph, retention_seconds=3_600).reclaim_once()

    assert graph.deleted_markers == []
    assert outcome.details["not_yet_reclaimable"] == 1


@pytest.mark.asyncio
async def test_a_quarantined_drained_unreferenced_generation_is_reclaimed_nodes_then_marker() -> (
    None
):
    """The whole positive path, including the deletion order.

    The marker goes last: it is the only record that this generation was retired,
    so a pass that died after removing it would strand its nodes with nothing left
    to identify them as reclaimable.
    """
    graph = _Graph(
        [_generation("g1", eligible_since=datetime.now(UTC) - timedelta(days=2))],
        nodes={"g1": 2_500},
    )
    outcome = await _reclaimer(graph, node_delete_batch_size=1_000).reclaim_once()

    assert outcome.reclaimed == 1
    assert outcome.reclaimed_ids == ("g1",)
    # 1000 + 1000 + 500 -- the last short batch is what ends the loop.
    assert [size for _, size in graph.node_delete_calls] == [1_000, 1_000, 1_000]
    assert graph.deleted_markers == ["g1"]


@pytest.mark.asyncio
async def test_an_unreadable_snapshot_collection_abandons_the_pass() -> None:
    """Not knowing which generation is serving is the one unacceptable state.

    An empty `active_generation_ids()` would make every RETIRED generation pass
    rule (2), including one whose marker is stale -- so an unreachable Mongo must
    raise rather than answer "none are active".
    """
    graph = _Graph(
        [_generation("g1", eligible_since=datetime.now(UTC) - timedelta(days=30))],
        nodes={"g1": 10},
    )
    with pytest.raises(RuntimeError):
        await _reclaimer(graph, active=_UnreadableActiveGenerations()).reclaim_once()

    assert graph.deleted_markers == []


@pytest.mark.asyncio
async def test_the_batch_limit_bounds_one_pass() -> None:
    old = datetime.now(UTC) - timedelta(days=30)
    graph = _Graph(
        [_generation(f"g{index}", eligible_since=old) for index in range(10)],
        nodes={f"g{index}": 1 for index in range(10)},
    )
    outcome = await _reclaimer(graph, batch_limit=3).reclaim_once()

    assert outcome.reclaimed == 3
    assert len(graph.deleted_markers) == 3
