"""Temporal activity wrapping DynamicOrderAgentCoordinator.process_turn.

Holds a real coordinator (constructed once at worker startup, exactly like
`dynamic_knowledge.integration.runtime_factory.build_dynamic_order_agent_runtime`
does for the FastAPI process) and reconstructs `GuardContext` per invocation
from the minimal identity fields carried across the Temporal boundary --
mirroring `runtime_factory.py`'s own `guard_context_factory` closure, which
does the same reconstruction from FastAPI request state. The Activity never
receives (or needs) the full `ActiveSchema` on the wire; it already holds one.
"""

from __future__ import annotations

from temporalio import activity

from return_platform.dynamic_knowledge.knowledge.guards import GuardContext, PrincipalContext
from return_platform.dynamic_knowledge.order_agent.contracts import AgentTurnRequest
from return_platform.dynamic_knowledge.order_agent.coordinator import (
    DynamicOrderAgentCoordinator,
    OrderAgentFailure,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.workflows.order_discovery_workflow import (
    AgentTurnResultPayload,
    OrderDiscoveryTurnError,
    OrderDiscoveryTurnOutcome,
    RunOrderDiscoveryTurnActivityInput,
)

__all__ = ["OrderDiscoveryActivities"]


class OrderDiscoveryActivities:
    """Narrow injected activity surface: one coordinator, one schema."""

    def __init__(self, *, coordinator: DynamicOrderAgentCoordinator, schema: ActiveSchema) -> None:
        self._coordinator = coordinator
        self._schema = schema

    @activity.defn(name="run_order_discovery_turn")
    async def run_order_discovery_turn(
        self, request: RunOrderDiscoveryTurnActivityInput
    ) -> OrderDiscoveryTurnOutcome:
        policy = self._schema.agent_policies.get(request.agent_id)
        if policy is None:
            return OrderDiscoveryTurnOutcome(
                result=None,
                error=OrderDiscoveryTurnError(
                    code="ORDER_AGENT_OUT_OF_SCOPE",
                    message="Agent policy is unavailable.",
                    retryable=False,
                ),
            )
        guard_context = GuardContext(
            schema=self._schema,
            agent_policy=policy,
            principal=PrincipalContext(
                principal_id=request.principal_id,
                tenant_id=request.tenant_id,
                roles=request.roles,
                branch_ids=request.branch_ids,
            ),
        )
        agent_turn_request = AgentTurnRequest(
            conversation_id=request.conversation_id,
            expected_conversation_version=request.expected_conversation_version,
            client_turn_id=request.client_turn_id,
            idempotency_key=request.idempotency_key,
            message_id=request.message_id,
            message=request.message,
            agent_id=request.agent_id,
        )
        try:
            result = await self._coordinator.process_turn(
                agent_turn_request,
                guard_context,
                workflow_id=request.workflow_id,
                resume_thread_id=request.resume_thread_id,
            )
        except OrderAgentFailure as exc:
            return OrderDiscoveryTurnOutcome(
                result=None,
                error=OrderDiscoveryTurnError(
                    code=exc.code, message=exc.message, retryable=exc.retryable
                ),
            )
        return OrderDiscoveryTurnOutcome(
            result=AgentTurnResultPayload(
                conversation_id=result.conversation_id,
                conversation_version=result.conversation_version,
                client_turn_id=result.client_turn_id,
                agent_turn_result_json=result.model_dump_json(),
                pending_clarification_thread_id=result.pending_clarification_thread_id,
            ),
            error=None,
        )
