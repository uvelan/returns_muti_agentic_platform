"""Small, generic adapters that let GenericSyncCoordinator drive the real
GenericGraphProjector + Neo4jDynamicGraphWriter pipeline, and dispatch to the
right connector per source without any business-specific branching.
"""

from __future__ import annotations

from typing import Protocol

from return_platform.dynamic_knowledge.graph.generation import GraphGenerationStatus
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    DynamicRecordMutation,
    DynamicSourceRecord,
    ProjectionReadScope,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema, ConnectorType


class GraphProjector(Protocol):
    async def project(self, *, schema: ActiveSchema, mutations: tuple[DynamicRecordMutation, ...]) -> object: ...


class GraphWriter(Protocol):
    async def write(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
        sync_run_id: str,
        chunk_id: str,
        batch: object,
    ) -> object: ...


class ProjectorGraphWriter:
    """Bridges GenericSyncCoordinator's ProjectionWriter protocol (records in,
    (nodes, relationships) counts out) onto the real projector+writer pipeline.

    ProjectionWriter.project_and_write receives only DynamicSourceRecord (the
    coordinator already stripped source_identity/resolved_key/read_scope when
    building it from each page's UPSERT mutations), so this reconstructs a
    minimal DynamicRecordMutation -- source_identity is synthesized from the
    natural key since the projector never reads it. One adapter instance is
    scoped to one sync run (sync_run_id is bound at construction), but
    expected_generation_status is a per-call parameter, not bound -- a single
    orchestrator-driven rebuild reuses one coordinator/writer instance across
    multiple full_sync calls expecting *different* statuses in sequence
    (BUILDING, then CATCHING_UP), so binding it once at construction would
    silently keep expecting the first call's status forever.
    """

    def __init__(
        self,
        *,
        projector: GraphProjector,
        writer: GraphWriter,
        sync_run_id: str,
    ) -> None:
        self._projector = projector
        self._writer = writer
        self._sync_run_id = sync_run_id
        self._chunk_sequence = 0

    async def project_and_write(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        records: tuple[DynamicSourceRecord, ...],
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
    ) -> tuple[int, int]:
        mutations = tuple(
            DynamicRecordMutation(
                operation="UPSERT",
                record=record,
                entity_id=record.entity_id,
                projection_id=schema.entity_node(record.entity_id).projection_id,
                source_asset_id=record.source_asset_id,
                source_identity=str(sorted(record.natural_key.items())),
                resolved_key=record.natural_key,
                read_scope=ProjectionReadScope.COMPLETE_SOURCE_DOCUMENT,
            )
            for record in records
        )
        batch = await self._projector.project(schema=schema, mutations=mutations)
        self._chunk_sequence += 1
        receipt = await self._writer.write(
            schema=schema,
            graph_generation_id=graph_generation_id,
            fencing_token=fencing_token,
            expected_generation_status=expected_generation_status,
            sync_run_id=self._sync_run_id,
            chunk_id=f"chunk-{self._chunk_sequence}",
            batch=batch,
        )
        return receipt.nodes_written, receipt.relationships_written  # type: ignore[attr-defined]


class SourceConnector(Protocol):
    async def capture_high_watermark(self, *, source_asset_id: str) -> object: ...


class SourceConnectorRegistry:
    """Dispatches to the one connector instance registered for each connector
    type -- both connectors already serve every source of their own type, so
    there is exactly one Mongo connector and one SQL Server connector per
    sync run, never one per source."""

    def __init__(
        self,
        *,
        schema: ActiveSchema,
        mongo_connector: SourceConnector | None = None,
        sqlserver_connector: SourceConnector | None = None,
    ) -> None:
        self._schema = schema
        self._connectors_by_type = {
            ConnectorType.MONGODB: mongo_connector,
            ConnectorType.MSSQL: sqlserver_connector,
        }

    def resolve(self, source_asset_id: str) -> SourceConnector:
        connector_type = self._schema.sources[source_asset_id].connector_type
        connector = self._connectors_by_type.get(connector_type)
        if connector is None:
            raise ValueError(f"no connector registered for connector_type {connector_type!r}")
        return connector
