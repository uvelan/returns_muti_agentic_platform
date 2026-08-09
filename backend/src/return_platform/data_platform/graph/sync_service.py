"""Graph projection synchronization from governed MongoDB and SQL Server sources.

Orchestration adapter over the generic dynamic_knowledge pipeline (extractor
-> projector -> Neo4jDynamicGraphWriter), driven by the interim ActiveSchema
in interim_active_schema.py. This module owns run bookkeeping (the
graph_sync_runs collection), connection wiring, and per-source document
counting for the run view -- it does not itself extract fields, decide graph
labels, or write Cypher; see the source-to-graph alignment plan's Step 8.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from neo4j import AsyncDriver
from pydantic import BaseModel, ConfigDict, Field
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.data_platform.graph.interim_active_schema import build_interim_active_schema
from return_platform.data_platform.schema_registry import SchemaRegistry
from return_platform.dynamic_knowledge.graph.constraints import required_node_constraints
from return_platform.dynamic_knowledge.graph.generation import (
    LEGACY_GENERATION_ID,
    GraphGenerationStatus,
)
from return_platform.dynamic_knowledge.graph.neo4j_writer import Neo4jDynamicGraphWriter
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.graph.write_compiler import compile_node_writes
from return_platform.dynamic_knowledge.on_demand_sync.contracts import GraphNodeMutation
from return_platform.dynamic_knowledge.on_demand_sync.extraction import GenericSourceRecordExtractor
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    ConnectorType,
    validate_graph_identifier,
)
from return_platform.dynamic_knowledge.sync.adapters import (
    ProjectorGraphWriter,
    SourceConnectorRegistry,
)
from return_platform.dynamic_knowledge.sync.coordinator import GenericSyncCoordinator
from return_platform.source_connectors.contracts import SourceCursor
from return_platform.source_connectors.mongodb import MongoDBSourceScanConnector, SeedPin
from return_platform.source_connectors.sqlserver import (
    SqlServerConnectionSettings,
    SqlServerSourceScanConnector,
)

_LEGACY_GENERATION_ID = LEGACY_GENERATION_ID
_LEGACY_FENCING_TOKEN = 1
_CONTACT_KEY_REFERENCE = "vault://return-platform/contact-lookup#hmac_key"


class GraphSyncScope(StrEnum):
    FULL = "FULL"
    SOURCE_MONGODB = "SOURCE_MONGODB"
    SQLSERVER = "SQLSERVER"


class GraphSyncModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GraphSyncRequest(GraphSyncModel):
    mode: GraphSyncScope = GraphSyncScope.FULL
    maxRecordsPerAsset: int = Field(default=1_000, ge=1, le=100_000)
    applySchema: bool = True


class GraphSyncRunView(GraphSyncModel):
    id: str
    mode: str
    status: Literal["RUNNING", "COMPLETED", "FAILED"]
    schemaVersion: str
    sourceCounts: dict[str, int]
    nodeWrites: int = Field(ge=0)
    relationshipWrites: int = Field(ge=0)
    constraintsApplied: list[str]
    configurationDigest: str
    errorCode: str | None = None
    startedBy: str
    startedAt: datetime
    completedAt: datetime | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


class _UnusedCheckpointStore:
    """full_sync never reads or writes checkpoints; only incremental_sync does."""

    async def read(self, *, source_asset_id: str, graph_generation_id: str) -> SourceCursor | None:
        raise NotImplementedError("GraphSyncService only ever runs full_sync")

    async def write(
        self,
        *,
        source_asset_id: str,
        graph_generation_id: str,
        checkpoint: SourceCursor,
        fencing_token: int,
    ) -> None:
        raise NotImplementedError("GraphSyncService only ever runs full_sync")


class _CountingConnector:
    """Wraps a connector to record per-source document counts for the run view --
    orchestration-level bookkeeping, not something the generic connectors need
    to know about themselves."""

    def __init__(self, inner: Any, counts: dict[str, int]) -> None:
        self._inner = inner
        self._counts = counts

    async def capture_high_watermark(self, *, source_asset_id: str) -> SourceCursor:
        return await self._inner.capture_high_watermark(source_asset_id=source_asset_id)

    def compare_cursors(
        self, *, source_asset_id: str, left: SourceCursor, right: SourceCursor
    ) -> Any:
        return self._inner.compare_cursors(source_asset_id=source_asset_id, left=left, right=right)

    async def scan(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        after: SourceCursor | None,
        through: SourceCursor,
    ) -> AsyncIterator[Any]:
        async for page in self._inner.scan(
            schema=schema, source_asset_id=source_asset_id, after=after, through=through
        ):
            self._counts[source_asset_id] = self._counts.get(source_asset_id, 0) + len(
                page.documents
            )
            yield page


class GraphSyncService:
    """Rebuildable graph projection writer. Source systems remain authoritative."""

    def __init__(
        self,
        *,
        platform_client: AsyncMongoClient[dict[str, object]],
        source_client: AsyncMongoClient[dict[str, object]],
        driver: AsyncDriver,
        settings: Settings,
        registry: SchemaRegistry,
    ) -> None:
        del registry  # accepted for constructor compatibility with existing call sites;
        # this service now derives its own interim ActiveSchema rather than the
        # legacy SchemaRegistry.graph, so it has nothing left to read from it.
        self._settings = settings
        self._platform_db = platform_client[settings.mongo_database]
        self._source_db = source_client[settings.source_mongo_database]
        self._driver = driver
        self._runs = self._platform_db["graph_sync_runs"]
        self._schema = build_interim_active_schema(
            configuration_release_id="sync-service-interim-v1",
            configuration_checksum=hashlib.sha256(b"sync-service-interim-v1").hexdigest(),
            approved_by="system",
            approved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self._writer = Neo4jDynamicGraphWriter(driver, database=settings.neo4j_database)
        self._projector = GenericGraphProjector()

    async def ensure_indexes(self) -> None:
        await self._runs.create_index([("startedAt", -1)])
        await self._runs.create_index("status")

    async def remove_source_mongodb_records(
        self, records: Sequence[tuple[str, Mapping[str, object]]]
    ) -> None:
        """Remove projections for authoritative source records that were rolled back.

        Deletes only the node itself (generation-scoped HARD_DELETE) -- unlike
        the prior hand-coded Cypher, this does not cascade-delete OrderLine or
        CustomerAccount children of a removed SalesOrder/Customer. Cascading
        child cleanup on deletion is the replace-child-set reconciliation
        machinery (a later stage of the source-to-graph alignment plan), not
        reimplemented ad hoc here.
        """

        mutations: list[GraphNodeMutation] = []
        for asset_id, payload in records:
            if asset_id == "source.mongodb.customer_outbound_cdm":
                key = _text(payload.get("partyId")) or _text(payload.get("customerId"))
                if key:
                    mutations.append(_delete("node_customer", {"customer_key": key}))
            elif asset_id == "source.mongodb.sales_inv":
                order_key = _text(payload.get("salesHdrEventData.orderId"))
                if order_key:
                    mutations.append(_delete("node_sales_order", {"order_id": order_key}))
                customer_key = _text(payload.get("salesHdr.salesHdrData.custId"))
                if customer_key:
                    mutations.append(_delete("node_customer", {"customer_key": customer_key}))
            elif asset_id == "source.mongodb.product_search":
                product_key = _text(payload.get("productId"))
                if product_key:
                    mutations.append(_delete("node_product", {"product_id": product_key}))
            elif asset_id == "source.mongodb.shipment_info":
                tracking_key = _text(payload.get("shipmentInfoEventData.trkNum"))
                if tracking_key:
                    mutations.append(_delete("node_shipment", {"tracking_number": tracking_key}))

        if not mutations:
            return
        async with self._driver.session(database=self._settings.neo4j_database) as session:
            for statement in compile_node_writes(
                self._schema, tuple(mutations), graph_generation_id=_LEGACY_GENERATION_ID
            ):
                result = await session.run(statement.cypher, statement.parameters)
                await result.consume()

    @staticmethod
    def _view(document: dict[str, Any]) -> GraphSyncRunView:
        return GraphSyncRunView.model_validate(
            {
                "id": str(document["_id"]),
                "mode": document["mode"],
                "status": document["status"],
                "schemaVersion": document["schemaVersion"],
                "sourceCounts": document.get("sourceCounts", {}),
                "nodeWrites": document.get("nodeWrites", 0),
                "relationshipWrites": document.get("relationshipWrites", 0),
                "constraintsApplied": document.get("constraintsApplied", []),
                "configurationDigest": document["configurationDigest"],
                "errorCode": document.get("errorCode"),
                "startedBy": document["startedBy"],
                "startedAt": document["startedAt"],
                "completedAt": document.get("completedAt"),
            }
        )

    def _configuration_digest(self) -> str:
        encoded = self._schema.configuration_checksum.encode()
        return hashlib.sha256(encoded).hexdigest()

    async def list_runs(self, limit: int = 100) -> list[GraphSyncRunView]:
        documents = await self._runs.find({}).sort("startedAt", -1).limit(limit).to_list()
        return [self._view(document) for document in documents]

    async def get_run(self, run_id: str) -> GraphSyncRunView | None:
        document = await self._runs.find_one({"_id": run_id})
        return self._view(document) if document else None

    async def sync(
        self,
        request: GraphSyncRequest,
        *,
        actor_id: str,
        seed_version: str | None = None,
        seed_digest: str | None = None,
    ) -> GraphSyncRunView:
        if (seed_version is None) is not (seed_digest is None):
            raise ValueError("Seed version and digest must be supplied together.")
        limit = min(request.maxRecordsPerAsset, self._settings.graph_sync_max_records)
        run_id = str(uuid.uuid4())
        now = _now()
        document: dict[str, Any] = {
            "_id": run_id,
            "mode": request.mode,
            "status": "RUNNING",
            "schemaVersion": self._schema.schema_version,
            "sourceCounts": {},
            "nodeWrites": 0,
            "relationshipWrites": 0,
            "constraintsApplied": [],
            "configurationDigest": self._configuration_digest(),
            "errorCode": None,
            "startedBy": actor_id,
            "startedAt": now,
            "completedAt": None,
        }
        await self._runs.insert_one(document)
        try:
            constraints = await self._apply_constraints() if request.applySchema else []
            await self._ensure_generation_marker()

            source_counts: dict[str, int] = {}
            node_writes, relationship_writes = await self._sync_participating_sources(
                request=request,
                run_id=run_id,
                limit=limit,
                seed_version=seed_version,
                seed_digest=seed_digest,
                source_counts=source_counts,
            )

            completed = _now()
            document.update(
                {
                    "status": "COMPLETED",
                    "sourceCounts": source_counts,
                    "nodeWrites": node_writes,
                    "relationshipWrites": relationship_writes,
                    "constraintsApplied": constraints,
                    "completedAt": completed,
                }
            )
            await self._runs.update_one({"_id": run_id}, {"$set": document})
            return self._view(document)
        except Exception as error:
            document.update(
                {
                    "status": "FAILED",
                    "errorCode": type(error).__name__.upper()[:100],
                    "completedAt": _now(),
                }
            )
            await self._runs.update_one({"_id": run_id}, {"$set": document})
            raise

    async def _sync_participating_sources(
        self,
        *,
        request: GraphSyncRequest,
        run_id: str,
        limit: int,
        seed_version: str | None,
        seed_digest: str | None,
        source_counts: dict[str, int],
    ) -> tuple[int, int]:
        mongo_source_ids = frozenset(
            source_id
            for source_id, source in self._schema.sources.items()
            if source.connector_type == ConnectorType.MONGODB
        )
        sql_source_ids = frozenset(
            source_id
            for source_id, source in self._schema.sources.items()
            if source.connector_type == ConnectorType.MSSQL
        )
        participating: set[str] = set()
        if request.mode in {GraphSyncScope.FULL, GraphSyncScope.SOURCE_MONGODB}:
            participating |= mongo_source_ids
        if request.mode in {GraphSyncScope.FULL, GraphSyncScope.SQLSERVER}:
            participating |= sql_source_ids
        if not participating:
            return 0, 0

        seed_pins = self._build_seed_pins(mongo_source_ids, seed_version, seed_digest)

        mongo_connector = _CountingConnector(
            MongoDBSourceScanConnector(
                self._source_db,
                schema=self._schema,
                page_size=self._settings.graph_sync_batch_size,
                seed_pins=seed_pins,
                max_records_per_source=limit,
            ),
            source_counts,
        )
        sql_connection = SqlServerConnectionSettings(
            server=self._settings.sqlserver_host,
            port=self._settings.sqlserver_port,
            user=self._settings.sqlserver_user,
            password=self._settings.sqlserver_password.get_secret_value(),
            database=self._settings.sqlserver_database,
            timeout_seconds=int(self._settings.operation_timeout_seconds),
        )
        sqlserver_connector = _CountingConnector(
            SqlServerSourceScanConnector(
                sql_connection,
                schema=self._schema,
                page_size=self._settings.graph_sync_batch_size,
                max_records_per_source=limit,
            ),
            source_counts,
        )
        connectors = SourceConnectorRegistry(
            schema=self._schema,
            mongo_connector=mongo_connector,
            sqlserver_connector=sqlserver_connector,
        )
        resolved_secrets = {
            _CONTACT_KEY_REFERENCE: self._settings.contact_lookup_hmac_key.get_secret_value()
        }
        extractor = GenericSourceRecordExtractor(resolved_secrets=resolved_secrets)
        projector_writer = ProjectorGraphWriter(
            projector=self._projector,
            writer=self._writer,
            sync_run_id=run_id,
        )
        coordinator = GenericSyncCoordinator(
            connectors=connectors,
            extractor=extractor,
            writer=projector_writer,
            checkpoints=_UnusedCheckpointStore(),
            reconciler=self._writer,
            ownership_reconciler=self._writer,
        )
        return await coordinator.full_sync(
            schema=self._schema,
            graph_generation_id=_LEGACY_GENERATION_ID,
            fencing_token=_LEGACY_FENCING_TOKEN,
            source_asset_ids=frozenset(participating),
            expected_generation_status=GraphGenerationStatus.ACTIVE,
            sync_run_id=run_id,
        )

    @staticmethod
    def _build_seed_pins(
        mongo_source_ids: frozenset[str], seed_version: str | None, seed_digest: str | None
    ) -> dict[str, SeedPin] | None:
        """Every Mongo source gets the same pin -- one seed generation covers the
        whole seed run, never a per-source mix. The actual fail-closed digest
        check and exhaustive (unlimited) read happen inside
        MongoDBSourceScanConnector.scan(); this only decides whether a pin
        applies at all and to which sources."""

        if seed_version is None or seed_digest is None:
            return None
        pin = SeedPin(seed_version=seed_version, seed_digest=seed_digest)
        return {source_id: pin for source_id in mongo_source_ids}

    async def _ensure_generation_marker(self) -> None:
        """This service does not run a real blue/green rebuild -- it resyncs
        directly into one stable, always-ACTIVE generation marker. The real
        generation lifecycle (PREPARING -> ... -> ACTIVE -> RETIRED) is a
        separate, later cutover (see the source-to-graph alignment plan)."""

        async with self._driver.session(database=self._settings.neo4j_database) as session:
            result = await session.run(
                "MERGE (g:GraphGeneration {generation_id: $generationId}) "
                "ON CREATE SET g.fencing_token = $fencingToken, g.status = $status",
                generationId=_LEGACY_GENERATION_ID,
                fencingToken=_LEGACY_FENCING_TOKEN,
                status=GraphGenerationStatus.ACTIVE.value,
            )
            await result.consume()

    async def _apply_constraints(self) -> list[str]:
        applied: list[str] = []
        async with self._driver.session(database=self._settings.neo4j_database) as session:
            for constraint in required_node_constraints(self._schema):
                label = validate_graph_identifier(constraint.label)
                properties = [
                    validate_graph_identifier(prop) for prop in constraint.graph_properties
                ]
                name = validate_graph_identifier(
                    f"uq_{label.lower()}_{'_'.join(properties)}".lower()
                )
                require = ", ".join(f"n.`{prop}`" for prop in properties)
                query = (
                    f"CREATE CONSTRAINT {name} IF NOT EXISTS "
                    f"FOR (n:`{label}`) REQUIRE ({require}) IS UNIQUE"
                )
                result = await session.run(query)
                await result.consume()
                applied.append(name)
        return applied


def _delete(projection_id: str, key_values: dict[str, Any]) -> GraphNodeMutation:
    entity_id = projection_id.removeprefix("node_")
    return GraphNodeMutation(
        operation="HARD_DELETE",
        projection_id=projection_id,
        entity_id=entity_id,
        key_values=key_values,
        properties={},
    )
