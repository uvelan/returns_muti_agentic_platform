"""Atomic conversation turn persistence over an internal-store document abstraction."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from return_platform.dynamic_knowledge.fingerprint import sha256_digest
from return_platform.dynamic_knowledge.order_agent.contracts import (
    AgentTurnRequest,
    AgentTurnResult,
)

logger = logging.getLogger(__name__)


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
    """What was said, and what the agent had on screen while saying it.

    `lastResultTurn` is the most recent turn that put results in front of the
    associate. **A whole turn travels, rather than the rows,** because which of
    a turn's several searches it was speaking about is decided by the citations
    its own statements carry -- a rule the client already applies to a live
    turn, and one that would have to be written a second time here to send rows
    instead. Sending the turn is what makes a resumed screen the same screen.

    `None` is an ordinary answer: a conversation that never searched has no such
    turn.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    conversationId: str
    conversationVersion: int
    messages: tuple[dict[str, str], ...]
    lastResultTurn: AgentTurnResult | None = None


class ConversationScope(BaseModel):
    """Who a conversation belongs to.

    Carried as a value rather than as two loose strings so a call site cannot
    pass the arguments the wrong way round -- `(principal, tenant)` and
    `(tenant, principal)` are both two strings and only one is correct.

    Every store method takes one and puts it in the *query filter*. That is the
    whole of the isolation guarantee: there is no read path that fetches first
    and checks ownership second.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    principal_id: str

    def filter(self) -> dict[str, str]:
        """The owner predicate, in stored-document field names."""
        return {"tenantId": self.tenant_id, "principalId": self.principal_id}


class AtomicConversationDocumentStore(Protocol):
    async def read(
        self, conversation_id: str, *, scope: ConversationScope
    ) -> dict[str, Any] | None: ...
    async def list_recent(
        self, *, scope: ConversationScope, limit: int = 30
    ) -> list[dict[str, Any]]: ...
    async def compare_and_set(
        self,
        *,
        conversation_id: str,
        expected_version: int,
        replacement: dict[str, Any],
        scope: ConversationScope,
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
        scope: ConversationScope,
    ) -> tuple[int, dict[str, Any], AgentTurnResult | None]:
        document = await self._store.read(request.conversation_id, scope=scope)
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

    async def list_recent(
        self, *, scope: ConversationScope, limit: int = 30
    ) -> list[ConversationSummary]:
        """The history list, scoped to one principal. Conversations with nothing
        said in them are skipped: a turn that failed before the associate's
        message was recorded leaves a record with no transcript, and a blank row
        is not history."""
        summaries: list[ConversationSummary] = []
        for document in await self._store.list_recent(scope=scope, limit=limit):
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

    async def read_transcript(
        self, conversation_id: str, *, scope: ConversationScope
    ) -> ConversationTranscript | None:
        """None both when the conversation does not exist and when it is not
        this principal's -- the caller 404s either way, so a probe cannot
        distinguish "no such conversation" from "not yours"."""
        document = await self._store.read(conversation_id, scope=scope)
        if document is None:
            return None
        return ConversationTranscript(
            conversationId=conversation_id,
            conversationVersion=int(document.get("version", 0)),
            messages=_transcript_of(document),
            lastResultTurn=_last_result_turn(document),
        )

    async def commit_turn(
        self,
        *,
        request: AgentTurnRequest,
        expected_version: int,
        result: AgentTurnResult,
        conversation_state: dict[str, Any],
        scope: ConversationScope,
    ) -> AgentTurnResult:
        document = await self._store.read(request.conversation_id, scope=scope)
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
            scope=scope,
        )
        if not committed:
            raise ValueError("CONVERSATION_VERSION_CONFLICT")
        return result.model_copy(update={"conversation_version": expected_version + 1})


def _agent_text(result: dict[str, Any]) -> str:
    """One turn's reply as one line, joined the way the stored transcript joins it."""
    response = result.get("response")
    if not isinstance(response, dict):
        return ""
    statements = response.get("statements")
    if not isinstance(statements, list):
        return ""
    return " ".join(
        str(statement.get("text", "")) for statement in statements if isinstance(statement, dict)
    ).strip()


