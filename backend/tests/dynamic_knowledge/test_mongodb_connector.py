from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from bson import ObjectId

from return_platform.dynamic_knowledge.connectors.mongodb import (
    MongoConnectorError,
    MongoDBSourceScanConnector,
    SeedPin,
)
from return_platform.dynamic_knowledge.on_demand_sync.contracts import CursorComparison, SourceCursor
from return_platform.dynamic_knowledge.schema import ActiveSchema


class FakeMongoCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, field: str, direction: int) -> "FakeMongoCursor":
        self._documents = sorted(
            self._documents, key=lambda d: d[field], reverse=direction < 0
        )
        return self

    def limit(self, count: int) -> "FakeMongoCursor":
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
        self.last_query: dict[str, Any] | None = None

    def find(self, query: dict[str, Any]) -> FakeMongoCursor:
        self.last_query = query
        selected = [doc for doc in self.documents if _matches(doc, query)]
        return FakeMongoCursor(selected)

    async def count_documents(self, query: dict[str, Any], **_kwargs: Any) -> int:
        return sum(1 for doc in self.documents if _matches(doc, query))


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, condition in query.items():
        value = document.get(key)
        if isinstance(condition, dict):
            if "$lte" in condition and not (value is not None and value <= condition["$lte"]):
                return False
            if "$gt" in condition and not (value is not None and value > condition["$gt"]):
                return False
            if "$ne" in condition and value == condition["$ne"]:
                return False
        elif value != condition:
            return False
    return True


class FakeDatabase:
    def __init__(self, collections: dict[str, FakeMongoCollection]) -> None:
        self._collections = collections

    def __getitem__(self, name: str) -> FakeMongoCollection:
        return self._collections[name]


def _mongo_source_schema(active_schema: ActiveSchema, *, cursor_field: str | None) -> ActiveSchema:
    raw = active_schema.model_dump(mode="json")
    raw["sources"]["source_a"]["object_ref"] = {"database": "db", "name": "objects"}
    raw["sources"]["source_a"]["incremental_cursor_field"] = cursor_field
    return ActiveSchema.model_validate(raw)


@pytest.mark.asyncio
async def test_capture_high_watermark_returns_an_object_id_cursor() -> None:
    connector = MongoDBSourceScanConnector(FakeDatabase({}))
    watermark = await connector.capture_high_watermark(source_asset_id="source_a")
    assert watermark.cursor_type == "OBJECT_ID"
    ObjectId(watermark.encoded_value)  # does not raise


def test_compare_cursors_orders_object_ids_by_value() -> None:
    connector = MongoDBSourceScanConnector(FakeDatabase({}))
    earlier = SourceCursor(cursor_type="OBJECT_ID", encoded_value=str(ObjectId(b"\x00" * 12)))
    later = SourceCursor(cursor_type="OBJECT_ID", encoded_value=str(ObjectId(b"\xff" * 12)))
    assert (
        connector.compare_cursors(source_asset_id="source_a", left=earlier, right=later)
        is CursorComparison.BEFORE
    )
    assert (
        connector.compare_cursors(source_asset_id="source_a", left=later, right=earlier)
        is CursorComparison.AFTER
    )
    assert (
        connector.compare_cursors(source_asset_id="source_a", left=earlier, right=earlier)
        is CursorComparison.EQUAL
    )


def test_compare_cursors_rejects_mismatched_types() -> None:
    connector = MongoDBSourceScanConnector(FakeDatabase({}))
    object_id_cursor = SourceCursor(cursor_type="OBJECT_ID", encoded_value=str(ObjectId()))
    field_cursor = SourceCursor(
        cursor_type="FIELD_DATETIME", encoded_value=datetime(2026, 8, 6, tzinfo=UTC).isoformat()
    )
    with pytest.raises(MongoConnectorError, match="different types"):
        connector.compare_cursors(source_asset_id="source_a", left=object_id_cursor, right=field_cursor)


@pytest.mark.asyncio
async def test_scan_without_cursor_field_bounds_by_object_id(active_schema: ActiveSchema) -> None:
    schema = _mongo_source_schema(active_schema, cursor_field=None)
    ids = [ObjectId(bytes([i]) * 12) for i in range(1, 4)]
    documents = [{"_id": oid, "configured_id": f"A-{index}"} for index, oid in enumerate(ids, start=1)]
    collection = FakeMongoCollection(documents)
    connector = MongoDBSourceScanConnector(FakeDatabase({"objects": collection}), page_size=10)

    through = SourceCursor(cursor_type="OBJECT_ID", encoded_value=str(ids[-1]))
    pages = [
        page
        async for page in connector.scan(
            schema=schema, source_asset_id="source_a", after=None, through=through
        )
    ]
    assert len(pages) == 1
    assert len(pages[0].documents) == 3
    assert pages[0].next_cursor is not None
    assert pages[0].next_cursor.encoded_value == str(ids[-1])


@pytest.mark.asyncio
async def test_scan_pages_at_the_configured_page_size(active_schema: ActiveSchema) -> None:
    schema = _mongo_source_schema(active_schema, cursor_field=None)
    ids = [ObjectId(bytes([i]) * 12) for i in range(1, 6)]
    documents = [{"_id": oid, "configured_id": f"A-{index}"} for index, oid in enumerate(ids, start=1)]
    collection = FakeMongoCollection(documents)
    connector = MongoDBSourceScanConnector(FakeDatabase({"objects": collection}), page_size=2)

    through = SourceCursor(cursor_type="OBJECT_ID", encoded_value=str(ids[-1]))
    pages = [
        page
        async for page in connector.scan(
            schema=schema, source_asset_id="source_a", after=None, through=through
        )
    ]
    assert [len(page.documents) for page in pages] == [2, 2, 1]


