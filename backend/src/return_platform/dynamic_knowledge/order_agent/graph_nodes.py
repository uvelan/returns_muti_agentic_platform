"""LangGraph node functions for Order Discovery reasoning -- a 1:1 decomposition
of the imperative loop that used to live entirely in `coordinator.py`.

Every node maps to exactly one branch of the original `process_turn()` for-loop.
Static, process-wide dependencies (guards, gateways, the compiler, the evidence
store) are bound via `GraphDependencies`, closed over when `graph.py` builds each
node function. Per-invocation, per-turn data that must never be checkpointed
(the caller's `GuardContext`, carrying principal/tenant/role information) flows
through LangGraph's `Runtime.context`, never through `OrderAgentGraphState`.

No raw `QueryEvidence` (or its raw `result`) is ever stored in state -- only
`query_execution_id` values in `evidence_refs`, written via `EvidenceStore.put()`
and rehydrated via `EvidenceStore.get_many()` immediately before a node needs the
real objects (`decide`, `respond`, `clarify`). See `state.py`'s
`OrderAgentGraphState`/`ORDER_DISCOVERY_CHECKPOINT_ALLOWLIST`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from return_platform.dynamic_knowledge.fingerprint import on_demand_request_digest, sha256_digest
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import (
    CypherCompiler,
    QueryCompilationError,
)
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
from return_platform.dynamic_knowledge.on_demand_sync.contracts import SyncOrigin
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.on_demand_sync.planner import build_targeted_read_plan
from return_platform.dynamic_knowledge.order_agent.contracts import (
    ActionType,
    AgentAction,
    AgentTurnContext,
    ModelInvocationResult,
    OrderConfirmation,
    OrderSearchIntent,
)
from return_platform.dynamic_knowledge.order_agent.errors import OrderAgentFailure
from return_platform.dynamic_knowledge.order_agent.search_strategy import (
    MAX_CACHED_CANDIDATES,
    RESULT_PAGE_SIZE,
    CustomerFulltextPolicy,
    build_customer_fulltext_plan,
    build_progressive_plans,
    candidate_key,
    narrow_fulltext_matches,
    rank_search_results,
    search_intent_signature,
)
from return_platform.dynamic_knowledge.order_agent.state import CandidateSet
from return_platform.dynamic_knowledge.order_agent.temporal_grounding import (
    normalize_session_timezone,
    resolve_date_windows,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema

logger = logging.getLogger("return_platform.dynamic_knowledge.order_agent.graph_nodes")

_OUT_OF_SCOPE_MESSAGE = (
    "That's outside what I can help with here — I'm set up for order "
    "discovery and return-management questions. Let me know if there's "
    "something in that space I can look into."
)


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


class EvidenceStore(Protocol):
    async def put(self, *, run_id: str, evidence: QueryEvidence) -> None: ...
    async def get_many(self, query_execution_ids: Sequence[str]) -> tuple[QueryEvidence, ...]: ...


@dataclass(frozen=True, slots=True)
class ConfirmedCase:
    """The case a confirmation resolved to, and whether it already existed.

    `already_existed` is not decoration: it is how a retried turn is
    distinguished from a first confirmation in the log, and it is what a caller
    would branch on before doing anything a second time.
    """

    case_id: str
    already_existed: bool


class CaseStore(Protocol):
    """Creating a case is the agent's only *write* outside its own conversation.

    Narrow on purpose. The agent may bring a case into existence and learn its
    id; it may not read other cases, list them, or change one. Anything wider
    would put the whole return domain inside the reasoning loop's reach.
    """

    async def case_facts(self, case_id: str) -> dict[str, Any]:
        """Current value per fact name for a case. Empty when it has none."""
        ...

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
    ) -> ConfirmedCase: ...


class CaseWorkflowStart(Protocol):
    """What a launcher reports back, described rather than imported.

    Structural on purpose. The concrete value is
    `workflows.return_case_launcher.StartedCaseWorkflow`, and naming that type
    here would drag `temporalio` into the reasoning module for the sake of two
    attributes. Mirrors `ConfirmedCase`: `already_running` is how a first
    confirmation is told apart from a retried or simultaneous one.
    """

    @property
    def workflow_id(self) -> str: ...

    @property
    def already_running(self) -> bool: ...


class CaseWorkflowLauncher(Protocol):
    """The agent's second and last write outside its own conversation.

    As narrow as `CaseStore` and for the same reason: the reasoning loop may
    bring the case's durable execution into being and learn its id, and it may
    do nothing else to it -- no signal, no query, no cancel. Everything after
    confirmation belongs to that workflow, and an agent able to reach into it
    would be an agent able to fake a support outcome.
    """

    async def ensure_case_workflow(
        self,
        *,
        case_id: str,
        tenant_id: str,
        principal_id: str,
        conversation_id: str,
        configuration_release_id: str,
    ) -> CaseWorkflowStart: ...


@dataclass(frozen=True, slots=True)
class TurnRuntimeContext:
    """Per-invocation data that must never be checkpointed. Passed via
    LangGraph's Runtime.context, never through OrderAgentGraphState."""

    guard_context: GuardContext


@dataclass(frozen=True, slots=True)
class GraphDependencies:
    """Static, process-wide singletons every node closes over."""

    schema: ActiveSchema
    model_gateway: ReasoningModelGateway
    knowledge_gateway: KnowledgeGateway
    evidence_store: EvidenceStore
    capability_guard: CapabilityGuard
    schema_guard: SchemaQueryGuard
    query_safety_guard: QuerySafetyGuard
    strong_anchor_guard: StrongAnchorGuard
    hallucination_guard: HallucinationGuard
    response_safety_guard: ResponseSafetyGuard
    on_demand_sync: OnDemandSyncCoordinator | None
    compiler: CypherCompiler
    # Optional for the same reason `on_demand_sync` is: a process without a
    # platform Mongo client can still search and answer. CONFIRM_ORDER fails
    # loudly rather than silently no-op'ing when it is absent.
    case_store: CaseStore | None = None
    # Optional on the same terms, and checked in the same breath: a process
    # that cannot start the case's workflow must not create the case either,
    # because the case would be durable and unreachable.
    case_workflow_launcher: CaseWorkflowLauncher | None = None
    # Which full-text index the misspelling fallback asks, and how far down its
    # ranking it reads. Defaulted rather than required so every existing
    # construction site keeps working; `runtime_factory` supplies the operator's
    # configured values, which is what makes the index name repointable without
    # a code change.
    customer_fulltext: CustomerFulltextPolicy = field(default_factory=CustomerFulltextPolicy)


