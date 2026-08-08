"""MongoDB-backed atomic conversation and graph-generation state adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import DESCENDING, AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from return_platform.dynamic_knowledge.graph.generation import LEGACY_GENERATION_ID
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    SyncReceipt,
    SyncReservation,
    SyncStatus,
)
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
        # Existing branch graphs predate generation records. Pin to the same literal
        # data_platform.graph.sync_service uses for its Neo4j GraphGeneration marker
        # -- any other value here would resolve a generation id with no matching
        # marker, and on-demand sync's write-side fencing check would reject every
        # write until the first real blue/green rebuild creates a durable generation.
        return LEGACY_GENERATION_ID


def _receipt_from_document(document: dict[str, Any]) -> SyncReceipt:
    return SyncReceipt(
        sync_request_id=document["syncRequestId"],
        request_digest=document["requestDigest"],
        status=SyncStatus(document["status"]),
        schema_version=document["schemaVersion"],
        graph_generation_id=document["graphGenerationId"],
        source_rows_read=document.get("sourceRowsRead", 0),
        dependency_rows_read=document.get("dependencyRowsRead", 0),
        nodes_written=document.get("nodesWritten", 0),
        relationships_written=document.get("relationshipsWritten", 0),
        error_code=document.get("errorCode"),
    )


class MongoOnDemandSyncStore:
    """Durable idempotency store for on-demand targeted-sync requests, keyed by
    request_digest -- the canonical dedup key a repeated identical targeted-read
    request naturally produces (see fingerprint.on_demand_request_digest).
    Mirrors MongoAtomicConversationStore's optimistic-insert-or-conflict shape;
    on-demand sync's own request_digest is a natural idempotency key the way a
    conversation's compare-and-set version isn't, so no expected_version concept
    is needed here."""

    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        database: str,
        *,
        collection: str = "dynamic_order_agent_on_demand_sync",
    ) -> None:
        self._collection = client[database][collection]

    async def ensure_indexes(self) -> None:
        await self._collection.create_index("createdAt")

    async def reserve(
        self,
        *,
        request_digest: str,
        proposed_request_id: str,
        schema_version: str,
        graph_generation_id: str,
    ) -> SyncReservation:
        now = datetime.now(UTC)
        document = {
            "_id": request_digest,
            "syncRequestId": proposed_request_id,
            "requestDigest": request_digest,
            "status": SyncStatus.RUNNING.value,
            "schemaVersion": schema_version,
            "graphGenerationId": graph_generation_id,
            "sourceRowsRead": 0,
            "dependencyRowsRead": 0,
            "nodesWritten": 0,
            "relationshipsWritten": 0,
            "errorCode": None,
            "createdAt": now,
            "updatedAt": now,
        }
        try:
            await self._collection.insert_one(document)
            return SyncReservation(acquired=True, sync_request_id=proposed_request_id)
        except DuplicateKeyError:
            existing = await self._collection.find_one({"_id": request_digest})
            assert existing is not None  # the insert above just lost this exact race
            if SyncStatus(str(existing["status"])) is SyncStatus.RUNNING:
                # Still in flight -- no receipt to hand back yet; the caller must
                # not blindly re-act (matches OnDemandSyncCoordinator's own
                # ON_DEMAND_SYNC_ALREADY_RUNNING handling for this exact case).
                return SyncReservation(acquired=False, sync_request_id=existing["syncRequestId"])
            return SyncReservation(
                acquired=False,
                sync_request_id=existing["syncRequestId"],
                existing_receipt=_receipt_from_document(existing),
            )

    async def complete(self, receipt: SyncReceipt) -> None:
        await self._collection.update_one(
            {"_id": receipt.request_digest},
            {
                "$set": {
                    "status": receipt.status.value,
                    "sourceRowsRead": receipt.source_rows_read,
                    "dependencyRowsRead": receipt.dependency_rows_read,
                    "nodesWritten": receipt.nodes_written,
                    "relationshipsWritten": receipt.relationships_written,
                    "errorCode": receipt.error_code,
                    "updatedAt": datetime.now(UTC),
                }
            },
        )
