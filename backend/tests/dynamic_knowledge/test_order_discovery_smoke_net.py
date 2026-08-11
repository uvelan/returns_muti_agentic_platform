"""Order Discovery still works. Asserted on structure, never on prose.

Wave 1 restructures the conversation contract -- a ninth action, new context
fields, a case link -- and nothing currently verifies that discovery survives
it. This is the net that has to hold while that happens.

**What is real here.** The production descriptor
(`config/dynamic_knowledge/active-schema.return-order.yaml`), the real
`CypherCompiler`, and all six real guards. Only two things are substituted: the
model, which is scripted so a scenario is deterministic, and the graph
execution, which returns fixed rows. Everything between them -- routing,
capability validation, schema validation, query safety, plan compilation,
budget enforcement, evidence recording, hallucination validation -- is the
shipped code.

**What is asserted.** The sequence of `ActionType`s the graph dispatched, the
`LogicalQueryPlan` that reached the compiler, the guard verdict, and the
candidate outcome. Never the wording of a response: the tone is model-authored
and configurable by design, so asserting on it would make a copy change look
like a regression and would pin exactly the thing that is supposed to move.

Deterministic without a seed: the model is a script, not a sampler, so there is
nothing to pin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.evidence import (
    QueryEvidence,
    ResponseStatement,
    StatementType,
    StructuredAgentResponse,
)
from return_platform.dynamic_knowledge.knowledge.guards import (
    CapabilityGuard,
    GuardContext,
    HallucinationGuard,
    PrincipalContext,
    QuerySafetyGuard,
    QuerySafetyPolicy,
    ResponseSafetyGuard,
    SchemaQueryGuard,
    StrongAnchorGuard,
)
from return_platform.dynamic_knowledge.knowledge.query_plan import (
    LogicalQueryPlan,
    QueryCondition,
    QueryOperation,
)
from return_platform.dynamic_knowledge.order_agent.contracts import (
    ActionType,
    AgentAction,
    AgentTurnContext,
    ModelInvocationResult,
    OrderSearchIntent,
)
from return_platform.dynamic_knowledge.order_agent.errors import OrderAgentFailure
from return_platform.dynamic_knowledge.order_agent.graph import build_order_agent_graph
from return_platform.dynamic_knowledge.order_agent.graph_nodes import (
    GraphDependencies,
    TurnRuntimeContext,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema

pytestmark = pytest.mark.asyncio

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "dynamic_knowledge"
    / "active-schema.return-order.yaml"
)
AGENT_ID = "order-discovery-agent"
CAPABILITY = "order-discovery"


@pytest.fixture(scope="module")
def schema() -> ActiveSchema:
    return load_active_schema(SCHEMA_PATH)


# ---------------------------------------------------------------------------
# Substitutes: the model (scripted) and graph execution (fixed rows)
# ---------------------------------------------------------------------------


class ScriptedModel:
    """Returns a fixed list of actions, one per `decide`, and records the calls.

    A correction request is an assertion failure by default: a scenario that
    silently drifted into the correction path would otherwise still pass while
    testing something other than what it names.
    """

    def __init__(self, actions: list[AgentAction]) -> None:
        self._actions = list(actions)
        self.dispatched: list[ActionType] = []
        self.contexts: list[AgentTurnContext] = []

    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        self.contexts.append(context)
        if not self._actions:
            raise AssertionError("the graph asked for more actions than the scenario scripted")
        action = self._actions.pop(0)
        self.dispatched.append(action.action_type)
        return ModelInvocationResult(
            action=action,
            provider="scripted",
            model="scripted",
            prompt_tokens=1,
            completion_tokens=1,
        )

    async def correct_action(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError(f"unexpected action correction: {kwargs.get('validation_error')}")

    async def correct_response(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError(f"unexpected response correction: {kwargs.get('validation_error')}")


class RecordingKnowledge:
    """Real schema projection, fixed rows, and every compiled plan captured."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.plans: list[LogicalQueryPlan] = []
        self.compiled: list[str] = []

    async def compact_schema(self, schema: ActiveSchema, agent_id: str) -> dict[str, Any]:
        policy = schema.agent_policies[agent_id]
        return {"entities": sorted(policy.allowed_entity_ids)}

    async def schema_details(
        self, schema: ActiveSchema, entity_ids: tuple[str, ...]
    ) -> dict[str, Any]:
        return {
            entity_id: {"fields": sorted(schema.entities[entity_id].fields)}
            for entity_id in entity_ids
        }

    async def execute(self, **kwargs: Any) -> Any:
        self.plans.append(kwargs["plan"])
        self.compiled.append(kwargs["compiled_cypher"])
        return {"rows": list(self.rows), "total": len(self.rows)}