async def _rehydrate_evidence(
    deps: GraphDependencies, evidence_refs: tuple[str, ...]
) -> tuple[QueryEvidence, ...]:
    if not evidence_refs:
        return ()
    return await deps.evidence_store.get_many(evidence_refs)


async def _build_context(deps: GraphDependencies, state: dict[str, Any]) -> AgentTurnContext:
    evidence = await _rehydrate_evidence(deps, state.get("evidence_refs", ()))
    requested_ids = state.get("requested_schema_entity_ids", ())
    schema_details = (
        await deps.knowledge_gateway.schema_details(deps.schema, requested_ids)
        if requested_ids
        else {}
    )
    order_search_cache = state.get("order_search_cache")
    # Read from state, never from the clock. The turn's as-of was pinned once
    # when the attempt started (coordinator._run_turn); re-reading it here --
    # this function runs on every node entry -- is exactly how a turn ends up
    # citing evidence from one "yesterday" in an answer about another.
    as_of, session_timezone = _pinned_grounding(state)
    return AgentTurnContext(
        clarification_exchanges=state.get("clarification_exchanges", ()),
        transcript=tuple(state.get("transcript", ())),
        conversation_id=state["conversation_id"],
        client_turn_id=state["client_turn_id"],
        agent_id=state["agent_id"],
        case_id=state.get("case_id"),
        correlation_id=state.get("correlation_id"),
        user_message=state["user_message"],
        as_of=as_of,
        session_timezone=session_timezone,
        resolved_date_windows=resolve_date_windows(as_of, session_timezone),
        schema_version=state["schema_version"],
        graph_generation_id=state["graph_generation_id"],
        configuration_release_id=state["configuration_release_id"],
        policy_version=state["policy_version"],
        prompt_version=state["prompt_version"],
        compact_schema=await deps.knowledge_gateway.compact_schema(deps.schema, state["agent_id"]),
        conversation_state=(
            {"orderSearchCache": order_search_cache} if order_search_cache is not None else {}
        ),
        query_evidence=evidence,
        schema_details=schema_details,
        case_facts=await _case_facts(deps, state.get("case_id")),
    )


def _pinned_grounding(state: dict[str, Any]) -> tuple[datetime, str]:
    """The turn's pinned as-of and session zone, or a loud failure.

    There is deliberately no clock fallback. A missing `as_of` means the state
    reached here without going through `coordinator._run_turn` -- which pins it
    on a fresh turn and back-fills it on a checkpoint resumed from before the
    field existed -- and quietly substituting `datetime.now()` would restore the
    exact per-node re-read this step removed, while looking like it worked.
    """
    raw = state.get("as_of")
    if not isinstance(raw, str) or not raw:
        raise OrderAgentFailure(
            "ORDER_AGENT_TURN_NOT_GROUNDED",
            "The reasoning turn has no pinned as-of instant.",
            retryable=False,
        )
    try:
        as_of = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise OrderAgentFailure(
            "ORDER_AGENT_TURN_NOT_GROUNDED",
            "The reasoning turn's pinned as-of instant is not a valid timestamp.",
            retryable=False,
        ) from exc
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    zone = state.get("session_timezone")
    return as_of, normalize_session_timezone(zone if isinstance(zone, str) else None)


async def _case_facts(deps: GraphDependencies, case_id: Any) -> dict[str, Any]:
    """The case's known facts, or nothing.

    Read per context build rather than checkpointed: the whole point is that a
    fact recorded by *another* channel since the last turn -- Support issuing an
    RMA -- is visible now, and a checkpointed copy would be exactly as stale as
    the conversation that captured it.
    """
    if not isinstance(case_id, str) or not case_id or deps.case_store is None:
        return {}
    try:
        return await deps.case_store.case_facts(case_id)
    except Exception:  # noqa: BLE001 - context is better incomplete than absent
        logger.warning("case_facts_unavailable", extra={"case_id": case_id})
        return {}


async def _invoke_decide(
    deps: GraphDependencies, context: AgentTurnContext
) -> ModelInvocationResult:
    try:
        return await deps.model_gateway.decide(context)
    except Exception as exc:
        logger.exception(
            "order_agent_decide_failed",
            extra={
                "conversation_id": context.conversation_id,
                "client_turn_id": context.client_turn_id,
            },
        )
        raise OrderAgentFailure(
            "ORDER_AGENT_LLM_FAILED",
            "The configured reasoning models could not process the request.",
            retryable=True,
        ) from exc


async def _invoke_correction(
    deps: GraphDependencies,
    *,
    context: AgentTurnContext,
    invalid_action: AgentAction,
    validation_error: str,
) -> ModelInvocationResult:
    try:
        return await deps.model_gateway.correct_action(
            context=context, invalid_action=invalid_action, validation_error=validation_error
        )
    except Exception as exc:
        raise OrderAgentFailure(
            "ORDER_AGENT_LLM_FAILED",
            "The configured reasoning models could not correct an invalid action.",
            retryable=True,
        ) from exc


async def _invoke_response_correction(
    deps: GraphDependencies,
    *,
    context: AgentTurnContext,
    invalid_response: StructuredAgentResponse,
    validation_error: str,
) -> ModelInvocationResult:
    try:
        return await deps.model_gateway.correct_response(
            context=context, invalid_response=invalid_response, validation_error=validation_error
        )
    except Exception as exc:
        raise OrderAgentFailure(
            "ORDER_AGENT_LLM_FAILED",
            "The configured reasoning models could not correct an invalid response.",
            retryable=True,
        ) from exc


