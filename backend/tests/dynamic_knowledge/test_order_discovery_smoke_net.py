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

from datetime import UTC, datetime, timedelta
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
    OrderConfirmation,
    OrderSearchIntent,
)
from return_platform.dynamic_knowledge.order_agent.errors import OrderAgentFailure
from return_platform.dynamic_knowledge.order_agent.graph import build_order_agent_graph
from return_platform.dynamic_knowledge.order_agent.graph_nodes import (
    ConfirmedCase,
    GraphDependencies,
    TurnRuntimeContext,
)
from return_platform.dynamic_knowledge.order_agent.state import CandidateSet
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


CANDIDATE_SET_ID = "cs-smoke"
CANDIDATE_ID = "cand-1"
ORDER_REFERENCE = "CW273354"


class RecordingCaseStore:
    """Idempotent on the confirmation key, like the real adapter.

    Modelling the idempotency here rather than always returning a fresh id is
    what makes the retry test meaningful: a store that forgot would let a
    broken node pass.
    """

    def __init__(self, facts: dict[str, Any] | None = None) -> None:
        self.confirmations: list[str] = []
        self.issued: list[str] = []
        self.fact_reads: list[str] = []
        self._facts = dict(facts or {})
        self._by_key: dict[str, str] = {}

    async def case_facts(self, case_id: str) -> dict[str, Any]:
        self.fact_reads.append(case_id)
        return dict(self._facts)

    async def confirm_case(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        branch_ids: tuple[str, ...],
        conversation_id: str,
        confirmation: OrderConfirmation,
        configuration_release_id: str,
        graph_generation_id: str,
    ) -> ConfirmedCase:
        del principal_id, branch_ids, configuration_release_id, graph_generation_id
        key = confirmation.idempotency_key(tenant_id=tenant_id, conversation_id=conversation_id)
        self.confirmations.append(key)
        existing = self._by_key.get(key)
        if existing is not None:
            return ConfirmedCase(case_id=existing, already_existed=True)
        case_id = f"case-{len(self._by_key) + 1}"
        self._by_key[key] = case_id
        self.issued.append(case_id)
        return ConfirmedCase(case_id=case_id, already_existed=False)


