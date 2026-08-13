from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from return_platform.dynamic_knowledge.graph.generation import (
    LEGACY_FENCING_TOKEN,
    ActiveRuntimeSnapshot,
    GraphGenerationStatus,
    RebuildLease,
)
from return_platform.dynamic_knowledge.lifecycle.orchestrator import (
    ActivationError,
    GenerationLifecycleOrchestrator,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


class FakeSnapshotStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, ActiveRuntimeSnapshot] = {}
        self.cas_calls: list[dict[str, Any]] = []
        self.force_cas_failure = False

    async def read(self, *, snapshot_name: str) -> ActiveRuntimeSnapshot | None:
        return self.snapshots.get(snapshot_name)

    async def compare_and_swap(
        self,
        *,
        snapshot_name: str,
        expected_activation_version: int | None,
        new_snapshot: ActiveRuntimeSnapshot,
    ) -> bool:
        self.cas_calls.append(
            {
                "snapshot_name": snapshot_name,
                "expected_activation_version": expected_activation_version,
                "new_snapshot": new_snapshot,
            }
        )
        if self.force_cas_failure:
            return False
        current = self.snapshots.get(snapshot_name)
        current_version = current.activation_version if current is not None else None
        if current_version != expected_activation_version:
            return False
        self.snapshots[snapshot_name] = new_snapshot
        return True


class FakeLeaseStore:
    def __init__(self) -> None:
        self.held: dict[str, RebuildLease] = {}
        self.release_calls: list[tuple[str, str]] = []

    async def acquire(
        self,
        *,
        snapshot_name: str,
        graph_generation_id: str,
        owner_instance_id: str,
        ttl_seconds: int,
    ) -> RebuildLease | None:
        if snapshot_name in self.held:
            return None
        lease = RebuildLease(
            lease_id=f"lease-{snapshot_name}",
            snapshot_name=snapshot_name,
            graph_generation_id=graph_generation_id,
            owner_instance_id=owner_instance_id,
            acquired_at=datetime.now(UTC),
            expires_at=datetime.now(UTC),
        )
        self.held[snapshot_name] = lease
        return lease

    async def release(self, *, snapshot_name: str, lease_id: str) -> None:
        self.release_calls.append((snapshot_name, lease_id))
        held = self.held.get(snapshot_name)
        if held is not None and held.lease_id == lease_id:
            del self.held[snapshot_name]


class FakeGenerationWriter:
    def __init__(self) -> None:
        self.statuses: dict[str, GraphGenerationStatus] = {}
        self.transitions: list[tuple[str, str, str]] = []

    async def create_generation(
        self, *, graph_generation_id: str, fencing_token: int, status: GraphGenerationStatus
    ) -> None:
        del fencing_token
        self.statuses[graph_generation_id] = status

    async def transition(
        self,
        *,
        graph_generation_id: str,
        fencing_token: int,
        expected_status: GraphGenerationStatus,
        new_status: GraphGenerationStatus,
    ) -> None:
        del fencing_token
        current = self.statuses.get(graph_generation_id)
        if current != expected_status:
            raise RuntimeError(
                f"generation {graph_generation_id!r} status {current!r} != expected {expected_status!r}"
            )
        self.statuses[graph_generation_id] = new_status
        self.transitions.append((graph_generation_id, expected_status.value, new_status.value))

    async def get_status(
        self, *, graph_generation_id: str
    ) -> tuple[GraphGenerationStatus, int] | None:
        status = self.statuses.get(graph_generation_id)
        if status is None:
            return None
        return status, 1