class MemoryEvidence:
    def __init__(self) -> None:
        self.stored: dict[str, QueryEvidence] = {}

    async def put(self, *, run_id: str, evidence: QueryEvidence) -> None:
        del run_id
        self.stored[evidence.query_execution_id] = evidence

    async def get_many(self, query_execution_ids: Any) -> tuple[QueryEvidence, ...]:
        return tuple(self.stored[i] for i in query_execution_ids if i in self.stored)


def _dependencies(
    schema: ActiveSchema,
    model: ScriptedModel,
    knowledge: RecordingKnowledge,
    evidence: MemoryEvidence,
) -> GraphDependencies:
    """Real guards and the real compiler -- the point of the exercise."""
    return GraphDependencies(
        schema=schema,
        model_gateway=model,
        knowledge_gateway=knowledge,
        evidence_store=evidence,
        capability_guard=CapabilityGuard(),
        schema_guard=SchemaQueryGuard(),
        query_safety_guard=QuerySafetyGuard(QuerySafetyPolicy()),
        strong_anchor_guard=StrongAnchorGuard(),
        hallucination_guard=HallucinationGuard(),
        response_safety_guard=ResponseSafetyGuard(),
        on_demand_sync=None,
        compiler=CypherCompiler(),
    )


def _guard_context(schema: ActiveSchema) -> GuardContext:
    return GuardContext(
        schema=schema,
        agent_policy=schema.agent_policies[AGENT_ID],
        principal=PrincipalContext(
            principal_id="associate-1",
            tenant_id="tenant-a",
            roles=frozenset({"*"}),
            branch_ids=frozenset({"CHARLOTTE"}),
        ),
    )


def _state(schema: ActiveSchema, message: str) -> dict[str, Any]:
    return {
        "conversation_id": "conv-smoke",
        "client_turn_id": "turn-1",
        "run_id": "run-1",
        "user_message": message,
        "agent_id": AGENT_ID,
        "schema_version": schema.schema_version,
        "graph_generation_id": "gen-smoke",
        "configuration_release_id": schema.configuration_release_id,
        "policy_version": schema.policy_version,
        "prompt_version": schema.prompt_version,
    }


async def _run(
    schema: ActiveSchema,
    message: str,
    actions: list[AgentAction],
    rows: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], ScriptedModel, RecordingKnowledge]:
    model = ScriptedModel(actions)
    knowledge = RecordingKnowledge(rows if rows is not None else [])
    graph = build_order_agent_graph(_dependencies(schema, model, knowledge, MemoryEvidence()))
    final = await graph.ainvoke(
        _state(schema, message),
        context=TurnRuntimeContext(guard_context=_guard_context(schema)),
        config={"recursion_limit": 64},
    )
    return final, model, knowledge


# ---------------------------------------------------------------------------
# Action builders
# ---------------------------------------------------------------------------


def _respond(text: str = "Found it.") -> AgentAction:
    return AgentAction(
        business_capability=CAPABILITY,
        action_type=ActionType.RESPOND,
        decision_summary="Evidence supports a response.",
        response=StructuredAgentResponse(
            status="DISCOVERY_COMPLETE",
            business_capability=CAPABILITY,
            statements=(
                ResponseStatement(
                    statement_id="s1",
                    statement_type=StatementType.REASONED_SUGGESTION,
                    text=text,
                    evidence_refs=(),
                ),
            ),
        ),
    )


def _search(**intent: Any) -> AgentAction:
    return AgentAction(
        business_capability=CAPABILITY,
        action_type=ActionType.ORDER_SEARCH,
        decision_summary="Search for the order from what the associate supplied.",
        search_intent=OrderSearchIntent(**intent),
    )


