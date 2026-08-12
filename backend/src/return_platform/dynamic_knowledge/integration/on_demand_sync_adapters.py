"""Production adapters wiring `on_demand_sync.OnDemandSyncCoordinator` to real
connectors and the real Neo4j writer (Phase 7 / Wave C2).

Before this, `OnDemandSyncCoordinator` was constructed nowhere in `src` --
only in a test file, against local fakes. `GenericGraphProjector` already
satisfies `DynamicGraphProjector`'s protocol exactly (`project(*, schema,
mutations) -> GraphMutationBatch`) and needs no adapter here; only the
connector-resolution and write sides needed new code.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from return_platform.dynamic_knowledge.graph.generation_writer import Neo4jGenerationWriter
from return_platform.dynamic_knowledge.graph.neo4j_writer import Neo4jDynamicGraphWriter
from return_platform.dynamic_knowledge.on_demand_sync.contracts import GraphMutationBatch
from return_platform.dynamic_knowledge.schema import ActiveSchema, ConnectorType
from return_platform.source_connectors.mongodb import MongoDBSourceScanConnector
from return_platform.source_connectors.protocols import TargetedSourceConnector
from return_platform.source_connectors.registry import SourceConnectorsByType
from return_platform.source_connectors.sqlserver import SqlServerSourceScanConnector


def targeted_connector_registry(
    *,
    schema: ActiveSchema,
    mongo: MongoDBSourceScanConnector | None,
    sqlserver: SqlServerSourceScanConnector | None,
    overrides: Mapping[str, TargetedSourceConnector] | None = None,
) -> SourceConnectorsByType[TargetedSourceConnector]:
    """The targeted-read half of the one connector-type dispatch.

    This was `OnDemandConnectorRegistry`, a hand-written MONGODB/MSSQL branch
    duplicating the one in `sync.adapters` -- so a source type reachable by a
    scheduled sync could still be unreachable on demand, and nothing said so
    until an agent turn hit it. Both now build `SourceConnectorsByType`.

    `overrides` mirrors `scan_connector_registry`'s and exists for the same
    reason: a connector type does not identify a *store*, and the platform's own
    operational collections are MongoDB sources in a different database from the
    upstream Ferguson ones. Without it a targeted read of a return record
    resolves to the upstream connector, finds no such collection, and reports
    SUCCEEDED having written nothing. The scan half gained this first; a
    targeted read that could not reach what a scheduled sync can is the same
    asymmetry this function was written to remove.

    The connector *instances* stay separate from any full-sync pipeline's:
    on-demand sync and full sync are started and stopped by different
    subsystems, even though `targeted_read()` is stateless with respect to
    page_size/seed_pins/max_records_per_source, so sharing would have been safe.
    """
    return SourceConnectorsByType(
        sources=schema.sources,
        connectors={ConnectorType.MONGODB: mongo, ConnectorType.MSSQL: sqlserver},
        overrides=overrides,
    )


class NoGenerationMarker(RuntimeError):
    """No Neo4j GraphGeneration marker exists for a graph_generation_id an
    on-demand write was asked to target -- there is nothing to fence against."""


class OnDemandNeo4jGraphWriter:
    """Adapter satisfying `on_demand_sync.coordinator.DynamicGraphWriter`,
    bridging to the real `Neo4jDynamicGraphWriter` (which needs a
    fencing_token/expected_generation_status the on-demand caller doesn't
    carry through its own state machine the way a full-sync run does -- so
    this adapter looks the current marker status up fresh on every write)."""

    def __init__(
        self, writer: Neo4jDynamicGraphWriter, generation_writer: Neo4jGenerationWriter
    ) -> None:
        self._writer = writer
        self._generation_writer = generation_writer

    async def write(
        self, *, schema: ActiveSchema, graph_generation_id: str, batch: GraphMutationBatch
    ) -> tuple[int, int]:
        status = await self._generation_writer.get_status(graph_generation_id=graph_generation_id)
        if status is None:
            raise NoGenerationMarker(
                f"no GraphGeneration marker exists for {graph_generation_id!r}; "
                "cannot fence an on-demand write against it"
            )
        expected_status, fencing_token = status
        receipt = await self._writer.write(
            schema=schema,
            graph_generation_id=graph_generation_id,
            fencing_token=fencing_token,
            expected_generation_status=expected_status,
            sync_run_id=f"ondemand-{graph_generation_id}",
            chunk_id=str(uuid4()),
            batch=batch,
        )
        return receipt.nodes_written, receipt.relationships_written
