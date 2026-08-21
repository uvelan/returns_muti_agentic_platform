"""Order Discovery turn orchestration: builds/wraps the LangGraph StateGraph
defined in graph.py/graph_nodes.py, and owns everything that graph itself must
not: conversation load/commit, reasoning-run lifecycle bookkeeping, and
evidence rehydration for the final committed AgentTurnResult.

No business logic lives here anymore -- see graph_nodes.py for that. This
module is the seam between the durable conversation record (ConversationStore)
and one reasoning attempt (the compiled graph, checkpointed independently).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from langgraph.types import Command
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
from return_platform.dynamic_knowledge.lifecycle.handle import GenerationHandleProvider
from return_platform.dynamic_knowledge.lifecycle.lease_store import GenerationLeaseStore
from return_platform.dynamic_knowledge.lifecycle.mongo_store import ActiveRuntimeSnapshotStore
from return_platform.dynamic_knowledge.on_demand_sync.coordinator import OnDemandSyncCoordinator
from return_platform.dynamic_knowledge.order_agent.contracts import (
    AgentTurnRequest,
    AgentTurnResult,
    CapturedTurnFact,
)
from return_platform.dynamic_knowledge.order_agent.conversation_repository import (
    ConversationScope,
)
from return_platform.dynamic_knowledge.order_agent.errors import OrderAgentFailure
from return_platform.dynamic_knowledge.order_agent.facts import FactCatalogue
from return_platform.dynamic_knowledge.order_agent.graph import build_order_agent_graph
from return_platform.dynamic_knowledge.order_agent.graph_nodes import (
    CaseStore,
    CaseWorkflowLauncher,
    EvidenceStore,
    GraphDependencies,
    KnowledgeGateway,
    ReasoningModelGateway,
    TurnRuntimeContext,
)
from return_platform.dynamic_knowledge.order_agent.identification import IdentificationCatalogue
from return_platform.dynamic_knowledge.order_agent.search_strategy import CustomerFulltextPolicy
from return_platform.dynamic_knowledge.order_agent.state import (
    TRANSCRIPT_LIMIT,
    OrderAgentGraphState,
)
from return_platform.dynamic_knowledge.order_agent.temporal_grounding import (
    normalize_session_timezone,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema, GenerationBinding
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


_LOGGER = logging.getLogger(__name__)


class ConversationStore(Protocol):
    async def load_for_turn(
        self,
        *,
        request: AgentTurnRequest,
        graph_generation_id: str,
        scope: ConversationScope,
    ) -> tuple[int, dict[str, object], AgentTurnResult | None]: ...

    async def commit_turn(
        self,
        *,
        request: AgentTurnRequest,
        expected_version: int,
        result: AgentTurnResult,
        conversation_state: dict[str, object],
        scope: ConversationScope,
    ) -> AgentTurnResult: ...


def _stored_observed_facts(conversation_state: dict[str, object]) -> tuple[dict[str, Any], ...]:
    """The facts earlier turns of this conversation captured.

    Defensive about shape because conversation documents outlive releases: a
    conversation started before this field existed simply has none.
    """
    stored = conversation_state.get("observedFacts")
    if not isinstance(stored, list):
        return ()
    return tuple(entry for entry in stored if isinstance(entry, dict))


def _scope_of(guard_context: GuardContext) -> ConversationScope:
    """The conversation's owner, taken from the turn's own authenticated identity.

    Deliberately derived here rather than accepted as a parameter: the only
    identity a turn may act under is the one its `GuardContext` already carries,
    and a scope passed in alongside it is a second, forgeable answer to the same
    question.
    """
    return ConversationScope(
        tenant_id=guard_context.principal.tenant_id,
        principal_id=guard_context.principal.principal_id,
    )


class GraphStateProvider(Protocol):
    async def active_generation(self, schema: ActiveSchema) -> str: ...


def _stored_transcript(conversation_state: dict[str, Any]) -> tuple[dict[str, str], ...]:
    """The transcript held on the conversation record, narrowed to its contract.

    Values on the record are `object`: it is persisted JSON that a previous
    release, a migration, or a hand-edit could have left in any shape. Anything
    that is not a role/text pair of strings is dropped rather than trusted,
    because a malformed entry here would reach the reasoning prompt.
    """
    stored = conversation_state.get("transcript")
    if not isinstance(stored, list):
        return ()
    return tuple(
        {"role": str(entry["role"]), "text": str(entry["text"])}
        for entry in stored
        if isinstance(entry, dict) and "role" in entry and "text" in entry
    )


def _extended_transcript(
    conversation_state: dict[str, Any],
    *,
    user_message: str,
    response: StructuredAgentResponse | None,
) -> list[dict[str, str]]:
    """This turn appended to what was said before, oldest first and bounded.

    The agent used to see only the current message plus the previous search's
    cache, so it could not tell a first mention from a repeat and would re-ask
    a question it had already asked. Only the response's own statement text is
    carried -- never evidence rows, which are rehydrated from the evidence store
    and must not be duplicated into conversation history.

    A turn that produced no response (a pause awaiting a clarification answer)
    still records what the associate said: the question the agent asked is
    already carried in `clarification_exchanges`.
    """
    transcript: list[dict[str, str]] = list(_stored_transcript(conversation_state))
    transcript.append({"role": "associate", "text": user_message})
    if response is not None:
        agent_text = " ".join(statement.text for statement in response.statements).strip()
        if agent_text:
            transcript.append({"role": "agent", "text": agent_text})
    return transcript[-TRANSCRIPT_LIMIT:]


def _cache_for_generation(
    cache: dict[str, Any] | None, graph_generation_id: str
) -> dict[str, Any] | None:
    """Drop a cached order search that belongs to a different generation.

    `orderSearchCache` survives between turns on the conversation record, and it
    holds an `evidenceRef` plus a `CandidateSet` built against one specific
    generation. Paging through it after a rebuild would serve candidates from a
    generation that may already be retired, and selecting one raises "candidate
    set belongs to a stale graph generation" from `validate_selection`. Reading
    the generation off the embedded CandidateSet avoids stamping a second copy
    of it onto the cache.

    A cache with no CandidateSet is left alone -- it predates this check and has
    no selection to invalidate.
    """
    if cache is None:
        return None
    candidate_set = cache.get("candidateSet")
    if not isinstance(candidate_set, dict):
        return cache
    cached_generation = candidate_set.get("graph_generation_id")
    if isinstance(cached_generation, str) and cached_generation != graph_generation_id:
        return None
    return cache


def _pinned_generation_of(values: dict[str, Any]) -> str | None:
    """The generation a paused checkpoint was reading, if it names one."""
    pinned = values.get("graph_generation_id")
    return pinned if isinstance(pinned, str) and pinned else None


def _has_pinned_grounding(values: dict[str, Any] | None) -> bool:
    if not values:
        return False
    as_of = values.get("as_of")
    return isinstance(as_of, str) and bool(as_of)


def _committed_grounding(final_state: dict[str, Any]) -> dict[str, Any]:
    """The as-of and zone the finished turn actually ran under, for the record.

    Read back off the final state rather than reused from the value pinned at
    the top of `_run_turn`, because on a resume those two differ: the pinned one
    is this HTTP request's clock read and the state's is the attempt's. The
    record must say which the reasoning used, or a replay reconstructs a
    different question than the one that was answered.
    """
    raw = final_state.get("as_of")
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        as_of = datetime.fromisoformat(raw)
    except ValueError:
        return {}
    zone = final_state.get("session_timezone")
    return {
        "as_of": as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=UTC),
        "session_timezone": normalize_session_timezone(zone if isinstance(zone, str) else None),
    }


def _committed_facts(final_state: dict[str, Any]) -> tuple[CapturedTurnFact, ...]:
    """The conversation's captured facts, as the turn reports them back.

    The merged set the graph is carrying, not this turn's new statements: the
    console's extracted-facts panel is a view of what the conversation knows,
    and a panel that only ever showed the last turn's additions would empty
    itself the moment the associate answered a question about something else.

    Entries are read defensively for the reason `_stored_observed_facts` gives:
    the list travels through a conversation document that outlives releases, and
    an entry that is not a named fact is dropped rather than reported as one.
    """
    stored = final_state.get("observed_facts", ())
    if not isinstance(stored, tuple | list):
        return ()
    facts: list[CapturedTurnFact] = []
    for entry in stored:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        facts.append(
            CapturedTurnFact(
                name=name,
                value=entry.get("value"),
                status=str(entry.get("status", "USABLE")),
                label=str(entry.get("label", "")),
                acquisition=str(entry.get("acquisition", "STATED")),
            )
        )
    return tuple(facts)


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
        generation_lease_store: GenerationLeaseStore | None = None,
        owner_instance_id: str = "order-agent",
        active_snapshot_store: ActiveRuntimeSnapshotStore | None = None,
        case_store: CaseStore | None = None,
        case_workflow_launcher: CaseWorkflowLauncher | None = None,
        customer_fulltext: CustomerFulltextPolicy | None = None,
        identification: IdentificationCatalogue | None = None,
        facts: FactCatalogue | None = None,
    ) -> None:
        self._schema = schema
        self._conversations = conversation_store
        self._graph_state = graph_state
        # Constructed here rather than injected: the rule is that generation
        # resolution goes through a handle, and letting a caller supply its own
        # resolver would reopen the door this closes. The lease store stays
        # optional so existing construction sites keep working unleased.
        self._generation_handles = GenerationHandleProvider(
            graph_state,
            lease_store=generation_lease_store,
            owner_instance_id=owner_instance_id,
            snapshot_store=active_snapshot_store,
        )
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
            case_store=case_store,
            case_workflow_launcher=case_workflow_launcher,
            customer_fulltext=customer_fulltext or CustomerFulltextPolicy(),
            identification=identification or IdentificationCatalogue(),
            facts=facts or FactCatalogue(),
        )
        checkpointer = SystemStoreCheckpointSaver(system_store, envelope_encryptor)
        self._graph = build_order_agent_graph(deps, checkpointer=checkpointer)

    async def process_turn(
        self,
        request: AgentTurnRequest,
        guard_context: GuardContext,
        *,
        workflow_id: str | None = None,
        resume_thread_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AgentTurnResult:
        """`workflow_id` is optional and stamped onto the run's `reasoning_runs`
        record verbatim -- the Temporal workflow host (Wave C2, Commit 3) passes
        its own `workflow.info().workflow_id` so `abandonment.py`'s "active
        Temporal workflow" precondition can find it; callers with no Temporal
        workflow of their own (direct/test invocations) leave it unset.

        `resume_thread_id`, when set, means this turn is the associate's answer
        to a clarifying question that suspended an earlier turn's graph
        execution: instead of starting a fresh reasoning attempt, the existing
        paused thread is resumed with `Command(resume=request.message)` and
        continues from inside the `clarify` node. Only the Temporal workflow
        knows a clarification is outstanding, so only it passes this.

        `correlation_id` is the platform request id of the API call behind this
        turn. It is a keyword here rather than a field on `AgentTurnRequest`
        because the server sets it: a request-body field would let a client
        supply its own, putting unvalidated caller text into the telemetry
        stream every dashboard reads."""
        policy = self._schema.agent_policies.get(request.agent_id)
        if policy is None or policy.agent_id != guard_context.agent_policy.agent_id:
            raise OrderAgentFailure(
                "ORDER_AGENT_OUT_OF_SCOPE", "Agent policy is unavailable.", retryable=False
            )
        # Resolving the generation and claiming a read lease on it are one step
        # (see lifecycle/handle.py). The lease is held for exactly this turn and
        # released on the way out, including on failure -- notably *not* across a
        # clarification pause, which can last days and would pin a generation
        # against retirement for the whole time. A resumed turn takes its own
        # lease here.
        paused_values = await self._paused_checkpoint_values(resume_thread_id)
        pinned = _pinned_generation_of(paused_values)
        if pinned is not None and policy.generation_binding is GenerationBinding.STRICT_PINNING:
            # Lease the generation the conversation started on, not whatever is
            # current: leasing "current" would leave the pinned generation
            # unprotected and free to retire while this turn reads it.
            async with self._generation_handles.acquire_read_pinned(pinned) as handle:
                return await self._run_turn(
                    request,
                    guard_context,
                    graph_generation_id=handle.graph_generation_id,
                    workflow_id=workflow_id,
                    resume_thread_id=resume_thread_id,
                    rebound_from=None,
                    paused_values=paused_values,
                    correlation_id=correlation_id,
                )

        async with self._generation_handles.acquire_read(self._schema) as handle:
            rebound_from = (
                pinned if pinned is not None and pinned != handle.graph_generation_id else None
            )
            return await self._run_turn(
                request,
                guard_context,
                graph_generation_id=handle.graph_generation_id,
                workflow_id=workflow_id,
                resume_thread_id=resume_thread_id,
                rebound_from=rebound_from,
                paused_values=paused_values,
                correlation_id=correlation_id,
            )

    async def _paused_checkpoint_values(self, resume_thread_id: str | None) -> dict[str, Any]:
        """The paused turn's own checkpoint state, or `{}` when there is none.

        Read once and handed to `_run_turn` rather than fetched per question of
        it: the resume path needs both the pinned generation and whether the
        checkpoint predates the turn's as-of, and two `aget_state` calls for one
        checkpoint invite the two answers to come from different reads.

        Returns `{}` rather than raising if the checkpoint is gone (swept by
        abandonment, expired retention): a resumed turn with no checkpoint has
        nothing to rebind *from*, and the graph invocation will fail on its own
        terms rather than here.
        """
        if resume_thread_id is None:
            return {}
        try:
            snapshot = await self._graph.aget_state(
                {"configurable": {"thread_id": resume_thread_id}}
            )
        except Exception:
            _LOGGER.exception(
                "Could not read the paused checkpoint for thread %s; treating the turn as unpinned",
                resume_thread_id,
            )
            return {}
        values = getattr(snapshot, "values", None) or {}
        return cast("dict[str, Any]", values) if isinstance(values, dict) else {}

    async def _run_turn(
        self,
        request: AgentTurnRequest,
        guard_context: GuardContext,
        *,
        graph_generation_id: str,
        workflow_id: str | None,
        resume_thread_id: str | None,
        rebound_from: str | None = None,
        paused_values: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> AgentTurnResult:
        """The turn itself, with its generation already resolved and pinned.

        Split out so the lease's scope is a single visible `async with` rather
        than a hundred-line indented block whose exit path is easy to lose.

        `rebound_from` is set when a resumed turn landed on a different
        generation than the one it paused on -- see the resume handling below."""
        scope = _scope_of(guard_context)
        version, conversation_state, replay = await self._conversations.load_for_turn(
            request=request,
            graph_generation_id=graph_generation_id,
            scope=scope,
        )
        if replay is not None:
            return replay
        if version != request.expected_conversation_version:
            raise OrderAgentFailure(
                "CONVERSATION_VERSION_CONFLICT",
                "The conversation was updated by another request.",
                retryable=True,
            )

        # A resumed clarification deliberately keeps the ORIGINAL turn's thread:
        # one paused graph execution is still one reasoning attempt, it just spans
        # two HTTP requests. thread_ids.py's "a thread is one attempt, not one
        # conversation" invariant is preserved -- this is the same attempt.
        thread_id = resume_thread_id or ReasoningThreadIdFactory.order_discovery_thread_id(
            conversation_id=request.conversation_id, turn_id=request.client_turn_id, attempt=1
        )
        run_id = thread_id
        # Idempotent for an already-registered thread, which is exactly the resume
        # case (see test_run_lifecycle.py::test_start_run_is_idempotent_for_the_same_thread).
        #
        # The turn's one clock read, and it comes back from the run record rather
        # than from `datetime.now()` so that it is the same instant on every
        # attempt at this turn. This runs inside a Temporal *activity*
        # (workflows/order_discovery_activities.py) or directly under FastAPI --
        # never in a workflow body -- so reading a real clock is allowed here and
        # nowhere further in; everything downstream reads it out of graph state.
        #
        # Re-reading the clock per attempt was not a cosmetic difference. The
        # instant travels in `contextJson.as_of` and in the temporal-grounding
        # prompt, so it is part of the request digest: a retried turn asked a
        # *different* question, and durable interception -- which recognises a
        # held request by that digest -- could hold a turn but never resume one.
        # An operator's answer was opened, answered, and then looked for under an
        # id nothing would ever derive again.
        as_of = await self._run_lifecycle.start_run(
            run_id=run_id, thread_id=thread_id, workflow_id=workflow_id
        )
        session_timezone = normalize_session_timezone(request.session_timezone)

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
            "as_of": as_of.isoformat(),
            "session_timezone": session_timezone,
            "correlation_id": correlation_id,
            "requested_schema_entity_ids": (),
            "evidence_refs": (),
            "order_search_cache": _cache_for_generation(
                cast("dict[str, Any] | None", conversation_state.get("orderSearchCache")),
                graph_generation_id,
            ),
            # Carried forward whole. Unlike the search cache these do not belong
            # to a graph generation -- what the associate said they are
            # returning does not stop being true because the graph was rebuilt.
            "observed_facts": _stored_observed_facts(conversation_state),
            "action": None,
            "reasoning_steps_used": 0,
            "queries_used": 0,
            "correction_attempts": 0,
            "clarifications_used": 0,
            "replans_used": 0,
            "targeted_syncs_used": 0,
            "clarification_exchanges": (),
            "transcript": _stored_transcript(conversation_state),
            "final_response": None,
        }

        # A resumed turn keeps the as-of its attempt started under -- it is the
        # instant the evidence already in the checkpoint was filtered against,
        # and moving it would leave that evidence and the next date filter
        # meaning different days. The back-fill below is only for a checkpoint
        # written before the field existed, which otherwise resumes into a turn
        # with no grounding at all.
        resume_update: dict[str, Any] = {}
        if resume_thread_id:
            if not _has_pinned_grounding(paused_values):
                resume_update["as_of"] = as_of.isoformat()
                resume_update["session_timezone"] = session_timezone
            # Always replaced, never back-filled: this turn is a different API
            # request from the one that paused, and the telemetry has to name
            # the request that is actually in flight.
            resume_update["correlation_id"] = correlation_id

        # Resuming feeds the answer into the suspended `interrupt()` and discards
        # `initial_state` entirely -- the paused checkpoint already holds the real
        # accumulated state, and re-sending a fresh initial state would clobber it.
        graph_input: OrderAgentGraphState | Command[Any]
        if resume_thread_id and rebound_from is not None:
            # REBIND_ON_RESUME: the graph rebuilt while this conversation was
            # waiting on a human. The checkpoint still names the old generation,
            # and every node reads it from state, so the resumed turn would keep
            # querying a generation that may already be retired. Carry the new
            # one in on the resume.
            #
            # Dropping `order_search_cache` is not tidiness -- it is required.
            # The cache holds a CandidateSet stamped with the generation it was
            # built from, and `CandidateSet.validate_selection` raises "candidate
            # set belongs to a stale graph generation" on mismatch. Keeping it
            # would turn the associate's answer into a hard error instead of a
            # fresh search.
            _LOGGER.info(
                "Rebinding resumed conversation %s from generation %s to %s",
                request.conversation_id,
                rebound_from,
                graph_generation_id,
            )
            graph_input = Command(
                resume=request.message,
                update={
                    "graph_generation_id": graph_generation_id,
                    "order_search_cache": None,
                    **resume_update,
                },
            )
        elif resume_thread_id:
            graph_input = Command(resume=request.message, update=resume_update)
        else:
            graph_input = initial_state
        try:
            final_state = await self._graph.ainvoke(
                graph_input,
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

        # A graph that suspended on `interrupt()` returns its state with an
        # `__interrupt__` entry and no `final_response`. That run is PENDING, not
        # terminal: marking it COMPLETED here would stamp an expiry on the very
        # checkpoint the associate's answer needs to resume from, and
        # abandonment.py would be free to sweep a conversation that is simply
        # waiting on a human. The turn is still committed, so the associate sees
        # the question and the conversation version advances normally.
        interrupts = final_state.get("__interrupt__")
        if interrupts:
            question = StructuredAgentResponse.model_validate(interrupts[0].value)
            paused = AgentTurnResult(
                conversation_id=request.conversation_id,
                conversation_version=version + 1,
                client_turn_id=request.client_turn_id,
                graph_generation_id=graph_generation_id,
                response=question,
                query_evidence=await self._evidence_store.get_many(
                    final_state.get("evidence_refs", ())
                ),
                captured_facts=_committed_facts(final_state),
                model_provider=final_state["last_provider"],
                model_name=final_state["last_model"],
                pending_clarification_thread_id=thread_id,
                case_id=final_state.get("case_id"),
                # From the final state, not from `as_of` above: a resumed turn
                # reasons under the grounding its checkpoint carries, and the
                # record has to say what was actually used.
                **_committed_grounding(final_state),
            )
            return await self._conversations.commit_turn(
                request=request,
                expected_version=version,
                result=paused,
                conversation_state={
                    **conversation_state,
                    "orderSearchCache": final_state.get("order_search_cache"),
                    "observedFacts": list(final_state.get("observed_facts", ())),
                    "transcript": _extended_transcript(
                        conversation_state, user_message=request.message, response=None
                    ),
                },
                scope=scope,
            )

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
            "observedFacts": list(final_state.get("observed_facts", ())),
            "transcript": _extended_transcript(
                conversation_state, user_message=request.message, response=response
            ),
        }
        provisional = AgentTurnResult(
            conversation_id=request.conversation_id,
            conversation_version=version + 1,
            client_turn_id=request.client_turn_id,
            graph_generation_id=graph_generation_id,
            response=response,
            query_evidence=evidence,
            captured_facts=_committed_facts(final_state),
            model_provider=final_state["last_provider"],
            model_name=final_state["last_model"],
            case_id=final_state.get("case_id"),
            **_committed_grounding(final_state),
        )
        return await self._conversations.commit_turn(
            request=request,
            expected_version=version,
            result=provisional,
            conversation_state=new_conversation_state,
            scope=scope,
        )
