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
from dataclasses import dataclass
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
    LEGACY_FENCING_TOKEN,
    LEGACY_GENERATION_ID,
    GraphGenerationStatus,
)
from return_platform.dynamic_knowledge.graph.generation_writer import Neo4jGenerationWriter
from return_platform.dynamic_knowledge.graph.neo4j_writer import Neo4jDynamicGraphWriter
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.graph.write_compiler import compile_node_writes
from return_platform.dynamic_knowledge.lifecycle.handle import DEFAULT_SNAPSHOT_NAME
from return_platform.dynamic_knowledge.lifecycle.lease_store import (
    GENERATION_LEASES_COLLECTION,
    MongoGenerationLeaseStore,
)
from return_platform.dynamic_knowledge.lifecycle.mongo_store import (
    ACTIVE_RUNTIME_SNAPSHOTS_COLLECTION,
    GENERATION_FENCING_TOKENS_COLLECTION,
    REBUILD_LEASES_COLLECTION,
    MongoActiveRuntimeSnapshotStore,
    MongoFencingTokenAllocator,
    MongoRebuildLeaseStore,
)
from return_platform.dynamic_knowledge.lifecycle.neo4j_validator import Neo4jGenerationValidator
from return_platform.dynamic_knowledge.lifecycle.orchestrator import (
    ActivationError,
    GenerationLifecycleOrchestrator,
    adopt_existing_generation,
)
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
from return_platform.dynamic_knowledge.sync.checkpoint_store import MongoSyncCheckpointStore
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
_LEGACY_FENCING_TOKEN = LEGACY_FENCING_TOKEN
"""Both survive only as *bootstrap* values, not as what production runs on.

This service used to write every sync into a single, permanently-active graph
generation under these two literals rather than building a new generation and
swapping. Three things followed, stated plainly because they were the reason
this had to change:

  * A full sync mutated the live graph in place, so a reader could observe a
    partially rebuilt graph while it ran.
  * A destructive schema change had no safe cutover path.
  * The fencing token was constant, so it fenced nothing -- neither the Neo4j
    marker's exact-match check nor `MongoSyncCheckpointStore`'s `$lte` refusal
    could tell an owner from a stale writer when every writer presented `1`.

The blue/green machinery that fixes this already existed and was complete
(`dynamic_knowledge/graph/generation.py`: GraphGeneration, fencing tokens,
read/write drain leases, ProjectionOwnership; `dynamic_knowledge/lifecycle/`:
the orchestrator, the snapshot compare-and-swap, the drain). It is now what this
service uses. Adopting generations here means, and here does mean: allocate a
generation, sync into it, validate, swap the ActiveRuntimeSnapshot, drain
readers on the old generation, retire it. **The active generation is never
dropped before its replacement validates** -- if the candidate fails at any
stage it is marked FAILED, the compare-and-swap never runs, and N keeps serving.

Where these literals still appear: a deployment whose graph predates the
protocol has nodes under `legacy-live` and no ActiveRuntimeSnapshot. That
generation is *adopted* as activation version 1 (`_resolve_active_generation`
below), which both gives the next rebuild a predecessor to drain and retire
instead of orphaning the live data, and claims a real allocated token on the
marker so a writer still holding `_LEGACY_FENCING_TOKEN` is fenced off from
that moment on. A brand-new deployment creates the same marker once, for the
same reason: readers that have not yet seen a snapshot fall back to this exact
id (see `LEGACY_GENERATION_ID`), so writer and reader must agree on it until
adoption completes.
"""


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
    #: Which *records* a run reads, orthogonal to `mode`, which chooses which
    #: *sources* take part. Kept as a separate field rather than a fourth
    #: `GraphSyncScope` value so the two questions stay composable -- an
    #: incremental Mongo-only pass is `SOURCE_MONGODB` + `incremental`, not a
    #: fifth enum member per combination. Defaults to False: a caller that has
    #: not thought about resume semantics gets the full scan it always got.
    incremental: bool = False


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
    # The generation the run actually wrote into. For a destructive rebuild this
    # is the *new* generation the run built and activated, not the one that was
    # serving when it started -- which is the question an operator reading the
    # ledger after a cutover is asking. Still null for runs recorded before the
    # service resolved a generation at all, rather than being back-filled with a
    # value those runs never had.
    graphGenerationId: str | None = None
    requestDigest: str | None = None
    requestedBy: SyncRunRequester | None = None
    #: FULL or INCREMENTAL, which is what the plan's S6 calls FULL/PARTIAL.
    #: Defaulted rather than required because every run recorded before
    #: incremental sync was reachable was a full scan, and back-filling a
    #: historical ledger to say so would be inventing a record.
    recordScope: Literal["FULL", "INCREMENTAL"] = "FULL"
    #: Sources that took no part in an incremental run because they declare no
    #: `incremental_cursor_field`. Empty on a full run. Surfaced because the
    #: alternative is an operator seeing a green run and a source that quietly
    #: never syncs -- exactly the failure this step exists to make visible.
    skippedSources: list[str] = Field(default_factory=list)


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
            "recordScope": document.get("recordScope", "FULL"),
            "skippedSources": document.get("skippedSources", []),
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


