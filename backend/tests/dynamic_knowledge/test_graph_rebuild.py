from __future__ import annotations

import pytest

from return_platform.dynamic_knowledge.graph.rebuild import (
    FencedLease,
    GraphProjectionState,
    GraphRebuildCoordinator,
    GraphStatus,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


class Store:
    def __init__(self) -> None:
        self.state = GraphProjectionState(status=GraphStatus.UNINITIALIZED)
        self.writes: list[GraphStatus] = []

    async def read_state(self) -> GraphProjectionState:
        return self.state

    async def write_state(
        self, state: GraphProjectionState, *, fencing_token: int | None = None
    ) -> None:
        if fencing_token is not None:
            assert fencing_token == 42
        self.state = state
        self.writes.append(state.status)

    async def acquire_rebuild_lease(self, *, owner_id: str, ttl_seconds: int) -> FencedLease:
        assert ttl_seconds > 0
        return FencedLease(lease_name="graph-rebuild", owner_id=owner_id, fencing_token=42)

    async def release_rebuild_lease(self, lease: FencedLease) -> None:
        assert lease.fencing_token == 42


class Admin:
    def __init__(self) -> None:
        self.dropped = False
        self.applied = False

    async def drop_business_graph(self, *, database: str, fencing_token: int) -> None:
        assert database == "business_knowledge"
        assert fencing_token == 42
        self.dropped = True

    async def apply_schema(self, *, schema: ActiveSchema, fencing_token: int) -> None:
        assert fencing_token == 42
        self.applied = True


class Sync:
    def __init__(self) -> None:
        self.called = False

    async def run_full_sync(self, *, schema: ActiveSchema, fencing_token: int) -> None:
        assert fencing_token == 42
        self.called = True


class Validator:
    async def validate(self, *, schema: ActiveSchema, fencing_token: int) -> None:
        assert fencing_token == 42


@pytest.mark.asyncio
async def test_schema_change_drops_and_fully_rebuilds_business_graph(
    active_schema: ActiveSchema,
) -> None:
    store, admin, sync = Store(), Admin(), Sync()
    coordinator = GraphRebuildCoordinator(
        store=store, graph_admin=admin, full_sync=sync, validator=Validator()
    )
    state = await coordinator.ensure_active(active_schema)
    assert state.status is GraphStatus.ACTIVE
    assert admin.dropped is True
    assert admin.applied is True
    assert sync.called is True
    assert store.writes == [
        GraphStatus.SCHEMA_CHANGE_DETECTED,
        GraphStatus.REBUILDING,
        GraphStatus.VALIDATING,
        GraphStatus.ACTIVE,
    ]


@pytest.mark.asyncio
async def test_same_fingerprint_does_not_rebuild(active_schema: ActiveSchema) -> None:
    store, admin, sync = Store(), Admin(), Sync()
    coordinator = GraphRebuildCoordinator(
        store=store, graph_admin=admin, full_sync=sync, validator=Validator()
    )
    first = await coordinator.ensure_active(active_schema)
    store.writes.clear()
    second = await coordinator.ensure_active(active_schema)
    assert second == first
    assert store.writes == []