def _replies_in_order(document: dict[str, Any]) -> list[str]:
    """Each turn's reply, oldest first, from the per-turn record.

    `turns` is keyed by idempotency key, so the ordering comes from the
    `conversation_version` each result carries rather than from insertion order.
    """
    turns = document.get("turns")
    if not isinstance(turns, dict):
        return []
    results = [
        turn["result"]
        for turn in turns.values()
        if isinstance(turn, dict) and isinstance(turn.get("result"), dict)
    ]
    results.sort(key=lambda result: int(result.get("conversation_version") or 0))
    return [_agent_text(result) for result in results]


def _last_result_turn(document: dict[str, Any]) -> AgentTurnResult | None:
    """The most recent turn that put results in front of the associate.

    **Reopening a conversation used to lose the matches.** The transcript
    carried what was said and nothing that was shown, so a past search -- one
    that never reached a case, which is most of them -- came back with an empty
    results pane, and the associate had to run the search again to see rows the
    agent had already found and quoted in the message above them.

    Nothing was lost. Every turn's whole result is in `turns`, evidence and all,
    which is also where `_transcript_of` recovers the agent's replies from.

    **The most recent turn that carried evidence, not simply the most recent
    turn.** A turn that produces no results leaves the table where it is -- ask
    a clarifying question and the matches you are asking about stay on screen --
    so the last turn to carry any is the one whose results are still up.

    Turns are keyed by idempotency key, so the ordering comes from the
    `conversation_version` each result carries. A result that will not validate
    is skipped rather than raised on: a legacy turn is a reason to show an older
    table, never a reason to fail the read that serves the conversation itself.
    """
    turns = document.get("turns")
    if not isinstance(turns, dict):
        return None
    results = [
        turn["result"]
        for turn in turns.values()
        if isinstance(turn, dict) and isinstance(turn.get("result"), dict)
    ]
    results.sort(key=lambda result: int(result.get("conversation_version") or 0), reverse=True)
    for result in results:
        if not result.get("query_evidence"):
            continue
        try:
            return AgentTurnResult.model_validate(result)
        except ValidationError:
            logger.warning(
                "conversation_turn_unreadable",
                extra={
                    "conversationId": document.get("_id"),
                    "conversationVersion": result.get("conversation_version"),
                },
                exc_info=True,
            )
    return None


def _transcript_of(document: dict[str, Any]) -> tuple[dict[str, str], ...]:
    """What was said, rebuilt from the turns where the stored transcript is short.

    **A turn that paused on a question recorded the associate's message and not
    the agent's**, so reopening a conversation that had stopped on a question
    showed the associate their own words and no question at all -- while the
    workflow was still waiting for its answer. Fixed at the writer, but that only
    helps conversations from here on: every conversation already in the store is
    missing its questions, and the operator's history is exactly the thing this
    endpoint exists to serve.

    Nothing was lost, only mis-read. `turns` carries the whole `AgentTurnResult`
    per turn, and on a paused turn the `response` **is** the question. So the
    reply comes from there and the associate's side from the stored transcript,
    which has always recorded it.

    The two are zipped by position, which is safe because a conversation strictly
    alternates: one associate message, one reply. When the counts disagree --
    a transcript truncated to `TRANSCRIPT_LIMIT` while `turns` kept every turn,
    or a document written by a version that did neither -- the stored transcript
    is served unchanged rather than interleaved into a plausible-looking wrong
    order.
    """
    state = document.get("state")
    stored = state.get("transcript") if isinstance(state, dict) else None
    if not isinstance(stored, list):
        return ()
    entries = [
        {"role": str(entry["role"]), "text": str(entry["text"])}
        for entry in stored
        if isinstance(entry, dict) and "role" in entry and "text" in entry
    ]

    prompts = [entry for entry in entries if entry["role"] == "associate"]
    replies = _replies_in_order(document)
    if len(prompts) != len(replies):
        return tuple(entries)

    rebuilt: list[dict[str, str]] = []
    for prompt, reply in zip(prompts, replies, strict=True):
        rebuilt.append(prompt)
        if reply:
            rebuilt.append({"role": "agent", "text": reply})
    return tuple(rebuilt)
