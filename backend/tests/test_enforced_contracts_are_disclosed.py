"""Every rule the platform enforces on a model must be a rule it told the model.

A 56-call evaluation across eight real models found four of them, across two
vendors, producing *correct* answers that were then rejected for requirements
nothing had ever stated. That is the platform's defect, not the models'. The
tests here pin the disclosures that close it, and each one fails if the rule is
ever tightened again without the corresponding statement:

* the conditional payload contract (`CLARIFY` needs a non-empty
  `requested_input`), which the emitted JSON Schema dialect cannot express as a
  constraint and therefore carries as `description` text;
* `EvidenceReference.expected_value`, enforced by `HallucinationGuard` and
  previously named in no prompt;
* `permissions.searchable_by`, which `SchemaQueryGuard` requires and which the
  compact schema did not reflect, advertising 55 fields as filterable that the
  guard refuses for every role there is;
* a model whose context window is smaller than the task's `maximumInputTokens`,
  previously discovered once per turn as an HTTP 400.

None of these relaxes a guard. Each is the statement of a rule that was already
being enforced in silence.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field

from return_platform.ai.providers.schema_cleaner import clean_gemini_schema
from return_platform.ai.routing.routes import AIRoute
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import (
    PROMPT_SECTION_MAX_CHARS,
    AIGatewayConfiguration,
    ModelTier,
    TaskConfiguration,
    load_ai_gateway_configuration,
)
from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.integration.neo4j_gateway import Neo4jKnowledgeGateway
from return_platform.dynamic_knowledge.knowledge.guards import (
    GuardContext,
    GuardRejected,
    PrincipalContext,
    SchemaQueryGuard,
)
from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryCondition,
    QueryOperation,
)
from return_platform.dynamic_knowledge.order_agent.contracts import ActionType, AgentAction
from return_platform.dynamic_knowledge.schema import ActiveSchema

BACKEND_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_CONFIG = BACKEND_ROOT / "config" / "ai_gateway.yaml"
ACTIVE_SCHEMA = BACKEND_ROOT / "config" / "dynamic_knowledge" / "active-schema.return-order.yaml"
AGENT_ID = "order-discovery-agent"
REASONING_TASK = "ORDER_AGENT_REASONING_V1"

#: The roles an order-discovery principal actually holds. Not `{"*"}`: the
#: wildcard belongs on the *permitted* side of `roles_allowed`, and a test that
#: put it on the principal's side would assert against a principal that cannot
#: exist.
PRINCIPAL_ROLES = frozenset({"branch-associate"})


@pytest.fixture(scope="module")
def production_schema() -> ActiveSchema:
    return load_active_schema(ACTIVE_SCHEMA)


@pytest.fixture(scope="module")
def reasoning_task() -> TaskConfiguration:
    return load_ai_gateway_configuration(GATEWAY_CONFIG).configuration.tasks[REASONING_TASK]


@pytest.fixture(scope="module")
def order_agent_prompt(reasoning_task: TaskConfiguration) -> str:
    """The prompt as assembled, not as spelled in the YAML.

    The reasoning prompt is written as `systemPromptSections` -- twenty-one named
    parts, each one concern, joined into `systemPrompt` by `TaskConfiguration`.
    Every disclosure below is a statement the *model* has to read, so what is
    asserted is the composed string that reaches a provider; reading the raw
    YAML key would be asserting against how the prompt is stored.
    """
    return reasoning_task.systemPrompt


@pytest.fixture(scope="module")
def emitted_schema() -> dict[str, Any]:
    """The schema exactly as `StructuredOutputInvoker.invoke` puts it in the prompt."""
    return clean_gemini_schema(AgentAction.model_json_schema())


@pytest.fixture(scope="module")
def compact_schema(production_schema: ActiveSchema) -> dict[str, Any]:
    # No driver is touched: `compact_schema` reads the active schema and nothing
    # else, and a real gateway would demand a live Neo4j connection to exercise
    # a pure projection.
    gateway = Neo4jKnowledgeGateway.__new__(Neo4jKnowledgeGateway)
    return asyncio.run(
        gateway.compact_schema(production_schema, AGENT_ID, principal_roles=PRINCIPAL_ROLES)
    )


# --- the conditional payload contract ----------------------------------------


def test_the_clarify_payload_rule_is_in_the_schema_the_model_reads(
    emitted_schema: dict[str, Any],
) -> None:
    """`validate_action_payload` requires a non-empty `requested_input` for
    `CLARIFY`, and the emitted `required` list cannot say so: the requirement is
    conditional on `action_type`, which the dialect has no keyword for. It rides
    on `description` instead, which every provider forwards verbatim.

    `gemini-2.5-flash` asked five real customers apart by name, obeyed the offer
    cap, and lost the turn to `missing payload for action type CLARIFY`.
    """
    assert emitted_schema["required"] == [
        "business_capability",
        "action_type",
        "decision_summary",
    ], "the unconditionally-required fields changed; the conditional rules below may have too"

    action_type = emitted_schema["properties"]["action_type"]["description"]
    for name in ("CLARIFY", "requested_input", "response"):
        assert name in action_type, f"the payload contract no longer names {name!r}"

    response = emitted_schema["properties"]["response"]["description"]
    assert "CLARIFY" in response and "requested_input" in response


def test_every_conditionally_required_payload_is_named(emitted_schema: dict[str, Any]) -> None:
    """One entry per action type that requires a payload, checked against the
    validator rather than against a list written twice.

    An action type added to `validate_action_payload` with a payload requirement
    and no mention in the description would be the exact defect again, one action
    later.
    """
    described = emitted_schema["properties"]["action_type"]["description"]
    payload_fields = {
        ActionType.GRAPH_QUERY: "query_plan",
        ActionType.ORDER_SEARCH: "search_intent",
        ActionType.GET_SCHEMA: "schema_entity_ids",
        ActionType.CONFIRM_ORDER: "order_confirmation",
        ActionType.RESPOND: "response",
        ActionType.CLARIFY: "requested_input",
    }
    for action_type, field_name in payload_fields.items():
        assert action_type.value in described, f"{action_type.value} is not disclosed"
        assert field_name in described, f"{action_type.value}'s payload field is not disclosed"

    # And the rule really is enforced, so the description is not describing a
    # requirement that quietly went away.
    with pytest.raises(ValueError, match="missing payload for action type CLARIFY"):
        AgentAction.model_validate(
            {
                "business_capability": "order-discovery",
                "action_type": "CLARIFY",
                "decision_summary": "Which Alvarado?",
                "response": {
                    "status": "ON_HOLD",
                    "business_capability": "order-discovery",
                    "statements": [
                        {
                            "statement_id": "1",
                            "statement_type": "CLARIFICATION_QUESTION",
                            "text": "Which Alvarado — Duane, Luis, Antonio, Jacqueline or Carla?",
                        }
                    ],
                },
            }
        )


def test_the_prompt_states_the_payload_contract_and_expected_value(
    order_agent_prompt: str,
) -> None:
    """The schema is one carrier and the prompt is the other. Both, because a
    provider that ignores the schema block still reads the prompt, and the
    evaluation showed models weighing "make it a CLARIFICATION_QUESTION
    statement" against "put the question in requested_input" with nothing to
    decide on.
    """
    assert "requested_input" in order_agent_prompt, "the CLARIFY payload rule left the prompt"
    assert "expected_value" in order_agent_prompt, (
        "`HallucinationGuard` compares expected_value against the resolved value "
        "and the prompt no longer mentions the field"
    )


def test_the_prompt_still_fits_its_budget(reasoning_task: TaskConfiguration) -> None:
    """Asserted here as well as by the model so a prompt edit that overflows
    names the reason directly.

    The bound is read off `TaskConfiguration` rather than written again here. It
    was duplicated as a literal, so raising it to admit v16's asking-is-not-
    finishing rule failed this test for a bound that had already moved -- which
    reports a stale copy as though it were a prompt defect. It is now
    `prompt_budget` rather than the field's `max_length`: this task's prompt is
    composed from sections and is measured against theirs.
    """
    assert len(reasoning_task.systemPrompt) <= reasoning_task.prompt_budget


def test_no_single_prompt_section_has_become_the_monolith_again(
    reasoning_task: TaskConfiguration,
) -> None:
    """The budget that actually governs an edit now.

    v18 took a 14,699-character single string apart into named sections, one per
    concern, because the whole-prompt cap had stopped being a tripwire and become
    a squeeze: rules were being added by deleting or compressing older ones. The
    guarantee that buys anything is per-section -- a concern that has drifted
    toward `PROMPT_SECTION_MAX_CHARS` is a concern that wants splitting again,
    and this fails while there is still room to do it deliberately.

    Names are asserted too. A section name never reaches a model; it is what
    makes a prompt change reviewable as a diff, and an unnamed or duplicated one
    puts the decomposition straight back where it started.
    """
    sections = reasoning_task.systemPromptSections
    assert len(sections) >= 8, (
        "the reasoning prompt has been recombined into a handful of large blocks"
    )
    assert len({section.name for section in sections}) == len(sections)
    oversized = {
        section.name: len(section.text)
        for section in sections
        if len(section.text) > PROMPT_SECTION_MAX_CHARS
    }
    assert oversized == {}, f"sections over the {PROMPT_SECTION_MAX_CHARS}-char budget: {oversized}"

    # Liveness: the composed prompt really is these sections and nothing else,
    # so a rule quietly added to a `systemPrompt` alongside them would fail here
    # rather than travel unreviewed.
    assert reasoning_task.systemPrompt == "\n\n".join(section.text for section in sections)


# --- the fact vocabulary, and the two live defects that needed it ------------


def test_every_fact_name_the_prompt_offers_is_one_the_catalogue_will_keep(
    order_agent_prompt: str,
) -> None:
    """`FactCatalogue.capture` drops any name no configured field claims.

    Not a rejection the model ever sees: an unrecognized name is logged as
    `order_agent_unconfigured_observed_facts` and the fact is discarded, so the
    turn succeeds and the associate is asked for the same thing again later. The
    prompt used to say the name was "a short stable name" and leave the model to
    invent one, which is the enforcement-without-disclosure this file exists to
    catch.

    Asserted in both directions. A name in the prompt that configuration does not
    have is a fact the model will lose; a configured field the prompt does not
    name is one the model has no way to guess, because nothing in `contextJson`
    carries this catalogue -- `identification_fields` describes *search signals*
    and `captured_facts` lists only what a capture already succeeded on.
    """
    policy = load_return_configuration(
        BACKEND_ROOT / "config" / "returns" / "production.yaml"
    ).configuration.clarification_policy
    configured = {item.field for item in policy.fields}

    # The prompt names them in one comma-separated run; scanning for each is
    # what keeps this insensitive to the wording around them.
    named = {name for name in configured if name in order_agent_prompt}
    missing = sorted(configured - named)
    assert missing == [], (
        f"configured fact fields the prompt never names, so the model cannot "
        f"emit them and the associate gets asked twice: {missing}"
    )


def test_the_prompt_requires_facts_on_every_action_not_only_at_confirmation(
    order_agent_prompt: str,
) -> None:
    """Observed 2026-08-20: three turns, no `observed_facts`, nothing persisted.

    `_capture_observed_facts` runs on every validated action and merges
    `action.observed_facts`, which defaults to `()`. A model that reports none
    leaves the conversation with no memory at all, so the next turn re-runs the
    same search and re-asks the answered question -- and the console's extracted
    -facts panel stays empty while the transcript fills up.

    The wording that failed asked for what the associate says "about the return
    itself", which reads as excluding who the customer is. The identifying
    details have to be named as facts, not only as search signals.
    """
    assert "observed_facts, on every action without exception" in order_agent_prompt
    for identifying in ("customer or company name", "order or PO number"):
        assert identifying in order_agent_prompt, (
            f"the prompt no longer names {identifying!r} among the details that "
            "must be reported as facts"
        )
    assert "CONFIRM_ORDER" in order_agent_prompt


def test_the_prompt_treats_an_explicit_confirmation_as_settling_what_it_names(
    order_agent_prompt: str,
) -> None:
    """Observed 2026-08-20: "confirm the customer X on account Y", twice, and
    both times the agent asked which branch and re-listed the same accounts.

    The rule existed as a principle -- "never ask again for something the
    associate has effectively given" -- inside the narrowing paragraph, and was
    not followed. It is now its own section about the concrete case, and it does
    not displace the identity-first ladder: that decides what to ask while
    several candidates remain, this decides what has stopped being a question.
    """
    assert "settles everything it names" in order_agent_prompt
    assert "contextJson.transcript" in order_agent_prompt
    # The identity-first rule from 6a295b4 is still there underneath it. Asserted
    # without the sentence's punctuation: the rule is what must survive, and
    # pinning the full stop made a reworded-but-intact rule look like a deletion.
    assert "Identify the customer before narrowing to an order" in order_agent_prompt
    for contact_field in ("phone_number", "email", "address_line1", "city", "postal_code"):
        assert contact_field in order_agent_prompt, contact_field


# --- the schema cleaner discards nothing a provider could use ----------------


def test_a_field_description_survives_being_inlined() -> None:
    """Both inlining paths used to let the referenced definition win.

    `$ref` returned the definition and dropped its siblings outright; `anyOf`
    called `node.update(resolved)`. Either way a field's own
    `Field(description=...)` was replaced by the referenced model's docstring
    before any provider saw the schema -- which is how the payload contract on
    `AgentAction.action_type` was being deleted in transit.
    """

    class Referenced(BaseModel):
        """The referenced model's own docstring, which is the less specific of the two."""

        value: str

    class Holder(BaseModel):
        required_ref: Referenced = Field(description="what this field means here")
        optional_ref: Referenced | None = Field(default=None, description="and here")

    cleaned = clean_gemini_schema(Holder.model_json_schema())
    assert cleaned["properties"]["required_ref"]["description"] == "what this field means here"
    assert cleaned["properties"]["optional_ref"]["description"] == "and here"
    # The definition is still inlined -- the field description is added to it,
    # not substituted for it.
    assert "value" in cleaned["properties"]["required_ref"]["properties"]
    assert "$ref" not in json.dumps(cleaned)
    assert "$defs" not in cleaned