def route_after_action(state: dict[str, Any]) -> str:
    action = state["action"]
    if action["action_type"] == ActionType.OUT_OF_SCOPE.value:
        return "out_of_scope"
    return "validate_action"


def route_after_validate_action(state: dict[str, Any]) -> str:
    if not state.get("capability_validated", False):
        return route_after_action(state)
    dispatch = {
        ActionType.GET_SCHEMA.value: "get_schema",
        ActionType.GRAPH_QUERY.value: "graph_query",
        ActionType.ORDER_SEARCH.value: "order_search",
        ActionType.REQUEST_ON_DEMAND_SYNC.value: "request_on_demand_sync",
        ActionType.CLARIFY.value: "clarify",
        ActionType.REPLAN.value: "replan",
        ActionType.RESPOND.value: "respond",
        ActionType.CONFIRM_ORDER.value: "confirm_order",
    }
    return dispatch[state["action"]["action_type"]]


def route_after_correctable_node(state: dict[str, Any]) -> str:
    """Shared post-node router for graph_query/request_on_demand_sync/respond/
    clarify: a correction produced a fresh `action` that must be re-routed
    exactly like a fresh decide() would be; success moves on to decide()."""
    if state.get("_corrected", False):
        return route_after_action(state)
    return "decide"


def make_decide_node(deps: GraphDependencies) -> Any:
    async def decide(state: dict[str, Any], runtime: Runtime[TurnRuntimeContext]) -> dict[str, Any]:
        del runtime
        context = await _build_context(deps, state)
        invocation = await _invoke_decide(deps, context)
        return {
            "action": invocation.action.model_dump(mode="json"),
            "last_provider": invocation.provider,
            "last_model": invocation.model,
            "_corrected": False,
        }

    return decide


def make_validate_action_node(deps: GraphDependencies) -> Any:
    async def validate_action(
        state: dict[str, Any], runtime: Runtime[TurnRuntimeContext]
    ) -> dict[str, Any]:
        guard_context = runtime.context.guard_context
        policy = guard_context.agent_policy
        if state.get("reasoning_steps_used", 0) >= policy.max_reasoning_steps:
            raise OrderAgentFailure(
                "MAX_REASONING_STEPS_REACHED",
                "The reasoning step limit was reached before discovery completed.",
                retryable=True,
            )
        action = AgentAction.model_validate(state["action"])
        correction_attempts = state.get("correction_attempts", 0)
        try:
            deps.capability_guard.validate(guard_context, action.business_capability)
        except GuardRejected as exc:
            if exc.code == "ORDER_AGENT_INVALID_CAPABILITY":
                if correction_attempts >= policy.max_correction_attempts:
                    raise OrderAgentFailure(
                        "ORDER_AGENT_OUT_OF_SCOPE", exc.message, retryable=False
                    ) from exc
                context = await _build_context(deps, state)
                invocation = await _invoke_correction(
                    deps, context=context, invalid_action=action, validation_error=exc.message
                )
                return {
                    "action": invocation.action.model_dump(mode="json"),
                    "last_provider": invocation.provider,
                    "last_model": invocation.model,
                    "correction_attempts": correction_attempts + 1,
                    "reasoning_steps_used": state.get("reasoning_steps_used", 0) + 1,
                    "capability_validated": False,
                }
            raise OrderAgentFailure(exc.code, exc.message, retryable=False) from exc
        return {
            "capability_validated": True,
            "reasoning_steps_used": state.get("reasoning_steps_used", 0) + 1,
        }

    return validate_action


def make_out_of_scope_node() -> Any:
    async def out_of_scope(
        state: dict[str, Any], runtime: Runtime[TurnRuntimeContext]
    ) -> dict[str, Any]:
        del state, runtime
        raise OrderAgentFailure("ORDER_AGENT_OUT_OF_SCOPE", _OUT_OF_SCOPE_MESSAGE, retryable=False)

    return out_of_scope


def make_get_schema_node(deps: GraphDependencies) -> Any:
    async def get_schema(
        state: dict[str, Any], runtime: Runtime[TurnRuntimeContext]
    ) -> dict[str, Any]:
        del runtime
        action = state["action"]
        requested = tuple(action["schema_entity_ids"])
        await deps.knowledge_gateway.schema_details(deps.schema, requested)
        existing = state.get("requested_schema_entity_ids", ())
        return {"requested_schema_entity_ids": tuple(sorted(set(existing) | set(requested)))}

    return get_schema