@pytest.mark.asyncio
async def test_scan_with_cursor_field_bounds_by_that_field(active_schema: ActiveSchema) -> None:
    schema = _mongo_source_schema(active_schema, cursor_field="changed_at")
    documents = [
        {"_id": ObjectId(), "configured_id": "A-1", "changed_at": datetime(2026, 1, 1, tzinfo=UTC)},
        {"_id": ObjectId(), "configured_id": "A-2", "changed_at": datetime(2026, 1, 2, tzinfo=UTC)},
        {"_id": ObjectId(), "configured_id": "A-3", "changed_at": datetime(2026, 1, 3, tzinfo=UTC)},
    ]
    collection = FakeMongoCollection(documents)
    connector = MongoDBSourceScanConnector(FakeDatabase({"objects": collection}), page_size=10)

    after = SourceCursor(
        cursor_type="FIELD_DATETIME", encoded_value=datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    )
    through = SourceCursor(
        cursor_type="FIELD_DATETIME", encoded_value=datetime(2026, 1, 3, tzinfo=UTC).isoformat()
    )
    pages = [
        page
        async for page in connector.scan(
            schema=schema, source_asset_id="source_a", after=after, through=through
        )
    ]
    assert len(pages) == 1
    identities = {doc.document["configured_id"] for doc in pages[0].documents}
    assert identities == {"A-2", "A-3"}


@pytest.mark.asyncio
async def test_scan_with_seed_pin_filters_to_matching_seed_and_ignores_cursor_bounds(
    active_schema: ActiveSchema,
) -> None:
    schema = _mongo_source_schema(active_schema, cursor_field=None)
    documents = [
        {
            "_id": ObjectId(),
            "configured_id": "A-1",
            "seedVersion": "v2",
            "seedDigest": "digest-v2",
        },
        {
            "_id": ObjectId(),
            "configured_id": "A-2",
            "seedVersion": "v1",
            "seedDigest": "digest-v1",
        },
    ]
    collection = FakeMongoCollection(documents)
    connector = MongoDBSourceScanConnector(
        FakeDatabase({"objects": collection}),
        page_size=10,
        seed_pins={"source_a": SeedPin(seed_version="v2", seed_digest="digest-v2")},
    )
    through = SourceCursor(cursor_type="OBJECT_ID", encoded_value=str(ObjectId(b"\xff" * 12)))
    pages = [
        page
        async for page in connector.scan(
            schema=schema, source_asset_id="source_a", after=None, through=through
        )
    ]
    assert len(pages) == 1
    identities = {doc.document["configured_id"] for doc in pages[0].documents}
    assert identities == {"A-1"}


@pytest.mark.asyncio
async def test_scan_truncates_to_max_records_per_source(active_schema: ActiveSchema) -> None:
    schema = _mongo_source_schema(active_schema, cursor_field=None)
    ids = [ObjectId(bytes([i]) * 12) for i in range(1, 6)]
    documents = [{"_id": oid, "configured_id": f"A-{index}"} for index, oid in enumerate(ids, start=1)]
    collection = FakeMongoCollection(documents)
    connector = MongoDBSourceScanConnector(
        FakeDatabase({"objects": collection}), page_size=10, max_records_per_source=2
    )
    through = SourceCursor(cursor_type="OBJECT_ID", encoded_value=str(ids[-1]))
    pages = [
        page
        async for page in connector.scan(
            schema=schema, source_asset_id="source_a", after=None, through=through
        )
    ]
    total = sum(len(page.documents) for page in pages)
    assert total == 2


@pytest.mark.asyncio
async def test_scan_with_seed_pin_ignores_max_records_per_source(active_schema: ActiveSchema) -> None:
    schema = _mongo_source_schema(active_schema, cursor_field=None)
    documents = [
        {"_id": ObjectId(), "configured_id": f"A-{i}", "seedVersion": "v2", "seedDigest": "digest-v2"}
        for i in range(5)
    ]
    collection = FakeMongoCollection(documents)
    connector = MongoDBSourceScanConnector(
        FakeDatabase({"objects": collection}),
        page_size=10,
        max_records_per_source=2,
        seed_pins={"source_a": SeedPin(seed_version="v2", seed_digest="digest-v2")},
    )
    through = SourceCursor(cursor_type="OBJECT_ID", encoded_value=str(ObjectId(b"\xff" * 12)))
    pages = [
        page
        async for page in connector.scan(
            schema=schema, source_asset_id="source_a", after=None, through=through
        )
    ]
    total = sum(len(page.documents) for page in pages)
    assert total == 5


@pytest.mark.asyncio
async def test_scan_with_seed_pin_fails_closed_on_digest_mismatch(
    active_schema: ActiveSchema,
) -> None:
    schema = _mongo_source_schema(active_schema, cursor_field=None)
    documents = [
        {"_id": ObjectId(), "configured_id": "A-1", "seedVersion": "v2", "seedDigest": "unexpected"},
    ]
    collection = FakeMongoCollection(documents)
    connector = MongoDBSourceScanConnector(
        FakeDatabase({"objects": collection}),
        page_size=10,
        seed_pins={"source_a": SeedPin(seed_version="v2", seed_digest="digest-v2")},
    )
    through = SourceCursor(cursor_type="OBJECT_ID", encoded_value=str(ObjectId(b"\xff" * 12)))
    with pytest.raises(MongoConnectorError, match="seed digest mismatch"):
        async for _ in connector.scan(
            schema=schema, source_asset_id="source_a", after=None, through=through
        ):
            pass
