"""Retirement drains, and a failed build does not leak its candidate.

Both properties were previously documented but absent. `GenerationReadLease`
and `GenerationWriteReservation` each carried a docstring promising that
"cleanup of a RETIRED generation waits for every read lease ... to drain or
expire", while the orchestrator went ACTIVE -> RETIRED the instant its
compare-and-swap succeeded; and any exception mid-build left its candidate
parked in BUILDING forever.

These run against doubles rather than Neo4j because what is under test is the
orchestrator's *ordering* -- close the lease store before changing status, wait
before retiring, never roll back an ACTIVE generation -- not Cypher. The lease
store's own atomicity claim is proved against real Mongo in
`tests/dynamic_knowledge/test_generation_lease_store_real_infra.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from return_platform.dynamic_knowledge.graph.generation import (
    ActiveRuntimeSnapshot,
    GraphGenerationStatus,
)
from return_platform.dynamic_knowledge.lifecycle.lease_store import LeaseClass
from return_platform.dynamic_knowledge.lifecycle.orchestrator import (
    GenerationLifecycleOrchestrator,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


class _FakeGenerationWriter:
    """Records every transition in order, so a test can assert the *path* taken
    and not merely the destination -- ACTIVE -> RETIRED and
    ACTIVE -> DRAINING -> RETIRED both end RETIRED, and only one is correct."""

    def __init__(self) -> None:
        self.statuses: dict[str, tuple[GraphGenerationStatus, int]] = {}
        self.transitions: list[tuple[str, GraphGenerationStatus, GraphGenerationStatus]] = []

    async def create_generation(
        self,
        *,
        graph_generation_id: str,
        fencing_token: int,
        status: GraphGenerationStatus = GraphGenerationStatus.PREPARING,
    ) -> None:
        self.statuses[graph_generation_id] = (status, fencing_token)

    async def transition(
        self,
        *,
        graph_generation_id: str,
        fencing_token: int,
        expected_status: GraphGenerationStatus,
        new_status: GraphGenerationStatus,
    ) -> None:
        current = self.statuses.get(graph_generation_id)
        if current is None or current[0] is not expected_status:
            raise RuntimeError(
                f"expected {expected_status} for {graph_generation_id}, found {current}"
            )
        self.statuses[graph_generation_id] = (new_status, fencing_token)
        self.transitions.append((graph_generation_id, expected_status, new_status))

    async def get_status(
        self, *, graph_generation_id: str
    ) -> tuple[GraphGenerationStatus, int] | None:
        return self.statuses.get(graph_generation_id)


class _FakeLeaseStore:
    """In-memory GenerationLeaseStore with the same refusal semantics."""

    def __init__(self) -> None:
        self.draining: set[str] = set()
        self.leases: dict[str, list[tuple[str, LeaseClass, datetime]]] = {}
        self.begin_drain_calls: list[str] = []

    async def acquire_read_lease(
        self,
        *,
        graph_generation_id: str,
        snapshot_activation_version: int,
        owner_instance_id: str,
        ttl_seconds: int,
    ) -> object | None:
        if graph_generation_id in self.draining:
            return None
        lease_id = str(uuid.uuid4())
        self.leases.setdefault(graph_generation_id, []).append(
            (lease_id, LeaseClass.READ, datetime.now(UTC) + timedelta(seconds=ttl_seconds))
        )
        return lease_id

    async def acquire_write_reservation(
        self, **kwargs: object
    ) -> object | None:  # pragma: no cover
        raise NotImplementedError

    async def release(self, *, graph_generation_id: str, lease_id: str) -> None:
        entries = self.leases.get(graph_generation_id, [])
        self.leases[graph_generation_id] = [e for e in entries if e[0] != lease_id]

    async def begin_drain(self, *, graph_generation_id: str) -> None:
        self.begin_drain_calls.append(graph_generation_id)
        self.draining.add(graph_generation_id)

    async def outstanding(self, *, graph_generation_id: str) -> dict[LeaseClass, int]:
        now = datetime.now(UTC)
        counts = {LeaseClass.READ: 0, LeaseClass.WRITE: 0}
        for _, lease_class, expires_at in self.leases.get(graph_generation_id, []):
            if expires_at > now:
                counts[lease_class] += 1
        return counts


class _FakeSnapshotStore:
    def __init__(self, existing: ActiveRuntimeSnapshot | None = None) -> None:
        self.snapshot = existing
        self.swap_result = True

    async def read(self, *, snapshot_name: str) -> ActiveRuntimeSnapshot | None:
        return self.snapshot

    async def compare_and_swap(
        self,
        *,
        snapshot_name: str,
        expected_activation_version: int | None,
        new_snapshot: ActiveRuntimeSnapshot,
    ) -> bool:
        if self.swap_result:
            self.snapshot = new_snapshot
        return self.swap_result


class _FakeRebuildLeaseStore:
    def __init__(self) -> None:
        self.released = False

    async def acquire(self, **kwargs: object) -> object:
        return type("_Lease", (), {"lease_id": "rebuild-lease-1"})()

    async def release(self, *, snapshot_name: str, lease_id: str) -> None:
        self.released = True


class _ExplodingSyncCoordinator:
    def __init__(self, message: str = "source unreachable") -> None:
        self._message = message

    async def full_sync(self, **kwargs: object) -> tuple[int, int]:
        raise RuntimeError(self._message)


class _QuietSyncCoordinator:
    async def full_sync(self, **kwargs: object) -> tuple[int, int]:
        return (0, 0)


class _CountingTokens:
    """Strictly increasing, as `MongoFencingTokenAllocator` is."""

    def __init__(self) -> None:
        self._next = 1

    async def allocate(self, *, scope: str) -> int:
        del scope
        self._next += 1
        return self._next


def _previous_snapshot(graph_generation_id: str) -> ActiveRuntimeSnapshot:
    return ActiveRuntimeSnapshot(
        snapshot_name="default",
        configuration_release_id="release-1",
        schema_fingerprint="fingerprint-1",
        graph_generation_id=graph_generation_id,
        search_index_release_id="none",
        activation_id=str(uuid.uuid4()),
        activation_version=1,
        activated_at=NOW,
    )


def _orchestrator(
    writer: _FakeGenerationWriter,
    *,
    lease_store: _FakeLeaseStore | None = None,
    snapshot_store: _FakeSnapshotStore | None = None,
    sync_coordinator: object | None = None,
    drain_timeout_seconds: float = 5.0,
) -> GenerationLifecycleOrchestrator:
    return GenerationLifecycleOrchestrator(
        snapshot_store=snapshot_store or _FakeSnapshotStore(),  # type: ignore[arg-type]
        lease_store=_FakeRebuildLeaseStore(),  # type: ignore[arg-type]
        generation_writer=writer,  # type: ignore[arg-type]
        sync_coordinator=sync_coordinator or _QuietSyncCoordinator(),  # type: ignore[arg-type]
        fencing_tokens=_CountingTokens(),  # type: ignore[arg-type]
        owner_instance_id="test-instance",
        generation_lease_store=lease_store,  # type: ignore[arg-type]
        drain_timeout_seconds=drain_timeout_seconds,
        drain_poll_seconds=0.01,
    )


# --- retirement path --------------------------------------------------------


@pytest.mark.asyncio
async def test_retirement_passes_through_draining_rather_than_jumping_to_retired() -> None:
    writer = _FakeGenerationWriter()
    leases = _FakeLeaseStore()
    await writer.create_generation(
        graph_generation_id="gen-old", fencing_token=1, status=GraphGenerationStatus.ACTIVE
    )
    orchestrator = _orchestrator(writer, lease_store=leases)

    await orchestrator._retire("gen-old")

    assert writer.statuses["gen-old"][0] is GraphGenerationStatus.RETIRED
    assert [(f, t) for _, f, t in writer.transitions] == [
        (GraphGenerationStatus.ACTIVE, GraphGenerationStatus.DRAINING),
        (GraphGenerationStatus.DRAINING, GraphGenerationStatus.RETIRED),
    ]


@pytest.mark.asyncio
async def test_the_lease_store_is_closed_before_the_status_changes() -> None:
    """Ordering, not just presence. If the status flipped first, there would be
    a window where the generation reads DRAINING but the lease store would
    still hand out a lease nobody will ever wait for."""
    writer = _FakeGenerationWriter()
    leases = _FakeLeaseStore()
    await writer.create_generation(
        graph_generation_id="gen-old", fencing_token=1, status=GraphGenerationStatus.ACTIVE
    )

    observed: list[str] = []

    original_begin_drain = leases.begin_drain

    async def _recording_begin_drain(*, graph_generation_id: str) -> None:
        observed.append("begin_drain")
        await original_begin_drain(graph_generation_id=graph_generation_id)

    original_transition = writer.transition

    async def _recording_transition(**kwargs: object) -> None:
        observed.append(f"transition:{kwargs['new_status']}")
        await original_transition(**kwargs)  # type: ignore[arg-type]

    leases.begin_drain = _recording_begin_drain  # type: ignore[assignment]
    writer.transition = _recording_transition  # type: ignore[assignment]

    await _orchestrator(writer, lease_store=leases)._retire("gen-old")

    assert observed[0] == "begin_drain"
    assert observed[1] == f"transition:{GraphGenerationStatus.DRAINING}"


@pytest.mark.asyncio
async def test_an_outstanding_read_lease_blocks_retirement_until_released() -> None:
    writer = _FakeGenerationWriter()
    leases = _FakeLeaseStore()
    await writer.create_generation(
        graph_generation_id="gen-old", fencing_token=1, status=GraphGenerationStatus.ACTIVE
    )
    lease_id = await leases.acquire_read_lease(
        graph_generation_id="gen-old",
        snapshot_activation_version=1,
        owner_instance_id="reader-1",
        ttl_seconds=300,
    )
    assert isinstance(lease_id, str)

    # Short timeout: with the lease held, the drain must give up rather than
    # retire, and must not raise -- the cutover already succeeded.
    await _orchestrator(writer, lease_store=leases, drain_timeout_seconds=0.05)._retire("gen-old")
    assert writer.statuses["gen-old"][0] is GraphGenerationStatus.DRAINING

    await leases.release(graph_generation_id="gen-old", lease_id=lease_id)
    await _orchestrator(writer, lease_store=leases)._retire("gen-old")
    assert writer.statuses["gen-old"][0] is GraphGenerationStatus.RETIRED


@pytest.mark.asyncio
async def test_an_expired_lease_does_not_block_retirement() -> None:
    """A holder that crashed never releases. Counting by presence rather than
    by expiry would make one crashed request block cleanup permanently."""
    writer = _FakeGenerationWriter()
    leases = _FakeLeaseStore()
    await writer.create_generation(
        graph_generation_id="gen-old", fencing_token=1, status=GraphGenerationStatus.ACTIVE
    )
    leases.leases["gen-old"] = [
        ("dead-holder", LeaseClass.READ, datetime.now(UTC) - timedelta(seconds=1))
    ]

    await _orchestrator(writer, lease_store=leases, drain_timeout_seconds=0.05)._retire("gen-old")

    assert writer.statuses["gen-old"][0] is GraphGenerationStatus.RETIRED


@pytest.mark.asyncio
async def test_a_generation_already_draining_is_not_transitioned_twice() -> None:
    """Retirement must be resumable: a rebuild that died after DRAINING should
    be finishable, not blocked by an expected-status mismatch."""
    writer = _FakeGenerationWriter()
    await writer.create_generation(
        graph_generation_id="gen-old", fencing_token=1, status=GraphGenerationStatus.DRAINING
    )

    await _orchestrator(writer, lease_store=_FakeLeaseStore())._retire("gen-old")

    assert writer.statuses["gen-old"][0] is GraphGenerationStatus.RETIRED
    assert [(f, t) for _, f, t in writer.transitions] == [
        (GraphGenerationStatus.DRAINING, GraphGenerationStatus.RETIRED)
    ]


@pytest.mark.asyncio
async def test_retirement_without_a_lease_store_still_uses_draining() -> None:
    """The lease store is optional so existing construction sites keep working,
    but the state machine must not silently revert to the old shortcut."""
    writer = _FakeGenerationWriter()
    await writer.create_generation(
        graph_generation_id="gen-old", fencing_token=1, status=GraphGenerationStatus.ACTIVE
    )

    await _orchestrator(writer, lease_store=None)._retire("gen-old")

    assert [(f, t) for _, f, t in writer.transitions] == [
        (GraphGenerationStatus.ACTIVE, GraphGenerationStatus.DRAINING),
        (GraphGenerationStatus.DRAINING, GraphGenerationStatus.RETIRED),
    ]


# --- failure rollback -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_build_marks_its_candidate_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _FakeGenerationWriter()
    orchestrator = _orchestrator(writer, sync_coordinator=_ExplodingSyncCoordinator())

    with pytest.raises(RuntimeError):
        await orchestrator.build_and_activate(
            schema=_schema(),
            snapshot_name="default",
            configuration_release_id="release-1",
        )

    assert len(writer.statuses) == 1
    ((status, _),) = writer.statuses.values()
    assert status is GraphGenerationStatus.FAILED


@pytest.mark.asyncio
async def test_the_original_failure_survives_a_rollback_that_itself_fails() -> None:
    """The caller needs to know the build failed and why. A rollback error
    replacing it would hide the actual cause behind a cleanup detail."""
    writer = _FakeGenerationWriter()

    async def _broken_get_status(**kwargs: object) -> None:
        raise RuntimeError("neo4j unreachable")

    writer.get_status = _broken_get_status  # type: ignore[assignment]
    orchestrator = _orchestrator(
        writer, sync_coordinator=_ExplodingSyncCoordinator("source unreachable")
    )

    with pytest.raises(RuntimeError, match="source unreachable"):
        await orchestrator.build_and_activate(
            schema=_schema(),
            snapshot_name="default",
            configuration_release_id="release-1",
        )


@pytest.mark.asyncio
async def test_an_active_generation_is_never_rolled_back_to_failed() -> None:
    """Marking a generation FAILED after it is ACTIVE and referenced by the
    snapshot would flag live traffic's own generation as broken."""
    writer = _FakeGenerationWriter()
    await writer.create_generation(
        graph_generation_id="gen-live", fencing_token=1, status=GraphGenerationStatus.ACTIVE
    )

    await _orchestrator(writer)._mark_failed("gen-live")

    assert writer.statuses["gen-live"][0] is GraphGenerationStatus.ACTIVE
    assert writer.transitions == []


def _schema() -> object:
    """Minimal stand-in: the failure happens in full_sync, before anything
    reads more than `configuration_checksum` off the schema."""
    return type("_Schema", (), {"configuration_checksum": "fingerprint-1"})()
