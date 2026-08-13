"""Structured reasoning-model action and turn contracts."""

from __future__ import annotations

from datetime import datetime
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
    # The transition from "an order was found" to "a return is being raised".
    # Discovery could previously only search and answer, so nothing turned a
    # conversation into a case -- the console inferred resolution from a
    # candidate list of length one and there was no durable record that the
    # associate had actually confirmed anything.
    CONFIRM_ORDER = "CONFIRM_ORDER"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class OrderSearchIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    orderIds: tuple[str, ...] = ()
    orderNumbers: tuple[str, ...] = ()
    customerNames: tuple[str, ...] = ()
    # The two the clarification policy ranks highest after the hard order
    # anchors -- email at 95, phone at 90 in `config/returns/production.yaml`.
    # They were absent here while the policy asked for them, and this model is
    # `extra="forbid"`, so the agent would ask an associate for the email on the
    # order and then have nowhere to put the answer: the reply was either
    # rejected as an unknown field or flattened into `freeTextTerms` and
    # searched against product descriptions.
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
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


class OrderConfirmation(BaseModel):
    """What the associate confirmed they are raising a return against.

    The order reference and the line set are both carried, and both are part of
    the idempotency key, because "this order" and "these lines of this order"
    are different confirmations: a partial return of two lines is not the same
    intent as a full return of five, and a retry of the first must not be
    mistaken for the second.

    `candidate_set_id` and `candidate_id` are what tie the confirmation to a
    search the agent actually ran. Without them a model could confirm an order
    it invented; with them the existing `CandidateSet.validate_selection` binds
    the choice to this conversation, this principal, this tenant and this graph
    generation, and refuses an expired set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_set_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    order_reference: str = Field(min_length=1, max_length=128)
    # Empty means the whole order. Stated rather than implied so the
    # idempotency key is stable either way.
    order_line_references: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_line_references(self) -> OrderConfirmation:
        if len(set(self.order_line_references)) != len(self.order_line_references):
            raise ValueError("order_line_references must not repeat a line")
        return self

    def idempotency_key(self, *, tenant_id: str, conversation_id: str) -> str:
        """Stable across retries, distinct across intents.

        Lines are sorted so the model listing them in a different order on a
        retry does not read as a different confirmation.
        """
        lines = ",".join(sorted(self.order_line_references))
        return f"{tenant_id}|{conversation_id}|{self.order_reference}|{lines}"


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
    order_confirmation: OrderConfirmation | None = None

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
            ActionType.CONFIRM_ORDER: self.order_confirmation is not None,
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
    # The associate's IANA time zone, as their browser reports it. Optional
    # because it is the client's contribution and not every caller has one --
    # an absent or unresolvable zone grounds the turn in UTC rather than
    # rejecting it (see temporal_grounding.normalize_session_timezone).
    session_timezone: str | None = Field(default=None, max_length=64)


class AgentTurnContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation_id: str
    client_turn_id: str
    user_message: str
    # When this turn is happening, decided once and reused by every context
    # build in it. Without it the model had no "now" at all and answered
    # date-bearing questions from whatever its training data implied; with it
    # re-read per build, two queries in one turn could resolve "yesterday"
    # differently and the turn would contradict itself in its own evidence.
    as_of: datetime
    session_timezone: str
    # `RELATIVE_DATE_PHRASES` -> {"start", "endExclusive"} as absolute UTC
    # instants, computed from `as_of` in `session_timezone`. The model selects a
    # window; it does not do calendar arithmetic across a UTC offset in prose.
    resolved_date_windows: dict[str, dict[str, str]] = Field(default_factory=dict)
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
    # Clarifying questions already asked this turn and the associate's answers,
    # oldest first. Populated when a CLARIFY paused the graph and the associate
    # answered (see graph_nodes.make_clarify_node) -- without this the resumed
    # `decide` would re-ask the same question, having no record it was answered.
    clarification_exchanges: tuple[dict[str, str], ...] = ()
    # What was said on earlier turns, oldest first: {"role", "text"} where role
    # is "associate" or "agent". `clarification_exchanges` above covers only the
    # pauses inside the current turn; this is the conversation itself, and
    # without it the agent re-asks across turns what it already knows.
    transcript: tuple[dict[str, str], ...] = ()
    # What the case already knows, once one exists: the projection of its fact
    # log, name -> value.
    #
    # Two jobs. It stops the agent asking for something the case has already
    # recorded -- the requirement that no agent re-asks a known fact -- and it
    # is how a Support outcome reaches the associate's *original* conversation:
    # the RMA lands on the case, and the next turn's context carries it without
    # a new chat, a poll, or a client-side join.
    case_facts: dict[str, Any] = Field(default_factory=dict)


class AgentTurnResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation_id: str
    conversation_version: int
    client_turn_id: str
    graph_generation_id: str
    response: StructuredAgentResponse
    # Set when the reasoning graph suspended on a clarifying question instead of
    # completing: the LangGraph thread the associate's next turn must resume.
    # Deliberately not sensitive -- it is composed from the conversation_id and
    # client_turn_id the caller itself supplied (see ReasoningThreadIdFactory).
    pending_clarification_thread_id: str | None = None
    # Set once this conversation has confirmed an order and a case exists. The
    # console needs it to stop inferring "an order was found" from a candidate
    # list of length one, and it is the handle everything downstream of
    # discovery hangs off.
    case_id: str | None = None
    query_evidence: tuple[QueryEvidence, ...]
    model_provider: str
    model_name: str
    # What "now" meant while this turn reasoned, and in whose calendar. Carried
    # out on the result because the result is what `commit_turn` persists under
    # the turn's idempotency key: replaying a date-bearing question a month
    # later has to resolve it against the instant it was actually asked, and a
    # turn that does not record its own as-of cannot be replayed at all.
    #
    # Optional only for turns committed before this field existed, which
    # `AgentTurnResult.model_validate` still has to read back out of the
    # conversation document on an idempotent retry.
    as_of: datetime | None = None
    session_timezone: str | None = None


class ModelInvocationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: AgentAction
    provider: str
    model: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
