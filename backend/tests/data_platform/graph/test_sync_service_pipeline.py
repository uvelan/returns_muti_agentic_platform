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

import pytest
from bson import ObjectId

from return_platform.data_platform.graph.interim_active_schema import build_interim_active_schema
from return_platform.data_platform.graph.sync_service import (
    GraphSyncRequest,
    GraphSyncScope,
    GraphSyncService,
)
from return_platform.dynamic_knowledge.graph.generation import LEGACY_GENERATION_ID
from return_platform.dynamic_knowledge.graph.neo4j_writer import (
    GenerationFencingError,
    Neo4jDynamicGraphWriter,
)
from return_platform.dynamic_knowledge.graph.projector import GenericGraphProjector


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
    def __init__(self, *, fence_matched: int) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._fence_matched = fence_matched

    async def run(
        self, query: str, parameters: dict[str, Any] | None = None, **kwargs: Any
    ) -> FakeResult:
        self.calls.append((query, parameters or kwargs))
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

    async def run(self, query: str, **kwargs: Any) -> FakeResult:
        return await self._tx.run(query, kwargs)


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
    return cast(GraphSyncService, service)


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
