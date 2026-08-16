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
    """What the associate said that might identify an order.

    **The identifying signals are not fields of this class.** They arrive as
    extra keys and are interpreted against the runtime identification catalogue
    (`discovery.identification_fields`), so adding the tenth -- or the
    eighteenth -- identification field is a configuration change and nothing
    else. This class carries only the three things that are about the *search*
    rather than about the customer, and those are genuinely fixed: which mode
    the search is in, how sure the model is, and whether this is a request for
    the next page of the previous search.

    Seventeen signal names used to be declared here under `extra="forbid"`, and
    that was the first of seven places an operator could not add a field
    without a release. It was also actively harmful: the clarification policy
    ranks email at 95 and phone at 90, so the agent would ask an associate for
    the email on the order and the reply had nowhere to go -- rejected as an
    unknown field, or flattened into free text and searched against product
    descriptions.

    `extra="allow"` is not a loosening of validation, it is a move of it. An
    unrecognized key is no longer refused by a class that cannot know what this
    deployment configured; it is named back to the turn as a signal nothing
    could use (`ParsedIntent.unknown_keys`). Refusing outright is the behaviour
    that lost the answer to the agent's own question.

    Values are read through `IdentificationCatalogue.parse`, which applies the
    configured multiplicity, normalization and validation. Nothing downstream
    reads an attribute of this model by name.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    searchMode: str = "DISCOVER"
    confidence: float = 0.0
    wantsMoreResults: bool = False

    @property
    def signal_values(self) -> dict[str, Any]:
        """Everything the model supplied that is not search metadata."""
        return dict(self.model_extra or {})


class ObservedFact(BaseModel):
    """Something the associate stated, reported by the model as it heard it.

    Carried on every action rather than only on the confirmation, because the
    facts worth keeping are usually stated in the opening sentence -- "the
    damaged red pump from order CW273354" establishes a return reason, a
    condition, a colour, a product and an order before any search has run, and
    a case to record them on does not exist for several turns yet.

    `source_message_id` is the provenance that makes the fact auditable back to
    the sentence it came from. `ambiguous` is the model's own hedge: a fact it
    was unsure of is kept and marked, not discarded, so the agent can raise
    exactly that one again instead of re-asking everything.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact: str = Field(min_length=1, max_length=128)
    value: Any = None
    source_message_id: str | None = None
    #: STATED (someone said it) or DERIVED (computed from other facts). Never
    #: OBSERVED: that means a source system reported it, and nothing a model
    #: heard in a conversation qualifies.
    acquisition: str = Field(default="STATED", pattern=r"^(STATED|DERIVED)$")
    ambiguous: bool = False


class CapturedTurnFact(BaseModel):
    """One fact this conversation has established, reported back to the caller.

    **The only wire form of `observedFacts`.** The conversation's captured facts
    are merged and kept on the conversation document, flushed into the case fact
    log at confirmation, and shown to the model every turn -- and until this
    field existed none of that was visible to the console. A screen whose
    heading promises *extracted facts* therefore had nothing to read but
    statement prose, and rendering the agent's narration ("Line 1 has no product
    recorded against it") as an extracted field is the same defect as a
    fabricated value wearing a different hat.

    `name` is the `clarification_policy.fields` field name, so it is the
    operator's vocabulary and not a second one; `label` is that field's
    associate-facing wording. `status` is `FactStatus`, and it is carried rather
    than filtered here because a `CONFLICTING` or `AMBIGUOUS` fact is something
    the conversation still owes the associate a question about -- a caller that
    rendered it as settled would be asserting more than the platform knows.

    Provenance is deliberately narrow: no `turnId`, no `sourceMessageId`, no
    `observedAt`. Those are audit fields the case fact log already keeps, and
    the caller of a turn has no use for them that is worth widening the
    conversation's wire surface for.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    #: Whatever the associate said, as the catalogue parsed it. `Any` because
    #: an identification field may be configured as a list or an integer.
    value: Any = None
    #: `FactStatus`. Not narrowed to a `Literal`: the statuses are the fact
    #: module's and re-declaring them here would be a second copy to drift.
    status: str
    #: The configured `label` of the field, or empty for a conversation
    #: captured before its field carried one.
    label: str = ""
    #: `STATED` or `DERIVED`. Never `OBSERVED` -- nothing a model heard in a
    #: conversation is a source system's report.
    acquisition: str = "STATED"


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
    # Facts the model heard in the associate's message this turn. Valid on
    # any action: the opening sentence usually carries them, and refusing
    # them until CONFIRM_ORDER is how the return reason came to be asked for
    # a second time.
    observed_facts: tuple[ObservedFact, ...] = ()

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
    agent_id: str
    # Which case this conversation resolved to, and which API request set the
    # whole thing off. Both exist for AI telemetry (W4.12): the metrics knew
    # every model call's route and token count and none of them knew which piece
    # of business work had caused it. Both are platform-issued identifiers --
    # see ai/gateway/telemetry.py for why nothing customer-identifying may join
    # them. `case_id` is null until the associate confirms an order;
    # `correlation_id` is null for a caller with no HTTP request behind it.
    case_id: str | None = None
    correlation_id: str | None = None
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
    # Which identifying signals this deployment can search on, what each is
    # called, and what else an associate might call it. Read from the runtime
    # identification catalogue every turn rather than written into the packaged
    # prompt: a prompt is one immutable string per configuration release, so a
    # field named only there could not appear without a release, which is the
    # whole defect. An operator adding a field to `discovery.identification_fields`
    # has it described to the model on the very next turn.
    identification_fields: tuple[dict[str, Any], ...] = ()
    # The signals worth asking for next, best first, each with the reasoning and
    # the evidence class behind it (DISC-03). Recomputed every turn from the
    # candidates actually in front of the associate, so it answers "what is
    # discriminating *now*" rather than restating a fixed question order.
    #
    # Evidence, not instruction: `clarification_policy.field_selection_owner` is
    # `LLM` and stays that way. The model is given the ranking and picks.
    suggested_discriminators: tuple[dict[str, Any], ...] = ()
    # What the associate has already told us this conversation, with each
    # fact's status. A fact listed here as USABLE has been answered and must
    # not be asked for again; one carrying `askAgainBecause` is the only kind
    # that may be raised a second time, and it says which of the five reasons
    # applies. Distinct from `case_facts`, which exists only once a case does.
    captured_facts: tuple[dict[str, Any], ...] = ()
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
    # Everything this conversation has established so far, not only what this
    # turn heard: the merged set is what the case receives at confirmation and
    # what the console's extracted-facts panel is a view of. Defaults empty so a
    # turn committed before the field existed still validates on an idempotent
    # replay.
    captured_facts: tuple[CapturedTurnFact, ...] = ()
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