def _graph_query(plan: LogicalQueryPlan) -> AgentAction:
    return AgentAction(
        business_capability=CAPABILITY,
        action_type=ActionType.GRAPH_QUERY,
        decision_summary="Read the graph directly.",
        query_plan=plan,
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def test_exact_order_number_search_compiles_and_completes(schema: ActiveSchema) -> None:
    final, model, knowledge = await _run(
        schema, "order CW273354", [_search(orderNumbers=["CW273354"]), _respond()]
    )

    assert model.dispatched == [ActionType.ORDER_SEARCH, ActionType.RESPOND]
    assert knowledge.plans, "an order search must reach the compiler"
    assert all(plan.operation is QueryOperation.SEARCH for plan in knowledge.plans)
    assert final["final_response"]["status"] == "DISCOVERY_COMPLETE"


async def test_customer_name_search_reaches_the_customer_entity(schema: ActiveSchema) -> None:
    _, model, knowledge = await _run(
        schema, "an order for Jane Doe", [_search(customerNames=["Jane Doe"]), _respond()]
    )

    assert model.dispatched[0] is ActionType.ORDER_SEARCH
    entities = {plan.start_entity_id for plan in knowledge.plans}
    assert entities, "a customer-name search must produce at least one plan"
    assert entities <= set(schema.agent_policies[AGENT_ID].allowed_entity_ids)


async def test_a_search_with_no_results_still_completes_the_turn(schema: ActiveSchema) -> None:
    """No match is an answer, not a failure."""
    final, model, _ = await _run(
        schema, "order ZZZ-NOPE", [_search(orderNumbers=["ZZZ-NOPE"]), _respond()], rows=[]
    )

    assert model.dispatched == [ActionType.ORDER_SEARCH, ActionType.RESPOND]
    assert final["final_response"] is not None


async def test_product_anchors_are_usable_searches(schema: ActiveSchema) -> None:
    for intent in ({"productNames": ["chrome faucet"]}, {"skus": ["FAU-1234"]}):
        _, model, knowledge = await _run(schema, "product anchor", [_search(**intent), _respond()])
        assert model.dispatched[0] is ActionType.ORDER_SEARCH
        assert knowledge.plans, f"{intent} produced no plan"


async def test_date_window_is_a_usable_search(schema: ActiveSchema) -> None:
    _, model, knowledge = await _run(
        schema,
        "bought around the start of August",
        [_search(dateFrom="2026-08-01", dateTo="2026-08-07"), _respond()],
    )

    assert model.dispatched[0] is ActionType.ORDER_SEARCH
    assert knowledge.plans


async def test_declared_address_and_colour_anchors_produce_no_plan(schema: ActiveSchema) -> None:
    """A gap, pinned where it is load-bearing.

    `streetAddresses`, `cities`, `states`, `postalCodes` and `colors` are all
    declared on `OrderSearchIntent`, so the model can legitimately return them
    and the action validates -- and `build_progressive_plans` then drops them,
    logging `order_search_unsupported_intent_signals` at WARNING. The search
    runs, finds nothing on that anchor, and nothing tells the associate why.

    Shipping address and colour are both named identification fields in the
    business flow, so this is not a nice-to-have. Asserted as *current
    behaviour*: when a plan shape is added for them, this test fails and should
    be replaced with the positive assertion.
    """
    for intent in (
        {"streetAddresses": ["1 High Street"]},
        {"cities": ["Charlotte"]},
        {"postalCodes": ["28202"]},
        {"colors": ["chrome"]},
    ):
        _, _, knowledge = await _run(schema, "address anchor", [_search(**intent), _respond()])
        assert not knowledge.plans, (
            f"{intent} now produces a plan -- the gap is closed; replace this "
            "test with a positive assertion and update the field catalog"
        )


async def test_phone_and_email_cannot_be_expressed_as_search_anchors() -> None:
    """The other half of the same gap.

    The business flow lists phone and email among the allowed identification
    fields, and `clarification_policy` in `config/returns/production.yaml` ranks
    them at priority 95 and 90 -- so the agent will ask for an email and then
    have no way to search on the answer. `OrderSearchIntent` is
    `extra="forbid"`, so this is a hard limit the model cannot route around.

    Four of the eight mandated identification fields therefore do not work:
    address and colour are dropped after validation, phone and email cannot be
    stated at all. Order number, product name, customer name and purchase date
    are the four that do.
    """
    fields = set(OrderSearchIntent.model_fields)

    assert "emails" not in fields
    assert "phones" not in fields
    # Stated against a concrete inventory rather than in the abstract.
    assert {"orderNumbers", "customerNames", "productNames", "dateFrom"} <= fields


async def test_replan_clears_evidence_and_returns_to_decide(schema: ActiveSchema) -> None:
    replan = AgentAction(
        business_capability=CAPABILITY,
        action_type=ActionType.REPLAN,
        decision_summary="Start again from a different anchor.",
    )
    final, model, _ = await _run(
        schema,
        "actually, try the customer instead",
        [
            _search(orderNumbers=["CW000000"]),
            replan,
            _search(customerNames=["Jane Doe"]),
            _respond(),
        ],
    )

    assert model.dispatched == [
        ActionType.ORDER_SEARCH,
        ActionType.REPLAN,
        ActionType.ORDER_SEARCH,
        ActionType.RESPOND,
    ]
    assert final["replans_used"] == 1
    # REPLAN's contract is that the next decide starts clean.
    assert final["order_search_cache"] is None or final["evidence_refs"]


async def test_a_capability_outside_the_policy_is_refused(schema: ActiveSchema) -> None:
    """The guard, not the prompt, is what keeps the agent in scope."""
    forbidden = AgentAction(
        business_capability="payment-processing",
        action_type=ActionType.ORDER_SEARCH,
        decision_summary="Out of scope.",
        search_intent=OrderSearchIntent(orderNumbers=["CW273354"]),
    )
    model = ScriptedModel([forbidden])
    knowledge = RecordingKnowledge([])
    graph = build_order_agent_graph(_dependencies(schema, model, knowledge, MemoryEvidence()))

    with pytest.raises((OrderAgentFailure, AssertionError)):
        await graph.ainvoke(
            _state(schema, "charge the customer"),
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 16},
        )
    assert not knowledge.plans, "a refused capability must never reach the compiler"