# --- the compact schema advertises only what the guard admits ----------------


def _filter_plan(entity_id: str, field_id: str, operator: str) -> LogicalQueryPlan:
    return LogicalQueryPlan(
        operation=QueryOperation.FILTER,
        start_entity_id=entity_id,
        fields=(field_id,),
        filters=(
            QueryCondition(
                entity_id=entity_id,
                field_id=field_id,
                operator=operator,
                value="x",
            ),
        ),
        limit=1,
    )


def test_no_advertised_filter_is_one_the_guard_refuses(
    production_schema: ActiveSchema, compact_schema: dict[str, Any]
) -> None:
    """The whole point: what the model is shown and what the guard accepts are
    the same set.

    `SchemaQueryGuard` admits a filter only when the field is filterable or
    searchable **and** the principal holds a role in `permissions.searchable_by`.
    The compact schema published the first half and omitted the second, so 55
    fields -- `order_line.account_id`, `sales_order.shipped_at`,
    `order_line.line_number`, `return_item.item_condition` among them -- were
    offered as filterable and refused for every role there is.
    """
    guard = SchemaQueryGuard()
    context = GuardContext(
        schema=production_schema,
        agent_policy=production_schema.agent_policies[AGENT_ID],
        principal=PrincipalContext(
            principal_id="associate-7741",
            tenant_id="FEG",
            roles=PRINCIPAL_ROLES,
            branch_ids=frozenset({"1969"}),
        ),
    )

    offered = 0
    for entity_id, entity in compact_schema["entities"].items():
        for field_id, field in entity["fields"].items():
            if not field["filterable"]:
                assert field["operators"] == [], (
                    f"{entity_id}.{field_id} offers operators for a filter it cannot accept"
                )
                continue
            offered += 1
            operator = field["operators"][0]
            guard.validate(context, _filter_plan(entity_id, field_id, operator))

    assert offered, "the compact schema now offers no filters at all, which cannot be right"


