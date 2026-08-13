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

from dataclasses import dataclass

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

__all__ = ["OrderDiscoveryActivities", "OrderDiscoveryRuntime"]


@dataclass(frozen=True, slots=True)
class OrderDiscoveryRuntime:
    """One coordinator and the schema it was built from, as an inseparable pair.

    The two must agree: the schema decides the agent policy and what the guards
    admit, while the coordinator compiles queries and pins
    `configuration_release_id` onto the turn from a schema of its own. A process
    holding one from release N and the other from N+1 would evaluate guards
    against a different schema than it queried with, so they are replaced
    together or not at all.
    """

    coordinator: DynamicOrderAgentCoordinator
    schema: ActiveSchema


class OrderDiscoveryActivities:
    """Narrow injected activity surface: one coordinator, one schema.

    The pair is swappable because the Order Agent's reasoning runs here rather
    than in the API process, so a release an administrator activates has to
    reach *this* process to take effect (T-16). `adopt` is called by the
    worker's configuration reconciler, never from workflow code -- see
    `scripts/run_order_discovery_worker.py`.
    """

    def __init__(self, *, coordinator: DynamicOrderAgentCoordinator, schema: ActiveSchema) -> None:
        self._runtime = OrderDiscoveryRuntime(coordinator=coordinator, schema=schema)

    @property
    def runtime(self) -> OrderDiscoveryRuntime:
        return self._runtime

    def adopt(self, runtime: OrderDiscoveryRuntime) -> None:
        """Point subsequent turns at a newly activated release.

        One attribute assignment of one frozen pair, which is the whole
        mechanism: a turn already running holds the pair it read on entry and
        finishes on the release it started with, and the next activity task
        picks up the new one. Nothing observes a half-swapped process.
        """

        self._runtime = runtime

    @activity.defn(name="run_order_discovery_turn")
    async def run_order_discovery_turn(
        self, request: RunOrderDiscoveryTurnActivityInput
    ) -> OrderDiscoveryTurnOutcome:
        # The turn's one configuration read. Everything below uses this pair,
        # so an activation mid-turn cannot change the schema underneath a
        # conversation that has already pinned its `configuration_release_id`.
        runtime = self._runtime
        policy = runtime.schema.agent_policies.get(request.agent_id)
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
            schema=runtime.schema,
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
            session_timezone=request.session_timezone,
        )
        try:
            result = await runtime.coordinator.process_turn(
                agent_turn_request,
                guard_context,
                workflow_id=request.workflow_id,
                resume_thread_id=request.resume_thread_id,
                correlation_id=request.correlation_id,
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
