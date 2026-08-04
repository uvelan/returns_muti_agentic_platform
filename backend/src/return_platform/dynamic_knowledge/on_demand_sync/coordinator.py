"""Idempotent targeted synchronization through the standard graph projection path."""

from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    DynamicSourceRecord,
    GraphWriteBatch,
    LogicalTargetedReadPlan,
    SyncReceipt,
    SyncReservation,
    SyncStatus,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema, RuntimeMode


class TargetedSourceConnector(Protocol):
    async def targeted_read(
        self,
        *,
        schema: ActiveSchema,
        plan: LogicalTargetedReadPlan,
    ) -> tuple[DynamicSourceRecord, ...]: ...


class ConnectorRegistry(Protocol):
    def resolve(self, source_asset_id: str) -> TargetedSourceConnector: ...


class DynamicGraphProjector(Protocol):
    async def project(
        self,
        *,
        schema: ActiveSchema,
        records: tuple[DynamicSourceRecord, ...],
    ) -> GraphWriteBatch: ...


class DynamicGraphWriter(Protocol):
    async def write(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        batch: GraphWriteBatch,
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
        projector: DynamicGraphProjector,
        writer: DynamicGraphWriter,
        store: OnDemandSyncStore,
    ) -> None:
        self._connectors = connectors
        self._projector = projector
        self._writer = writer
        self._store = store

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
            connector = self._connectors.resolve(plan.source_asset_id)
            records = await connector.targeted_read(schema=schema, plan=plan)
            if len(records) > plan.maximum_rows + plan.maximum_dependency_records:
                raise RuntimeError("ON_DEMAND_SYNC_RESULT_LIMIT_EXCEEDED")
            batch = await self._projector.project(schema=schema, records=records)
            nodes_written, relationships_written = await self._writer.write(
                schema=schema,
                graph_generation_id=graph_generation_id,
                batch=batch,
            )
            succeeded = SyncReceipt(
                sync_request_id=reservation.sync_request_id,
                request_digest=request_digest,
                status=SyncStatus.SUCCEEDED,
                schema_version=schema.schema_version,
                graph_generation_id=graph_generation_id,
                source_rows_read=len(records),
                nodes_written=nodes_written,
                relationships_written=relationships_written,
            )
            await self._store.complete(succeeded)
            return succeeded
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
