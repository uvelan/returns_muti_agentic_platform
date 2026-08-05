"""Multi-step LLM reasoning loop with no deterministic business fallback."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import uuid4

from return_platform.dynamic_knowledge.fingerprint import on_demand_request_digest, sha256_digest
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.evidence import (
    QueryEvidence,
    StructuredAgentResponse,
)
from return_platform.dynamic_knowledge.knowledge.guards import (
    CapabilityGuard,
    GuardContext,
    GuardRejected,
    HallucinationGuard,
    QuerySafetyGuard,
    ResponseSafetyGuard,
    SchemaQueryGuard,
    StrongAnchorGuard,
)
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.planner import build_targeted_read_plan
from return_platform.dynamic_knowledge.order_agent.contracts import (
    ActionType,
    AgentAction,
    AgentTurnContext,
    AgentTurnRequest,
    AgentTurnResult,
    ModelInvocationResult,
)
from return_platform.dynamic_knowledge.order_agent.search_strategy import (
    build_progressive_plans,
    rank_search_results,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema


class OrderAgentFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class ReasoningModelGateway(Protocol):
    async def decide(self, context: AgentTurnContext) -> ModelInvocationResult: ...
    async def correct_action(
        self,
        *,
        context: AgentTurnContext,
        invalid_action: AgentAction | None,
        validation_error: str,
    ) -> ModelInvocationResult: ...
    async def correct_response(
        self,
        *,
        context: AgentTurnContext,
        invalid_response: StructuredAgentResponse,
        validation_error: str,
    ) -> ModelInvocationResult: ...


class KnowledgeGateway(Protocol):
    async def compact_schema(self, schema: ActiveSchema, agent_id: str) -> dict[str, Any]: ...
    async def schema_details(
        self, schema: ActiveSchema, entity_ids: tuple[str, ...]
    ) -> dict[str, Any]: ...
    async def execute(
        self,
        *,
        schema: ActiveSchema,
        graph_generation_id: str,
        plan: Any,
        compiled_cypher: str,
        parameters: dict[str, Any],
    ) -> Any: ...


class ConversationStore(Protocol):
    async def load_for_turn(
        self,
        *,
        request: AgentTurnRequest,
        graph_generation_id: str,
    ) -> tuple[int, dict[str, Any], AgentTurnResult | None]: ...

    async def commit_turn(
        self,
        *,
        request: AgentTurnRequest,
        expected_version: int,
        result: AgentTurnResult,
    ) -> AgentTurnResult: ...


class GraphStateProvider(Protocol):
    async def active_generation(self, schema: ActiveSchema) -> str: ...


class DynamicOrderAgentCoordinator:
    """All conversational interpretation is delegated to a standard reasoning model."""

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
        response_safety_guard: ResponseSafetyGuard | None = None,
        on_demand_sync: OnDemandSyncCoordinator | None,
        cypher_compiler: CypherCompiler | None = None,
    ) -> None:
        self._schema = schema
        self._model = model_gateway
        self._knowledge = knowledge_gateway
        self._conversations = conversation_store
        self._graph_state = graph_state
        self._capability_guard = capability_guard
        self._schema_guard = schema_guard
        self._query_safety_guard = query_safety_guard
        self._strong_anchor_guard = strong_anchor_guard
        self._hallucination_guard = hallucination_guard
        self._response_safety_guard = response_safety_guard or ResponseSafetyGuard()
        self._on_demand_sync = on_demand_sync
        self._compiler = cypher_compiler or CypherCompiler()

    async def process_turn(
        self, request: AgentTurnRequest, guard_context: GuardContext
    ) -> AgentTurnResult:
        policy = self._schema.agent_policies.get(request.agent_id)
        if policy is None or policy.agent_id != guard_context.agent_policy.agent_id:
            raise OrderAgentFailure(
                "ORDER_AGENT_OUT_OF_SCOPE", "Agent policy is unavailable.", retryable=False
            )
        graph_generation_id = await self._graph_state.active_generation(self._schema)
        version, state, replay = await self._conversations.load_for_turn(
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
        context = AgentTurnContext(
            conversation_id=request.conversation_id,
            client_turn_id=request.client_turn_id,
            user_message=request.message,
            schema_version=self._schema.schema_version,
            graph_generation_id=graph_generation_id,
            configuration_release_id=self._schema.configuration_release_id,
            policy_version=self._schema.policy_version,
            prompt_version=self._schema.prompt_version,
            compact_schema=await self._knowledge.compact_schema(self._schema, request.agent_id),
            conversation_state=state,
        )
        invocation = await self._invoke_decide(context)
        last_provider = invocation.provider
        last_model = invocation.model
        queries_used = 0
        correction_attempts = 0

        for _step in range(policy.max_reasoning_steps):
            action = invocation.action
            if action.action_type is ActionType.OUT_OF_SCOPE:
                raise OrderAgentFailure(
                    "ORDER_AGENT_OUT_OF_SCOPE",
                    "I can assist only with configured order discovery and return-management business operations.",
                    retryable=False,
                )
            try:
                self._capability_guard.validate(guard_context, action.business_capability)
            except GuardRejected as exc:
                raise OrderAgentFailure(exc.code, exc.message, retryable=False) from exc

            if action.action_type is ActionType.GET_SCHEMA:
                details = await self._knowledge.schema_details(
                    self._schema, action.schema_entity_ids
                )
                context = context.model_copy(
                    update={"schema_details": {**context.schema_details, **details}}
                )
                invocation = await self._invoke_decide(context)
                last_provider, last_model = invocation.provider, invocation.model
                continue

            if action.action_type is ActionType.GRAPH_QUERY:
                if queries_used >= policy.max_graph_queries_per_turn:
                    raise OrderAgentFailure(
                        "ORDER_AGENT_QUERY_BUDGET_EXCEEDED",
                        "The request exceeded the configured knowledge-query limits.",
                        retryable=False,
                    )
                plan = action.query_plan
                if plan is None:
                    raise AssertionError("validated GRAPH_QUERY action lacks query_plan")
                try:
                    self._schema_guard.validate(guard_context, plan)
                    self._query_safety_guard.validate(plan)
                    compiled = self._compiler.compile_read(self._schema, plan)
                except (GuardRejected, ValueError) as exc:
                    if correction_attempts >= policy.max_correction_attempts:
                        code = (
                            exc.code
                            if isinstance(exc, GuardRejected)
                            else "ORDER_AGENT_MODEL_OUTPUT_INVALID"
                        )
                        raise OrderAgentFailure(code, str(exc), retryable=True) from exc
                    correction_attempts += 1
                    invocation = await self._invoke_correction(
                        context=context,
                        invalid_action=action,
                        validation_error=str(exc),
                    )
                    last_provider, last_model = invocation.provider, invocation.model
                    continue
                raw_result = await self._knowledge.execute(
                    schema=self._schema,
                    graph_generation_id=graph_generation_id,
                    plan=plan,
                    compiled_cypher=compiled.cypher,
                    parameters=compiled.parameters,
                )
                evidence = QueryEvidence.create(
                    query_execution_id=str(uuid4()),
                    schema_version=self._schema.schema_version,
                    graph_generation_id=graph_generation_id,
                    logical_plan_checksum=sha256_digest(plan.model_dump(mode="json")),
                    compiled_query_checksum=compiled.checksum,
                    result=raw_result,
                )
                queries_used += 1
                context = context.model_copy(
                    update={"query_evidence": (*context.query_evidence, evidence)}
                )
                invocation = await self._invoke_decide(context)
                last_provider, last_model = invocation.provider, invocation.model
                continue

            if action.action_type is ActionType.ORDER_SEARCH:
                if queries_used >= policy.max_graph_queries_per_turn:
                    raise OrderAgentFailure(
                        "ORDER_AGENT_QUERY_BUDGET_EXCEEDED",
                        "The request exceeded the configured knowledge-query limits.",
                        retryable=False,
                    )
                intent = action.search_intent
                if intent is None:
                    raise AssertionError("validated ORDER_SEARCH action lacks search_intent")
                
                plans = build_progressive_plans(intent)
                raw_results = []
                for plan in plans:
                    if queries_used >= policy.max_graph_queries_per_turn:
                        break
                    try:
                        self._query_safety_guard.validate(plan)
                        compiled = self._compiler.compile_read(self._schema, plan)
                        raw_result = await self._knowledge.execute(
                            schema=self._schema,
                            graph_generation_id=graph_generation_id,
                            plan=plan,
                            compiled_cypher=compiled.cypher,
                            parameters=compiled.parameters,
                        )
                        raw_results.append(raw_result)
                        queries_used += 1
                    except Exception:
                        pass # Ignore failing query plans during progressive search
                        
                ranked = rank_search_results(intent, raw_results)
                
                evidence = QueryEvidence.create(
                    query_execution_id=str(uuid4()),
                    schema_version=self._schema.schema_version,
                    graph_generation_id=graph_generation_id,
                    logical_plan_checksum=sha256_digest(intent.model_dump(mode="json")),
                    compiled_query_checksum="ORDER_SEARCH_STRATEGY",
                    result=ranked,
                )
                context = context.model_copy(
                    update={"query_evidence": (*context.query_evidence, evidence)}
                )
                invocation = await self._invoke_decide(context)
                last_provider, last_model = invocation.provider, invocation.model
                continue

            if action.action_type is ActionType.REQUEST_ON_DEMAND_SYNC:
                if self._on_demand_sync is None:
                    raise OrderAgentFailure(
                        "ON_DEMAND_SYNC_SOURCE_UNAVAILABLE",
                        "Targeted source synchronization is not enabled.",
                        retryable=True,
                    )
                anchor_request = action.strong_anchor_request
                original_query = action.original_query_plan
                if anchor_request is None or original_query is None:
                    raise AssertionError("validated sync action lacks required payload")
                try:
                    normalized_values = self._strong_anchor_guard.validate(
                        guard_context, anchor_request
                    )
                    self._schema_guard.validate(guard_context, original_query)
                    self._query_safety_guard.validate(original_query)
                except GuardRejected as exc:
                    if correction_attempts >= policy.max_correction_attempts:
                        raise OrderAgentFailure(exc.code, exc.message, retryable=True) from exc
                    correction_attempts += 1
                    invocation = await self._invoke_correction(
                        context=context,
                        invalid_action=action,
                        validation_error=exc.message,
                    )
                    last_provider, last_model = invocation.provider, invocation.model
                    continue
                entity = self._schema.entities[anchor_request.entity_id]
                normalized_for_plan = {
                    anchor.field_id: (anchor.operator, normalized_values[anchor.field_id])
                    for anchor in anchor_request.anchors
                }
                source_plan = build_targeted_read_plan(
                    schema=self._schema,
                    entity_id=anchor_request.entity_id,
                    normalized_anchors=normalized_for_plan,
                )
                digest = on_demand_request_digest(
                    tenant_scope=guard_context.principal.tenant_id,
                    source_asset_id=entity.source_asset_id,
                    entity_id=anchor_request.entity_id,
                    strong_anchor_id=anchor_request.strong_anchor_id,
                    normalized_anchors=normalized_values,
                    schema_version=self._schema.schema_version,
                    graph_generation_id=graph_generation_id,
                    mapping_version=self._schema.compiler_version,
                )
                await self._on_demand_sync.synchronize(
                    schema=self._schema,
                    graph_generation_id=graph_generation_id,
                    request_digest=digest,
                    plan=source_plan,
                )
                compiled = self._compiler.compile_read(self._schema, original_query)
                raw_result = await self._knowledge.execute(
                    schema=self._schema,
                    graph_generation_id=graph_generation_id,
                    plan=original_query,
                    compiled_cypher=compiled.cypher,
                    parameters=compiled.parameters,
                )
                evidence = QueryEvidence.create(
                    query_execution_id=str(uuid4()),
                    schema_version=self._schema.schema_version,
                    graph_generation_id=graph_generation_id,
                    logical_plan_checksum=sha256_digest(original_query.model_dump(mode="json")),
                    compiled_query_checksum=compiled.checksum,
                    result=raw_result,
                )
                queries_used += 1
                context = context.model_copy(
                    update={"query_evidence": (*context.query_evidence, evidence)}
                )
                invocation = await self._invoke_decide(context)
                last_provider, last_model = invocation.provider, invocation.model
                continue

            if action.action_type in {ActionType.RESPOND, ActionType.OUT_OF_SCOPE}:
                if action.response is None:
                    raise AssertionError("validated response action lacks response")
                try:
                    self._response_safety_guard.validate(
                        action.response,
                        allowed_capabilities=guard_context.agent_policy.allowed_business_capabilities,
                    )
                except GuardRejected as exc:
                    raise OrderAgentFailure(exc.code, exc.message, retryable=True) from exc
                validation = self._hallucination_guard.validate(
                    response=action.response,
                    evidence=context.query_evidence,
                    graph_generation_id=graph_generation_id,
                )
                if not validation.valid:
                    if correction_attempts >= policy.max_correction_attempts:
                        raise OrderAgentFailure(
                            "ORDER_AGENT_RESPONSE_VALIDATION_FAILED",
                            "The response could not be validated against the active knowledge graph.",
                            retryable=True,
                        )
                    correction_attempts += 1
                    invocation = await self._invoke_response_correction(
                        context=context,
                        invalid_response=action.response,
                        validation_error="; ".join(item.reason for item in validation.failures),
                    )
                    last_provider, last_model = invocation.provider, invocation.model
                    continue
                return await self._commit_response(
                    request=request,
                    expected_version=version,
                    context=context,
                    response=action.response,
                    provider=last_provider,
                    model=last_model,
                )

        raise OrderAgentFailure(
            "MAX_REASONING_STEPS_REACHED",
            "The reasoning step limit was reached before discovery completed.",
            retryable=True,
        )

    async def _invoke_decide(self, context: AgentTurnContext) -> ModelInvocationResult:
        try:
            return await self._model.decide(context)
        except Exception as exc:
            print("DEBUG EXCEPTION IN DECIDE:", repr(exc))
            raise OrderAgentFailure(
                "ORDER_AGENT_LLM_FAILED",
                "The configured reasoning models could not process the request.",
                retryable=True,
            ) from exc

    async def _invoke_correction(
        self,
        *,
        context: AgentTurnContext,
        invalid_action: AgentAction,
        validation_error: str,
    ) -> ModelInvocationResult:
        try:
            return await self._model.correct_action(
                context=context,
                invalid_action=invalid_action,
                validation_error=validation_error,
            )
        except Exception as exc:
            raise OrderAgentFailure(
                "ORDER_AGENT_LLM_FAILED",
                "The configured reasoning models could not correct an invalid action.",
                retryable=True,
            ) from exc

    async def _invoke_response_correction(
        self,
        *,
        context: AgentTurnContext,
        invalid_response: StructuredAgentResponse,
        validation_error: str,
    ) -> ModelInvocationResult:
        try:
            return await self._model.correct_response(
                context=context,
                invalid_response=invalid_response,
                validation_error=validation_error,
            )
        except Exception as exc:
            raise OrderAgentFailure(
                "ORDER_AGENT_LLM_FAILED",
                "The configured reasoning models could not correct an invalid response.",
                retryable=True,
            ) from exc

    async def _commit_response(
        self,
        *,
        request: AgentTurnRequest,
        expected_version: int,
        context: AgentTurnContext,
        response: StructuredAgentResponse,
        provider: str,
        model: str,
    ) -> AgentTurnResult:
        provisional = AgentTurnResult(
            conversation_id=request.conversation_id,
            conversation_version=expected_version + 1,
            client_turn_id=request.client_turn_id,
            graph_generation_id=context.graph_generation_id,
            response=response,
            query_evidence=context.query_evidence,
            model_provider=provider,
            model_name=model,
        )
        return await self._conversations.commit_turn(
            request=request,
            expected_version=expected_version,
            result=provisional,
        )
