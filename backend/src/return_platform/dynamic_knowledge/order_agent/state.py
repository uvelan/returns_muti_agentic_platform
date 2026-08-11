"""Immutable candidate sets and graph-generation-pinned conversation facts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict

from return_platform.dynamic_knowledge.fingerprint import sha256_digest

#: How many prior messages travel with a conversation.
#:
#: Bounded on purpose. The transcript rides in every checkpoint write and every
#: prompt, so an unbounded one grows the reasoning payload without limit and
#: eventually trips the gateway's input cap mid-conversation -- a failure that
#: would appear only in long sessions, which are exactly the ones where the
#: history matters most. Twelve covers the clarify-and-narrow exchanges this
#: agent actually has.
TRANSCRIPT_LIMIT = 12


class CandidateSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_set_id: str
    conversation_id: str
    turn_id: str
    principal_id: str
    tenant_id: str
    schema_version: str
    graph_generation_id: str
    query_execution_id: str
    candidate_ids: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    checksum: str

    @classmethod
    def create(
        cls,
        *,
        candidate_set_id: str,
        conversation_id: str,
        turn_id: str,
        principal_id: str,
        tenant_id: str,
        schema_version: str,
        graph_generation_id: str,
        query_execution_id: str,
        candidate_ids: tuple[str, ...],
        created_at: datetime,
        expires_at: datetime,
    ) -> CandidateSet:
        payload = {
            "candidate_set_id": candidate_set_id,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "principal_id": principal_id,
            "tenant_id": tenant_id,
            "schema_version": schema_version,
            "graph_generation_id": graph_generation_id,
            "query_execution_id": query_execution_id,
            "candidate_ids": candidate_ids,
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        return cls(**payload, checksum=sha256_digest(payload))

    def validate_selection(
        self,
        *,
        candidate_id: str,
        conversation_id: str,
        principal_id: str,
        tenant_id: str,
        graph_generation_id: str,
        now: datetime,
    ) -> None:
        if (
            conversation_id != self.conversation_id
            or principal_id != self.principal_id
            or tenant_id != self.tenant_id
        ):
            raise ValueError("candidate set ownership mismatch")
        if graph_generation_id != self.graph_generation_id:
            raise ValueError("candidate set belongs to a stale graph generation")
        if now >= self.expires_at:
            raise ValueError("candidate set expired")
        if candidate_id not in self.candidate_ids:
            raise ValueError("candidate is not present in the immutable candidate set")


class OrderAgentGraphState(TypedDict, total=False):
    """One LangGraph checkpoint's state for one Order Discovery reasoning turn.

    Every field here is either a bounded, model-generated/derived value (`action`,
    `final_response` -- schema-constrained by AgentAction/StructuredAgentResponse,
    never a raw source document) or a reference/id/counter. Full `QueryEvidence`
    (including its raw `result`) is never stored here -- only `query_execution_id`
    values in `evidence_refs`, rehydrated on demand via QueryEvidenceStore. See
    ORDER_DISCOVERY_CHECKPOINT_ALLOWLIST, which this schema's keys must exactly
    match; a field added here without adding it there fails closed at every
    checkpoint write via CheckpointRedactor.enforce().
    """

    # Pinned identity, set once at graph input, never mutated.
    conversation_id: str
    client_turn_id: str
    user_message: str
    schema_version: str
    graph_generation_id: str
    configuration_release_id: str
    policy_version: str
    prompt_version: str
    agent_id: str
    run_id: str

    # Accumulated working state -- ids only, never raw business data.
    requested_schema_entity_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    order_search_cache: dict[str, Any] | None
    # {"question", "answer"} pairs from CLARIFY pauses resumed within this turn.
    # Both halves are conversation text of exactly the same sensitivity as
    # `user_message` (already checkpointed above): a model-generated question and
    # the associate's own reply. Bounded by the policy's max_clarifications.
    clarification_exchanges: tuple[dict[str, str], ...]
    # What was actually said on earlier turns, oldest first: {"role", "text"}
    # where role is "associate" or "agent".
    #
    # Without it the agent saw only the current message and the previous
    # search's cache, so it could not tell a first mention from a repeat, knew
    # nothing of what it had already asked, and would happily ask again. Same
    # sensitivity argument as `clarification_exchanges` above: it is the
    # associate's own words and the agent's own replies, both already
    # checkpointed in other fields. Bounded by TRANSCRIPT_LIMIT so neither the
    # checkpoint nor the prompt grows with conversation length.
    transcript: tuple[dict[str, str], ...]

    # Current in-flight model action.
    action: dict[str, Any] | None
    last_provider: str
    last_model: str
    capability_validated: bool

    # Budgets/counters.
    reasoning_steps_used: int
    queries_used: int
    correction_attempts: int
    clarifications_used: int
    replans_used: int
    targeted_syncs_used: int

    # The case this conversation confirmed an order against, once it has. Set
    # by `confirm_order` and carried for the rest of the turn so `respond` and
    # the committed AgentTurnResult can both see it. An id, not the case -- the
    # durable record lives in the case store and must not be duplicated into a
    # checkpoint that could then disagree with it.
    case_id: str | None

    # Terminal payload.
    final_response: dict[str, Any] | None


ORDER_DISCOVERY_CHECKPOINT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "conversation_id",
        "client_turn_id",
        "user_message",
        "schema_version",
        "graph_generation_id",
        "configuration_release_id",
        "policy_version",
        "prompt_version",
        "agent_id",
        "run_id",
        "requested_schema_entity_ids",
        "evidence_refs",
        "order_search_cache",
        "clarification_exchanges",
        "transcript",
        "action",
        "last_provider",
        "last_model",
        "capability_validated",
        "reasoning_steps_used",
        "queries_used",
        "correction_attempts",
        "clarifications_used",
        "replans_used",
        "targeted_syncs_used",
        # Safe to checkpoint: an opaque identifier the platform issued, with no
        # business data in it. Resuming a paused clarification must not forget
        # that the order was already confirmed.
        "case_id",
        "final_response",
    }
)