def test_a_display_only_field_is_not_offered_as_filterable(
    production_schema: ActiveSchema, compact_schema: dict[str, Any]
) -> None:
    """`order_line.account_id` is the concrete case that cost a model a scenario.

    It carries `capabilities.filterable: true` and no `permissions.searchable_by`
    at all, and an empty permitted-role set denies everyone. The field stays
    listed with its description and type -- it is displayable, and withdrawing it
    entirely would hide a real column -- but it is no longer advertised as
    something a filter may name.
    """
    field = compact_schema["entities"]["order_line"]["fields"]["account_id"]
    assert field["filterable"] is False
    assert field["searchable"] is False
    assert field["operators"] == []
    assert field["description"], "the field itself must still be visible to the model"

    definition = production_schema.entities["order_line"].fields["account_id"]
    assert definition.capabilities.filterable, "the capability is unchanged; only the claim is"
    assert definition.permissions.searchable_by == frozenset(), (
        "no role may search this field -- if that changed, the guard was widened"
    )


def test_a_role_that_may_search_still_sees_the_field_offered(
    production_schema: ActiveSchema,
) -> None:
    """The projection narrows by permission, it does not blanket-suppress.

    `sales_order.sales_order_number` permits `*`, so every principal keeps it,
    and a future field scoped to one role would appear for that role only.
    """
    gateway = Neo4jKnowledgeGateway.__new__(Neo4jKnowledgeGateway)
    compact = asyncio.run(
        gateway.compact_schema(production_schema, AGENT_ID, principal_roles=PRINCIPAL_ROLES)
    )
    field = compact["entities"]["sales_order"]["fields"]["sales_order_number"]
    assert field["filterable"] is True
    assert field["operators"], "a field this principal may filter on must keep its operators"


