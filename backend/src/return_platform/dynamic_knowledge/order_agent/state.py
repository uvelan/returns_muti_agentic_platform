"""Immutable candidate sets and graph-generation-pinned conversation facts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict

from return_platform.dynamic_knowledge.fingerprint import sha256_digest


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
        "final_response",
    }
)