class FakeSyncCoordinator:
    def __init__(self, *, raise_on_status: GraphGenerationStatus | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raise_on_status = raise_on_status

    async def full_sync(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
        sync_run_id: str | None = None,
    ) -> tuple[int, int]:
        self.calls.append(
            {
                "graph_generation_id": graph_generation_id,
                "expected_generation_status": expected_generation_status,
                "sync_run_id": sync_run_id,
            }
        )
        if (
            self._raise_on_status is not None
            and expected_generation_status is self._raise_on_status
        ):
            raise RuntimeError("simulated sync failure")
        return 10, 5


class FakeTokens:
    """Stands in for `MongoFencingTokenAllocator`: strictly increasing, starting
    above `LEGACY_FENCING_TOKEN` exactly as the real one does."""

    def __init__(self) -> None:
        self.issued: list[int] = []
        self._next = LEGACY_FENCING_TOKEN

    async def allocate(self, *, scope: str, floor: int = 0) -> int:
        del scope
        self._next = max(self._next, floor) + 1
        self.issued.append(self._next)
        return self._next


def _orchestrator(
    *,
    snapshot_store: FakeSnapshotStore | None = None,
    lease_store: FakeLeaseStore | None = None,
    generation_writer: FakeGenerationWriter | None = None,
    sync_coordinator: FakeSyncCoordinator | None = None,
) -> tuple[
    GenerationLifecycleOrchestrator,
    FakeSnapshotStore,
    FakeLeaseStore,
    FakeGenerationWriter,
    FakeSyncCoordinator,
]:
    snapshot_store = snapshot_store or FakeSnapshotStore()
    lease_store = lease_store or FakeLeaseStore()
    generation_writer = generation_writer or FakeGenerationWriter()
    sync_coordinator = sync_coordinator or FakeSyncCoordinator()
    orchestrator = GenerationLifecycleOrchestrator(
        snapshot_store=snapshot_store,
        lease_store=lease_store,
        generation_writer=generation_writer,
        sync_coordinator=sync_coordinator,
        fencing_tokens=FakeTokens(),
        owner_instance_id="worker-1",
    )
    return orchestrator, snapshot_store, lease_store, generation_writer, sync_coordinator


@pytest.mark.asyncio
async def test_first_activation_ever_creates_version_one(active_schema: ActiveSchema) -> None:
    orchestrator, snapshot_store, lease_store, writer, _coordinator = _orchestrator()
    snapshot = await orchestrator.build_and_activate(
        schema=active_schema, snapshot_name="ORDER_DISCOVERY", configuration_release_id="release-1"
    )
    assert snapshot.activation_version == 1
    assert snapshot_store.snapshots["ORDER_DISCOVERY"] is snapshot
    assert lease_store.held == {}  # released
    assert writer.statuses[snapshot.graph_generation_id] == GraphGenerationStatus.ACTIVE


@pytest.mark.asyncio
async def test_transitions_happen_in_the_correct_order(active_schema: ActiveSchema) -> None:
    orchestrator, _, _, writer, _ = _orchestrator()
    snapshot = await orchestrator.build_and_activate(
        schema=active_schema, snapshot_name="ORDER_DISCOVERY", configuration_release_id="release-1"
    )
    generation_transitions = [t for t in writer.transitions if t[0] == snapshot.graph_generation_id]
    assert [(frm, to) for _, frm, to in generation_transitions] == [
        ("PREPARING", "BUILDING"),
        ("BUILDING", "CATCHING_UP"),
        ("CATCHING_UP", "VALIDATING"),
        ("VALIDATING", "READY_FOR_ACTIVATION"),
        ("READY_FOR_ACTIVATION", "ACTIVE"),
    ]


@pytest.mark.asyncio
async def test_full_sync_is_called_for_both_build_and_catchup_phases(
    active_schema: ActiveSchema,
) -> None:
    orchestrator, _, _, _, coordinator = _orchestrator()
    await orchestrator.build_and_activate(
        schema=active_schema, snapshot_name="ORDER_DISCOVERY", configuration_release_id="release-1"
    )
    statuses = [call["expected_generation_status"] for call in coordinator.calls]
    assert statuses == [GraphGenerationStatus.BUILDING, GraphGenerationStatus.CATCHING_UP]
    run_ids = [call["sync_run_id"] for call in coordinator.calls]
    assert run_ids[0] != run_ids[1]
    assert "build" in run_ids[0] and "catchup" in run_ids[1]


@pytest.mark.asyncio
async def test_second_activation_increments_version_and_retires_the_previous_generation(
    active_schema: ActiveSchema,
) -> None:
    orchestrator, snapshot_store, _, writer, _ = _orchestrator()
    first = await orchestrator.build_and_activate(
        schema=active_schema, snapshot_name="ORDER_DISCOVERY", configuration_release_id="release-1"
    )
    second = await orchestrator.build_and_activate(
        schema=active_schema, snapshot_name="ORDER_DISCOVERY", configuration_release_id="release-2"
    )
    assert second.activation_version == 2
    assert snapshot_store.snapshots["ORDER_DISCOVERY"] is second
    assert writer.statuses[first.graph_generation_id] == GraphGenerationStatus.RETIRED
    assert writer.statuses[second.graph_generation_id] == GraphGenerationStatus.ACTIVE


@pytest.mark.asyncio
async def test_raises_when_a_rebuild_is_already_in_progress(active_schema: ActiveSchema) -> None:
    lease_store = FakeLeaseStore()
    orchestrator, _, _, writer, coordinator = _orchestrator(lease_store=lease_store)
    # Simulate a concurrent rebuild already holding the lease.
    await lease_store.acquire(
        snapshot_name="ORDER_DISCOVERY",
        graph_generation_id="other-gen",
        owner_instance_id="other-worker",
        ttl_seconds=60,
    )
    with pytest.raises(ActivationError) as excinfo:
        await orchestrator.build_and_activate(
            schema=active_schema,
            snapshot_name="ORDER_DISCOVERY",
            configuration_release_id="release-1",
        )
    assert excinfo.value.stage == "ACQUIRE_REBUILD_LEASE"
    assert writer.statuses == {}  # no generation was ever created
    assert coordinator.calls == []


@pytest.mark.asyncio
async def test_cas_failure_marks_the_candidate_failed_and_releases_the_lease(
    active_schema: ActiveSchema,
) -> None:
    snapshot_store = FakeSnapshotStore()
    snapshot_store.force_cas_failure = True
    orchestrator, _, lease_store, writer, _ = _orchestrator(snapshot_store=snapshot_store)
    with pytest.raises(ActivationError) as excinfo:
        await orchestrator.build_and_activate(
            schema=active_schema,
            snapshot_name="ORDER_DISCOVERY",
            configuration_release_id="release-1",
        )
    assert excinfo.value.stage == "ACTIVATE_SNAPSHOT_CAS"
    (generation_id,) = writer.statuses.keys()
    assert writer.statuses[generation_id] == GraphGenerationStatus.FAILED
    assert lease_store.held == {}  # released even on failure


@pytest.mark.asyncio
async def test_sync_failure_during_build_propagates_and_still_releases_the_lease(
    active_schema: ActiveSchema,
) -> None:
    coordinator = FakeSyncCoordinator(raise_on_status=GraphGenerationStatus.BUILDING)
    orchestrator, _, lease_store, writer, _ = _orchestrator(sync_coordinator=coordinator)
    with pytest.raises(RuntimeError, match="simulated sync failure"):
        await orchestrator.build_and_activate(
            schema=active_schema,
            snapshot_name="ORDER_DISCOVERY",
            configuration_release_id="release-1",
        )
    assert lease_store.held == {}
    (generation_id,) = writer.statuses.keys()
    # Previously this asserted BUILDING -- "stuck, not cleaned up" -- recording a
    # known gap. The orchestrator now rolls a failed candidate back, so a dead
    # rebuild no longer leaves something that reads as an in-progress build.
    assert writer.statuses[generation_id] == GraphGenerationStatus.FAILED
