"""Order Discovery turn orchestration: builds/wraps the LangGraph StateGraph
defined in graph.py/graph_nodes.py, and owns everything that graph itself must
not: conversation load/commit, reasoning-run lifecycle bookkeeping, and
evidence rehydration for the final committed AgentTurnResult.

No business logic lives here anymore -- see graph_nodes.py for that. This
module is the seam between the durable conversation record (ConversationStore)
and one reasoning attempt (the compiled graph, checkpointed independently).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, cast

from pymongo import AsyncMongoClient

from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.evidence import (
    QueryEvidence,
    StructuredAgentResponse,
)
from return_platform.dynamic_knowledge.knowledge.guards import (
    CapabilityGuard,
    GuardContext,
    HallucinationGuard,
    QuerySafetyGuard,
    ResponseSafetyGuard,
    SchemaQueryGuard,
    StrongAnchorGuard,
)
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.order_agent.contracts import (
    AgentTurnRequest,
    AgentTurnResult,
)
from return_platform.dynamic_knowledge.order_agent.errors import OrderAgentFailure
from return_platform.dynamic_knowledge.order_agent.graph import build_order_agent_graph
from return_platform.dynamic_knowledge.order_agent.graph_nodes import (
    EvidenceStore,
    GraphDependencies,
    KnowledgeGateway,
    ReasoningModelGateway,
    TurnRuntimeContext,
)
from return_platform.dynamic_knowledge.order_agent.state import OrderAgentGraphState
from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.platform.reasoning.checkpoint import SystemStoreCheckpointSaver
from return_platform.platform.reasoning.retention import (
    CheckpointRetentionPolicy,
    RunLifecycleState,
)
from return_platform.platform.reasoning.run_lifecycle import ReasoningRunLifecycle
from return_platform.platform.reasoning.thread_ids import ReasoningThreadIdFactory
from return_platform.platform.secrets.envelope import EnvelopeEncryptor
from return_platform.platform.system_store.repository import SystemStore

__all__ = ["DynamicOrderAgentCoordinator", "OrderAgentFailure"]

# Each policy-enforced loop turn is several LangGraph super-steps (decide ->
# validate_action -> a business node -> back to decide); the default
# recursion_limit (25) is comfortably exceeded by policy's own allowed ceiling
# (max_reasoning_steps up to 32), so every invocation raises this explicitly
# rather than risking LangGraph's own GraphRecursionError masking a real
# OrderAgentFailure("MAX_REASONING_STEPS_REACHED", ...) that would otherwise fire first.
_RECURSION_LIMIT = 256


class ConversationStore(Protocol):
    async def load_for_turn(
        self,
        *,
        request: AgentTurnRequest,
        graph_generation_id: str,
    ) -> tuple[int, dict[str, object], AgentTurnResult | None]: ...

    async def commit_turn(
        self,
        *,
        request: AgentTurnRequest,
        expected_version: int,
        result: AgentTurnResult,
        conversation_state: dict[str, object],
    ) -> AgentTurnResult: ...


class GraphStateProvider(Protocol):
    async def active_generation(self, schema: ActiveSchema) -> str: ...


class DynamicOrderAgentCoordinator:
    """Owns one compiled Order Discovery reasoning graph and the conversation/
    run-lifecycle bookkeeping around invoking it. All conversational
    interpretation is delegated to the graph's `decide` node's standard
    reasoning model -- there is no deterministic business fallback."""

    def __init__(
        self,
        *,
        schema: ActiveSchema,
        model_gateway: ReasoningModelGateway,
        knowledge_gateway: KnowledgeGateway,
        conversation_store: ConversationStore,
        graph_state: GraphStateProvider,
        capability_guard: CapabilityGuard,
        schema_guard: SchemaQueryGuard,
        query_safety_guard: QuerySafetyGuard,
        strong_anchor_guard: StrongAnchorGuard,
        hallucination_guard: HallucinationGuard,
        evidence_store: EvidenceStore,
        system_store: SystemStore,
        envelope_encryptor: EnvelopeEncryptor,
        mongo_client: AsyncMongoClient[dict[str, object]],
        response_safety_guard: ResponseSafetyGuard | None = None,
        on_demand_sync: OnDemandSyncCoordinator | None,
        cypher_compiler: CypherCompiler | None = None,
        terminal_retention_hours: float = 168.0,
    ) -> None:
        self._schema = schema
        self._conversations = conversation_store
        self._graph_state = graph_state
        self._evidence_store = evidence_store
        self._mongo_client = mongo_client
        self._system_store = system_store
        self._run_lifecycle = ReasoningRunLifecycle(system_store)
        self._retention = CheckpointRetentionPolicy(
            terminal_retention_hours=terminal_retention_hours
        )

        deps = GraphDependencies(
            schema=schema,
            model_gateway=model_gateway,
            knowledge_gateway=knowledge_gateway,
            evidence_store=evidence_store,
            capability_guard=capability_guard,
            schema_guard=schema_guard,
            query_safety_guard=query_safety_guard,
            strong_anchor_guard=strong_anchor_guard,
            hallucination_guard=hallucination_guard,
            response_safety_guard=response_safety_guard or ResponseSafetyGuard(),
            on_demand_sync=on_demand_sync,
            compiler=cypher_compiler or CypherCompiler(),
        )
        checkpointer = SystemStoreCheckpointSaver(system_store, envelope_encryptor)
        self._graph = build_order_agent_graph(deps, checkpointer=checkpointer)

    async def process_turn(
        self,
        request: AgentTurnRequest,
        guard_context: GuardContext,
        *,
        workflow_id: str | None = None,
    ) -> AgentTurnResult:
        """`workflow_id` is optional and stamped onto the run's `reasoning_runs`
        record verbatim -- the Temporal workflow host (Wave C2, Commit 3) passes
        its own `workflow.info().workflow_id` so `abandonment.py`'s "active
        Temporal workflow" precondition can find it; callers with no Temporal
        workflow of their own (direct/test invocations) leave it unset."""
        policy = self._schema.agent_policies.get(request.agent_id)
        if policy is None or policy.agent_id != guard_context.agent_policy.agent_id:
            raise OrderAgentFailure(
                "ORDER_AGENT_OUT_OF_SCOPE", "Agent policy is unavailable.", retryable=False
            )
        graph_generation_id = await self._graph_state.active_generation(self._schema)
        version, conversation_state, replay = await self._conversations.load_for_turn(
            request=request,
            graph_generation_id=graph_generation_id,
        )
        if replay is not None:
            return replay
        if version != request.expected_conversation_version:
            raise OrderAgentFailure(
                "CONVERSATION_VERSION_CONFLICT",
                "The conversation was updated by another request.",
                retryable=True,
            )

        thread_id = ReasoningThreadIdFactory.order_discovery_thread_id(
            conversation_id=request.conversation_id, turn_id=request.client_turn_id, attempt=1
        )
        run_id = thread_id
        await self._run_lifecycle.start_run(
            run_id=run_id, thread_id=thread_id, workflow_id=workflow_id
        )

        initial_state: OrderAgentGraphState = {
            "conversation_id": request.conversation_id,
            "client_turn_id": request.client_turn_id,
            "user_message": request.message,
            "schema_version": self._schema.schema_version,
            "graph_generation_id": graph_generation_id,
            "configuration_release_id": self._schema.configuration_release_id,
            "policy_version": self._schema.policy_version,
            "prompt_version": self._schema.prompt_version,
            "agent_id": request.agent_id,
            "run_id": run_id,
            "requested_schema_entity_ids": (),
            "evidence_refs": (),
            "order_search_cache": cast(
                "dict[str, Any] | None", conversation_state.get("orderSearchCache")
            ),
            "action": None,
            "reasoning_steps_used": 0,
            "queries_used": 0,
            "correction_attempts": 0,
            "clarifications_used": 0,
            "replans_used": 0,
            "targeted_syncs_used": 0,
            "final_response": None,
        }

        try:
            final_state = await self._graph.ainvoke(
                initial_state,
                context=TurnRuntimeContext(guard_context=guard_context),
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": _RECURSION_LIMIT,
                },
            )
        except Exception:
            await self._retention.mark_terminal(
                self._system_store,
                self._mongo_client,
                run_id=run_id,
                thread_id=thread_id,
                lifecycle_state=RunLifecycleState.FAILED,
                terminal_at=datetime.now(UTC),
            )
            raise

        await self._retention.mark_terminal(
            self._system_store,
            self._mongo_client,
            run_id=run_id,
            thread_id=thread_id,
            lifecycle_state=RunLifecycleState.COMPLETED,
            terminal_at=datetime.now(UTC),
        )

        response = StructuredAgentResponse.model_validate(final_state["final_response"])
        evidence: tuple[QueryEvidence, ...] = await self._evidence_store.get_many(
            final_state.get("evidence_refs", ())
        )
        new_conversation_state = {
            **conversation_state,
            "orderSearchCache": final_state.get("order_search_cache"),
        }
        provisional = AgentTurnResult(
            conversation_id=request.conversation_id,
            conversation_version=version + 1,
            client_turn_id=request.client_turn_id,
            graph_generation_id=graph_generation_id,
            response=response,
            query_evidence=evidence,
            model_provider=final_state["last_provider"],
            model_name=final_state["last_model"],
        )
        return await self._conversations.commit_turn(
            request=request,
            expected_version=version,
            result=provisional,
            conversation_state=new_conversation_state,
        )