def make_graph_query_node(deps: GraphDependencies) -> Any:
    async def graph_query(
        state: dict[str, Any], runtime: Runtime[TurnRuntimeContext]
    ) -> dict[str, Any]:
        guard_context = runtime.context.guard_context
        policy = guard_context.agent_policy
        if state.get("queries_used", 0) >= policy.max_graph_queries_per_turn:
            raise OrderAgentFailure(
                "ORDER_AGENT_QUERY_BUDGET_EXCEEDED",
                "The request exceeded the configured knowledge-query limits.",
                retryable=False,
            )
        action = AgentAction.model_validate(state["action"])
        plan = action.query_plan
        if plan is None:
            raise AssertionError("validated GRAPH_QUERY action lacks query_plan")
        correction_attempts = state.get("correction_attempts", 0)
        if plan.candidate_set_id is not None:
            cache = state.get("order_search_cache")
            candidate_set_dict = (cache or {}).get("candidateSet")
            try:
                if candidate_set_dict is None:
                    raise ValueError("no candidate set is active for this conversation")
                candidate_set = CandidateSet.model_validate(candidate_set_dict)
                if candidate_set.candidate_set_id != plan.candidate_set_id:
                    raise ValueError("candidate_set_id does not match the active candidate set")
                candidate_set.validate_selection(
                    candidate_id=action.selected_candidate_id or "",
                    conversation_id=state["conversation_id"],
                    principal_id=guard_context.principal.principal_id,
                    tenant_id=guard_context.principal.tenant_id,
                    graph_generation_id=state["graph_generation_id"],
                    now=_now(),
                )
            except ValueError as exc:
                exc = GuardRejected("ORDER_AGENT_INVALID_CANDIDATE_SELECTION", str(exc))
                return await _correct_or_raise_action(
                    deps,
                    state=state,
                    action=action,
                    exc=exc,
                    correction_attempts=correction_attempts,
                    max_correction_attempts=policy.max_correction_attempts,
                )
        try:
            deps.schema_guard.validate(guard_context, plan)
            deps.query_safety_guard.validate(plan)
            compiled = deps.compiler.compile_read(deps.schema, plan)
        except (GuardRejected, ValueError) as exc:
            return await _correct_or_raise_action(
                deps,
                state=state,
                action=action,
                exc=exc,
                correction_attempts=correction_attempts,
                max_correction_attempts=policy.max_correction_attempts,
            )
        raw_result = await deps.knowledge_gateway.execute(
            schema=deps.schema,
            graph_generation_id=state["graph_generation_id"],
            plan=plan,
            compiled_cypher=compiled.cypher,
            parameters=compiled.parameters,
        )
        evidence = QueryEvidence.create(
            query_execution_id=str(uuid4()),
            schema_version=deps.schema.schema_version,
            graph_generation_id=state["graph_generation_id"],
            logical_plan_checksum=sha256_digest(plan.model_dump(mode="json")),
            compiled_query_checksum=compiled.checksum,
            result=raw_result,
        )
        await deps.evidence_store.put(run_id=state["run_id"], evidence=evidence)
        return {
            "evidence_refs": (*state.get("evidence_refs", ()), evidence.query_execution_id),
            "queries_used": state.get("queries_used", 0) + 1,
            "_corrected": False,
        }

    return graph_query


async def _correct_or_raise_action(
    deps: GraphDependencies,
    *,
    state: dict[str, Any],
    action: AgentAction,
    exc: Exception,
    correction_attempts: int,
    max_correction_attempts: int,
) -> dict[str, Any]:
    """Shared correction protocol used by graph_query/request_on_demand_sync:
    on GuardRejected/ValueError, correct up to the policy limit, else raise."""
    if correction_attempts >= max_correction_attempts:
        code = exc.code if isinstance(exc, GuardRejected) else "ORDER_AGENT_MODEL_OUTPUT_INVALID"
        raise OrderAgentFailure(code, str(exc), retryable=True) from exc
    context = await _build_context(deps, state)
    invocation = await _invoke_correction(
        deps, context=context, invalid_action=action, validation_error=str(exc)
    )
    return {
        "action": invocation.action.model_dump(mode="json"),
        "last_provider": invocation.provider,
        "last_model": invocation.model,
        "correction_attempts": correction_attempts + 1,
        "_corrected": True,
    }


def _now() -> datetime:
    return datetime.now(UTC)


