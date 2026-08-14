"""End-to-end equivalence test: GraphSyncService.sync() driven entirely by fakes
(no live Mongo/Neo4j/SQL Server), proving the schema-driven pipeline that
replaced sync_service.py's hand-coded MERGE Cypher actually produces graph
writes for realistic customerOutboundCDM/salesInv-shaped documents -- the
same two collections the old hand-coded path read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from bson import ObjectId

from return_platform.data_platform.graph.interim_active_schema import build_interim_active_schema
from return_platform.data_platform.graph.sync_service import (
    GraphSyncRequest,
    GraphSyncScope,
    GraphSyncService,
)
from return_platform.dynamic_knowledge.graph.generation import (
    LEGACY_FENCING_TOKEN,
    LEGACY_GENERATION_ID,
    RebuildLease,
)
from return_platform.dynamic_knowledge.graph.generation_writer import Neo4jGenerationWriter
from return_platform.dynamic_knowledge.graph.neo4j_writer import (
    GenerationFencingError,
    Neo4jDynamicGraphWriter,
)
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector
from return_platform.dynamic_knowledge.graph.validation import (
    GenerationValidationReport,
    ValidationCheckId,
    ValidationFinding,
    ValidationSeverity,
)
from return_platform.dynamic_knowledge.lifecycle.handle import DEFAULT_SNAPSHOT_NAME
from return_platform.dynamic_knowledge.lifecycle.orchestrator import ActivationError
from return_platform.dynamic_knowledge.release_migration import (
    MigrationPlan,
    MigrationStrategy,
    SchemaChangeClass,
)
from return_platform.dynamic_knowledge.schema import ConnectorType
from return_platform.dynamic_knowledge.sync.adapters import scan_connector_registry


class FakeMongoCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, field: str, direction: int) -> FakeMongoCursor:
        self._documents = sorted(
            self._documents, key=lambda d: str(d.get(field)), reverse=direction < 0
        )
        return self

    def limit(self, count: int) -> FakeMongoCursor:
        self._documents = self._documents[:count]
        return self

    def __aiter__(self) -> Any:
        return self._iterate()

    async def _iterate(self) -> Any:
        for document in self._documents:
            yield document


class FakeMongoCollection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    def find(
        self, query: dict[str, Any], projection: dict[str, Any] | None = None
    ) -> FakeMongoCursor:
        del query, projection
        return FakeMongoCursor(list(self.documents))

    async def count_documents(self, query: dict[str, Any], **_kwargs: Any) -> int:
        return 0


class FakeDatabase:
    def __init__(
        self, collections: dict[str, FakeMongoCollection], *, name: str = "source"
    ) -> None:
        self._collections = collections
        #: `GraphSyncService.platform_store_source_ids` routes on the platform
        #: database's own name, so the fake standing in for it needs one.
        self.name = name

    def __getitem__(self, name: str) -> FakeMongoCollection:
        return self._collections.setdefault(name, FakeMongoCollection([]))


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __aiter__(self) -> Any:
        return self._iterate()

    async def _iterate(self) -> Any:
        for row in self._rows:
            yield row

    async def consume(self) -> None:
        return None


class FakeTransaction:
    """An in-memory stand-in for Neo4j that models the GraphGeneration marker.

    It has to model the marker rather than answer statically, because the
    service now *claims* write ownership before it writes: the claim raises the
    marker's fencing token and the write then fences against the value the
    marker actually holds. A fake that answered a fixed token could not tell a
    successful claim from a rejected one.

    What it still does not model is the write fence's own matching -- that is
    `fence_matched`, kept as a dial so the failure test can force a rejection.
    The authoritative version of both is
    `tests/dynamic_knowledge/test_graph_sync_cutover_real_infra.py`, against a
    real Neo4j.
    """

    def __init__(self, *, fence_matched: int) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._fence_matched = fence_matched
        #: Set when the legacy marker is MERGEd, exactly as production creates it.
        self.marker_token: int | None = None
        self.marker_status = "ACTIVE"

    async def run(
        self, query: str, parameters: dict[str, Any] | None = None, **kwargs: Any
    ) -> FakeResult:
        parameters = parameters or kwargs
        self.calls.append((query, parameters))
        if query.startswith("MERGE (g:GraphGeneration"):
            if self.marker_token is None:
                self.marker_token = int(parameters["fencingToken"])
                self.marker_status = str(parameters["status"])
            return FakeResult([])
        if "SET g.fencing_token = CASE" in query:
            if self.marker_token is None:
                return FakeResult([])
            requested = int(parameters["fencingToken"])
            self.marker_token = max(self.marker_token, requested)
            return FakeResult([{"status": self.marker_status, "fencing_token": self.marker_token}])
        if "MATCH (g:GraphGeneration" in query and "RETURN g.status AS status" in query:
            if self.marker_token is None:
                return FakeResult([])
            return FakeResult([{"status": self.marker_status, "fencing_token": self.marker_token}])
        if "MATCH (g:GraphGeneration" in query and "RETURN count(g)" in query:
            return FakeResult([{"matched": self._fence_matched}])
        if query.startswith("MATCH (r:GraphWriteReceipt"):
            return FakeResult([])
        if "MERGE (a)-[rel:" in query:
            return FakeResult([{"relationshipsWritten": 0}])
        if "AS violations" in query:
            return FakeResult([{"violations": 0}])
        return FakeResult([])


class FakeSession:
    def __init__(self, tx: FakeTransaction) -> None:
        self._tx = tx

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    async def execute_write(self, work: Any, **kwargs: Any) -> Any:
        return await work(self._tx, **kwargs)

    async def execute_read(self, work: Any, **kwargs: Any) -> Any:
        return await work(self._tx, **kwargs)

    async def run(self, query: str, **kwargs: Any) -> FakeResult:
        return await self._tx.run(query, kwargs)


class FakeSnapshotStore:
    """`ActiveRuntimeSnapshotStore` over one in-memory slot, with a real CAS."""

    def __init__(self) -> None:
        self.snapshot: Any = None
        self.history: list[str] = []

    async def read(self, *, snapshot_name: str) -> Any:
        del snapshot_name
        return self.snapshot

    async def compare_and_swap(
        self, *, snapshot_name: str, expected_activation_version: int | None, new_snapshot: Any
    ) -> bool:
        del snapshot_name
        current = None if self.snapshot is None else self.snapshot.activation_version
        if current != expected_activation_version:
            return False
        self.snapshot = new_snapshot
        self.history.append(new_snapshot.graph_generation_id)
        return True


class FakeTokens:
    """Strictly increasing above `LEGACY_FENCING_TOKEN`, as the real allocator is."""

    def __init__(self) -> None:
        self.issued: list[int] = []
        self._next = LEGACY_FENCING_TOKEN

    async def allocate(self, *, scope: str, floor: int = 0) -> int:
        del scope
        self._next = max(self._next, floor) + 1
        self.issued.append(self._next)
        return self._next


class FakeDriver:
    def __init__(self, tx: FakeTransaction) -> None:
        self._tx = tx

    def session(self, *, database: str | None = None) -> FakeSession:
        del database
        return FakeSession(self._tx)


class FakeRuns:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}

    async def insert_one(self, document: dict[str, Any]) -> None:
        self.documents[document["_id"]] = dict(document)

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> None:
        self.documents[query["_id"]].update(update["$set"])

    async def create_index(self, *args: Any, **kwargs: Any) -> None:
        return None


class _FakeSecret:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


class FakeReleases:
    """A published release that resolves to the schema the test built.

    `GraphSyncService.sync` calls `refresh_schema` before it reads anything, and
    this fake had no `_releases` and no `dynamic_knowledge_schema_path` -- both
    tests below died in `refresh_schema` with an `AttributeError` long before
    reaching the pipeline they exist to exercise. The fake was stale, not the
    service: `refresh_schema` was added after this module was written and nothing
    updated it.

    Returning the interim schema (rather than `None`, which falls through to
    loading the shipped YAML) keeps the assertions below meaningful. The shipped
    descriptor names its sources `source_sales`/`source_customers`; this module
    is deliberately written against `build_interim_active_schema`'s
    `sales_inventory`/`customer_outbound`, and silently swapping one for the
    other would make the run counts below assert nothing.
    """

    def __init__(self, schema: Any) -> None:
        self._schema = schema

    async def active(self) -> Any:
        return self._schema


class FakeCheckpoints:
    """A full run must never read or write checkpoints; only an incremental one
    does. Raising keeps that a fact rather than an assumption -- the coordinator
    is now handed a real store in production, so "full_sync does not checkpoint"
    is worth continuing to enforce somewhere."""

    async def read(self, **kwargs: Any) -> None:
        raise AssertionError("full_sync must not read checkpoints")

    async def write(self, **kwargs: Any) -> None:
        raise AssertionError("full_sync must not write checkpoints")


class FakeSettings:
    dynamic_knowledge_schema_path = Path("config/dynamic_knowledge/active-schema.return-order.yaml")
    mongo_database = "platform"
    source_mongo_database = "returns_source"
    neo4j_database = "neo4j"
    graph_sync_batch_size = 500
    graph_sync_max_records = 10_000
    sqlserver_host = "sql.internal"
    sqlserver_port = 1433
    sqlserver_user = "u"
    sqlserver_password = _FakeSecret("p")
    sqlserver_database = "returns_platform"
    operation_timeout_seconds = 10.0
    contact_lookup_hmac_key = _FakeSecret("s" * 32)


def _service_with(tx: FakeTransaction, source_db: FakeDatabase) -> GraphSyncService:
    service = cast(Any, object.__new__(GraphSyncService))
    service._settings = FakeSettings()
    service._platform_db = FakeDatabase({}, name=FakeSettings.mongo_database)
    service._source_db = source_db
    service._driver = FakeDriver(tx)
    service._runs = FakeRuns()
    service._schema = build_interim_active_schema(
        configuration_release_id="release-1",
        configuration_checksum="a" * 64,
        approved_by="admin",
        approved_at=datetime(2026, 8, 7, tzinfo=UTC),
    )
    service._releases = FakeReleases(service._schema)
    service._checkpoints = FakeCheckpoints()
    service._writer = Neo4jDynamicGraphWriter(service._driver, database="neo4j")
    service._projector = GenericGraphProjector()
    # The blue/green half, assembled as production assembles it. The Neo4j-side
    # writer is the real one over the fake driver; the Mongo-side stores are
    # in-memory but keep their real semantics (a compare-and-swap that can lose,
    # a counter that only goes up).
    service._generation_writer = Neo4jGenerationWriter(service._driver, database="neo4j")
    service._snapshots = FakeSnapshotStore()
    service._rebuild_leases = _AlwaysGrantedRebuildLease()
    service._generation_leases = None
    service._fencing_tokens = FakeTokens()
    service._validator = _PassingValidator()
    service._owner_instance_id = "test-instance"
    return cast(GraphSyncService, service)


class _AlwaysGrantedRebuildLease:
    """The lease is contention control, not part of what these tests assert; its
    refusal path has its own coverage in `test_lifecycle_orchestrator.py`."""

    def __init__(self) -> None:
        self.released: list[str] = []

    async def acquire(
        self,
        *,
        snapshot_name: str,
        graph_generation_id: str,
        owner_instance_id: str,
        ttl_seconds: int,
    ) -> Any:
        del owner_instance_id, ttl_seconds
        return RebuildLease(
            lease_id=f"lease-{graph_generation_id}",
            snapshot_name=snapshot_name,
            graph_generation_id=graph_generation_id,
            owner_instance_id="test-instance",
            acquired_at=datetime(2026, 8, 7, tzinfo=UTC),
            expires_at=datetime(2026, 8, 7, 1, tzinfo=UTC),
        )

    async def release(self, *, snapshot_name: str, lease_id: str) -> None:
        del snapshot_name
        self.released.append(lease_id)


class _PassingValidator:
    """Records what it was asked to validate. Deep validation has its own tests
    (`test_generation_validation.py`) and its own real-Neo4j coverage; what
    matters here is that the cutover routes the *candidate* generation through
    it before the swap, never the one that is serving."""

    def __init__(self) -> None:
        self.validated: list[str] = []

    async def validate(self, *, schema: Any, graph_generation_id: str) -> Any:
        del schema
        self.validated.append(graph_generation_id)
        return GenerationValidationReport(graph_generation_id=graph_generation_id, findings=())


class _FailingValidator:
    def __init__(self) -> None:
        self.validated: list[str] = []

    async def validate(self, *, schema: Any, graph_generation_id: str) -> Any:
        del schema
        self.validated.append(graph_generation_id)
        return GenerationValidationReport(
            graph_generation_id=graph_generation_id,
            findings=(
                ValidationFinding(
                    check_id=ValidationCheckId.NODE_LABEL_POPULATED,
                    severity=ValidationSeverity.ERROR,
                    subject="ConfiguredAlpha",
                    observed_count=0,
                    detail="candidate projected nothing",
                ),
            ),
        )


@pytest.mark.asyncio
async def test_sync_mongodb_only_writes_customer_and_order_nodes_via_the_generic_pipeline() -> None:
    customer_document = {
        "_id": ObjectId(),
        "partyId": "900781",
        "customerId": None,
        "customerName": "Acme Plumbing",
        "phoneNumber": "555-0100",
        "email": None,
        "updatedAt": "2026-08-01T00:00:00Z",
        "accounts": [{"accountNumber": "232385"}],
    }
    order_document = {
        "_id": ObjectId(),
        "salesHdrEventData": {
            "orderId": "WE130468",
            "orderStatus": "SHIPPED",
            "sellWhseId": "12",
            "shipFromWhseId": "12",
            "srcSysCode": "ESO",
        },
        "salesHdr": {
            "salesHdrData": {"custId": "900781", "custName": "Acme Plumbing"},
            "shipping": {"shipViaCode": "GROUND"},
        },
        "updatedAt": "2026-08-01T00:00:00Z",
        "salesLines": [],
    }
    source_db = FakeDatabase(
        {
            "customerOutboundCDM": FakeMongoCollection([customer_document]),
            "salesInv": FakeMongoCollection([order_document]),
            "shipmentInfo": FakeMongoCollection([]),
            "lkpSearchProduct": FakeMongoCollection([]),
        }
    )
    tx = FakeTransaction(fence_matched=1)
    service = _service_with(tx, source_db)

    run = await service.sync(
        GraphSyncRequest(
            mode=GraphSyncScope.SOURCE_MONGODB, maxRecordsPerAsset=100, applySchema=True
        ),
        actor_id="test",
    )

    assert run.status == "COMPLETED"
    assert run.nodeWrites >= 2
    assert run.sourceCounts.get("customer_outbound") == 1
    assert run.sourceCounts.get("sales_inventory") == 1

    queries = [query for query, _ in tx.calls]
    assert any("MERGE (n:`Customer`" in q for q in queries)
    assert any("MERGE (n:`SalesOrder`" in q for q in queries)
    assert any("MERGE (n:`CustomerAccount`" in q for q in queries)
    assert any(q.startswith("MATCH (g:GraphGeneration") for q in queries)
    # CustomerAccount is configured with an ownership_policy (exploded from
    # accounts[]) -- proves reconcile_child_ownership actually runs end to end.
    assert any("MERGE (o:ProjectionOwnership" in q for q in queries)


@pytest.mark.asyncio
async def test_sync_records_failure_status_when_a_write_raises() -> None:
    source_db = FakeDatabase(
        {
            # `updatedAt` is the configured incremental cursor field. Without it
            # this document never reached the writer at all: the run died in
            # `capture_high_watermark` instead, and the blind `pytest.raises`
            # below swallowed the difference -- the test asserted FAILED status
            # for a failure that had nothing to do with a write.
            "customerOutboundCDM": FakeMongoCollection(
                [
                    {
                        "_id": ObjectId(),
                        "partyId": "1",
                        "customerName": "n",
                        "updatedAt": "2026-08-01T00:00:00Z",
                    }
                ]
            ),
            "salesInv": FakeMongoCollection([]),
            "shipmentInfo": FakeMongoCollection([]),
            "lkpSearchProduct": FakeMongoCollection([]),
        }
    )
    tx = FakeTransaction(fence_matched=0)  # fencing mismatch -> write raises
    service = _service_with(tx, source_db)

    # Named, not blind: a bare `Exception` here also passed if the fixture wiring
    # broke before the write was ever attempted, which is the one outcome that
    # would make the FAILED-status assertions below meaningless.
    with pytest.raises(GenerationFencingError):
        await service.sync(
            GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, applySchema=True),
            actor_id="test",
        )

    stored = service._runs.documents  # type: ignore[attr-defined]
    (run_document,) = stored.values()
    assert run_document["status"] == "FAILED"
    assert run_document["errorCode"]


class RecordingCheckpoints:
    """A checkpoint store that answers instead of raising, for the incremental
    branch. `FakeMongoCollection.find` ignores the query it is given, so this
    module cannot show that a stored cursor actually *narrows* a scan -- that is
    proved against real MongoDB in
    `tests/dynamic_knowledge/test_incremental_sync_real_infra.py`. What it can
    show, and what nothing else does, is that `GraphSyncService` reaches
    `incremental_sync` at all rather than quietly running a full scan under an
    incremental label."""

    def __init__(self) -> None:
        self.reads: list[tuple[str, str]] = []
        self.writes: list[tuple[str, str, int]] = []

    async def read(self, *, source_asset_id: str, graph_generation_id: str) -> None:
        self.reads.append((source_asset_id, graph_generation_id))
        return None

    async def write(
        self,
        *,
        source_asset_id: str,
        graph_generation_id: str,
        checkpoint: Any,
        fencing_token: int,
    ) -> None:
        del checkpoint
        self.writes.append((source_asset_id, graph_generation_id, fencing_token))


def _mongo_source_db() -> FakeDatabase:
    return FakeDatabase(
        {
            "customerOutboundCDM": FakeMongoCollection(
                [
                    {
                        "_id": ObjectId(),
                        "partyId": "1",
                        "customerName": "n",
                        "updatedAt": "2026-08-01T00:00:00Z",
                    }
                ]
            ),
            "salesInv": FakeMongoCollection([]),
            "shipmentInfo": FakeMongoCollection([]),
            "lkpSearchProduct": FakeMongoCollection([]),
        }
    )


@pytest.mark.asyncio
async def test_an_incremental_request_consults_the_checkpoint_store_and_says_so() -> None:
    """The wiring W2.8 was missing.

    `GraphSyncService` previously constructed the coordinator with a checkpoint
    store whose every method raised, and called `full_sync` unconditionally --
    `incremental_sync` was unreachable from the API, the console, or any
    scheduler. The `FakeCheckpoints` used by the full-scan tests above still
    raises, so this test would fail loudly if the incremental request fell
    through to `full_sync`.
    """
    checkpoints = RecordingCheckpoints()
    service = _service_with(FakeTransaction(fence_matched=1), _mongo_source_db())
    service._checkpoints = checkpoints  # type: ignore[attr-defined]

    run = await service.sync(
        GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, incremental=True, applySchema=False),
        actor_id="test",
    )

    assert run.status == "COMPLETED"
    assert run.recordScope == "INCREMENTAL"
    assert ("customer_outbound", LEGACY_GENERATION_ID) in checkpoints.reads
    # The one source with a document is the one that produces a page and so the
    # one that advances. An empty source has nothing to checkpoint.
    assert [source for source, _generation, _token in checkpoints.writes] == ["customer_outbound"]


@pytest.mark.asyncio
async def test_a_full_request_still_records_itself_as_a_full_scan() -> None:
    """`recordScope` defaults are not decorative: an operator reading the run
    list has to be able to tell the two apart, and every historical run in the
    ledger predates the field entirely."""
    service = _service_with(FakeTransaction(fence_matched=1), _mongo_source_db())

    run = await service.sync(
        GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, applySchema=False),
        actor_id="test",
    )

    assert run.recordScope == "FULL"
    assert run.skippedSources == []


@pytest.mark.asyncio
async def test_an_incremental_run_names_the_sources_it_could_not_resume() -> None:
    """A source with no `incremental_cursor_field` is skipped by the coordinator
    by design -- there is no position to resume from. Silently is the problem:
    an operator triggers an incremental run, sees COMPLETED, and never learns
    that one source has not synced since the last full scan.
    """
    checkpoints = RecordingCheckpoints()
    service = _service_with(FakeTransaction(fence_matched=1), _mongo_source_db())
    service._checkpoints = checkpoints  # type: ignore[attr-defined]
    schema = service._schema  # type: ignore[attr-defined]
    without_cursor = schema.sources["shipment_info"].model_copy(
        update={"incremental_cursor_field": None}
    )
    service._schema = schema.model_copy(  # type: ignore[attr-defined]
        update={"sources": {**schema.sources, "shipment_info": without_cursor}}
    )
    service._releases = FakeReleases(service._schema)  # type: ignore[attr-defined]

    run = await service.sync(
        GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, incremental=True, applySchema=False),
        actor_id="test",
    )

    assert run.skippedSources == ["shipment_info"]
    assert "shipment_info" not in [source for source, _generation in checkpoints.reads]


# --- GRAPH-01: the generation lifecycle carries production traffic ------------
#
# What this section can prove with fakes: which path a request takes, what the
# service resolves and claims, and that a failed candidate never moves the
# pointer. What it deliberately does NOT claim to prove is the cutover itself --
# the atomic swap, the drain and the retirement against real stores are in
# `tests/dynamic_knowledge/test_graph_sync_cutover_real_infra.py`, because a
# cutover proved by a mock is a cutover proved by nothing.


def _without_sqlserver_sources(service: Any) -> None:
    """Narrow the schema to its MongoDB half.

    `mode=FULL` means every configured source, and the interim schema declares a
    SQL Server one -- so a full request from this module would try to open a real
    `pymssql` connection. The mode is what selects the cutover path, not the
    connector mix, so removing the source keeps these tests about the lifecycle
    rather than about having a database. Real-infra coverage runs the full source
    set.
    """
    schema = service._schema
    mongo_only = {
        source_id: source
        for source_id, source in schema.sources.items()
        if source.connector_type is not ConnectorType.MSSQL
    }
    dropped = set(schema.sources) - set(mongo_only)
    entities = {
        entity_id: entity
        for entity_id, entity in schema.entities.items()
        if entity.source_asset_id not in dropped
    }
    nodes = {
        projection_id: node
        for projection_id, node in schema.graph.nodes.items()
        if node.entity_id in entities
    }
    relationships = {
        relationship_id: relationship
        for relationship_id, relationship in schema.graph.relationships.items()
        if relationship.source_entity_id in entities and relationship.target_entity_id in entities
    }
    service._schema = schema.model_copy(
        update={
            "sources": mongo_only,
            "entities": entities,
            "graph": schema.graph.model_copy(
                update={"nodes": nodes, "relationships": relationships}
            ),
        }
    )
    service._releases = FakeReleases(service._schema)


def _generation_ids_written(tx: FakeTransaction) -> set[str]:
    """Every generation a node MERGE/MATCH was scoped to.

    `compile_node_writes` puts the generation in `$generationId` on every
    statement, so this is the direct answer to "which generation did this run
    actually touch" -- not an inference from the run ledger, which is what the
    service reports about itself.
    """
    return {
        str(parameters["generationId"])
        for query, parameters in tx.calls
        if "generationId" in parameters and ("MERGE (n:" in query or "MATCH (n:" in query)
    }


@pytest.mark.asyncio
async def test_a_partial_resync_adopts_the_legacy_generation_instead_of_pinning_to_it() -> None:
    """The migration path off `legacy-live`, at the first run that needs it.

    The literal is still where an un-adopted deployment's data lives, so the run
    must write there -- but it must no longer be a permanent pin. Afterwards an
    ActiveRuntimeSnapshot exists naming that generation, which is what gives the
    next full sync a predecessor to cut over from, drain and retire rather than
    activating a new generation beside an orphaned graph.
    """
    tx = FakeTransaction(fence_matched=1)
    service = _service_with(tx, _mongo_source_db())

    run = await service.sync(
        GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, applySchema=False), actor_id="test"
    )

    assert run.status == "COMPLETED"
    assert run.graphGenerationId == LEGACY_GENERATION_ID
    assert _generation_ids_written(tx) == {LEGACY_GENERATION_ID}

    snapshot = await service._snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)  # type: ignore[attr-defined]
    assert snapshot is not None, "the legacy generation must be adopted, not merely written to"
    assert snapshot.graph_generation_id == LEGACY_GENERATION_ID
    assert snapshot.activation_version == 1


@pytest.mark.asyncio
async def test_adoption_replaces_the_constant_fencing_token_on_the_marker() -> None:
    """The token stops being `1`, which is the whole reason it fenced nothing.

    The marker is created with the legacy constant, exactly as an existing
    deployment's already is. Adoption then claims an allocated token, so from
    that moment a writer still presenting the constant no longer matches the
    marker -- the equality fence in `compile_generation_fence` starts rejecting
    it. Every token this run uses must be above the constant, or nothing has
    actually changed.
    """
    tx = FakeTransaction(fence_matched=1)
    service = _service_with(tx, _mongo_source_db())

    await service.sync(
        GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, applySchema=False), actor_id="test"
    )

    assert tx.marker_token is not None
    assert tx.marker_token > LEGACY_FENCING_TOKEN, "the marker still carries the constant token"
    issued = service._fencing_tokens.issued  # type: ignore[attr-defined]
    assert issued, "no fencing token was allocated at all"
    assert all(token > LEGACY_FENCING_TOKEN for token in issued)
    assert issued == sorted(issued) and len(set(issued)) == len(issued), "tokens must be monotonic"

    # And the run's writes carried the claimed token, not the constant.
    fence_tokens = {
        int(parameters["fencingToken"])
        for query, parameters in tx.calls
        if "RETURN count(g) AS matched" in query
    }
    assert fence_tokens and LEGACY_FENCING_TOKEN not in fence_tokens


@pytest.mark.asyncio
async def test_a_full_sync_builds_a_new_generation_instead_of_rebuilding_in_place() -> None:
    """The defect this task exists to remove.

    A full, non-incremental sync used to re-derive the whole projection *into
    the live generation*, so a reader was free to observe a half-rebuilt graph.
    It must now build a new generation and swap. Two things prove the change and
    neither is the service's own report: the candidate is a different generation
    from the one that was serving, and no node write during the build was scoped
    to the serving one.
    """
    tx = FakeTransaction(fence_matched=1)
    service = _service_with(tx, _mongo_source_db())
    _without_sqlserver_sources(service)
    # Establish a serving generation first, the way a real deployment has one.
    await service.sync(
        GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, applySchema=False), actor_id="test"
    )
    serving = await service._snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)  # type: ignore[attr-defined]
    assert serving is not None
    tx.calls.clear()

    run = await service.sync(
        GraphSyncRequest(mode=GraphSyncScope.FULL, applySchema=False), actor_id="test"
    )

    assert run.status == "COMPLETED"
    assert run.graphGenerationId is not None
    assert run.graphGenerationId != serving.graph_generation_id, "this rebuilt in place"
    assert serving.graph_generation_id not in _generation_ids_written(tx), (
        "the build wrote into the generation that was serving; a reader could see it half-built"
    )

    # The pointer moved atomically, once, to the validated candidate.
    after = await service._snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)  # type: ignore[attr-defined]
    assert after is not None
    assert after.graph_generation_id == run.graphGenerationId
    assert after.activation_version == serving.activation_version + 1
    assert service._validator.validated == [run.graphGenerationId]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_failed_candidate_leaves_the_previous_generation_serving() -> None:
    """`If N+1 fails at any point, N stays active.`

    Validation is the stage forced here because it is the last one before the
    swap -- a candidate that fails later than this would be the hardest case to
    get right. The snapshot must not move, and the run must be recorded FAILED
    rather than reporting a cutover that did not happen.
    """
    tx = FakeTransaction(fence_matched=1)
    service = _service_with(tx, _mongo_source_db())
    _without_sqlserver_sources(service)
    await service.sync(
        GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, applySchema=False), actor_id="test"
    )
    serving = await service._snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)  # type: ignore[attr-defined]
    assert serving is not None
    service._validator = _FailingValidator()  # type: ignore[attr-defined]

    with pytest.raises(ActivationError) as caught:
        await service.sync(
            GraphSyncRequest(mode=GraphSyncScope.FULL, applySchema=False), actor_id="test"
        )

    assert caught.value.stage == "VALIDATE"
    after = await service._snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)  # type: ignore[attr-defined]
    assert after is not None
    assert after.graph_generation_id == serving.graph_generation_id, "N stopped serving"
    assert after.activation_version == serving.activation_version
    failed = [d for d in service._runs.documents.values() if d["status"] == "FAILED"]  # type: ignore[attr-defined]
    assert len(failed) == 1 and failed[0]["errorCode"]


@pytest.mark.asyncio
async def test_an_incremental_run_still_updates_the_serving_generation() -> None:
    """Not everything is a cutover, and turning incremental sync into one would
    make an operator resuming a few thousand records pay for a full re-projection
    of every source. An incremental pass keeps writing into what is serving --
    under a claimed token, not the constant."""
    checkpoints = RecordingCheckpoints()
    tx = FakeTransaction(fence_matched=1)
    service = _service_with(tx, _mongo_source_db())
    service._checkpoints = checkpoints  # type: ignore[attr-defined]

    run = await service.sync(
        GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, incremental=True, applySchema=False),
        actor_id="test",
    )

    snapshot = await service._snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)  # type: ignore[attr-defined]
    assert snapshot is not None
    assert run.graphGenerationId == snapshot.graph_generation_id
    assert [token for _s, _g, token in checkpoints.writes] == [tx.marker_token]
    assert tx.marker_token is not None and tx.marker_token > LEGACY_FENCING_TOKEN


@pytest.mark.asyncio
async def test_the_watermark_is_still_captured_before_any_scan_begins() -> None:
    """Regression guard, not a new property.

    `full_sync` captures every participating source's high watermark before any
    source's scan starts, and the run ledger's checkpoint semantics depend on
    it. The rebuild path now reaches `full_sync` through the orchestrator rather
    than directly, which is exactly the kind of change that could quietly move
    the capture -- so the ordering is asserted rather than assumed.
    """
    order: list[tuple[str, str]] = []

    class _OrderRecordingConnector:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def capabilities(self) -> Any:
            return self._inner.capabilities()

        def compare_cursors(self, **kwargs: Any) -> Any:
            return self._inner.compare_cursors(**kwargs)

        async def capture_high_watermark(self, *, source_asset_id: str) -> Any:
            order.append(("watermark", source_asset_id))
            return await self._inner.capture_high_watermark(source_asset_id=source_asset_id)

        async def scan(self, **kwargs: Any) -> Any:
            order.append(("scan", kwargs["source_asset_id"]))
            async for page in self._inner.scan(**kwargs):
                yield page

    tx = FakeTransaction(fence_matched=1)
    service = _service_with(tx, _mongo_source_db())
    inner_registry = scan_connector_registry
    resolved: dict[str, Any] = {}

    def _wrapping_registry(**kwargs: Any) -> Any:
        registry = inner_registry(**kwargs)
        original = registry.resolve

        def resolve(source_asset_id: str) -> Any:
            connector = resolved.get(source_asset_id)
            if connector is None:
                connector = _OrderRecordingConnector(original(source_asset_id))
                resolved[source_asset_id] = connector
            return connector

        registry.resolve = resolve  # type: ignore[method-assign]
        return registry

    with patch(
        "return_platform.data_platform.graph.sync_service.scan_connector_registry",
        _wrapping_registry,
    ):
        await service.sync(
            GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, applySchema=False),
            actor_id="test",
        )

    first_scan = next(index for index, (kind, _) in enumerate(order) if kind == "scan")
    watermarks_before = [source for kind, source in order[:first_scan] if kind == "watermark"]
    scanned = {source for kind, source in order if kind == "scan"}
    assert scanned, "the run scanned nothing, so this proves nothing"
    assert scanned <= set(watermarks_before), (
        "a source was scanned before every participating source's watermark was captured"
    )


# --- GRAPH-02: a classified change reaches the execution it earned ------------
#
# The classifier had no executor: `plan_migration` recorded a verdict and
# nothing acted on it. These prove each class lands on a different real path,
# and -- the part that would be easy to get wrong -- that the two cheap tiers do
# NOT go through the cutover just because they run in FULL mode.


def _plan(strategy: MigrationStrategy, **updates: Any) -> MigrationPlan:
    return MigrationPlan(
        from_release_id="release-1",
        to_release_id="release-2",
        strategy=strategy,
        **updates,
    )


@pytest.mark.asyncio
async def test_a_destructive_plan_runs_the_generation_cutover() -> None:
    tx = FakeTransaction(fence_matched=1)
    service = _service_with(tx, _mongo_source_db())
    _without_sqlserver_sources(service)
    await service.sync(
        GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, applySchema=False), actor_id="setup"
    )
    serving = await service._snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)  # type: ignore[attr-defined]
    assert serving is not None

    run = await service.apply_migration_plan(
        _plan(MigrationStrategy.FULL_REBUILD, change_class=SchemaChangeClass.DESTRUCTIVE),
        actor_id="operator",
    )

    assert run is not None
    assert run.graphGenerationId != serving.graph_generation_id, "no cutover happened"
    after = await service._snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)  # type: ignore[attr-defined]
    assert after is not None and after.activation_version == serving.activation_version + 1


@pytest.mark.asyncio
async def test_a_compatible_plan_resyncs_only_the_affected_sources_in_place() -> None:
    """The tier that did not exist. It must correct values inside the generation
    that is serving -- not cut over, and not touch sources the change never
    reached."""
    tx = FakeTransaction(fence_matched=1)
    service = _service_with(tx, _mongo_source_db())
    _without_sqlserver_sources(service)
    await service.sync(
        GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, applySchema=False), actor_id="setup"
    )
    serving = await service._snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)  # type: ignore[attr-defined]
    assert serving is not None

    run = await service.apply_migration_plan(
        _plan(
            MigrationStrategy.AFFECTED_SCOPE_RESYNC,
            change_class=SchemaChangeClass.COMPATIBLE,
            affected_source_asset_ids=("customer_outbound",),
        ),
        actor_id="operator",
    )

    assert run is not None
    assert run.graphGenerationId == serving.graph_generation_id, "a resync must not cut over"
    after = await service._snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)  # type: ignore[attr-defined]
    assert after is not None and after.activation_version == serving.activation_version
    # Only the named source was read.
    assert set(run.sourceCounts) <= {"customer_outbound"}


@pytest.mark.asyncio
async def test_an_additive_plan_backfills_the_affected_sources() -> None:
    tx = FakeTransaction(fence_matched=1)
    service = _service_with(tx, _mongo_source_db())
    _without_sqlserver_sources(service)
    await service.sync(
        GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, applySchema=False), actor_id="setup"
    )
    serving = await service._snapshots.read(snapshot_name=DEFAULT_SNAPSHOT_NAME)  # type: ignore[attr-defined]
    assert serving is not None

    run = await service.apply_migration_plan(
        _plan(
            MigrationStrategy.BACKFILL,
            change_class=SchemaChangeClass.ADDITIVE,
            affected_source_asset_ids=("customer_outbound",),
        ),
        actor_id="operator",
    )

    assert run is not None
    assert run.graphGenerationId == serving.graph_generation_id
    # A backfill is a *full* scan of a narrow scope, never an incremental pass:
    # the records that need the new property are exactly the ones that did not
    # change, and a checkpointed run would skip every one of them.
    assert run.recordScope == "FULL"


@pytest.mark.asyncio
async def test_a_no_change_plan_runs_nothing_at_all() -> None:
    """An empty run in the ledger would be a migration an operator has to explain."""
    service = _service_with(FakeTransaction(fence_matched=1), _mongo_source_db())

    assert (
        await service.apply_migration_plan(
            _plan(MigrationStrategy.NO_CHANGE, change_class=SchemaChangeClass.NONE), actor_id="op"
        )
        is None
    )
    assert service._runs.documents == {}  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_legacy_incremental_plan_is_not_re_judged() -> None:
    """INCREMENTAL only appears on plans recorded before the change classes
    existed. Deriving a strategy for one now would be inventing a judgement for
    a migration that already happened."""
    service = _service_with(FakeTransaction(fence_matched=1), _mongo_source_db())

    assert (
        await service.apply_migration_plan(_plan(MigrationStrategy.INCREMENTAL), actor_id="op")
        is None
    )


@pytest.mark.asyncio
async def test_a_cheap_plan_with_no_scope_is_refused_rather_than_run_empty() -> None:
    """Scope is what makes the cheap tiers cheap. Without it the run would read
    nothing, complete green, and report a migration as performed."""
    service = _service_with(FakeTransaction(fence_matched=1), _mongo_source_db())

    with pytest.raises(ValueError, match="names no affected sources"):
        await service.apply_migration_plan(
            _plan(
                MigrationStrategy.AFFECTED_SCOPE_RESYNC,
                change_class=SchemaChangeClass.COMPATIBLE,
            ),
            actor_id="op",
        )
