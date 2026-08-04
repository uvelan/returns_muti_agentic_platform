"""MongoDB-backed atomic conversation and graph-generation state adapters."""

from __future__ import annotations

from typing import Any

from pymongo import DESCENDING, AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from return_platform.dynamic_knowledge.schema import ActiveSchema


class MongoAtomicConversationStore:
    """Durable compare-and-set store for Order Agent conversations."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        database: str,
        *,
        collection: str = "dynamic_order_agent_conversations",
    ) -> None:
        self._collection = client[database][collection]

    async def ensure_indexes(self) -> None:
        await self._collection.create_index("updatedAt")
        await self._collection.create_index("graphGenerationId")

    async def read(self, conversation_id: str) -> dict[str, Any] | None:
        document = await self._collection.find_one({"_id": conversation_id})
        return dict(document) if document is not None else None

    async def compare_and_set(
        self,
        *,
        conversation_id: str,
        expected_version: int,
        replacement: dict[str, Any],
    ) -> bool:
        candidate = {**replacement, "_id": conversation_id}
        if expected_version == 0:
            existing = await self._collection.find_one({"_id": conversation_id}, {"version": 1})
            if existing is None:
                try:
                    await self._collection.insert_one(candidate)
                    return True
                except DuplicateKeyError:
                    return False
        result = await self._collection.replace_one(
            {"_id": conversation_id, "version": expected_version},
            candidate,
            upsert=False,
        )
        return result.modified_count == 1


class MongoGraphStateProvider:
    """Resolve the active graph generation, with a stable legacy-generation bridge."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        database: str,
        *,
        collection: str = "dynamic_graph_generations",
    ) -> None:
        self._collection = client[database][collection]

    async def ensure_indexes(self) -> None:
        await self._collection.create_index([("status", 1), ("activatedAt", DESCENDING)])

    async def active_generation(self, schema: ActiveSchema) -> str:
        document = await self._collection.find_one(
            {"status": "ACTIVE", "schemaVersion": schema.schema_version},
            sort=[("activatedAt", DESCENDING)],
        )
        if document is not None:
            value = document.get("generationId") or document.get("_id")
            if value:
                return str(value)
        # Existing branch graphs predate generation records. Pin them to the active
        # release checksum until the first fenced rebuild creates a durable generation.
        return f"legacy-{schema.configuration_checksum[:20]}"