def make_order_search_node(deps: GraphDependencies) -> Any:
    async def order_search(
        state: dict[str, Any], runtime: Runtime[TurnRuntimeContext]
    ) -> dict[str, Any]:
        guard_context = runtime.context.guard_context
        policy = guard_context.agent_policy
        action = AgentAction.model_validate(state["action"])
        intent = action.search_intent
        if intent is None:
            raise AssertionError("validated ORDER_SEARCH action lacks search_intent")

        cache = state.get("order_search_cache")
        queries_used = state.get("queries_used", 0)

        if intent.wantsMoreResults and cache and cache.get("evidenceRef"):
            cached_full_evidence = await deps.evidence_store.get_many((cache["evidenceRef"],))
            full_result = (
                cached_full_evidence[0].result if cached_full_evidence else {"candidates": []}
            )
            all_candidates = full_result.get("candidates", [])
            shown = int(cache.get("shown", 0))
            page = all_candidates[shown : shown + RESULT_PAGE_SIZE]
            page_result = {
                "intent": cache.get("intent"),
                "candidates": page,
                "total_found": cache.get("totalFound", len(all_candidates)),
                "unsupported_signals": [],
            }
            page_evidence = QueryEvidence.create(
                query_execution_id=str(uuid4()),
                schema_version=deps.schema.schema_version,
                graph_generation_id=state["graph_generation_id"],
                logical_plan_checksum=sha256_digest(intent.model_dump(mode="json")),
                compiled_query_checksum=sha256_digest(
                    {"cachedPage": True, "shown": shown + len(page)}
                ),
                result=page_result,
            )
            await deps.evidence_store.put(run_id=state["run_id"], evidence=page_evidence)
            updated_cache = {**cache, "shown": shown + len(page)}
            return {
                "evidence_refs": (
                    *state.get("evidence_refs", ()),
                    page_evidence.query_execution_id,
                ),
                "order_search_cache": updated_cache,
            }

        if queries_used >= policy.max_graph_queries_per_turn:
            raise OrderAgentFailure(
                "ORDER_AGENT_QUERY_BUDGET_EXCEEDED",
                "The request exceeded the configured knowledge-query limits.",
                retryable=False,
            )

        plans = build_progressive_plans(intent)
        raw_results: list[Any] = []
        compiled_checksums: list[str] = []
        for plan in plans:
            if queries_used >= policy.max_graph_queries_per_turn:
                break
            try:
                deps.schema_guard.validate(guard_context, plan)
                deps.query_safety_guard.validate(plan)
                compiled = deps.compiler.compile_read(deps.schema, plan)
            except (GuardRejected, QueryCompilationError) as exc:
                logger.debug(
                    "order_search_plan_rejected",
                    extra={
                        "conversation_id": state["conversation_id"],
                        "client_turn_id": state["client_turn_id"],
                        "operation": plan.operation.value,
                        "entity_id": plan.start_entity_id,
                        "reason": str(exc),
                    },
                )
                continue
            try:
                raw_result = await deps.knowledge_gateway.execute(
                    schema=deps.schema,
                    graph_generation_id=state["graph_generation_id"],
                    plan=plan,
                    compiled_cypher=compiled.cypher,
                    parameters=compiled.parameters,
                )
            except Exception as exc:
                raise OrderAgentFailure(
                    "ORDER_AGENT_SEARCH_EXECUTION_FAILED",
                    "The order search could not be completed against the knowledge graph.",
                    retryable=True,
                ) from exc
            raw_results.append(raw_result)
            compiled_checksums.append(compiled.checksum)
            queries_used += 1

        ranked = rank_search_results(intent, raw_results)

        if (
            ranked["total_found"] == 0
            and intent.customerNames
            and queries_used < policy.max_graph_queries_per_turn
        ):
            ranked = await _fuzzy_customer_fallback(
                deps, intent=intent, ranked=ranked, guard_context=guard_context, state=state
            )
            queries_used += 1

        all_candidates = ranked["candidates"][:MAX_CACHED_CANDIDATES]
        page_candidates = all_candidates[:RESULT_PAGE_SIZE]

        full_evidence = QueryEvidence.create(
            query_execution_id=str(uuid4()),
            schema_version=deps.schema.schema_version,
            graph_generation_id=state["graph_generation_id"],
            logical_plan_checksum=sha256_digest(intent.model_dump(mode="json")),
            compiled_query_checksum=sha256_digest({"plans": sorted(compiled_checksums)}),
            result={**ranked, "candidates": all_candidates},
        )
        await deps.evidence_store.put(run_id=state["run_id"], evidence=full_evidence)

        page_evidence = QueryEvidence.create(
            query_execution_id=str(uuid4()),
            schema_version=deps.schema.schema_version,
            graph_generation_id=state["graph_generation_id"],
            logical_plan_checksum=sha256_digest(intent.model_dump(mode="json")),
            compiled_query_checksum=sha256_digest({"plans": sorted(compiled_checksums)}),
            result={**ranked, "candidates": page_candidates},
        )
        await deps.evidence_store.put(run_id=state["run_id"], evidence=page_evidence)

        candidate_set = CandidateSet.create(
            candidate_set_id=str(uuid4()),
            conversation_id=state["conversation_id"],
            turn_id=state["client_turn_id"],
            principal_id=guard_context.principal.principal_id,
            tenant_id=guard_context.principal.tenant_id,
            schema_version=deps.schema.schema_version,
            graph_generation_id=state["graph_generation_id"],
            query_execution_id=full_evidence.query_execution_id,
            candidate_ids=tuple(
                candidate.get("candidate_id", candidate_key(candidate.get("data", {})))
                for candidate in all_candidates
            ),
            created_at=_now(),
            expires_at=_now() + timedelta(minutes=30),
        )
        new_cache = {
            "signature": search_intent_signature(intent),
            "intent": ranked["intent"],
            "evidenceRef": full_evidence.query_execution_id,
            "shown": len(page_candidates),
            "totalFound": ranked["total_found"],
            "candidateSet": candidate_set.model_dump(mode="json"),
        }
        return {
            "evidence_refs": (*state.get("evidence_refs", ()), page_evidence.query_execution_id),
            "order_search_cache": new_cache,
            "queries_used": queries_used,
        }

    return order_search