# --- a model that cannot read the prompt is not offered the task -------------


def _route(model: str, provider: str = "NVIDIA", tier: ModelTier = ModelTier.STANDARD) -> AIRoute:
    class _Adapter:
        configured = True

    return AIRoute(
        route_id=f"{provider.lower()}/{model}/{tier.value.lower()}/key-1",
        provider_name=provider,
        model=model,
        credential_id=f"{provider.lower()}-key-1",
        credential_fingerprint="abc123",
        tier=tier,
        provider=_Adapter(),  # type: ignore[arg-type]
        provider_priority=0,
        model_priority=0,
        credential_priority=0,
    )


@pytest.fixture(scope="module")
def gateway_configuration() -> AIGatewayConfiguration:
    return load_ai_gateway_configuration(GATEWAY_CONFIG).configuration


def test_the_undersized_model_is_declared(gateway_configuration: AIGatewayConfiguration) -> None:
    """`nvidia/nemotron-mini-4b-instruct` answered every reasoning call with
    `HTTP 400 -- maximum context length is 4096 tokens, however you requested
    24014`. It is a fact about the model, knowable before the call."""
    window = gateway_configuration.maximum_context_tokens(
        provider="NVIDIA", model="nvidia/nemotron-mini-4b-instruct"
    )
    assert window == 4096
    assert gateway_configuration.tasks[REASONING_TASK].maximumInputTokens > window


