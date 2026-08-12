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
from return_platform.data_platform.schema_registry import SchemaRegistry
from return_platform.dynamic_knowledge.config_loader import (
    load_active_schema,
    resolve_active_schema,
)
from return_platform.dynamic_knowledge.graph.constraints import required_node_constraints
from return_platform.dynamic_knowledge.graph.generation import (
    LEGACY_GENERATION_ID,
    GraphGenerationStatus,
)
from return_platform.dynamic_knowledge.graph.neo4j_writer import Neo4jDynamicGraphWriter
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.graph.write_compiler import compile_node_writes
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    GraphNodeMutation,
    SyncOrigin,
    SyncReceipt,
    SyncStatus,
)
from return_platform.dynamic_knowledge.on_demand_sync.extraction import (
    GenericSourceRecordExtractor,
    contact_digest_secrets,
)
from return_platform.dynamic_knowledge.release_store import SchemaReleaseStore
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    ConnectorType,
    validate_graph_identifier,
)
from return_platform.dynamic_knowledge.sync.adapters import (
    ProjectorGraphWriter,
    scan_connector_registry,
)
from return_platform.dynamic_knowledge.sync.coordinator import GenericSyncCoordinator
from return_platform.source_connectors.contracts import (
    CursorComparison,
    RawSourcePage,
    SourceConnectorCapabilities,
    SourceCursor,
)
from return_platform.source_connectors.mongodb import MongoDBSourceScanConnector, SeedPin
from return_platform.source_connectors.protocols import SourceScanConnector
from return_platform.source_connectors.sqlserver import (
    SqlServerConnectionSettings,
    SqlServerSourceScanConnector,
)

_LEGACY_GENERATION_ID = LEGACY_GENERATION_ID
_LEGACY_FENCING_TOKEN = 1


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


class SyncRunRequester(GraphSyncModel):
    """The agent turn a targeted run was performed for. Absent on a scheduled run.

    Anchor *field ids*, not anchor values -- see `SyncOrigin` for why a run list
    is not somewhere to accumulate order numbers.
    """

    agentId: str
    conversationId: str
    clientTurnId: str
    entityId: str
    strongAnchorId: str
    anchorFieldIds: list[str]


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
    # Populated for ON_DEMAND runs only. A scheduled run writes into the one
    # legacy generation and has no requesting turn, so both stay null there
    # rather than being invented.
    graphGenerationId: str | None = None
    requestDigest: str | None = None
    requestedBy: SyncRunRequester | None = None


GRAPH_SYNC_RUNS_COLLECTION = "graph_sync_runs"


def _now() -> datetime:
    return datetime.now(UTC)


def sync_run_view(document: dict[str, Any]) -> GraphSyncRunView:
    """One `graph_sync_runs` document as the console reads it.

    Module-level rather than a method because two writers now share this
    collection -- the scheduled `GraphSyncService.sync` below and
    `MongoTargetedSyncRunLedger`, which records what an agent turn pulled from a
    source on demand. One ledger, deliberately: before this the two ran in
    separate books and only the scheduled one had a reader, so a targeted sync
    was invisible to everyone including the person debugging why the graph
    changed.
    """
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
            "graphGenerationId": document.get("graphGenerationId"),
            "requestDigest": document.get("requestDigest"),
            "requestedBy": document.get("requestedBy"),
        }
    )


