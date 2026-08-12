"""Small, generic adapters that let GenericSyncCoordinator drive the real
GenericGraphProjector + Neo4jDynamicGraphWriter pipeline, and dispatch to the
right connector per source without any business-specific branching.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from return_platform.dynamic_knowledge.graph.generation import (
    GraphGenerationStatus,
    GraphWriteReceipt,
)
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    DynamicRecordMutation,
    DynamicSourceRecord,
    GraphMutationBatch,
    ProjectionReadScope,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema, ConnectorType
from return_platform.source_connectors.protocols import SourceScanConnector
from return_platform.source_connectors.registry import SourceConnectorsByType


class GraphProjector(Protocol):
    async def project(
        self, *, schema: ActiveSchema, mutations: tuple[DynamicRecordMutation, ...]
    ) -> GraphMutationBatch: ...


class GraphWriter(Protocol):
    """Named types, not `object`.

    `batch: object` reads as permissive but is the opposite: a protocol
    parameter is contravariant, so declaring `object` demands an implementation
    accepting *anything*, which `Neo4jDynamicGraphWriter.write(batch:
    GraphMutationBatch)` does not -- it was unsatisfiable, and the `object`
    return then forced a `type: ignore` on every field read of the receipt.
    """

    async def write(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
        sync_run_id: str,
        chunk_id: str,
        batch: GraphMutationBatch,
    ) -> GraphWriteReceipt: ...


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
        return receipt.nodes_written, receipt.relationships_written


def scan_connector_registry(
    *,
    schema: ActiveSchema,
    mongo_connector: SourceScanConnector | None = None,
    sqlserver_connector: SourceScanConnector | None = None,
    overrides: Mapping[str, SourceScanConnector] | None = None,
) -> SourceConnectorsByType[SourceScanConnector]:
    """The scan half of the one connector-type dispatch.

    `overrides` answers per source rather than per type, because a connector
    type does not identify a *store*: two MongoDB sources can live in different
    databases -- the upstream Ferguson collections and the platform's own
    operational store -- and a connector is bound to one at construction.
    Without it the platform-store sources resolve to the upstream connector and
    scan collections that are not there, which reads as an empty projection
    rather than as a misconfiguration.
    """
    return SourceConnectorsByType(
        sources=schema.sources,
        connectors={
            ConnectorType.MONGODB: mongo_connector,
            ConnectorType.MSSQL: sqlserver_connector,
        },
        overrides=overrides,
    )
