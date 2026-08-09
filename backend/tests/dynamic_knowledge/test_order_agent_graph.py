"""Proves the compiled LangGraph StateGraph reproduces the exact behaviors of
the original imperative `DynamicOrderAgentCoordinator.process_turn()` loop --
ported scenarios from test_order_agent.py, now invoking the graph directly
(via an InMemorySaver, no real Mongo/Neo4j needed for these control-flow
proofs) instead of the coordinator wrapper (which C2.5 builds around this
same graph).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.evidence import (
    EvidenceReference,
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


class FakeEvidenceStore:
    def __init__(self) -> None:
        self._by_id: dict[str, QueryEvidence] = {}

    async def put(self, *, run_id: str, evidence: QueryEvidence) -> None:
        del run_id
        self._by_id[evidence.query_execution_id] = evidence

    async def get_many(self, query_execution_ids: Any) -> tuple[QueryEvidence, ...]:
        return tuple(self._by_id[eid] for eid in query_execution_ids)


class Knowledge:
    async def compact_schema(self, schema: ActiveSchema, agent_id: str) -> dict[str, Any]:
        del agent_id
        return {"entityIds": list(schema.entities)}

    async def schema_details(
        self, schema: ActiveSchema, entity_ids: tuple[str, ...]
    ) -> dict[str, Any]:
        return {
            entity_id: schema.entities[entity_id].model_dump(mode="json")
            for entity_id in entity_ids
        }

    async def execute(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        plan: Any,
        compiled_cypher: str,
        parameters: dict[str, Any],
    ) -> Any:
        del schema, plan, parameters
        assert graph_generation_id == "generation-1"
        assert "DELETE" not in compiled_cypher
        return {"rows": [{"id": "A-1", "name": "Configured value"}], "total": 1}


class FailingModel:
    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        del context
        raise TimeoutError("provider unavailable")

    async def correct_action(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("must not be called")

    async def correct_response(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("must not be called")


class QueryThenRespondModel:
    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        if not context.query_evidence:
            action = AgentAction(
                business_capability="order-discovery",
                action_type=ActionType.GRAPH_QUERY,
                decision_summary="Search the configured graph entity using the supplied value.",
                query_plan=LogicalQueryPlan(
                    operation=QueryOperation.SEARCH,
                    start_entity_id="entity_a",
                    fields=("id", "name"),
                    filters=(
                        QueryCondition(
                            entity_id="entity_a", field_id="id", operator="EXACT", value="A-1"
                        ),
                    ),
                ),
            )
        else:
            evidence = context.query_evidence[0]
            response = StructuredAgentResponse(
                status="DISCOVERY_COMPLETE",
                business_capability="order-discovery",
                statements=(
                    ResponseStatement(
                        statement_id="s1",
                        statement_type=StatementType.GRAPH_FACT,
                        text="One configured record was found.",
                        evidence_refs=(
                            EvidenceReference(
                                query_execution_id=evidence.query_execution_id,
                                result_path=("total",),
                                expected_value=1,
                            ),
                        ),
                    ),
                ),
            )
            action = AgentAction(
                business_capability="order-discovery",
                action_type=ActionType.RESPOND,
                decision_summary="The graph evidence supports a final response.",
                response=response,
            )
        return ModelInvocationResult(
            action=action,
            provider="provider-a",
            model="standard-model",
            prompt_tokens=10,
            completion_tokens=10,
        )

    async def correct_action(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("correction not expected")

    async def correct_response(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("correction not expected")


class MiscasedCapabilityThenValidModel:
    def __init__(self) -> None:
        self.correction_calls = 0

    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        del context
        action = AgentAction(
            business_capability="ORDER_DISCOVERY",
            action_type=ActionType.RESPOND,
            decision_summary="Wrongly-cased capability, matching a real model's mistake.",
            response=StructuredAgentResponse(
                status="DISCOVERY_COMPLETE",
                business_capability="ORDER_DISCOVERY",
                statements=(
                    ResponseStatement(
                        statement_id="s0",
                        statement_type=StatementType.CLARIFICATION_QUESTION,
                        text="This should never reach the caller.",
                        evidence_refs=(),
                    ),
                ),
            ),
        )
        return ModelInvocationResult(
            action=action,
            provider="provider-a",
            model="standard-model",
            prompt_tokens=5,
            completion_tokens=5,
        )

    async def correct_action(self, **kwargs: Any) -> ModelInvocationResult:
        self.correction_calls += 1
        response = StructuredAgentResponse(
            status="DISCOVERY_COMPLETE",
            business_capability="order-discovery",
            statements=(
                ResponseStatement(
                    statement_id="s1",
                    statement_type=StatementType.CLARIFICATION_QUESTION,
                    text="Corrected after capability formatting was fixed.",
                    evidence_refs=(),
                ),
            ),
        )
        action = AgentAction(
            business_capability="order-discovery",
            action_type=ActionType.RESPOND,
            decision_summary="Corrected capability formatting.",
            response=response,
        )
        return ModelInvocationResult(
            action=action,
            provider="provider-a",
            model="standard-model",
            prompt_tokens=5,
            completion_tokens=5,
        )

    async def correct_response(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("response correction not expected in this test")


def make_graph(schema: ActiveSchema, model: Any) -> Any:
    deps = GraphDependencies(
        schema=schema,
        model_gateway=model,
        knowledge_gateway=Knowledge(),
        evidence_store=FakeEvidenceStore(),
        capability_guard=CapabilityGuard(),
        schema_guard=SchemaQueryGuard(),
        query_safety_guard=QuerySafetyGuard(QuerySafetyPolicy()),
        strong_anchor_guard=StrongAnchorGuard(),
        hallucination_guard=HallucinationGuard(),
        response_safety_guard=ResponseSafetyGuard(),
        on_demand_sync=None,
        compiler=CypherCompiler(),
    )
    return build_order_agent_graph(deps, checkpointer=InMemorySaver())


def guard_context(schema: ActiveSchema) -> GuardContext:
    return GuardContext(
        schema=schema,
        agent_policy=schema.agent_policies["agent_a"],
        principal=PrincipalContext(
            principal_id="p1", tenant_id="t1", roles=frozenset({"associate"})
        ),
    )


def initial_state(schema: ActiveSchema) -> dict[str, Any]:
    return {
        "conversation_id": "c1",
        "client_turn_id": "ct1",
        "user_message": "Find the configured record A-1",
        "schema_version": schema.schema_version,
        "graph_generation_id": "generation-1",
        "configuration_release_id": schema.configuration_release_id,
        "policy_version": schema.policy_version,
        "prompt_version": schema.prompt_version,
        "agent_id": "agent_a",
        "run_id": str(uuid4()),
        "requested_schema_entity_ids": (),
        "evidence_refs": (),
        "order_search_cache": None,
        "action": None,
        "reasoning_steps_used": 0,
        "queries_used": 0,
        "correction_attempts": 0,
        "clarifications_used": 0,
        "replans_used": 0,
        "targeted_syncs_used": 0,
        "final_response": None,
    }


@pytest.mark.asyncio
async def test_model_failure_is_explicit_and_has_no_fallback(active_schema: ActiveSchema) -> None:
    graph = make_graph(active_schema, FailingModel())
    with pytest.raises(OrderAgentFailure) as error:
        await graph.ainvoke(
            initial_state(active_schema),
            context=TurnRuntimeContext(guard_context=guard_context(active_schema)),
            config={"configurable": {"thread_id": "t1"}},
        )
    assert error.value.code == "ORDER_AGENT_LLM_FAILED"


@pytest.mark.asyncio
async def test_every_turn_uses_model_and_returns_evidence_bound_response(
    active_schema: ActiveSchema,
) -> None:
    graph = make_graph(active_schema, QueryThenRespondModel())
    final_state = await graph.ainvoke(
        initial_state(active_schema),
        context=TurnRuntimeContext(guard_context=guard_context(active_schema)),
        config={"configurable": {"thread_id": "t2"}},
    )
    assert final_state["final_response"]["status"] == "DISCOVERY_COMPLETE"
    assert len(final_state["evidence_refs"]) == 1


@pytest.mark.asyncio
async def test_miscased_capability_is_corrected_not_hard_failed(
    active_schema: ActiveSchema,
) -> None:
    model = MiscasedCapabilityThenValidModel()
    graph = make_graph(active_schema, model)
    final_state = await graph.ainvoke(
        initial_state(active_schema),
        context=TurnRuntimeContext(guard_context=guard_context(active_schema)),
        config={"configurable": {"thread_id": "t3"}},
    )
    assert model.correction_calls == 1
    assert final_state["final_response"]["status"] == "DISCOVERY_COMPLETE"
    assert final_state["final_response"]["business_capability"] == "order-discovery"


class OutOfScopeModel:
    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        del context
        return ModelInvocationResult(
            action=AgentAction(
                business_capability="order-discovery",
                action_type=ActionType.OUT_OF_SCOPE,
                decision_summary="This request is outside the agent's scope.",
            ),
            provider="provider-a",
            model="standard-model",
            prompt_tokens=1,
            completion_tokens=1,
        )

    async def correct_action(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("must not be called")

    async def correct_response(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("must not be called")


@pytest.mark.asyncio
async def test_out_of_scope_raises_before_any_capability_check(active_schema: ActiveSchema) -> None:
    graph = make_graph(active_schema, OutOfScopeModel())
    with pytest.raises(OrderAgentFailure) as error:
        await graph.ainvoke(
            initial_state(active_schema),
            context=TurnRuntimeContext(guard_context=guard_context(active_schema)),
            config={"configurable": {"thread_id": "t4"}},
        )
    assert error.value.code == "ORDER_AGENT_OUT_OF_SCOPE"


class ClarifyModel:
    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        del context
        return ModelInvocationResult(
            action=AgentAction(
                business_capability="order-discovery",
                action_type=ActionType.CLARIFY,
                decision_summary="Need more information before searching.",
                response=StructuredAgentResponse(
                    status="NEEDS_CLARIFICATION",
                    business_capability="order-discovery",
                    statements=(),
                    requested_input="Which order number are you asking about?",
                ),
            ),
            provider="provider-a",
            model="standard-model",
            prompt_tokens=1,
            completion_tokens=1,
        )

    async def correct_action(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("must not be called")

    async def correct_response(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("must not be called")


class ClarifyThenRespondModel:
    """Clarifies on the first decide, then -- once the associate's answer has
    been recorded in `clarification_exchanges` -- responds for real."""

    def __init__(self) -> None:
        self.decide_calls = 0
        self.seen_exchanges: tuple[dict[str, str], ...] = ()

    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        self.decide_calls += 1
        self.seen_exchanges = context.clarification_exchanges
        if self.decide_calls == 1:
            action = AgentAction(
                business_capability="order-discovery",
                action_type=ActionType.CLARIFY,
                decision_summary="Need more information before searching.",
                response=StructuredAgentResponse(
                    status="NEEDS_CLARIFICATION",
                    business_capability="order-discovery",
                    statements=(),
                    requested_input="Which order number are you asking about?",
                ),
            )
        else:
            action = AgentAction(
                business_capability="order-discovery",
                action_type=ActionType.RESPOND,
                decision_summary="The associate supplied the missing order number.",
                response=StructuredAgentResponse(
                    status="DISCOVERY_COMPLETE",
                    business_capability="order-discovery",
                    statements=(
                        ResponseStatement(
                            statement_id="s1",
                            statement_type=StatementType.CLARIFICATION_QUESTION,
                            text="Answered after the clarification was resolved.",
                            evidence_refs=(),
                        ),
                    ),
                ),
            )
        return ModelInvocationResult(
            action=action,
            provider="provider-a",
            model="standard-model",
            prompt_tokens=1,
            completion_tokens=1,
        )

    async def correct_action(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("must not be called")

    async def correct_response(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("must not be called")


@pytest.mark.asyncio
async def test_clarify_suspends_the_graph_instead_of_ending_the_turn(
    active_schema: ActiveSchema,
) -> None:
    """CLARIFY is no longer terminal: the graph pauses on `interrupt()` with the
    question as the interrupt value and NO final_response, so the same execution
    can later be resumed rather than a fresh turn being started."""
    graph = make_graph(active_schema, ClarifyModel())
    state = await graph.ainvoke(
        initial_state(active_schema),
        context=TurnRuntimeContext(guard_context=guard_context(active_schema)),
        config={"configurable": {"thread_id": "t5"}},
    )
    interrupts = state["__interrupt__"]
    assert len(interrupts) == 1
    assert interrupts[0].value["status"] == "NEEDS_CLARIFICATION"
    assert interrupts[0].value["requested_input"] == ("Which order number are you asking about?")
    assert state.get("final_response") is None


@pytest.mark.asyncio
async def test_clarify_resumes_the_same_thread_with_the_associates_answer(
    active_schema: ActiveSchema,
) -> None:
    """The associate's answer resumes the SAME paused execution (same thread_id)
    via Command(resume=...) rather than starting a new one -- and the answer is
    visible to the resumed `decide` so it cannot re-ask the same question."""
    model = ClarifyThenRespondModel()
    graph = make_graph(active_schema, model)
    config = {"configurable": {"thread_id": "t5-resume"}}
    context = TurnRuntimeContext(guard_context=guard_context(active_schema))

    paused = await graph.ainvoke(initial_state(active_schema), context=context, config=config)
    assert paused["__interrupt__"]
    assert model.decide_calls == 1

    resumed = await graph.ainvoke(Command(resume="ORD-10001"), context=context, config=config)

    assert resumed.get("__interrupt__") in (None, ())
    assert resumed["final_response"]["status"] == "DISCOVERY_COMPLETE"
    assert resumed["clarifications_used"] == 1
    assert resumed["clarification_exchanges"] == (
        {"question": "Which order number are you asking about?", "answer": "ORD-10001"},
    )
    # The resumed decide genuinely saw the answer.
    assert model.decide_calls == 2
    assert model.seen_exchanges[0]["answer"] == "ORD-10001"


class ReplanThenRespondModel:
    def __init__(self) -> None:
        self.decide_calls = 0

    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        self.decide_calls += 1
        if self.decide_calls == 1:
            action = AgentAction(
                business_capability="order-discovery",
                action_type=ActionType.REPLAN,
                decision_summary="Abandoning this approach, starting over.",
            )
        else:
            assert context.query_evidence == ()  # replan reset evidence_refs
            action = AgentAction(
                business_capability="order-discovery",
                action_type=ActionType.RESPOND,
                decision_summary="Responding after replanning.",
                response=StructuredAgentResponse(
                    status="DISCOVERY_COMPLETE",
                    business_capability="order-discovery",
                    statements=(),
                ),
            )
        return ModelInvocationResult(
            action=action,
            provider="provider-a",
            model="standard-model",
            prompt_tokens=1,
            completion_tokens=1,
        )

    async def correct_action(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("must not be called")

    async def correct_response(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("must not be called")


@pytest.mark.asyncio
async def test_replan_resets_evidence_and_continues_reasoning(active_schema: ActiveSchema) -> None:
    model = ReplanThenRespondModel()
    graph = make_graph(active_schema, model)
    final_state = await graph.ainvoke(
        initial_state(active_schema),
        context=TurnRuntimeContext(guard_context=guard_context(active_schema)),
        config={"configurable": {"thread_id": "t6"}},
    )
    assert model.decide_calls == 2
    assert final_state["replans_used"] == 1
    assert final_state["final_response"]["status"] == "DISCOVERY_COMPLETE"


class AlwaysQueryModel:
    """Never responds -- used to prove the reasoning-step budget fires."""

    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        del context
        action = AgentAction(
            business_capability="order-discovery",
            action_type=ActionType.GET_SCHEMA,
            decision_summary="Keep asking for schema, never respond.",
            schema_entity_ids=("entity_a",),
        )
        return ModelInvocationResult(
            action=action,
            provider="provider-a",
            model="standard-model",
            prompt_tokens=1,
            completion_tokens=1,
        )

    async def correct_action(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("must not be called")

    async def correct_response(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("must not be called")


@pytest.mark.asyncio
async def test_max_reasoning_steps_budget_is_enforced(active_schema: ActiveSchema) -> None:
    graph = make_graph(active_schema, AlwaysQueryModel())
    with pytest.raises(OrderAgentFailure) as error:
        await graph.ainvoke(
            initial_state(active_schema),
            context=TurnRuntimeContext(guard_context=guard_context(active_schema)),
            config={"configurable": {"thread_id": "t7"}, "recursion_limit": 200},
        )
    assert error.value.code == "MAX_REASONING_STEPS_REACHED"


class OrderSearchThenRespondModel:
    """The active_schema fixture only defines entity_a/entity_b, not the
    sales_order/customer/product entities build_progressive_plans() targets --
    every progressive plan is rejected by SchemaQueryGuard for an unknown
    entity, so this proves the zero-results path completes cleanly (no raw
    candidate data ever needed) rather than exercising real scoring, which is
    already covered by test_search_strategy.py's own fixtures."""

    def __init__(self) -> None:
        self.decide_calls = 0

    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        self.decide_calls += 1
        if self.decide_calls == 1:
            action = AgentAction(
                business_capability="order-discovery",
                action_type=ActionType.ORDER_SEARCH,
                decision_summary="Search for orders matching the free-text intent.",
                search_intent=OrderSearchIntent(orderNumbers=("10001",)),
            )
        else:
            action = AgentAction(
                business_capability="order-discovery",
                action_type=ActionType.RESPOND,
                decision_summary="No matching orders were found.",
                response=StructuredAgentResponse(
                    status="DISCOVERY_COMPLETE",
                    business_capability="order-discovery",
                    statements=(),
                ),
            )
        return ModelInvocationResult(
            action=action,
            provider="provider-a",
            model="standard-model",
            prompt_tokens=1,
            completion_tokens=1,
        )

    async def correct_action(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("must not be called")

    async def correct_response(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("must not be called")


@pytest.mark.asyncio
async def test_order_search_completes_and_caches_an_empty_result(
    active_schema: ActiveSchema,
) -> None:
    graph = make_graph(active_schema, OrderSearchThenRespondModel())
    final_state = await graph.ainvoke(
        initial_state(active_schema),
        context=TurnRuntimeContext(guard_context=guard_context(active_schema)),
        config={"configurable": {"thread_id": "t8"}},
    )
    assert final_state["final_response"]["status"] == "DISCOVERY_COMPLETE"
    assert final_state["order_search_cache"]["totalFound"] == 0
    assert len(final_state["evidence_refs"]) == 1