@dataclass(frozen=True, slots=True)
class _SyncOutcome:
    """What one run wrote, and where.

    `graph_generation_id` is carried out rather than assumed by the caller
    because it is no longer a constant: a destructive rebuild writes into the
    generation it built, every other run writes into the one that was serving,
    and the run ledger has to record which.
    """

    node_writes: int
    relationship_writes: int
    graph_generation_id: str | None


def _is_destructive_rebuild(request: GraphSyncRequest) -> bool:
    """Which requests replace the graph rather than update it in place.

    A full scan of every source *is* a rebuild -- it re-derives the whole
    projection -- so it goes through the blue/green cutover. A single-source
    resync or an incremental pass updates part of a graph that stays live, and
    forcing those through a full rebuild would make an operator re-reading one
    collection pay for a complete re-projection of every other.
    """
    return request.mode is GraphSyncScope.FULL and not request.incremental


class _ScopedRebuildCoordinator:
    """Pins a rebuild to this run's participating sources, and totals what it wrote.

    Two mismatches to bridge. `RebuildSyncCoordinator` takes no
    `source_asset_ids` (a rebuild is every source, by definition), while this
    service resolves its own participating set including the platform-store
    routing; and `build_and_activate` returns a snapshot, not write counts, which
    the run ledger records.

    Accumulating across calls is correct rather than lossy: the orchestrator runs
    `full_sync` twice, once to build and once to catch up on what changed since
    the build's watermarks were captured, and both wrote into the generation this
    run activated.
    """

    def __init__(self, inner: GenericSyncCoordinator, source_asset_ids: frozenset[str]) -> None:
        self._inner = inner
        self._source_asset_ids = source_asset_ids
        self.node_writes = 0
        self.relationship_writes = 0

    async def full_sync(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        fencing_token: int,
        expected_generation_status: GraphGenerationStatus,
        sync_run_id: str | None = None,
    ) -> tuple[int, int]:
        nodes, relationships = await self._inner.full_sync(
            schema=schema,
            graph_generation_id=graph_generation_id,
            fencing_token=fencing_token,
            source_asset_ids=self._source_asset_ids,
            expected_generation_status=expected_generation_status,
            sync_run_id=sync_run_id,
        )
        self.node_writes += nodes
        self.relationship_writes += relationships
        return nodes, relationships


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
        self._checkpoints = MongoSyncCheckpointStore(platform_client, settings.mongo_database)
        self._writer = Neo4jDynamicGraphWriter(driver, database=settings.neo4j_database)
        self._projector = GenericGraphProjector()
        # The blue/green half. Every store here already existed and had no
        # production construction site; this is that site. The collection names
        # come from the modules that own them so this service and the request
        # path (lifecycle/handle.py) cannot drift onto different collections --
        # which would look exactly like a cutover no reader ever noticed.
        self._generation_writer = Neo4jGenerationWriter(driver, database=settings.neo4j_database)
        self._snapshots = MongoActiveRuntimeSnapshotStore(
            self._platform_db[ACTIVE_RUNTIME_SNAPSHOTS_COLLECTION]
        )
        self._rebuild_leases = MongoRebuildLeaseStore(self._platform_db[REBUILD_LEASES_COLLECTION])
        self._generation_leases = MongoGenerationLeaseStore(
            self._platform_db[GENERATION_LEASES_COLLECTION]
        )
        self._fencing_tokens = MongoFencingTokenAllocator(
            self._platform_db[GENERATION_FENCING_TOKENS_COLLECTION]
        )
        self._validator = Neo4jGenerationValidator(driver, database=settings.neo4j_database)
        # Identifies this process to the rebuild lease, so a lease held by a
        # crashed instance is attributable rather than anonymous.
        self._owner_instance_id = f"graph-sync-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def incremental_skipped_source_ids(
        schema: ActiveSchema, participating: frozenset[str]
    ) -> tuple[str, ...]:
        """Participating sources an incremental run cannot resume, because they
        declare no `incremental_cursor_field`.

        The coordinator skips these by design -- there is no position to read
        from. Naming them here is what keeps that from being invisible: a run
        that completes green having never touched a source is the failure mode
        this whole step is about.
        """

        return tuple(
            sorted(
                source_id
                for source_id in participating
                if schema.sources[source_id].incremental_cursor_field is None
            )
        )

    async def refresh_schema(self) -> None:
        """Pick up a release published since this service started."""
        self._schema = await resolve_active_schema(
            self._settings.dynamic_knowledge_schema_path, self._releases
        )

    async def ensure_indexes(self) -> None:
        await self._releases.ensure_indexes()
        await self._checkpoints.ensure_indexes()
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
        # The generation that is actually serving, not the legacy literal: after
        # a cutover the rolled-back records live in the new generation, and
        # deleting them from the retired one would leave the live graph still
        # projecting source records that no longer exist.
        graph_generation_id = await self._resolve_active_generation()
        async with self._driver.session(database=self._settings.neo4j_database) as session:
            for statement in compile_node_writes(
                self._schema, tuple(mutations), graph_generation_id=graph_generation_id
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
            "graphGenerationId": None,
            "recordScope": "INCREMENTAL" if request.incremental else "FULL",
            "skippedSources": [],
        }
        await self._runs.insert_one(document)
        try:
            constraints = await self._apply_constraints() if request.applySchema else []

            source_counts: dict[str, int] = {}
            skipped: list[str] = []
            outcome = await self._sync_participating_sources(
                request=request,
                run_id=run_id,
                limit=limit,
                seed_version=seed_version,
                seed_digest=seed_digest,
                source_counts=source_counts,
                skipped=skipped,
            )

            completed = _now()
            document.update(
                {
                    "status": "COMPLETED",
                    "sourceCounts": source_counts,
                    "skippedSources": skipped,
                    "nodeWrites": outcome.node_writes,
                    "relationshipWrites": outcome.relationship_writes,
                    "constraintsApplied": constraints,
                    "graphGenerationId": outcome.graph_generation_id,
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
        skipped: list[str],
    ) -> _SyncOutcome:
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
            # Nothing to sync, so nothing to resolve, adopt or cut over to. A
            # generation id here would name a generation this run never touched.
            return _SyncOutcome(0, 0, None)
        if request.incremental:
            skipped.extend(
                self.incremental_skipped_source_ids(self._schema, frozenset(participating))
            )

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
            checkpoints=self._checkpoints,
            reconciler=self._writer,
            ownership_reconciler=self._writer,
        )
        participating_ids = frozenset(participating)

        if _is_destructive_rebuild(request):
            return await self._rebuild_and_activate(
                coordinator=coordinator, source_asset_ids=participating_ids
            )

        # Every other shape writes into the generation that is already serving:
        # an incremental pass resuming from its own checkpoints, or a
        # single-source resync. Neither replaces the graph, so neither is a
        # cutover -- but both are still fenced, with a token allocated now rather
        # than a constant, so a run of either kind that stalled and was
        # superseded is rejected instead of writing on top of its successor.
        graph_generation_id = await self._resolve_active_generation()
        fencing_token = await self._claim_write_ownership(graph_generation_id)

        if request.incremental:
            # No `sync_run_id`: the run manifest records the watermark bounds a
            # *full* run fixed up front, and an incremental run has no such
            # single set -- each source resumes from its own checkpoint. No
            # ownership reconciliation either; the coordinator withholds it from
            # incremental_sync deliberately (see `_project_page`), because a
            # replace-child-set pass driven by a partial page would delete the
            # children of every parent the page did not contain.
            nodes, relationships = await coordinator.incremental_sync(
                schema=self._schema,
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                source_asset_ids=participating_ids,
                expected_generation_status=GraphGenerationStatus.ACTIVE,
            )
        else:
            nodes, relationships = await coordinator.full_sync(
                schema=self._schema,
                graph_generation_id=graph_generation_id,
                fencing_token=fencing_token,
                source_asset_ids=participating_ids,
                expected_generation_status=GraphGenerationStatus.ACTIVE,
                sync_run_id=run_id,
            )
        return _SyncOutcome(nodes, relationships, graph_generation_id)

    async def _rebuild_and_activate(
        self, *, coordinator: GenericSyncCoordinator, source_asset_ids: frozenset[str]
    ) -> _SyncOutcome:
        """The C9 destructive flow, driven by the lifecycle that already implements it.

        allocate generation N+1 -> capture high watermark -> sync into N+1 ->
        catch up deltas -> validate N+1 -> atomically swap ActiveRuntimeSnapshot
        -> block new readers from N -> drain old readers -> retire N. Every step
        of that belongs to `GenerationLifecycleOrchestrator`; this method exists
        to hand it the pipeline *this service* assembles rather than a second one
        built beside it, so a rebuild reads through the same connectors, the same
        seed pins, the same per-source counting and the same record limits as
        every other run.

        A rebuild already running elsewhere is reported, not swallowed. The
        request-scoped API caller asked for a sync now and needs to know it did
        not happen; `RebuildTrigger` swallows the same condition because its
        callers are startup and schedules, where standing down is correct.

        If N+1 fails at any point -- build, catch-up, validation, or the
        compare-and-swap -- the orchestrator marks it FAILED and never moves the
        snapshot, so N stays active and serving. That is the property, and it is
        why this must never pre-emptively touch N.
        """
        scoped = _ScopedRebuildCoordinator(coordinator, source_asset_ids)
        orchestrator = GenerationLifecycleOrchestrator(
            snapshot_store=self._snapshots,
            lease_store=self._rebuild_leases,
            generation_writer=self._generation_writer,
            sync_coordinator=scoped,
            fencing_tokens=self._fencing_tokens,
            owner_instance_id=self._owner_instance_id,
            generation_lease_store=self._generation_leases,
            validator=self._validator,
        )
        # Adopt first. Without a predecessor the cutover would activate the new
        # generation beside the live graph instead of replacing it, and every
        # node already there would be orphaned under `legacy-live` with nothing
        # left pointing at it.
        await self._resolve_active_generation()
        snapshot = await orchestrator.build_and_activate(
            schema=self._schema,
            snapshot_name=DEFAULT_SNAPSHOT_NAME,
            configuration_release_id=self._schema.configuration_release_id,
        )
        return _SyncOutcome(
            scoped.node_writes, scoped.relationship_writes, snapshot.graph_generation_id
        )

    async def _resolve_active_generation(self) -> str:
        """Which generation is serving, creating and adopting one if none is yet.

        Three states, in the order they are checked:

        1. An `ActiveRuntimeSnapshot` exists -- the normal case once a cutover
           has run. Its generation is authoritative, and it is the same pointer
           `lifecycle/handle.py` resolves on the read path, so writer and reader
           cannot disagree about what is live.
        2. No snapshot, but a `legacy-live` marker exists -- a deployment whose
           graph predates the protocol. Adopt it (see
           `adopt_existing_generation`): its data stays served, and the next
           destructive rebuild has a predecessor to drain and retire.
        3. Neither -- a new deployment. Create the marker exactly as this service
           always has, then adopt it, so a partial or incremental sync on a fresh
           install still has somewhere to write and readers falling back to
           `LEGACY_GENERATION_ID` still agree with it.

        States 2 and 3 converge deliberately: after either, a snapshot exists and
        nothing resolves "the current generation" by falling back to a literal.
        """
        snapshot = await self._snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)
        if snapshot is not None:
            return snapshot.graph_generation_id

        await self._ensure_generation_marker()
        adopted = await adopt_existing_generation(
            snapshot_store=self._snapshots,
            generation_writer=self._generation_writer,
            fencing_tokens=self._fencing_tokens,
            graph_generation_id=_LEGACY_GENERATION_ID,
            snapshot_name=DEFAULT_SNAPSHOT_NAME,
            configuration_release_id=self._schema.configuration_release_id,
            schema_fingerprint=self._schema.configuration_checksum,
        )
        return adopted.graph_generation_id

    async def _claim_write_ownership(self, graph_generation_id: str) -> int:
        """Allocate this run's fencing token and stamp it on the generation.

        The token is durable and monotonic, so claiming it is what fences off any
        earlier run that still believes it owns this generation: its writes stop
        matching the Neo4j marker, and `MongoSyncCheckpointStore` refuses to let
        its lower token rewind a cursor this run has advanced. Before this, every
        run presented the same constant and no run could ever be fenced.

        ACTIVE is required rather than merely observed. The snapshot only ever
        points at an ACTIVE generation, so anything else means the generation
        began draining between resolution and the claim -- and a run that carried
        on would write into a generation about to be retired, reporting success
        for data that is discarded.
        """
        token = await self._fencing_tokens.allocate(scope=DEFAULT_SNAPSHOT_NAME)
        status, _ = await self._generation_writer.claim_write_ownership(
            graph_generation_id=graph_generation_id, fencing_token=token
        )
        if status is not GraphGenerationStatus.ACTIVE:
            raise ActivationError(
                f"generation {graph_generation_id!r} is {status.value}, not ACTIVE; "
                "this run has been superseded and must not write",
                stage="CLAIM_WRITE_OWNERSHIP",
            )
        return token

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
        """Create the legacy marker if this deployment has never had a generation.

        Bootstrap only, and reached exactly once per deployment: the very next
        step adopts it into an `ActiveRuntimeSnapshot`, after which
        `_resolve_active_generation` answers from the snapshot and never comes
        back here. It is deliberately still a MERGE with `ON CREATE SET` -- an
        existing marker carries an adopted status and an allocated fencing token,
        and overwriting either would un-fence whoever currently owns it.
        """

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