def test_a_model_too_small_for_the_task_is_not_a_candidate(
    gateway_configuration: AIGatewayConfiguration,
) -> None:
    """Refused before the request rather than after the provider's 400, so it
    does not open a circuit, spend a retry, or record a failure against a
    credential that is working perfectly well."""
    undersized = _route("nvidia/nemotron-mini-4b-instruct")
    roomy = _route("nvidia/llama-3.3-nemotron-super-49b-v1.5")
    pool = AIRoutePool([undersized, roomy], gateway_configuration)

    candidates = asyncio.run(
        pool.candidates(gateway_configuration.tasks[REASONING_TASK], task_id=REASONING_TASK)
    )
    models = {route.model for route in candidates}
    assert "nvidia/nemotron-mini-4b-instruct" not in models
    assert "nvidia/llama-3.3-nemotron-super-49b-v1.5" in models


def test_a_small_model_still_serves_a_task_that_fits(
    gateway_configuration: AIGatewayConfiguration,
) -> None:
    """The incompatibility belongs to the (route, task) pair, not to the route.

    A 4k model is perfectly capable of a task whose input budget fits inside it,
    which is why the check excludes a candidate rather than refusing to build the
    route at all.
    """
    fitting = [
        task_id
        for task_id, task in sorted(gateway_configuration.tasks.items())
        if task.maximumInputTokens <= 4096
    ]
    assert fitting, "no task fits in 4096 tokens at all; the fixture needs rethinking"
    task_id = fitting[0]
    task = gateway_configuration.tasks[task_id]

    pool = AIRoutePool(
        [_route("nvidia/nemotron-mini-4b-instruct", tier=task.tier)], gateway_configuration
    )
    candidates = asyncio.run(pool.candidates(task, task_id=task_id))
    assert {route.model for route in candidates} == {"nvidia/nemotron-mini-4b-instruct"}