class MongoTargetedSyncRunLedger:
    """Publishes an agent's targeted sync into the platform's one run ledger.

    Satisfies `on_demand_sync.coordinator.TargetedSyncRunLedger`. Lives here,
    beside `GraphSyncService`, because this module owns `graph_sync_runs` and
    `dynamic_knowledge` deliberately does not import `data_platform` -- the
    coordinator is handed the port, and the composition root
    (`scripts/run_order_discovery_worker.py`) supplies this adapter.

    Upserts on the sync request id: `synchronize` records a run twice, once
    RUNNING and once terminal, and `startedAt` must survive the second write.
    """

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        database: str,
    ) -> None:
        self._runs = client[database][GRAPH_SYNC_RUNS_COLLECTION]

    async def record(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        receipt: SyncReceipt,
        origin: SyncOrigin | None,
    ) -> None:
        now = _now()
        terminal = receipt.status in {SyncStatus.SUCCEEDED, SyncStatus.FAILED}
        update: dict[str, Any] = {
            "mode": "ON_DEMAND",
            # The ledger's vocabulary, not the receipt's: an operator comparing a
            # targeted run against a scheduled one should not have to know that
            # one says SUCCEEDED and the other COMPLETED.
            "status": "COMPLETED" if receipt.status is SyncStatus.SUCCEEDED else receipt.status,
            "schemaVersion": receipt.schema_version,
            "sourceCounts": {source_asset_id: receipt.source_rows_read},
            "nodeWrites": receipt.nodes_written,
            "relationshipWrites": receipt.relationships_written,
            # A targeted read never applies constraints; it writes into a
            # generation a rebuild already prepared.
            "constraintsApplied": [],
            "configurationDigest": hashlib.sha256(
                schema.configuration_checksum.encode()
            ).hexdigest(),
            "errorCode": receipt.error_code,
            "graphGenerationId": receipt.graph_generation_id,
            "requestDigest": receipt.request_digest,
            "completedAt": now if terminal else None,
        }
        if origin is not None:
            update["requestedBy"] = {
                "agentId": origin.agent_id,
                "conversationId": origin.conversation_id,
                "clientTurnId": origin.client_turn_id,
                "entityId": origin.entity_id,
                "strongAnchorId": origin.strong_anchor_id,
                "anchorFieldIds": list(origin.anchor_field_ids),
            }
        await self._runs.update_one(
            {"_id": receipt.sync_request_id},
            {
                "$set": update,
                "$setOnInsert": {
                    "startedAt": now,
                    "startedBy": origin.agent_id if origin is not None else "on-demand-sync",
                },
            },
            upsert=True,
        )


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

    def __init__(self, inner: SourceScanConnector, counts: dict[str, int]) -> None:
        self._inner = inner
        self._counts = counts

    def capabilities(self) -> SourceConnectorCapabilities:
        return self._inner.capabilities()

    async def capture_high_watermark(self, *, source_asset_id: str) -> SourceCursor:
        return await self._inner.capture_high_watermark(source_asset_id=source_asset_id)

    def compare_cursors(
        self, *, source_asset_id: str, left: SourceCursor, right: SourceCursor
    ) -> CursorComparison:
        return self._inner.compare_cursors(source_asset_id=source_asset_id, left=left, right=right)

    async def scan(
        self,
        *,
        schema: ActiveSchema,
        source_asset_id: str,
        after: SourceCursor | None,
        through: SourceCursor,
    ) -> AsyncIterator[RawSourcePage]:
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
        self._runs = self._platform_db[GRAPH_SYNC_RUNS_COLLECTION]
        # The configured schema, not a second one built in code.
        #
        # This used to call `build_interim_active_schema`, whose own docstring
        # called itself "a bridge, not the destination" and named the cutover
        # to the field-path-corrected Ferguson schema as a later step. That
        # schema now exists in `config/dynamic_knowledge/`, verified against
        # real source documents, and the order agent already reads it -- so
        # keeping a second, divergent copy here meant the graph was built from
        # different field paths than the agent queries it with.
        # The file is the starting point, not the last word: `refresh_schema`
        # replaces it with the published release before every run, so a schema
        # an analyst activated takes effect on the next sync rather than on the
        # next deploy. Resolved here synchronously because a constructor cannot
        # await, and a service that had no schema until its first run would
        # make every read of `self._schema` optional for no gain.
        self._schema = load_active_schema(settings.dynamic_knowledge_schema_path)
        self._releases = SchemaReleaseStore(platform_client, settings.mongo_database)
        self._writer = Neo4jDynamicGraphWriter(driver, database=settings.neo4j_database)
        self._projector = GenericGraphProjector()

    async def refresh_schema(self) -> None:
        """Pick up a release published since this service started."""
        self._schema = await resolve_active_schema(
            self._settings.dynamic_knowledge_schema_path, self._releases
        )

    async def ensure_indexes(self) -> None:
        await self._releases.ensure_indexes()
        await self._runs.create_index([("startedAt", -1)])
        await self._runs.create_index("status")
        # The ledger now holds both scheduled and on-demand runs, and the first
        # thing an operator does with a mixed list is filter it to one kind.
        await self._runs.create_index("mode")

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
        return sync_run_view(document)

    def _configuration_digest(self) -> str:
        encoded = self._schema.configuration_checksum.encode()
        return hashlib.sha256(encoded).hexdigest()

    async def list_runs(
        self, limit: int = 100, *, mode: str | None = None
    ) -> list[GraphSyncRunView]:
        query: dict[str, Any] = {} if mode is None else {"mode": mode}
        documents = await self._runs.find(query).sort("startedAt", -1).limit(limit).to_list()
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
        # Before anything is read or written: a run records the schema version
        # it built under, and picking up a newly activated release halfway
        # through would make that record a lie.
        await self.refresh_schema()
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
        platform_mongo_source_ids = self.platform_store_source_ids(
            self._schema, mongo_source_ids, self._platform_db.name
        )
        upstream_mongo_source_ids = mongo_source_ids - platform_mongo_source_ids
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

        # Only the upstream store. A seed generation is something the seeder
        # writes into the Ferguson source collections; the platform's own
        # operational documents carry no seedVersion/seedDigest, so pinning them
        # would filter every case, RMA and item out of a seeded run and leave
        # the return side of the graph silently empty.
        seed_pins = self._build_seed_pins(upstream_mongo_source_ids, seed_version, seed_digest)

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
        # A second connector, not a second pipeline: the return side lives in the
        # platform's own database, and a connector is bound to one database for
        # its lifetime (see MongoDBSourceScanConnector's class docstring).
        platform_mongo_connector = _CountingConnector(
            MongoDBSourceScanConnector(
                self._platform_db,
                schema=self._schema,
                page_size=self._settings.graph_sync_batch_size,
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
        connectors = scan_connector_registry(
            schema=self._schema,
            mongo_connector=mongo_connector,
            sqlserver_connector=sqlserver_connector,
            overrides={
                source_id: platform_mongo_connector for source_id in platform_mongo_source_ids
            },
        )
        extractor = GenericSourceRecordExtractor(
            resolved_secrets=contact_digest_secrets(
                self._schema,
                hmac_key=self._settings.contact_lookup_hmac_key.get_secret_value(),
            )
        )
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
    def platform_store_source_ids(
        schema: ActiveSchema, mongo_source_ids: frozenset[str], platform_database: str
    ) -> frozenset[str]:
        """Which Mongo sources name the platform's own database rather than upstream.

        `object_ref.database` was decorative until now -- the connector takes the
        database it is constructed with and ignores what the source declares --
        so a source pointing at the platform store was read from the upstream one
        and found nothing. Honouring it here is what makes the return-side
        entities (cases, RMAs, items, handling units) reachable at all.

        Anything that is not an exact match keeps the previous behaviour and goes
        to the upstream connector. That is deliberately not an error: the four
        Ferguson sources declare a database name that only equals
        `source_mongo_database` by default, and an operator who renamed it should
        not have their sync start failing.
        """

        return frozenset(
            source_id
            for source_id in mongo_source_ids
            if schema.sources[source_id].object_ref.get("database") == platform_database
        )

    @staticmethod
    def _build_seed_pins(
        mongo_source_ids: frozenset[str], seed_version: str | None, seed_digest: str | None
    ) -> dict[str, SeedPin] | None:
        """Every source given here gets the same pin -- one seed generation covers
        the whole seed run, never a per-source mix. The actual fail-closed digest
        check and exhaustive (unlimited) read happen inside
        MongoDBSourceScanConnector.scan(); this only decides whether a pin
        applies at all and to which sources. The caller passes the upstream Mongo
        sources only; see the call site for why the platform store is excluded."""

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
