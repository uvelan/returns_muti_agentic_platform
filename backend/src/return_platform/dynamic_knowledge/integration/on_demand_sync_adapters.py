"""Production adapters wiring `on_demand_sync.OnDemandSyncCoordinator` to real
connectors and the real Neo4j writer (Phase 7 / Wave C2).

Before this, `OnDemandSyncCoordinator` was constructed nowhere in `src` --
only in a test file, against local fakes. `GenericGraphProjector` already
satisfies `DynamicGraphProjector`'s protocol exactly (`project(*, schema,
mutations) -> GraphMutationBatch`) and needs no adapter here; only the
connector-resolution and write sides needed new code.
"""

from __future__ import annotations

from uuid import uuid4

from return_platform.dynamic_knowledge.graph.generation_writer import Neo4jGenerationWriter
from return_platform.dynamic_knowledge.graph.neo4j_writer import Neo4jDynamicGraphWriter
from return_platform.dynamic_knowledge.on_demand_sync.contracts import GraphMutationBatch
from return_platform.dynamic_knowledge.schema import ActiveSchema, ConnectorType
from return_platform.source_connectors.mongodb import MongoDBSourceScanConnector
from return_platform.source_connectors.protocols import TargetedSourceConnector
from return_platform.source_connectors.sqlserver import SqlServerSourceScanConnector


class UnavailableOnDemandConnector(RuntimeError):
    """No on-demand-sync connector is configured for a source's connector_type."""


class OnDemandConnectorRegistry:
    """Dispatches by a source asset's configured connector_type to one
    dedicated, long-lived targeted-read connector instance per type.

    Deliberately separate instances from any full-sync pipeline's connectors
    (e.g. data_platform.graph.sync_service's) -- on-demand sync and full sync
    are started/stopped by different subsystems with different lifecycles,
    even though `targeted_read()` itself is stateless with respect to
    page_size/seed_pins/max_records_per_source, so sharing would have been
    safe too.
    """

    def __init__(
        self,
        *,
        schema: ActiveSchema,
        mongo: MongoDBSourceScanConnector | None,
        sqlserver: SqlServerSourceScanConnector | None,
    ) -> None:
        self._schema = schema
        self._mongo = mongo
        self._sqlserver = sqlserver

    def resolve(self, source_asset_id: str) -> TargetedSourceConnector:
        source = self._schema.sources[source_asset_id]
        if source.connector_type is ConnectorType.MONGODB:
            if self._mongo is None:
                raise UnavailableOnDemandConnector(
                    f"source {source_asset_id!r} is MONGODB but no on-demand Mongo "
                    "connector was configured"
                )
            return self._mongo
        if source.connector_type is ConnectorType.MSSQL:
            if self._sqlserver is None:
                raise UnavailableOnDemandConnector(
                    f"source {source_asset_id!r} is MSSQL but no on-demand SQL Server "
                    "connector was configured"
                )
            return self._sqlserver
        raise UnavailableOnDemandConnector(
            f"no on-demand-sync connector exists for connector_type "
            f"{source.connector_type.value!r} (source {source_asset_id!r})"
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
