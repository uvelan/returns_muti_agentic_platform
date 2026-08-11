"""Atomic conversation turn persistence over an internal-store document abstraction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from return_platform.dynamic_knowledge.fingerprint import sha256_digest
from return_platform.dynamic_knowledge.order_agent.contracts import (
    AgentTurnRequest,
    AgentTurnResult,
)


class ConversationSummary(BaseModel):
    """One row of the copilot's history list.

    `title` is the associate's opening message rather than a generated label:
    it is what they will recognise, it needs no model call, and it cannot drift
    from what the conversation was actually about.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    conversationId: str
    title: str
    messageCount: int
    updatedAt: datetime | None


class ConversationTranscript(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    conversationId: str
    conversationVersion: int
    messages: tuple[dict[str, str], ...]


class AtomicConversationDocumentStore(Protocol):
    async def read(self, conversation_id: str) -> dict[str, Any] | None: ...
    async def list_recent(self, *, limit: int = 30) -> list[dict[str, Any]]: ...
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

    async def list_recent(self, *, limit: int = 30) -> list[ConversationSummary]:
        """The history list. Conversations with nothing said in them are skipped:
        a turn that failed before the associate's message was recorded leaves a
        record with no transcript, and a blank row is not history."""
        summaries: list[ConversationSummary] = []
        for document in await self._store.list_recent(limit=limit):
            transcript = _transcript_of(document)
            if not transcript:
                continue
            updated = document.get("updatedAt")
            summaries.append(
                ConversationSummary(
                    conversationId=str(document["_id"]),
                    title=transcript[0]["text"],
                    messageCount=len(transcript),
                    updatedAt=updated if isinstance(updated, datetime) else None,
                )
            )
        return summaries

    async def read_transcript(self, conversation_id: str) -> ConversationTranscript | None:
        document = await self._store.read(conversation_id)
        if document is None:
            return None
        return ConversationTranscript(
            conversationId=conversation_id,
            conversationVersion=int(document.get("version", 0)),
            messages=_transcript_of(document),
        )

    async def commit_turn(
        self,
        *,
        request: AgentTurnRequest,
        expected_version: int,
        result: AgentTurnResult,
        conversation_state: dict[str, Any],
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
        state_to_persist = {
            key: value for key, value in conversation_state.items() if not key.startswith("_")
        }
        replacement = {
            **document,
            "version": expected_version + 1,
            "graphGenerationId": result.graph_generation_id,
            "turns": turns,
            "state": state_to_persist,
            "lastResponse": result.response.model_dump(mode="json"),
            # The store indexes `updatedAt` and nothing was setting it, so the
            # index sorted on a field that did not exist and "most recent
            # conversations" could not be answered at all.
            "updatedAt": datetime.now(UTC),
        }
        committed = await self._store.compare_and_set(
            conversation_id=request.conversation_id,
            expected_version=expected_version,
            replacement=replacement,
        )
        if not committed:
            raise ValueError("CONVERSATION_VERSION_CONFLICT")
        return result.model_copy(update={"conversation_version": expected_version + 1})


def _transcript_of(document: dict[str, Any]) -> tuple[dict[str, str], ...]:
    state = document.get("state")
    stored = state.get("transcript") if isinstance(state, dict) else None
    if not isinstance(stored, list):
        return ()
    return tuple(
        {"role": str(entry["role"]), "text": str(entry["text"])}
        for entry in stored
        if isinstance(entry, dict) and "role" in entry and "text" in entry
    )