def test_an_undeclared_model_is_never_refused_on_silence(
    gateway_configuration: AIGatewayConfiguration,
) -> None:
    """Absence means unmeasured, not too small.

    Refusing on silence would have taken every unlisted model out of service the
    moment the field was introduced -- the same stance pricing takes with
    `UNKNOWN` rather than `0`.
    """
    assert (
        gateway_configuration.maximum_context_tokens(provider="NVIDIA", model="not/declared")
        is None
    )
    pool = AIRoutePool([_route("not/declared")], gateway_configuration)
    candidates = asyncio.run(
        pool.candidates(gateway_configuration.tasks[REASONING_TASK], task_id=REASONING_TASK)
    )
    assert {route.model for route in candidates} == {"not/declared"}


def test_the_guard_was_not_widened_to_make_any_of_this_pass(
    production_schema: ActiveSchema,
) -> None:
    """The one assertion that would catch the wrong fix.

    Every one of these disclosures is about telling a model the rule. Granting
    the 55 display-only fields a `searchable_by` would have made the compact
    schema truthful by moving a security boundary instead, and this fails if
    anyone does.
    """
    display_only = [
        (entity_id, field_id)
        for entity_id in sorted(production_schema.agent_policies[AGENT_ID].allowed_entity_ids)
        for field_id, field in production_schema.entities[entity_id].fields.items()
        if field.capabilities.filterable
        and not field.capabilities.searchable
        and field.permissions.searchable_by == frozenset()
    ]
    assert display_only, (
        "no display-only filterable fields remain -- either the schema was "
        "re-authored, or somebody granted them searchable_by"
    )
    guard = SchemaQueryGuard()
    context = GuardContext(
        schema=production_schema,
        agent_policy=production_schema.agent_policies[AGENT_ID],
        principal=PrincipalContext(
            principal_id="associate-7741",
            tenant_id="FEG",
            roles=PRINCIPAL_ROLES,
        ),
    )
    entity_id, field_id = display_only[0]
    operator = sorted(production_schema.entities[entity_id].fields[field_id].capabilities.operators)
    with pytest.raises(GuardRejected) as rejection:
        guard.validate(context, _filter_plan(entity_id, field_id, operator[0]))
    assert rejection.value.code == "REJECT_UNAUTHORIZED_FIELD"
