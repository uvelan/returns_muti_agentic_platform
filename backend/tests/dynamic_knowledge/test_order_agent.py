from __future__ import annotations

from typing import Any

import pytest

from return_platform.dynamic_knowledge.knowledge.evidence import (
    EvidenceReference,
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
    AgentTurnRequest,
    AgentTurnResult,
    ModelInvocationResult,
)
from return_platform.dynamic_knowledge.order_agent.coordinator import (
    DynamicOrderAgentCoordinator,
    OrderAgentFailure,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


class GraphState:
    async def active_generation(self, schema: ActiveSchema) -> str:
        return "generation-1"


class ConversationStore:
    def __init__(self) -> None:
        self.result: AgentTurnResult | None = None
        self.conversation_state: dict[str, Any] = {}

    async def load_for_turn(
        self,
        *,
        request: AgentTurnRequest,
        graph_generation_id: str,
    ) -> tuple[int, dict[str, Any], AgentTurnResult | None]:
        version = self.result.conversation_version if self.result is not None else 0
        return version, dict(self.conversation_state), None

    async def commit_turn(
        self,
        *,
        request: AgentTurnRequest,
        expected_version: int,
        result: AgentTurnResult,
        conversation_state: dict[str, Any],
    ) -> AgentTurnResult:
        self.result = result
        self.conversation_state = conversation_state
        return result


class Knowledge:
    async def compact_schema(self, schema: ActiveSchema, agent_id: str) -> dict[str, Any]:
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
        assert graph_generation_id == "generation-1"
        assert "DELETE" not in compiled_cypher
        return {"rows": [{"id": "A-1", "name": "Configured value"}], "total": 1}


class FailingModel:
    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
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
    """First action uses a wrongly-formatted (but conceptually correct)
    business_capability, exactly like a real model returning 'ORDER_DISCOVERY'
    instead of 'order-discovery' - this must be corrected, not hard-failed."""

    def __init__(self) -> None:
        self.correction_calls = 0

    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult:
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
            action=action, provider="provider-a", model="standard-model",
            prompt_tokens=5, completion_tokens=5,
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
            action=action, provider="provider-a", model="standard-model",
            prompt_tokens=5, completion_tokens=5,
        )

    async def correct_response(self, **kwargs: Any) -> ModelInvocationResult:
        raise AssertionError("response correction not expected in this test")


def build_coordinator(schema: ActiveSchema, model: Any) -> DynamicOrderAgentCoordinator:
    return DynamicOrderAgentCoordinator(
        schema=schema,
        model_gateway=model,
        knowledge_gateway=Knowledge(),
        conversation_store=ConversationStore(),
        graph_state=GraphState(),
        capability_guard=CapabilityGuard(),
        schema_guard=SchemaQueryGuard(),
        query_safety_guard=QuerySafetyGuard(QuerySafetyPolicy()),
        strong_anchor_guard=StrongAnchorGuard(),
        hallucination_guard=HallucinationGuard(),
        on_demand_sync=None,
    )


def guard_context(schema: ActiveSchema) -> GuardContext:
    return GuardContext(
        schema=schema,
        agent_policy=schema.agent_policies["agent_a"],
        principal=PrincipalContext(
            principal_id="p1", tenant_id="t1", roles=frozenset({"associate"})
        ),
    )


def turn() -> AgentTurnRequest:
    return AgentTurnRequest(
        conversation_id="c1",
        expected_conversation_version=0,
        client_turn_id="ct1",
        idempotency_key="ik1",
        message_id="m1",
        message="Find the configured record A-1",
        agent_id="agent_a",
    )


@pytest.mark.asyncio
async def test_model_failure_is_explicit_and_has_no_fallback(active_schema: ActiveSchema) -> None:
    with pytest.raises(OrderAgentFailure) as error:
        await build_coordinator(active_schema, FailingModel()).process_turn(
            turn(), guard_context(active_schema)
        )
    assert error.value.code == "ORDER_AGENT_LLM_FAILED"


@pytest.mark.asyncio
async def test_every_turn_uses_model_and_returns_evidence_bound_response(
    active_schema: ActiveSchema,
) -> None:
    result = await build_coordinator(active_schema, QueryThenRespondModel()).process_turn(
        turn(), guard_context(active_schema)
    )
    assert result.model_name == "standard-model"
    assert result.response.status == "DISCOVERY_COMPLETE"
    assert len(result.query_evidence) == 1


@pytest.mark.asyncio
async def test_miscased_capability_is_corrected_not_hard_failed(
    active_schema: ActiveSchema,
) -> None:
    """A real model returning 'ORDER_DISCOVERY' instead of 'order-discovery' is a
    formatting mistake, not an out-of-scope request - it must get a correction
    attempt like any other invalid action, not an immediate hard failure."""
    model = MiscasedCapabilityThenValidModel()
    result = await build_coordinator(active_schema, model).process_turn(
        turn(), guard_context(active_schema)
    )
    assert model.correction_calls == 1
    assert result.response.status == "DISCOVERY_COMPLETE"
    assert result.response.business_capability == "order-discovery"