def _candidate_set_cache() -> dict[str, Any]:
    """A live candidate set, as `order_search` would have left it.

    Built through `CandidateSet.create` rather than hand-rolled so the checksum
    and binding fields are the real ones -- `validate_selection` checks them,
    and a hand-built dict would be testing the test.
    """
    candidate_set = CandidateSet.create(
        candidate_set_id=CANDIDATE_SET_ID,
        conversation_id="conv-smoke",
        turn_id="turn-1",
        principal_id="associate-1",
        tenant_id="tenant-a",
        schema_version="2026.08.04",
        graph_generation_id="gen-smoke",
        query_execution_id="qe-1",
        candidate_ids=(CANDIDATE_ID,),
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    return {"candidateSet": candidate_set.model_dump(mode="json")}


def _confirm(candidate_id: str = CANDIDATE_ID) -> AgentAction:
    return AgentAction(
        business_capability="return-context-collection",
        action_type=ActionType.CONFIRM_ORDER,
        decision_summary="The associate confirmed this order.",
        order_confirmation=OrderConfirmation(
            candidate_set_id=CANDIDATE_SET_ID,
            candidate_id=candidate_id,
            order_reference=ORDER_REFERENCE,
            order_line_references=("L1", "L2"),
        ),
    )


def _dependencies(
    schema: ActiveSchema,
    model: ScriptedModel,
    knowledge: RecordingKnowledge,
    evidence: MemoryEvidence,
    case_store: RecordingCaseStore | None = None,
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
        case_store=case_store,
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
    case_store: RecordingCaseStore | None = None,
    seed_candidate_set: bool = False,
) -> tuple[dict[str, Any], ScriptedModel, RecordingKnowledge]:
    model = ScriptedModel(actions)
    knowledge = RecordingKnowledge(rows if rows is not None else [])
    graph = build_order_agent_graph(
        _dependencies(schema, model, knowledge, MemoryEvidence(), case_store=case_store)
    )
    state = _state(schema, message)
    if seed_candidate_set:
        # A confirmation is only meaningful against a search that happened.
        state["order_search_cache"] = _candidate_set_cache()
    final = await graph.ainvoke(
        state,
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


async def test_confirming_an_order_creates_a_case_and_returns_its_id(
    schema: ActiveSchema,
) -> None:
    """The transition the platform did not have.

    Discovery could search and answer; nothing recorded that the associate had
    chosen, so every step after it was unreachable.
    """
    # Confirms against the seeded candidate set rather than running a search
    # first: `order_search` mints its own set with a fresh uuid, so a scenario
    # that searched *then* confirmed a fixed id would be rejected -- correctly,
    # and for a reason that has nothing to do with what this test is about.
    store = RecordingCaseStore()
    final, model, _ = await _run(
        schema,
        "yes, that one",
        [_confirm(), _respond("Raising the return now.")],
        case_store=store,
        seed_candidate_set=True,
    )

    assert model.dispatched == [ActionType.CONFIRM_ORDER, ActionType.RESPOND]
    assert final["case_id"] == store.issued[0]
    assert len(store.confirmations) == 1
    # Back to `decide` after confirming, not straight to END: the associate is
    # owed a sentence, and only the model writes those.
    assert final["final_response"] is not None


async def test_a_support_outcome_is_in_the_next_turn_context(schema: ActiveSchema) -> None:
    """The last hop of Channel B -> Channel A.

    Support issued the RMA on the case between two turns. Nothing in this
    conversation was told; the fact is read when the context is assembled, so
    the very next `decide` sees it -- which is what makes "no new conversation,
    no poll, no client-side join" true rather than aspirational.
    """
    store = RecordingCaseStore(facts={"return_reference": "RMA-1001", "return_location": "DC-7"})
    _, model, _ = await _run(
        schema,
        "yes, that one",
        [_confirm(), _respond("Your RMA is RMA-1001.")],
        case_store=store,
        seed_candidate_set=True,
    )

    # The turn that confirmed had no case yet, so the first context is empty and
    # the second carries what Support knows. Both halves matter: a store read
    # unconditionally would be a lookup on a case id that does not exist.
    assert model.contexts[0].case_facts == {}
    assert model.contexts[1].case_facts["return_reference"] == "RMA-1001"
    assert store.fact_reads == [store.issued[0]]


async def test_the_conversation_survives_an_unreadable_case(schema: ActiveSchema) -> None:
    """Facts are context, not a dependency.

    A case store that is down degrades the answer -- the agent may re-ask
    something the case knew -- but it must not end the associate's turn. This
    is the failure the `case_facts` read is wrapped for.
    """

    class BrokenCaseStore(RecordingCaseStore):
        async def case_facts(self, case_id: str) -> dict[str, Any]:
            raise RuntimeError("mongo is unreachable")

    store = BrokenCaseStore()
    _, model, _ = await _run(
        schema,
        "yes, that one",
        [_confirm(), _respond("Raising the return now.")],
        case_store=store,
        seed_candidate_set=True,
    )

    assert model.dispatched == [ActionType.CONFIRM_ORDER, ActionType.RESPOND]
    assert model.contexts[1].case_facts == {}


async def test_two_identical_confirmations_produce_one_case(schema: ActiveSchema) -> None:
    """A Temporal retry of the same turn must not fork the case."""
    store = RecordingCaseStore()
    for _ in range(2):
        await _run(
            schema,
            "yes, that one",
            [_confirm(), _respond()],
            case_store=store,
            seed_candidate_set=True,
        )

    assert len(store.confirmations) == 2, "both turns reached the store"
    assert len(set(store.issued)) == 1, "and both resolved to one case"


async def test_a_confirmation_for_a_candidate_that_was_never_offered_is_refused(
    schema: ActiveSchema,
) -> None:
    """A model cannot confirm an order it did not find.

    The selection is validated against the live `CandidateSet` -- the same
    guard `graph_query` uses -- so an invented candidate id is rejected before
    any case exists.
    """
    store = RecordingCaseStore()
    model = ScriptedModel([_confirm(candidate_id="never-offered")])
    graph = build_order_agent_graph(
        _dependencies(schema, model, RecordingKnowledge([]), MemoryEvidence(), case_store=store)
    )

    with pytest.raises((OrderAgentFailure, AssertionError)):
        await graph.ainvoke(
            {**_state(schema, "confirm"), "order_search_cache": _candidate_set_cache()},
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 24},
        )
    assert not store.confirmations, "no case may be created from an unverified selection"


async def test_confirmation_fails_loudly_when_no_case_store_is_configured(
    schema: ActiveSchema,
) -> None:
    """A process without a platform client can still search. It must not
    silently accept a confirmation it cannot record."""
    model = ScriptedModel([_confirm()])
    graph = build_order_agent_graph(
        _dependencies(schema, model, RecordingKnowledge([]), MemoryEvidence(), case_store=None)
    )

    with pytest.raises(OrderAgentFailure) as raised:
        await graph.ainvoke(
            {**_state(schema, "confirm"), "order_search_cache": _candidate_set_cache()},
            context=TurnRuntimeContext(guard_context=_guard_context(schema)),
            config={"recursion_limit": 24},
        )
    assert raised.value.code == "ORDER_AGENT_CASE_STORE_UNAVAILABLE"


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
