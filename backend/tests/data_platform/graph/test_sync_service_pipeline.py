"""End-to-end equivalence test: GraphSyncService.sync() driven entirely by fakes
(no live Mongo/Neo4j/SQL Server), proving the schema-driven pipeline that
replaced sync_service.py's hand-coded MERGE Cypher actually produces graph
writes for realistic customerOutboundCDM/salesInv-shaped documents -- the
same two collections the old hand-coded path read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from bson import ObjectId

from return_platform.data_platform.graph.interim_active_schema import build_interim_active_schema
from return_platform.data_platform.graph.sync_service import (
    GraphSyncRequest,
    GraphSyncScope,
    GraphSyncService,
)
from return_platform.dynamic_knowledge.graph.neo4j_writer import Neo4jDynamicGraphWriter
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
    def __init__(self, collections: dict[str, FakeMongoCollection]) -> None:
        self._collections = collections

    def __getitem__(self, name: str) -> FakeMongoCollection:
        return self._collections[name]


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


class FakeSettings:
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
    service._platform_db = None
    service._source_db = source_db
    service._driver = FakeDriver(tx)
    service._runs = FakeRuns()
    service._schema = build_interim_active_schema(
        configuration_release_id="release-1",
        configuration_checksum="a" * 64,
        approved_by="admin",
        approved_at=datetime(2026, 8, 7, tzinfo=UTC),
    )
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
            "customerOutboundCDM": FakeMongoCollection(
                [{"_id": ObjectId(), "partyId": "1", "customerName": "n"}]
            ),
            "salesInv": FakeMongoCollection([]),
            "shipmentInfo": FakeMongoCollection([]),
            "lkpSearchProduct": FakeMongoCollection([]),
        }
    )
    tx = FakeTransaction(fence_matched=0)  # fencing mismatch -> write raises
    service = _service_with(tx, source_db)

    with pytest.raises(Exception):
        await service.sync(
            GraphSyncRequest(mode=GraphSyncScope.SOURCE_MONGODB, applySchema=True),
            actor_id="test",
        )

    stored = service._runs.documents  # type: ignore[attr-defined]
    (run_document,) = stored.values()
    assert run_document["status"] == "FAILED"
    assert run_document["errorCode"]