async def _fuzzy_customer_fallback(
    deps: GraphDependencies,
    *,
    intent: OrderSearchIntent,
    ranked: dict[str, Any],
    guard_context: GuardContext,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Best-effort misspelling recovery when an exact/partial name search finds nothing.

    One ranked read of the customer full-text index: every customer is searched
    server-side and the best matches come back scored, so the correct one cannot
    sit outside a window the way it could when this fetched an unordered batch
    and compared strings on the client.

    Never blocks or fails the turn if this step itself errors -- the associate is
    no worse off than the zero-result search that triggered it. It does say so in
    the log, though: a full-text index that is missing or offline degrades every
    misspelled name to "not found", and that is an infrastructure fault to fix
    rather than a search that legitimately found nothing.
    """
    plan = build_customer_fulltext_plan(intent.customerNames, deps.customer_fulltext)
    if plan is None:
        return ranked
    try:
        deps.schema_guard.validate(guard_context, plan)
        deps.query_safety_guard.validate(plan)
        compiled = deps.compiler.compile_read(deps.schema, plan)
        raw_result = await deps.knowledge_gateway.execute(
            schema=deps.schema,
            graph_generation_id=state["graph_generation_id"],
            plan=plan,
            compiled_cypher=compiled.cypher,
            parameters=compiled.parameters,
        )
    except Exception:
        logger.warning(
            "order_search_fuzzy_fallback_unavailable",
            exc_info=True,
            extra={
                "conversation_id": state["conversation_id"],
                "client_turn_id": state["client_turn_id"],
                "fulltext_index": plan.fulltext_index,
            },
        )
        return ranked

    rows = raw_result.get("rows", []) if isinstance(raw_result, dict) else []
    matches = narrow_fulltext_matches(rows, policy=deps.customer_fulltext)
    if not matches:
        return ranked

    logger.info(
        "order_search_fuzzy_fallback_matched",
        extra={
            "conversation_id": state["conversation_id"],
            "client_turn_id": state["client_turn_id"],
            "match_count": len(matches),
            "returned_rows": len(rows),
        },
    )
    # `customer_name_fuzzy` and a score below every confirmed signal, on purpose:
    # a name the associate half-remembered is a candidate to show, never a fact
    # to act on. The best match keeps the 0.6 the constant-scored version gave
    # every hit; the rest are scaled by how far behind it they ranked, so the
    # order the index computed survives into the candidate set instead of being
    # flattened into a tie.
    best_score = matches[0][1]
    candidates = [
        {
            "candidate_id": candidate_key(row),
            "data": row,
            "score": round(0.6 * (score / best_score), 4),
            "matches": ["customer_name_fuzzy"],
        }
        for row, score in matches
    ]
    return {
        "intent": ranked["intent"],
        "candidates": candidates,
        "total_found": len(candidates),
        "unsupported_signals": ranked["unsupported_signals"],
    }


def make_request_on_demand_sync_node(deps: GraphDependencies) -> Any:
    async def request_on_demand_sync(
        state: dict[str, Any], runtime: Runtime[TurnRuntimeContext]
    ) -> dict[str, Any]:
        guard_context = runtime.context.guard_context
        policy = guard_context.agent_policy
        if deps.on_demand_sync is None:
            raise OrderAgentFailure(
                "ON_DEMAND_SYNC_SOURCE_UNAVAILABLE",
                "Targeted source synchronization is not enabled.",
                retryable=True,
            )
        if state.get("targeted_syncs_used", 0) >= policy.max_targeted_syncs_per_turn:
            raise OrderAgentFailure(
                "ORDER_AGENT_SYNC_BUDGET_EXCEEDED",
                "The request exceeded the configured on-demand synchronization limits.",
                retryable=False,
            )
        action = AgentAction.model_validate(state["action"])
        anchor_request = action.strong_anchor_request
        original_query = action.original_query_plan
        if anchor_request is None or original_query is None:
            raise AssertionError("validated sync action lacks required payload")
        correction_attempts = state.get("correction_attempts", 0)
        try:
            normalized_values = deps.strong_anchor_guard.validate(guard_context, anchor_request)
            deps.schema_guard.validate(guard_context, original_query)
            deps.query_safety_guard.validate(original_query)
        except GuardRejected as exc:
            if correction_attempts >= policy.max_correction_attempts:
                raise OrderAgentFailure(exc.code, exc.message, retryable=True) from exc
            context = await _build_context(deps, state)
            invocation = await _invoke_correction(
                deps, context=context, invalid_action=action, validation_error=exc.message
            )
            return {
                "action": invocation.action.model_dump(mode="json"),
                "last_provider": invocation.provider,
                "last_model": invocation.model,
                "correction_attempts": correction_attempts + 1,
                "_corrected": True,
            }
        entity = deps.schema.entities[anchor_request.entity_id]
        normalized_for_plan = {
            anchor.field_id: (anchor.operator, normalized_values[anchor.field_id])
            for anchor in anchor_request.anchors
        }
        source_plan = build_targeted_read_plan(
            schema=deps.schema,
            entity_id=anchor_request.entity_id,
            normalized_anchors=normalized_for_plan,
        )
        digest = on_demand_request_digest(
            tenant_scope=guard_context.principal.tenant_id,
            source_asset_id=entity.source_asset_id,
            entity_id=anchor_request.entity_id,
            strong_anchor_id=anchor_request.strong_anchor_id,
            normalized_anchors=normalized_values,
            schema_version=deps.schema.schema_version,
            graph_generation_id=state["graph_generation_id"],
            mapping_version=deps.schema.compiler_version,
        )
        receipt = await deps.on_demand_sync.synchronize(
            schema=deps.schema,
            graph_generation_id=state["graph_generation_id"],
            request_digest=digest,
            plan=source_plan,
            # What the sync control screen shows an operator: a run that no human
            # started, attributed to the turn that needed it.
            origin=SyncOrigin(
                agent_id=state["agent_id"],
                conversation_id=state["conversation_id"],
                client_turn_id=state["client_turn_id"],
                entity_id=anchor_request.entity_id,
                strong_anchor_id=anchor_request.strong_anchor_id,
                anchor_field_ids=tuple(sorted(normalized_values)),
            ),
        )
        logger.info(
            "order_agent_on_demand_sync_completed",
            extra={
                "conversation_id": state["conversation_id"],
                "client_turn_id": state["client_turn_id"],
                "sync_request_id": receipt.sync_request_id,
                "source_asset_id": source_plan.source_asset_id,
                "status": receipt.status.value,
                # The number that says whether the escalation actually helped.
                # A SUCCEEDED sync that wrote nothing is the failure mode this
                # whole path was rebuilt around: the source answered and the
                # projection threw the answer away.
                "nodes_written": receipt.nodes_written,
                "source_rows_read": receipt.source_rows_read,
            },
        )
        compiled = deps.compiler.compile_read(deps.schema, original_query)
        raw_result = await deps.knowledge_gateway.execute(
            schema=deps.schema,
            graph_generation_id=state["graph_generation_id"],
            plan=original_query,
            compiled_cypher=compiled.cypher,
            parameters=compiled.parameters,
        )
        evidence = QueryEvidence.create(
            query_execution_id=str(uuid4()),
            schema_version=deps.schema.schema_version,
            graph_generation_id=state["graph_generation_id"],
            logical_plan_checksum=sha256_digest(original_query.model_dump(mode="json")),
            compiled_query_checksum=compiled.checksum,
            result=raw_result,
        )
        await deps.evidence_store.put(run_id=state["run_id"], evidence=evidence)
        return {
            "evidence_refs": (*state.get("evidence_refs", ()), evidence.query_execution_id),
            "queries_used": state.get("queries_used", 0) + 1,
            "targeted_syncs_used": state.get("targeted_syncs_used", 0) + 1,
            "_corrected": False,
        }

    return request_on_demand_sync


def make_clarify_node(deps: GraphDependencies) -> Any:
    async def clarify(
        state: dict[str, Any], runtime: Runtime[TurnRuntimeContext]
    ) -> dict[str, Any]:
        guard_context = runtime.context.guard_context
        policy = guard_context.agent_policy
        if state.get("clarifications_used", 0) >= policy.max_clarifications:
            raise OrderAgentFailure(
                "ORDER_AGENT_CLARIFICATION_BUDGET_EXCEEDED",
                "The request exceeded the configured clarification limits.",
                retryable=False,
            )
        action = AgentAction.model_validate(state["action"])
        assert action.response is not None  # guaranteed by AgentAction's own validator
        correction_attempts = state.get("correction_attempts", 0)
        try:
            deps.response_safety_guard.validate(
                action.response,
                allowed_capabilities=guard_context.agent_policy.allowed_business_capabilities,
            )
        except GuardRejected as exc:
            raise OrderAgentFailure(exc.code, exc.message, retryable=True) from exc
        evidence = await _rehydrate_evidence(deps, state.get("evidence_refs", ()))
        validation = deps.hallucination_guard.validate(
            response=action.response,
            evidence=evidence,
            graph_generation_id=state["graph_generation_id"],
        )
        if not validation.valid:
            if correction_attempts >= policy.max_correction_attempts:
                raise OrderAgentFailure(
                    "ORDER_AGENT_RESPONSE_VALIDATION_FAILED",
                    "The response could not be validated against the active knowledge graph.",
                    retryable=True,
                )
            context = await _build_context(deps, state)
            invocation = await _invoke_response_correction(
                deps,
                context=context,
                invalid_response=action.response,
                validation_error="; ".join(item.reason for item in validation.failures),
            )
            return {
                "action": invocation.action.model_dump(mode="json"),
                "last_provider": invocation.provider,
                "last_model": invocation.model,
                "correction_attempts": correction_attempts + 1,
                "_corrected": True,
            }
        # Pause the whole graph execution here and surface the clarifying question
        # to the caller. `interrupt()` raises GraphInterrupt on this pass; the
        # coordinator turns that into the turn's visible response and records the
        # thread as pending. When the associate answers (a later HTTP request /
        # Temporal update), the graph is resumed with Command(resume=<answer>) and
        # LangGraph RE-EXECUTES THIS NODE FROM ITS FIRST LINE -- everything above
        # runs a second time, which is safe precisely because it is all read-only
        # validation (budget check, two guards, an evidence read). Nothing above
        # may ever be given a side effect without revisiting this.
        answer = interrupt(action.response.model_dump(mode="json"))
        return {
            "clarification_exchanges": (
                *state.get("clarification_exchanges", ()),
                {
                    "question": action.response.requested_input or "",
                    "answer": str(answer),
                },
            ),
            "clarifications_used": state.get("clarifications_used", 0) + 1,
            # Drop the CLARIFY action so the resumed `decide` produces a fresh one
            # against the newly-answered context instead of re-proposing this one.
            "action": None,
            "_corrected": False,
        }

    return clarify


def make_confirm_order_node(deps: GraphDependencies) -> Any:
    async def confirm_order(
        state: dict[str, Any], runtime: Runtime[TurnRuntimeContext]
    ) -> dict[str, Any]:
        """Confirms exactly one order for this conversation and starts the case's
        durable workflow.

        The node is idempotent on (tenant | conversation | order | line-set): a
        repeated or simultaneous confirmation returns the existing case rather
        than creating a second one. CandidateSet.validate_selection re-binds the
        selection to the conversation, principal, tenant and graph generation
        before anything is written, so a candidate captured in one conversation
        cannot be confirmed in another.

        After the case is committed, this node starts exactly one
        ReturnCaseWorkflow, keyed by return_case_workflow_id(case_id). The start
        is idempotent: an existing execution with that id is adopted rather than
        duplicated. Everything after confirmation - concurrent Bay Assignment,
        the support conversation, durable business-time waits and reminders, RMA
        recording and propagation of the RMA back into this conversation -
        belongs to that workflow and to nothing else.

        If the workflow cannot be started, the confirmation fails. A case that
        exists without its workflow is unreachable by every downstream agent.

        The failure is reported as retryable and the case is left committed, so
        the next attempt at the same confirmation resolves to the same case and
        starts the same workflow id. `workflows/return_case_recovery.py` is what
        closes the gap when no next attempt arrives.
        """
        guard_context = runtime.context.guard_context
        if deps.case_store is None:
            raise OrderAgentFailure(
                "ORDER_AGENT_CASE_STORE_UNAVAILABLE",
                "Return cases cannot be created in this process.",
                retryable=True,
            )
        if deps.case_workflow_launcher is None:
            # Checked *before* the case is written, not after. A process that
            # cannot start the workflow would otherwise commit a durable case
            # and then discover it has no way to make it reachable -- the exact
            # orphan this node exists to prevent.
            raise OrderAgentFailure(
                "ORDER_AGENT_CASE_WORKFLOW_UNAVAILABLE",
                "The case's durable workflow cannot be started from this process.",
                retryable=True,
            )
        action = AgentAction.model_validate(state["action"])
        confirmation = action.order_confirmation
        if confirmation is None:
            raise AssertionError("validated CONFIRM_ORDER action lacks order_confirmation")

        cache = state.get("order_search_cache")
        candidate_set_dict = (cache or {}).get("candidateSet")
        correction_attempts = state.get("correction_attempts", 0)
        policy = guard_context.agent_policy
        try:
            if candidate_set_dict is None:
                raise ValueError("no candidate set is active for this conversation")
            candidate_set = CandidateSet.model_validate(candidate_set_dict)
            if candidate_set.candidate_set_id != confirmation.candidate_set_id:
                raise ValueError("candidate_set_id does not match the active candidate set")
            candidate_set.validate_selection(
                candidate_id=confirmation.candidate_id,
                conversation_id=state["conversation_id"],
                principal_id=guard_context.principal.principal_id,
                tenant_id=guard_context.principal.tenant_id,
                graph_generation_id=state["graph_generation_id"],
                now=_now(),
            )
        except ValueError as error:
            return await _correct_or_raise_action(
                deps,
                state=state,
                action=action,
                exc=GuardRejected("ORDER_AGENT_INVALID_CANDIDATE_SELECTION", str(error)),
                correction_attempts=correction_attempts,
                max_correction_attempts=policy.max_correction_attempts,
            )

        case = await deps.case_store.confirm_case(
            tenant_id=guard_context.principal.tenant_id,
            principal_id=guard_context.principal.principal_id,
            branch_ids=tuple(sorted(guard_context.principal.branch_ids)),
            conversation_id=state["conversation_id"],
            confirmation=confirmation,
            configuration_release_id=state["configuration_release_id"],
            graph_generation_id=state["graph_generation_id"],
        )
        # Attempted on every confirmation, including one that resolved to an
        # existing case. A turn that committed the case and then failed to start
        # the workflow is precisely the state that needs the next attempt to try
        # again, and skipping the start for `already_existed` would make the
        # retry a no-op that reports success.
        try:
            started = await deps.case_workflow_launcher.ensure_case_workflow(
                case_id=case.case_id,
                tenant_id=guard_context.principal.tenant_id,
                principal_id=guard_context.principal.principal_id,
                conversation_id=state["conversation_id"],
                configuration_release_id=state["configuration_release_id"],
            )
        except Exception as error:
            logger.error(
                "order_agent_case_workflow_start_failed case_id=%s error=%s",
                case.case_id,
                error,
                extra={
                    "conversation_id": state["conversation_id"],
                    "client_turn_id": state["client_turn_id"],
                    "case_id": case.case_id,
                },
                exc_info=True,
            )
            raise OrderAgentFailure(
                "ORDER_AGENT_CASE_WORKFLOW_START_FAILED",
                "The return was recorded but its workflow could not be started.",
                retryable=True,
            ) from error
        logger.info(
            "order_agent_order_confirmed",
            extra={
                "conversation_id": state["conversation_id"],
                "client_turn_id": state["client_turn_id"],
                "case_id": case.case_id,
                "already_existed": case.already_existed,
                "case_workflow_id": started.workflow_id,
                "case_workflow_already_running": started.already_running,
            },
        )
        return {
            "case_id": case.case_id,
            # Back to `decide` rather than ending the turn: the associate has
            # confirmed, and the agent still owes them a sentence saying so.
            # Ending here would commit the case and return silence.
            "action": None,
            "_corrected": False,
        }

    return confirm_order


def make_replan_node() -> Any:
    async def replan(state: dict[str, Any], runtime: Runtime[TurnRuntimeContext]) -> dict[str, Any]:
        policy = runtime.context.guard_context.agent_policy
        if state.get("replans_used", 0) >= policy.max_replans:
            raise OrderAgentFailure(
                "ORDER_AGENT_REPLAN_BUDGET_EXCEEDED",
                "The request exceeded the configured replanning limits.",
                retryable=False,
            )
        return {
            "evidence_refs": (),
            "order_search_cache": None,
            "replans_used": state.get("replans_used", 0) + 1,
        }

    return replan


def make_respond_node(deps: GraphDependencies) -> Any:
    async def respond(
        state: dict[str, Any], runtime: Runtime[TurnRuntimeContext]
    ) -> dict[str, Any]:
        guard_context = runtime.context.guard_context
        policy = guard_context.agent_policy
        action = AgentAction.model_validate(state["action"])
        assert action.response is not None  # guaranteed by AgentAction's own validator
        correction_attempts = state.get("correction_attempts", 0)
        try:
            deps.response_safety_guard.validate(
                action.response,
                allowed_capabilities=guard_context.agent_policy.allowed_business_capabilities,
            )
        except GuardRejected as exc:
            raise OrderAgentFailure(exc.code, exc.message, retryable=True) from exc
        evidence = await _rehydrate_evidence(deps, state.get("evidence_refs", ()))
        validation = deps.hallucination_guard.validate(
            response=action.response,
            evidence=evidence,
            graph_generation_id=state["graph_generation_id"],
        )
        if not validation.valid:
            # The reasons go in the *message*, not only in `extra`. They were
            # already computed and then discarded by every log formatter that
            # does not render extras, which left the only record of why a turn
            # was refused sitting in a dictionary nobody prints -- an operator
            # saw "could not be validated" and had nowhere to go. The reasons
            # name statement ids, evidence paths and generation ids, all of
            # which are platform identifiers rather than customer data.
            reasons = "; ".join(
                f"{item.statement_id}: {item.reason}" for item in validation.failures
            )
            logger.warning(
                "order_agent_response_validation_failed attempt=%d failures=%d reasons=%s",
                correction_attempts,
                len(validation.failures),
                reasons,
                extra={
                    "conversation_id": state["conversation_id"],
                    "client_turn_id": state["client_turn_id"],
                    "correction_attempts": correction_attempts,
                    "failures": [
                        {"statement_id": item.statement_id, "reason": item.reason}
                        for item in validation.failures
                    ],
                },
            )
            if correction_attempts >= policy.max_correction_attempts:
                raise OrderAgentFailure(
                    "ORDER_AGENT_RESPONSE_VALIDATION_FAILED",
                    "The response could not be validated against the active knowledge graph.",
                    retryable=True,
                )
            context = await _build_context(deps, state)
            invocation = await _invoke_response_correction(
                deps,
                context=context,
                invalid_response=action.response,
                validation_error="; ".join(item.reason for item in validation.failures),
            )
            return {
                "action": invocation.action.model_dump(mode="json"),
                "last_provider": invocation.provider,
                "last_model": invocation.model,
                "correction_attempts": correction_attempts + 1,
                "_corrected": True,
            }
        return {"final_response": action.response.model_dump(mode="json"), "_corrected": False}

    return respond


NODE_NAMES: tuple[str, ...] = (
    "decide",
    "validate_action",
    "out_of_scope",
    "get_schema",
    "graph_query",
    "order_search",
    "request_on_demand_sync",
    "clarify",
    "replan",
    "respond",
    "confirm_order",
)

__all__ = [
    "END",
    "NODE_NAMES",
    "CaseStore",
    "CaseWorkflowLauncher",
    "CaseWorkflowStart",
    "ConfirmedCase",
    "GraphDependencies",
    "TurnRuntimeContext",
    "make_clarify_node",
    "make_confirm_order_node",
    "make_decide_node",
    "make_get_schema_node",
    "make_graph_query_node",
    "make_order_search_node",
    "make_out_of_scope_node",
    "make_replan_node",
    "make_request_on_demand_sync_node",
    "make_respond_node",
    "make_validate_action_node",
    "route_after_action",
    "route_after_correctable_node",
    "route_after_validate_action",
]
