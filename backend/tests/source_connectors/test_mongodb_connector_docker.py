"""Real MongoDB tests for source_connectors.mongodb -- no mocks.

Per the execution plan's "validate against Docker Mongo/SQL, not mocks" --
the existing tests/dynamic_knowledge/test_mongodb_connector.py tests are
fast, fake-based unit tests for edge cases; these are the first real-infra
proof that the connector's queries are actually correct against a real
MongoDB replica set, including the new targeted_read()/fetch_one()/
find_many()/sample_documents() primitives built in Phase 8 / Wave C1.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from bson import ObjectId
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.on_demand_sync.planner import build_targeted_read_plan
from return_platform.source_connectors.mongodb import (
    MongoDBSourceScanConnector,
    fetch_one,
    find_many,
    sample_documents,
)
from tests.source_connectors._schema_fixture import build_active_schema

MongoFixture = tuple[AsyncDatabase[dict[str, Any]], str]


@pytest_asyncio.fixture
async def mongo_collection(test_settings: Settings) -> AsyncIterator[MongoFixture]:
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    database = client["return_platform"]
    collection_name = f"source_connectors_test_{uuid.uuid4().hex[:12]}"
    collection = database[collection_name]
    await collection.insert_many(
        [
            {
                "_id": "row-1",
                "configured_id": "A-1",
                "configured_changed_at": datetime(2026, 1, 1, tzinfo=UTC),
            },
            {
                "_id": "row-2",
                "configured_id": "A-2",
                "configured_changed_at": datetime(2026, 1, 2, tzinfo=UTC),
            },
            {
                "_id": "row-3",
                "configured_id": "A-3",
                "configured_changed_at": datetime(2026, 1, 3, tzinfo=UTC),
            },
        ]
    )
    try:
        yield database, collection_name
    finally:
        await collection.drop()
        await client.close()


def _configured_id(document: Mapping[str, Any] | None) -> Any:
    assert document is not None
    return document["configured_id"]


@pytest.mark.asyncio
async def test_scan_reads_real_documents_ordered_by_cursor_field(
    mongo_collection: MongoFixture,
) -> None:
    database, collection_name = mongo_collection
    schema = build_active_schema(
        mongo_collection=collection_name, sql_table="unused", sql_schema="dbo"
    )
    connector = MongoDBSourceScanConnector(database, schema=schema)
    watermark = await connector.capture_high_watermark(source_asset_id="source_mongo")
    pages = [
        page
        async for page in connector.scan(
            schema=schema, source_asset_id="source_mongo", after=None, through=watermark
        )
    ]
    identities = [_configured_id(doc.document) for page in pages for doc in page.documents]
    assert identities == ["A-1", "A-2", "A-3"]


@pytest.mark.asyncio
async def test_targeted_read_returns_only_the_matching_document(
    mongo_collection: MongoFixture,
) -> None:
    database, collection_name = mongo_collection
    schema = build_active_schema(
        mongo_collection=collection_name, sql_table="unused", sql_schema="dbo"
    )
    connector = MongoDBSourceScanConnector(database, schema=schema)
    plan = build_targeted_read_plan(
        schema=schema, entity_id="entity_mongo", normalized_anchors={"id": ("EXACT", "A-2")}
    )
    page = await connector.targeted_read(schema=schema, plan=plan)
    assert len(page.documents) == 1
    assert _configured_id(page.documents[0].document) == "A-2"


@pytest.mark.asyncio
async def test_fetch_one_returns_exact_document_by_key(mongo_collection: MongoFixture) -> None:
    database, collection_name = mongo_collection
    document = await fetch_one(database, collection_name=collection_name, key={"_id": "row-2"})
    assert document is not None
    assert document["configured_id"] == "A-2"

    missing = await fetch_one(database, collection_name=collection_name, key={"_id": "row-404"})
    assert missing is None


@pytest.mark.asyncio
async def test_find_many_applies_an_or_filter(mongo_collection: MongoFixture) -> None:
    database, collection_name = mongo_collection
    documents = await find_many(
        database,
        collection_name=collection_name,
        filter={"$or": [{"configured_id": "A-1"}, {"configured_id": "A-3"}]},
        limit=10,
    )
    identities = sorted(document["configured_id"] for document in documents)
    assert identities == ["A-1", "A-3"]


@pytest.mark.asyncio
async def test_sample_documents_paginates_real_collection(mongo_collection: MongoFixture) -> None:
    database, collection_name = mongo_collection
    first_page = await sample_documents(
        database, collection_name=collection_name, offset=0, limit=2
    )
    second_page = await sample_documents(
        database, collection_name=collection_name, offset=2, limit=2
    )
    assert len(first_page) == 2
    assert len(second_page) == 1


@pytest.mark.asyncio
async def test_object_id_cursor_survives_a_real_round_trip(mongo_collection: MongoFixture) -> None:
    """capture_high_watermark's ObjectId() cursor must genuinely round-trip
    through real Mongo query bounds, not just construct without raising."""

    database, collection_name = mongo_collection
    schema = build_active_schema(
        mongo_collection=collection_name, sql_table="unused", sql_schema="dbo"
    )
    raw = schema.model_dump(mode="json")
    raw["sources"]["source_mongo"]["incremental_cursor_field"] = None
    schema = schema.model_validate(raw)
    await database[collection_name].insert_one({"_id": ObjectId(), "configured_id": "A-oid"})
    connector = MongoDBSourceScanConnector(database, schema=schema)
    watermark = await connector.capture_high_watermark(source_asset_id="source_mongo")
    pages = [
        page
        async for page in connector.scan(
            schema=schema, source_asset_id="source_mongo", after=None, through=watermark
        )
    ]
    identities = {_configured_id(doc.document) for page in pages for doc in page.documents}
    assert "A-oid" in identities
