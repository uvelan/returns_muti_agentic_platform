"""Real MongoDB tests proving MongoGraphStateProvider's legacy fallback agrees
with the id data_platform.graph.sync_service actually marks in Neo4j.

Before this fix, the fallback returned f"legacy-{checksum[:20]}" while
sync_service.py's Neo4j GraphGeneration marker used the fixed literal
"legacy-live" -- these never matched, so any on-demand-sync write against a
not-yet-generation-managed schema would fence-reject against a marker that
doesn't exist under the id the reader actually pinned.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.data_platform.graph.sync_service import _LEGACY_GENERATION_ID
from return_platform.dynamic_knowledge.graph.generation import LEGACY_GENERATION_ID
from return_platform.dynamic_knowledge.integration.mongo_store import MongoGraphStateProvider
from return_platform.dynamic_knowledge.schema import ActiveSchema


def test_sync_service_and_generation_module_agree_on_the_legacy_id() -> None:
    assert _LEGACY_GENERATION_ID == LEGACY_GENERATION_ID == "legacy-live"


@pytest.mark.asyncio
async def test_active_generation_falls_back_to_the_shared_legacy_id_when_unset(
    active_schema: ActiveSchema, test_settings: Settings
) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    database_name = "return_platform"
    collection_name = f"dynamic_graph_generations_test_{uuid.uuid4().hex[:12]}"
    try:
        provider = MongoGraphStateProvider(client, database_name, collection=collection_name)
        # No document exists for this schema_version -- must fall back, not raise.
        generation_id = await provider.active_generation(active_schema)
        assert generation_id == LEGACY_GENERATION_ID
        assert generation_id == "legacy-live"
    finally:
        await client[database_name][collection_name].drop()
        await client.close()


@pytest.mark.asyncio
async def test_active_generation_prefers_a_real_document_over_the_fallback(
    active_schema: ActiveSchema, test_settings: Settings
) -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    database_name = "return_platform"
    collection_name = f"dynamic_graph_generations_test_{uuid.uuid4().hex[:12]}"
    try:
        await client[database_name][collection_name].insert_one(
            {
                "_id": "real-generation-1",
                "generationId": "real-generation-1",
                "status": "ACTIVE",
                "schemaVersion": active_schema.schema_version,
                "activatedAt": datetime.now(UTC),
            }
        )
        provider = MongoGraphStateProvider(client, database_name, collection=collection_name)
        generation_id = await provider.active_generation(active_schema)
        assert generation_id == "real-generation-1"
    finally:
        await client[database_name][collection_name].drop()
        await client.close()
