"""MongoDB-backed atomic conversation and graph-generation state adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from return_platform.dynamic_knowledge.graph.generation import LEGACY_GENERATION_ID
from return_platform.dynamic_knowledge.on_demand_sync.contracts import (
    SyncReceipt,
    SyncReservation,
    SyncStatus,
)
from return_platform.dynamic_knowledge.order_agent.conversation_repository import (
    ConversationScope,
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
        # Compound and ordered to serve the scoped history query directly:
        # equality on the two owner fields, then the sort field.
        await self._collection.create_index(
            [("tenantId", ASCENDING), ("principalId", ASCENDING), ("updatedAt", DESCENDING)]
        )
        await self._collection.create_index("graphGenerationId")

    async def read(
        self, conversation_id: str, *, scope: ConversationScope
    ) -> dict[str, Any] | None:
        """One conversation, or None if it is not this principal's.

        The scope is part of the *filter*, not a check applied to the result.
        A conversation id is a client-generated UUID that travels in URLs, so
        "look it up, then compare the owner" is one forgotten comparison away
        from an IDOR; a query that cannot match another owner's document has no
        such branch to forget.
        """
        document = await self._collection.find_one({"_id": conversation_id, **scope.filter()})
        return dict(document) if document is not None else None

    async def list_recent(
        self, *, scope: ConversationScope, limit: int = 30
    ) -> list[dict[str, Any]]:
        """Recent conversations for one principal, newest first.

        Projects the summary fields only. `turns` holds a full AgentTurnResult
        per turn -- every statement and every evidence row -- and loading all of
        that to render a list of titles would move megabytes to display a few
        lines. The transcript's first message is the title, so `state.transcript`
        is the one nested field worth fetching.

        This used to be `find({})`. Transcripts carry customer names, addresses
        and phone numbers, so an unfiltered history list handed every associate's
        conversations to every caller.
        """
        cursor = (
            self._collection.find(
                scope.filter(),
                {
                    "_id": 1,
                    "version": 1,
                    "updatedAt": 1,
                    "graphGenerationId": 1,
                    "state.transcript": 1,
                },
            )
            .sort("updatedAt", -1)
            .limit(limit)
        )
        return [dict(document) async for document in cursor]

    async def compare_and_set(
        self,
        *,
        conversation_id: str,
        expected_version: int,
        replacement: dict[str, Any],
        scope: ConversationScope,
    ) -> bool:
        """Commit a turn, stamping ownership on create and enforcing it on update.

        The owner fields are written here rather than by the caller so a document
        cannot reach the collection unstamped: an unstamped document matches no
        scoped filter and would be invisible to the associate who created it.
        """
        candidate = {**replacement, "_id": conversation_id, **scope.filter()}
        if expected_version == 0:
            existing = await self._collection.find_one({"_id": conversation_id}, {"version": 1})
            if existing is None:
                try:
                    await self._collection.insert_one(candidate)
                    return True
                except DuplicateKeyError:
                    return False
        # The scope is in the filter, so another principal replaying a guessed id
        # and version matches nothing and is reported as a version conflict
        # rather than silently overwriting a conversation they cannot read.
        result = await self._collection.replace_one(
            {"_id": conversation_id, "version": expected_version, **scope.filter()},
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
        receipt_ttl_seconds: int = 900,
    ) -> None:
        self._collection = client[database][collection]
        self._receipt_ttl_seconds = receipt_ttl_seconds

    async def ensure_indexes(self) -> None:
        # Expiry belongs to Mongo, not to a sweeper: the record has to stop
        # answering even if nothing ever reads it again, and a TTL index is the
        # only thing that holds when no process is running.
        #
        # The digest for "sync this order key" is stable across conversations,
        # so a receipt that lived forever meant the second associate to ask
        # about an order was told the source had been checked when the check
        # had happened days earlier. Deduplicating a retry is the point;
        # deduplicating tomorrow is a stale answer wearing a success.
        await self._collection.create_index(
            "createdAt",
            expireAfterSeconds=self._receipt_ttl_seconds,
            name="ttl_created_at",
        )

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
