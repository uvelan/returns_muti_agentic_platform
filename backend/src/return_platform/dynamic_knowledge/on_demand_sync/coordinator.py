"""Idempotent targeted synchronization through the standard graph projection path."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, nullcontext
from typing import Protocol
from uuid import uuid4

from return_platform.dynamic_knowledge.lifecycle.handle import GenerationHandleProvider
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    DynamicRecordMutation,
    GraphMutationBatch,
    ProjectionReadScope,
    SyncReceipt,
    SyncReservation,
    SyncStatus,
)
from return_platform.dynamic_knowledge.on_demand_sync.extraction import SourceRecordExtractor
from return_platform.dynamic_knowledge.schema import ActiveSchema, RuntimeMode
from return_platform.source_connectors.contracts import LogicalTargetedReadPlan
from return_platform.source_connectors.protocols import ConnectorRegistry, TargetedSourceConnector

__all__ = [
    "ConnectorRegistry",
    "DynamicGraphProjector",
    "DynamicGraphWriter",
    "OnDemandSyncCoordinator",
    "OnDemandSyncStore",
    "TargetedSourceConnector",
]


class DynamicGraphProjector(Protocol):
    async def project(
        self,
        *,
        schema: ActiveSchema,
        mutations: tuple[DynamicRecordMutation, ...],
    ) -> GraphMutationBatch: ...


class DynamicGraphWriter(Protocol):
    async def write(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        batch: GraphMutationBatch,
    ) -> tuple[int, int]: ...


class OnDemandSyncStore(Protocol):
    async def reserve(
        self,
        *,
        request_digest: str,
        proposed_request_id: str,
        schema_version: str,
        graph_generation_id: str,
    ) -> SyncReservation: ...

    async def complete(self, receipt: SyncReceipt) -> None: ...


class OnDemandSyncCoordinator:
    """Execute one targeted source read per canonical request digest."""

    def __init__(
        self,
        *,
        connectors: ConnectorRegistry,
        extractor: SourceRecordExtractor,
        projector: DynamicGraphProjector,
        writer: DynamicGraphWriter,
        store: OnDemandSyncStore,
        generation_handles: GenerationHandleProvider | None = None,
    ) -> None:
        self._connectors = connectors
        self._extractor = extractor
        self._projector = projector
        self._writer = writer
        self._store = store
        # Optional so existing construction sites keep working unreserved, which
        # is the behaviour that shipped before write reservations existed.
        self._generation_handles = generation_handles

    async def synchronize(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        request_digest: str,
        plan: LogicalTargetedReadPlan,
    ) -> SyncReceipt:
        if schema.runtime_mode is not RuntimeMode.CONNECTED_SYNC:
            raise RuntimeError("ON_DEMAND_SYNC_SOURCE_UNAVAILABLE")
        request_id = str(uuid4())
        reservation = await self._store.reserve(
            request_digest=request_digest,
            proposed_request_id=request_id,
            schema_version=schema.schema_version,
            graph_generation_id=graph_generation_id,
        )
        if not reservation.acquired:
            if reservation.existing_receipt is None:
                raise RuntimeError("ON_DEMAND_SYNC_ALREADY_RUNNING")
            return reservation.existing_receipt
        running = SyncReceipt(
            sync_request_id=reservation.sync_request_id,
            request_digest=request_digest,
            status=SyncStatus.RUNNING,
            schema_version=schema.schema_version,
            graph_generation_id=graph_generation_id,
        )
        await self._store.complete(running)
        try:
            # Reserved before the source read, not just around the graph write.
            # Reserving only around the write leaves a window: retirement could
            # begin draining while the source read is in flight, count no
            # outstanding work, retire the generation, and let this write land
            # in something already gone. Holding across the read only makes a
            # drain wait slightly longer, which is the cheap side of the trade.
            async with self._write_reservation(graph_generation_id):
                return await self._execute(
                    schema=schema,
                    graph_generation_id=graph_generation_id,
                    request_digest=request_digest,
                    plan=plan,
                    sync_request_id=reservation.sync_request_id,
                )
        except Exception as exc:
            failed = SyncReceipt(
                sync_request_id=reservation.sync_request_id,
                request_digest=request_digest,
                status=SyncStatus.FAILED,
                schema_version=schema.schema_version,
                graph_generation_id=graph_generation_id,
                error_code=type(exc).__name__,
            )
            await self._store.complete(failed)
            raise

    def _write_reservation(self, graph_generation_id: str) -> AbstractAsyncContextManager[object]:
        if self._generation_handles is None:
            return nullcontext(None)
        return self._generation_handles.reserve_write(graph_generation_id)

    async def _execute(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        request_digest: str,
        plan: LogicalTargetedReadPlan,
        sync_request_id: str,
    ) -> SyncReceipt:
        """The sync itself, with the write reservation already held.

        Split out so the reservation's scope is a single visible `async with`
        in `synchronize` rather than another level of nesting inside the
        existing failure-receipt handler."""
        connector = self._connectors.resolve(plan.source_asset_id)
        page = await connector.targeted_read(schema=schema, plan=plan)
        # An on-demand anchor read only ever touches some of an entity's exploded
        # children (e.g. one customer's accounts, not every account in the source),
        # so REPLACE_CHILD_SET reconciliation must never run against it -- only a
        # COMPLETE_SOURCE_DOCUMENT full/incremental scan is allowed to conclude an
        # unseen child was deleted.
        mutations = self._extractor.extract(
            schema=schema,
            source_asset_id=plan.source_asset_id,
            page=page,
            read_scope=ProjectionReadScope.PARTIAL_TARGETED_READ,
        )
        if len(mutations) > plan.maximum_rows + plan.maximum_dependency_records:
            raise RuntimeError("ON_DEMAND_SYNC_RESULT_LIMIT_EXCEEDED")
        batch = await self._projector.project(schema=schema, mutations=mutations)
        nodes_written, relationships_written = await self._writer.write(
            schema=schema,
            graph_generation_id=graph_generation_id,
            batch=batch,
        )
        succeeded = SyncReceipt(
            sync_request_id=sync_request_id,
            request_digest=request_digest,
            status=SyncStatus.SUCCEEDED,
            schema_version=schema.schema_version,
            graph_generation_id=graph_generation_id,
            source_rows_read=len(mutations),
            nodes_written=nodes_written,
            relationships_written=relationships_written,
        )
        await self._store.complete(succeeded)
        return succeeded