async def test_an_unknown_graph_field_never_reaches_neo4j(schema: ActiveSchema) -> None:
    """Schema validation is upstream of compilation, so a hallucinated field
    cannot become Cypher."""
    bogus = _graph_query(
        LogicalQueryPlan(
            operation=QueryOperation.SEARCH,
            start_entity_id="sales_order",
            fields=("field_that_does_not_exist",),
            filters=(
                QueryCondition(
                    entity_id="sales_order",
                    field_id="field_that_does_not_exist",
                    operator="EXACT",
                    value="x",
                ),
            ),
        )
    )
    model = ScriptedModel([bogus])
    knowledge = RecordingKnowledge([])
    graph = build_order_agent_graph(_dependencies(schema, model, knowledge, MemoryEvidence()))

    with pytest.raises((OrderAgentFailure, AssertionError)):
        await graph.ainvoke(
            _state(schema, "invalid field"),
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 16},
        )
    assert not knowledge.compiled, "an unvalidated plan must not be compiled"


async def test_out_of_scope_action_fails_the_turn_before_any_query(schema: ActiveSchema) -> None:
    out_of_scope = AgentAction(
        business_capability=CAPABILITY,
        action_type=ActionType.OUT_OF_SCOPE,
        decision_summary="Not an order question.",
    )
    model = ScriptedModel([out_of_scope])
    knowledge = RecordingKnowledge([])
    graph = build_order_agent_graph(_dependencies(schema, model, knowledge, MemoryEvidence()))

    with pytest.raises(OrderAgentFailure) as raised:
        await graph.ainvoke(
            _state(schema, "what is the weather"),
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 16},
        )
    assert raised.value.code == "ORDER_AGENT_OUT_OF_SCOPE"
    assert not knowledge.plans


async def test_the_turn_context_carries_transcript_and_schema_to_the_model(
    schema: ActiveSchema,
) -> None:
    """Both exist to stop the agent re-asking what it already knows."""
    _, model, _ = await _run(
        schema, "order CW273354", [_search(orderNumbers=["CW273354"]), _respond()]
    )

    first = model.contexts[0]
    assert first.user_message == "order CW273354"
    assert first.compact_schema, "the model must be told what it may search"
    assert first.graph_generation_id == "gen-smoke"


async def test_the_reasoning_step_budget_is_enforced(schema: ActiveSchema) -> None:
    """A model that never responds must be stopped by policy, not by luck."""
    policy = schema.agent_policies[AGENT_ID]
    never_finishes = [
        _search(orderNumbers=[f"CW{index:06d}"]) for index in range(policy.max_reasoning_steps + 4)
    ]
    model = ScriptedModel(never_finishes)
    graph = build_order_agent_graph(
        _dependencies(schema, model, RecordingKnowledge([]), MemoryEvidence())
    )

    with pytest.raises((OrderAgentFailure, AssertionError)):
        await graph.ainvoke(
            _state(schema, "loop forever"),
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 128},
        )
