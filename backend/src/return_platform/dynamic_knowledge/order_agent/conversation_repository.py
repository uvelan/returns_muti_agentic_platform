"""Atomic conversation turn persistence over an internal-store document abstraction."""

from __future__ import annotations

from typing import Any, Protocol

from return_platform.dynamic_knowledge.fingerprint import sha256_digest
from return_platform.dynamic_knowledge.order_agent.contracts import (
    AgentTurnRequest,
    AgentTurnResult,
)


class AtomicConversationDocumentStore(Protocol):
    async def read(self, conversation_id: str) -> dict[str, Any] | None: ...
    async def compare_and_set(
        self,
        *,
        conversation_id: str,
        expected_version: int,
        replacement: dict[str, Any],
    ) -> bool: ...


class AtomicConversationRepository:
    """Provides turn idempotency and optimistic concurrency without process-local locks."""

    def __init__(self, store: AtomicConversationDocumentStore) -> None:
        self._store = store

    async def load_for_turn(
        self,
        *,
        request: AgentTurnRequest,
        graph_generation_id: str,
    ) -> tuple[int, dict[str, Any], AgentTurnResult | None]:
        document = await self._store.read(request.conversation_id)
        if document is None:
            document = {
                "conversationId": request.conversation_id,
                "version": 0,
                "graphGenerationId": graph_generation_id,
                "turns": {},
                "state": {},
            }
        digest = sha256_digest({"message": request.message, "messageId": request.message_id})
        existing = document.get("turns", {}).get(request.idempotency_key)
        if existing is not None:
            if existing["digest"] != digest:
                raise ValueError("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_MESSAGE")
            return (
                int(document["version"]),
                dict(document.get("state", {})),
                AgentTurnResult.model_validate(existing["result"]),
            )
        if document.get("graphGenerationId") not in {None, graph_generation_id}:
            document["state"] = {}
            document["graphGenerationId"] = graph_generation_id
        state = dict(document.get("state", {}))
        state["_pendingTurnDigest"] = digest
        return int(document["version"]), state, None

    async def commit_turn(
        self,
        *,
        request: AgentTurnRequest,
        expected_version: int,
        result: AgentTurnResult,
    ) -> AgentTurnResult:
        document = await self._store.read(request.conversation_id)
        if document is None:
            document = {
                "conversationId": request.conversation_id,
                "version": 0,
                "graphGenerationId": result.graph_generation_id,
                "turns": {},
                "state": {},
            }
        digest = sha256_digest({"message": request.message, "messageId": request.message_id})
        turns = dict(document.get("turns", {}))
        existing = turns.get(request.idempotency_key)
        if existing is not None:
            if existing["digest"] != digest:
                raise ValueError("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_MESSAGE")
            return AgentTurnResult.model_validate(existing["result"])
        turns[request.idempotency_key] = {
            "digest": digest,
            "clientTurnId": request.client_turn_id,
            "result": result.model_dump(mode="json"),
        }
        replacement = {
            **document,
            "version": expected_version + 1,
            "graphGenerationId": result.graph_generation_id,
            "turns": turns,
            "lastResponse": result.response.model_dump(mode="json"),
        }
        committed = await self._store.compare_and_set(
            conversation_id=request.conversation_id,
            expected_version=expected_version,
            replacement=replacement,
        )
        if not committed:
            raise ValueError("CONVERSATION_VERSION_CONFLICT")
        return result.model_copy(update={"conversation_version": expected_version + 1})
