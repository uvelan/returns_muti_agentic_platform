"""Structured reasoning-model action and turn contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from return_platform.dynamic_knowledge.knowledge.evidence import QueryEvidence, StructuredAgentResponse
from return_platform.dynamic_knowledge.knowledge.guards import StrongAnchorRequest
from return_platform.dynamic_knowledge.knowledge.query_plan import LogicalQueryPlan


class ActionType(StrEnum):
    GET_SCHEMA = "GET_SCHEMA"
    GRAPH_QUERY = "GRAPH_QUERY"
    REQUEST_ON_DEMAND_SYNC = "REQUEST_ON_DEMAND_SYNC"
    RESPOND = "RESPOND"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class AgentAction(BaseModel):
    """Only action shape accepted from the reasoning model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    business_capability: str
    action_type: ActionType
    decision_summary: str = Field(min_length=1, max_length=500)
    schema_entity_ids: tuple[str, ...] = ()
    query_plan: LogicalQueryPlan | None = None
    strong_anchor_request: StrongAnchorRequest | None = None
    original_query_plan: LogicalQueryPlan | None = None
    response: StructuredAgentResponse | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> AgentAction:
        requirements = {
            ActionType.GRAPH_QUERY: self.query_plan is not None,
            ActionType.REQUEST_ON_DEMAND_SYNC: (
                self.strong_anchor_request is not None and self.original_query_plan is not None
            ),
            ActionType.RESPOND: self.response is not None,
            ActionType.OUT_OF_SCOPE: True,
            ActionType.GET_SCHEMA: bool(self.schema_entity_ids),
        }
        if not requirements[self.action_type]:
            raise ValueError(f"missing payload for action type {self.action_type.value}")
        return self


class AgentTurnRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation_id: str
    expected_conversation_version: int = Field(ge=0)
    client_turn_id: str
    idempotency_key: str
    message_id: str
    message: str = Field(min_length=1, max_length=20_000)
    agent_id: str


class AgentTurnContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation_id: str
    client_turn_id: str
    user_message: str
    schema_version: str
    graph_generation_id: str
    configuration_release_id: str
    policy_version: str
    prompt_version: str
    compact_schema: dict[str, Any]
    conversation_state: dict[str, Any]
    query_evidence: tuple[QueryEvidence, ...] = ()
    schema_details: dict[str, Any] = Field(default_factory=dict)
    tool_failures: tuple[dict[str, Any], ...] = ()


class AgentTurnResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation_id: str
    conversation_version: int
    client_turn_id: str
    graph_generation_id: str
    response: StructuredAgentResponse
    query_evidence: tuple[QueryEvidence, ...]
    model_provider: str
    model_name: str


class ModelInvocationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: AgentAction
    provider: str
    model: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
