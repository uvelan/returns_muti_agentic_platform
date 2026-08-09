"""Structured reasoning-model action and turn contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from return_platform.dynamic_knowledge.knowledge.evidence import (
    QueryEvidence,
    StructuredAgentResponse,
)
from return_platform.dynamic_knowledge.knowledge.guards import StrongAnchorRequest
from return_platform.dynamic_knowledge.knowledge.query_plan import LogicalQueryPlan


class ActionType(StrEnum):
    GET_SCHEMA = "GET_SCHEMA"
    GRAPH_QUERY = "GRAPH_QUERY"
    ORDER_SEARCH = "ORDER_SEARCH"
    REQUEST_ON_DEMAND_SYNC = "REQUEST_ON_DEMAND_SYNC"
    CLARIFY = "CLARIFY"
    REPLAN = "REPLAN"
    RESPOND = "RESPOND"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class OrderSearchIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    orderIds: tuple[str, ...] = ()
    orderNumbers: tuple[str, ...] = ()
    customerNames: tuple[str, ...] = ()
    streetAddresses: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    postalCodes: tuple[str, ...] = ()
    dateFrom: str | None = None
    dateTo: str | None = None
    approximateDate: str | None = None
    skus: tuple[str, ...] = ()
    productNames: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    quantities: tuple[int, ...] = ()
    freeTextTerms: tuple[str, ...] = ()
    searchMode: str = "DISCOVER"
    confidence: float = 0.0
    wantsMoreResults: bool = False


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
    search_intent: OrderSearchIntent | None = None
    selected_candidate_id: str | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> AgentAction:
        requirements = {
            ActionType.GRAPH_QUERY: self.query_plan is not None,
            ActionType.ORDER_SEARCH: self.search_intent is not None,
            ActionType.REQUEST_ON_DEMAND_SYNC: (
                self.strong_anchor_request is not None and self.original_query_plan is not None
            ),
            ActionType.RESPOND: self.response is not None,
            ActionType.CLARIFY: (self.response is not None and bool(self.response.requested_input)),
            ActionType.REPLAN: True,
            ActionType.OUT_OF_SCOPE: True,
            ActionType.GET_SCHEMA: bool(self.schema_entity_ids),
        }
        if not requirements[self.action_type]:
            raise ValueError(f"missing payload for action type {self.action_type.value}")
        if (
            self.query_plan is not None
            and self.query_plan.candidate_set_id is not None
            and not self.selected_candidate_id
        ):
            raise ValueError(
                "query_plan references a candidate_set_id but selected_candidate_id is missing"
            )
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
