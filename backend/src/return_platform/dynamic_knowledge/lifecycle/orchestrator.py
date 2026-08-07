"""Orchestrates one full blue/green rebuild-and-activate cycle: acquire a
rebuild lease, build a new generation via full_sync (BUILDING), catch up on
what changed since the build started (CATCHING_UP), validate, activate
(Neo4j ACTIVE + Mongo ActiveRuntimeSnapshot compare-and-swap), then retire
the previous generation. See the source-to-graph alignment plan's
"Activation protocol" for the full state machine this implements.

Known simplification versus the full plan: nothing in this codebase yet
acquires a GenerationReadLease/GenerationWriteReservation when resolving
ActiveRuntimeSnapshot (no request-resolution path does that today), so the
old generation's retirement here has no real drain to wait for -- it
transitions ACTIVE -> RETIRED immediately after the CAS succeeds. That is
not safe once something actually starts pinning requests to a generation;
real lease-draining must be added before this runs against live traffic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol

from return_platform.dynamic_knowledge.graph.generation import (
    ActiveRuntimeSnapshot,
    GraphGenerationStatus,
)
from return_platform.dynamic_knowledge.graph.generation_writer import Neo4jGenerationWriter
from return_platform.dynamic_knowledge.lifecycle.mongo_store import (
    ActiveRuntimeSnapshotStore,
    RebuildLeaseStore,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


class ActivationError(RuntimeError):
    """The activation protocol could not complete; .stage says where it failed."""

    def __init__(self, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.stage = stage


class RebuildSyncCoordinator(Protocol):
    """The subset of GenericSyncCoordinator this orchestrator drives."""

    async def full_sync(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
        sync_run_id: str | None = None,
    ) -> tuple[int, int]: ...


class GenerationLifecycleOrchestrator:
    def __init__(
        self,
        *,
        snapshot_store: ActiveRuntimeSnapshotStore,
        lease_store: RebuildLeaseStore,
        generation_writer: Neo4jGenerationWriter,
        sync_coordinator: RebuildSyncCoordinator,
        owner_instance_id: str,
        lease_ttl_seconds: int = 3600,
    ) -> None:
        self._snapshot_store = snapshot_store
        self._lease_store = lease_store
        self._generation_writer = generation_writer
        self._sync_coordinator = sync_coordinator
        self._owner_instance_id = owner_instance_id
        self._lease_ttl_seconds = lease_ttl_seconds

    async def build_and_activate(
        self,
        *,
        schema: ActiveSchema,
        snapshot_name: str,
        configuration_release_id: str,
        search_index_release_id: str = "none",
    ) -> ActiveRuntimeSnapshot:
        graph_generation_id = str(uuid.uuid4())
        fencing_token = 1

        lease = await self._lease_store.acquire(
            snapshot_name=snapshot_name,
            graph_generation_id=graph_generation_id,
            owner_instance_id=self._owner_instance_id,
            ttl_seconds=self._lease_ttl_seconds,
        )
        if lease is None:
            raise ActivationError(
                f"a rebuild is already in progress for snapshot {snapshot_name!r}",
                stage="ACQUIRE_REBUILD_LEASE",
            )

        try:
            await self._generation_writer.create_generation(
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                status=GraphGenerationStatus.PREPARING,
            )
            await self._transition(
                graph_generation_id,
                fencing_token,
                GraphGenerationStatus.PREPARING,
                GraphGenerationStatus.BUILDING,
                stage="BUILD",
            )
            await self._sync_coordinator.full_sync(
                schema=schema,
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                expected_generation_status=GraphGenerationStatus.BUILDING,
                sync_run_id=f"rebuild-{graph_generation_id}-build",
            )

            await self._transition(
                graph_generation_id,
                fencing_token,
                GraphGenerationStatus.BUILDING,
                GraphGenerationStatus.CATCHING_UP,
                stage="CATCH_UP",
            )
            # One catch-up replay pass, capturing whatever changed at the
            # sources since the BUILDING scan's watermarks were captured.
            # Re-running full_sync is safe: every write is MERGE-based.
            await self._sync_coordinator.full_sync(
                schema=schema,
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                expected_generation_status=GraphGenerationStatus.CATCHING_UP,
                sync_run_id=f"rebuild-{graph_generation_id}-catchup",
            )

            await self._transition(
                graph_generation_id,
                fencing_token,
                GraphGenerationStatus.CATCHING_UP,
                GraphGenerationStatus.VALIDATING,
                stage="VALIDATE",
            )
            # Deep validation (Atlas Search release checks, count/consistency
            # checks) is out of scope for this pass -- validation here is
            # just the state transition itself succeeding.
            await self._transition(
                graph_generation_id,
                fencing_token,
                GraphGenerationStatus.VALIDATING,
                GraphGenerationStatus.READY_FOR_ACTIVATION,
                stage="VALIDATE",
            )
            await self._transition(
                graph_generation_id,
                fencing_token,
                GraphGenerationStatus.READY_FOR_ACTIVATION,
                GraphGenerationStatus.ACTIVE,
                stage="ACTIVATE_GRAPH",
            )

            previous = await self._snapshot_store.read(snapshot_name=snapshot_name)
            new_snapshot = ActiveRuntimeSnapshot(
                snapshot_name=snapshot_name,
                configuration_release_id=configuration_release_id,
                schema_fingerprint=schema.configuration_checksum,
                graph_generation_id=graph_generation_id,
                search_index_release_id=search_index_release_id,
                activation_id=str(uuid.uuid4()),
                activation_version=(previous.activation_version + 1) if previous is not None else 1,
                activated_at=datetime.now(UTC),
            )
            swapped = await self._snapshot_store.compare_and_swap(
                snapshot_name=snapshot_name,
                expected_activation_version=previous.activation_version if previous is not None else None,
                new_snapshot=new_snapshot,
            )
            if not swapped:
                # Per the plan: on CAS failure the candidate reverts (here:
                # FAILED, since a fresh rebuild always starts a fresh
                # generation rather than resuming this one) and the old
                # snapshot remains authoritative -- never left ACTIVE and
                # unreferenced.
                await self._transition(
                    graph_generation_id,
                    fencing_token,
                    GraphGenerationStatus.ACTIVE,
                    GraphGenerationStatus.FAILED,
                    stage="ACTIVATE_SNAPSHOT_CAS",
                )
                raise ActivationError(
                    f"ActiveRuntimeSnapshot for {snapshot_name!r} changed concurrently during "
                    "activation; this candidate generation has been marked FAILED",
                    stage="ACTIVATE_SNAPSHOT_CAS",
                )

            if previous is not None:
                await self._retire(previous.graph_generation_id)
            return new_snapshot
        finally:
            await self._lease_store.release(snapshot_name=snapshot_name, lease_id=lease.lease_id)

    async def _transition(
        self,
        graph_generation_id: str,
        fencing_token: int,
        expected: GraphGenerationStatus,
        new: GraphGenerationStatus,
        *,
        stage: str,
    ) -> None:
        try:
            await self._generation_writer.transition(
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                expected_status=expected,
                new_status=new,
            )
        except Exception as error:
            raise ActivationError(str(error), stage=stage) from error

    async def _retire(self, graph_generation_id: str) -> None:
        status = await self._generation_writer.get_status(graph_generation_id=graph_generation_id)
        if status is None or status[0] is GraphGenerationStatus.RETIRED:
            return
        current_status, fencing_token = status
        await self._transition(
            graph_generation_id, fencing_token, current_status, GraphGenerationStatus.RETIRED, stage="RETIRE_PREVIOUS"
        )
