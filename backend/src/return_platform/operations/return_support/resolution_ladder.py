"""The resolution ladder (contracts.md sect. 9).

> *Ladder: case facts -> graph (own on-demand sync) -> registered tools ->
> associate clarification; thresholds `*_millionths`. Sub-threshold /
> conflicting sources / missing entity -> escalate, never answer.
> `per_case_llm_budget` exhaustion -> fact + escalation.*

A LangGraph `StateGraph`, on the verdict step:01 recorded: neither
`agents/support_response.py` nor `operations/return_support/auto_responder.py`
can carry it -- they answer the platform's *outbound* handoff **as Support**,
and this answers Support's *inbound* question on the associate's behalf.

## The ladder is a descent, not a search

Each rung is tried once, in order, and a rung that answers **ends the descent**
-- `test_the_ladder_short_circuits_when_the_facts_answer` proves it by asserting
the graph and tool stubs were never called, not by asserting the answer came
from facts (which would be true either way). A rung that cannot answer descends;
a rung that answers *badly* does not.

That distinction is the whole safety property, and it is why there are three
separate escalation reasons rather than one "could not answer":

* **sub-threshold** -- the model was not sure enough. Descend, and escalate if
  the rungs run out.
* **conflicting sources** -- two rungs answered and disagreed. **Escalate
  immediately**, without descending further and without preferring either. A
  ladder that resolved a disagreement by precedence would be inventing an
  answer neither source gave; `trusted_entities_from` takes the same position
  for tool arguments.
* **missing required entity** -- the tool rung refused because no trusted fact
  or graph result supplies what the tool needs. Sect. 9's own words.

None of the three produces an answer. There is no branch in this module that
sends a reply the configured threshold did not clear.

## What the budget is, and what exhausting it does

`per_case_llm_budget` counts model invocations. It is checked **before** each
invocation, never after: a budget enforced after the fact has already spent the
thing it was protecting. Exhaustion writes `support_resolver_budget_exhausted`
and escalates, so a case that outruns its budget becomes visible work rather
than an unanswered message.

## Ports, and why this module imports no infrastructure

Every collaborator is a `Protocol` -- the idiom `message_classification.py`
established for the same reason: the loop is the thing worth testing and it must
be testable with stubs whose call counts can be asserted. In particular
`GraphSyncPort` is the agent's *own* on-demand sync
(`OnDemandSyncCoordinator.synchronize` behind an adapter), so this module never
imports `dynamic_knowledge`.
"""

from __future__ import annotations

import logging
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from return_platform.configuration.support_resolver_configuration import (
    SupportResolverConfiguration,
)
from return_platform.operations.fact_names import SUPPORT_RESOLVER_BUDGET_EXHAUSTED
from return_platform.operations.models import FactAcquisition, FactChannel
from return_platform.operations.return_support.resolution_state import (
    RUNG_FACTS,
    RUNG_GRAPH,
    RUNG_TOOL,
    SupportResolverState,
)
from return_platform.platform.capabilities.tool_router import (
    RefusalReason,
    ToolExecutionResult,
    ToolInvocationPlan,
    ToolRefusal,
    plan_tool_invocation,
    trusted_entities_from,
    validate_intent,
)
from return_platform.platform.reasoning.case_context import (
    AssembledContext,
    ContextPolicy,
    assemble_case_context,
)

logger = logging.getLogger("return_platform.support_resolver")

__all__ = [
    "AGENT_ID",
    "CaseFactsPort",
    "EscalationReason",
    "GraphReadPort",
    "GraphSyncPort",
    "LadderDependencies",
    "LadderRungUnserviceable",
    "ResolutionAttempt",
    "ResolutionInvokerPort",
    "ScopedFactWriterPort",
    "ToolExecutorPort",
    "TrustedEntityPort",
    "build_resolution_ladder",
    "compiled_rungs",
    "make_finalize_node",
    "make_sync_graph_node",
    "parse_resolution_attempt",
]

#: Stamped on every fact this module writes, so a reader can filter the
#: resolver's derivations out of a case's fact log without matching fact names.
#: Deliberately *not* `SUPPORT_RESPONSE_ACTOR` -- that identity belongs to the
#: Support-side simulator, and reusing it would make the platform's own answer
#: indistinguishable from Support's (step:01, reason 1).
AGENT_ID: Final = "support-question-resolver"

#: The `ai_gateway` task the resolve rungs invoke (contracts.md sect. 10).
RESOLVE_TASK_ID: Final = "support.question.resolve.v1"


class EscalationReason(StrEnum):
    """Why the ladder gave no answer. Never collapsed into one value.

    An associate reading a clarification needs to know whether the platform was
    unsure, whether two sources disagreed, or whether a fact was simply not on
    file -- those call for three different things from them, and a single
    "could not answer" would ask them to guess which.
    """

    SUB_THRESHOLD = "SUB_THRESHOLD"
    CONFLICTING_SOURCES = "CONFLICTING_SOURCES"
    MISSING_REQUIRED_ENTITY = "MISSING_REQUIRED_ENTITY"
    NO_ELIGIBLE_TOOL = "NO_ELIGIBLE_TOOL"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


@dataclass(frozen=True, slots=True)
class ResolutionAttempt:
    """One rung's answer, under the declared response schema.

    Never a raw provider payload: `parse_resolution_attempt` is the only way to
    build one, and it refuses anything whose confidence is not an integer in
    `[0, 1_000_000]`. A missing or malformed confidence reads as **zero**, not
    as "unknown, assume fine" -- an answer whose confidence could not be read is
    an answer no threshold has cleared.
    """

    answer_text: str
    confidence_millionths: int
    cited_fact_ids: tuple[str, ...] = ()
    unresolvable_reason: str | None = None
    needed_field: str | None = None
    #: The graph rung's verdict on the fact rung's answer. `None` on the first
    #: rung, where there is nothing prior to agree with.
    agrees_with_prior: bool | None = None

    def as_state(self) -> dict[str, Any]:
        return {
            "answerText": self.answer_text,
            "confidenceMillionths": self.confidence_millionths,
            "citedFactIds": list(self.cited_fact_ids),
            "unresolvableReason": self.unresolvable_reason,
            "neededField": self.needed_field,
            "agreesWithPrior": self.agrees_with_prior,
        }


def _confidence(raw: Any) -> int:
    """A reported confidence, or zero.

    `bool` is excluded before the `int` check for the reason
    `EntityField.coerced` documents: `isinstance(True, int)` is true in Python,
    and without this line a `True` would read as the confidence 1 millionth --
    which is not the bug it looks like, because 1 is *below* every sane
    threshold, but a `False` reading as 0 and a `True` reading as 1 is a
    coincidence, not a guarantee.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return raw if 0 <= raw <= 1_000_000 else 0


def parse_resolution_attempt(raw: Mapping[str, Any]) -> ResolutionAttempt:
    """A provider response as a `ResolutionAttempt`. Total, never raising.

    Total on purpose: a malformed response is an *unusable answer*, which the
    ladder already handles (it fails the threshold and the rung descends). If
    this raised, one bad response would abort the whole descent and the case
    would get no clarification either.
    """
    cited = raw.get("citedFactIds") or raw.get("cited_fact_ids") or ()
    return ResolutionAttempt(
        answer_text=str(raw.get("answerText") or raw.get("answer_text") or ""),
        confidence_millionths=_confidence(
            raw.get("confidenceMillionths", raw.get("confidence_millionths"))
        ),
        cited_fact_ids=tuple(str(item) for item in cited if item is not None),
        unresolvable_reason=_optional_text(
            raw.get("unresolvableReason", raw.get("unresolvable_reason"))
        ),
        needed_field=_optional_text(raw.get("neededField", raw.get("needed_field"))),
        agrees_with_prior=(
            raw.get("agreesWithPrior", raw.get("agrees_with_prior"))
            if isinstance(raw.get("agreesWithPrior", raw.get("agrees_with_prior")), bool)
            else None
        ),
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ------------------------------------------------------------------- the ports


class ResolutionInvokerPort(Protocol):
    """One `support.question.resolve.v1` call, bound to one release.

    A port rather than `StructuredOutputInvoker` directly, for the reason
    `StageInvokerPort` gives: the ladder is the thing worth testing and it must
    be testable with a stub whose call count can be asserted. The production
    adapter is `StructuredOutputInvoker`-backed and lives at the wiring site.
    """

    release_id: str
    prompt_version: str

    async def invoke(self, *, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


class CaseFactsPort(Protocol):
    """The case's whole fact log, for the *prompt*.

    Two **separate ports** rather than one with two methods, which is what this
    used to be. The docstring then warned that serving both projections from one
    object "would invite a caller to pass the prompt's projection to the router";
    phase 2 found that the warning was the only thing enforcing it, and a warning
    is not a boundary. Now the trust projection is a different type
    (`TrustedEntityPort`), supplied through a different field, and required only
    by the rung that is allowed to use it -- so a deployment that has decided
    nothing about tool-argument trust cannot accidentally hand the router the
    bag `assemble_case_context` budgets for the prompt.

    `fact_log` is the whole log, not a projection: `assemble_case_context`
    performs the scoped-latest collapse itself so the ordering rule and the
    projection rule are applied by one piece of code in one order.
    """

    async def fact_log(self, case_id: str) -> Sequence[Mapping[str, Any]]: ...


class TrustedEntityPort(Protocol):
    """The entity-keyed projection the *tool router* may take arguments from.

    Deliberately its own port, and deliberately **not implemented anywhere in
    `src/` yet**. `trusted_entities_from` documents its input as "a scoped-latest
    fact projection keyed by **entity name**", and nothing in this build maps
    fact names onto entity names. Choosing that mapping decides what a released
    tool binding is allowed to be filled from, which is the trust decision
    contracts.md sect. 9 puts *behind* the tool boundary rather than at a wiring
    site -- so it is not invented here.

    The consequence is structural rather than advisory: with no implementation
    to supply, `LadderDependencies` carries no tool rung, and
    `build_resolution_ladder` compiles a graph that does not contain one. See
    `LadderDependencies.tool_rung_available`.
    """

    async def trusted_entities(
        self, case_id: str
    ) -> tuple[Mapping[str, Any], Mapping[str, str]]: ...


class GraphSyncPort(Protocol):
    """The resolver's *own* on-demand sync (contracts.md sect. 9, brief item 2).

    Behind an adapter over `OnDemandSyncCoordinator.synchronize`, so this module
    never imports `dynamic_knowledge`. Returns the receipt id, or `None` when no
    sync could run -- unavailability is not an error here: the graph may already
    hold what the question needs, and refusing to read it because a refresh
    failed would escalate a question the platform could have answered.
    """

    async def synchronize_for_case(self, *, case_id: str) -> str | None: ...


class GraphReadPort(Protocol):
    """What the knowledge graph holds about this case.

    **Takes the case id and nothing else.** No question text reaches the graph:
    a read parameterised by support prose would be a second way for untrusted
    text to choose what the platform looks up, which is the boundary
    `tool_router.py` exists to hold.
    """

    async def read_case_graph(self, *, case_id: str) -> Mapping[str, Any]: ...


class ToolExecutorPort(Protocol):
    """`ToolExecutor.execute`, structurally."""

    async def execute(
        self, plan: ToolInvocationPlan, *, principal_id: str, case_id: str
    ) -> ToolExecutionResult | ToolRefusal: ...


class ScopedFactWriterPort(Protocol):
    """`ReturnCaseActivities.append_scoped_fact_once`, structurally.

    `actor_id` is named rather than absorbed into `**fact` for the reason
    `reply_gating.ScopedFactWriterPort` states. The ladder's own fact -- budget
    exhaustion -- is `DERIVED` from the platform's counters with no command
    behind it, so it passes no actor and the parameter defaults to `None`; that
    is the honest value, not an omission.
    """

    async def __call__(
        self, *, record_scope: str | None, actor_id: str | None = None, **fact: Any
    ) -> bool: ...


class LadderRungUnserviceable(ValueError):
    """A rung was half-supplied. Neither a full rung nor an absent one.

    Raised at construction rather than tolerated, because the two halves of a
    rung fail at different moments and the failure of either is invisible: a
    graph read with no sync reads a stale graph and answers confidently from it;
    a tool executor with no trusted-entity source refuses every invocation and
    reports `MISSING_REQUIRED_ENTITY`, which reads exactly like a case that
    genuinely lacked the entity. Both are wiring mistakes that look like
    outcomes.
    """


@dataclass(frozen=True, slots=True)
class LadderDependencies:
    """Everything the nodes need, supplied once at build time.

    ## A rung the deployment cannot serve is **absent**, not stubbed

    Three of the ports below have no implementation in `src/` and cannot get one
    without deciding something this slice does not own -- what the resolver reads
    from the graph, which facts may fill a tool argument, which contract classes
    an executor admits, and under whose authority it runs. The tempting shapes
    are all worse than absence: a `GraphReadPort` that returns `{}` hands a model
    an empty context it can still answer confidently from; a port whose method
    raises is accepted at construction and swallowed at runtime.

    So they are `None`-able, and `build_resolution_ladder` compiles a graph
    **without the nodes they would have fed**. `rungs_attempted` on the finished
    state then reports what was actually tried, an escalation says the descent
    ran out of rungs rather than that a rung silently answered nothing, and
    `tool_rung_available` is a fact about the compiled topology that a test can
    assert instead of a claim in a docstring.

    Half a rung is refused outright -- see `LadderRungUnserviceable`.
    """

    configuration: SupportResolverConfiguration
    context_policy: ContextPolicy
    facts: CaseFactsPort
    resolver: ResolutionInvokerPort
    append_scoped_fact_once: ScopedFactWriterPort
    #: The taxonomy a classification must be a member of before it can select a
    #: tool. Supplied from `support_ingress.normalized_intents`, so the resolver
    #: and the classifier score against the same closed set.
    intent_taxonomy: frozenset[str]

    # -- The graph rung. Both or neither. -------------------------------------
    graph_sync: GraphSyncPort | None = None
    graph_read: GraphReadPort | None = None

    # -- The tool rung. All three or none. ------------------------------------
    trusted_facts: TrustedEntityPort | None = None
    tools: ToolExecutorPort | None = None
    #: Whose authority a tool runs under. The *platform's* service principal,
    #: never the support sender's: a message from outside the tenancy must not
    #: be able to borrow an internal principal's reach. There is no default and
    #: no placeholder -- a deployment that has not named one has no tool rung.
    principal_id: str | None = None

    def __post_init__(self) -> None:
        graph = (self.graph_sync is not None, self.graph_read is not None)
        if any(graph) and not all(graph):
            raise LadderRungUnserviceable(
                "the graph rung needs both graph_sync and graph_read; a read without a "
                "sync answers from a graph nobody refreshed, and a sync without a read "
                "pays for a refresh nothing consumes"
            )
        tool = (
            self.trusted_facts is not None,
            self.tools is not None,
            bool(self.principal_id and self.principal_id.strip()),
        )
        if any(tool) and not all(tool):
            raise LadderRungUnserviceable(
                "the tool rung needs trusted_facts, tools and principal_id together; a "
                "partially supplied tool rung refuses every invocation for a reason "
                "indistinguishable from a case that genuinely lacked the entity"
            )

    @property
    def graph_rung_available(self) -> bool:
        return self.graph_read is not None

    @property
    def tool_rung_available(self) -> bool:
        return self.tools is not None


# ------------------------------------------------------------------- the nodes


def _budget_spent(state: SupportResolverState) -> int:
    return int(state.get("llm_invocations_used", 0) or 0)


def _rungs(state: SupportResolverState, rung: str) -> tuple[str, ...]:
    attempted = tuple(state.get("rungs_attempted", ()) or ())
    return attempted if rung in attempted else (*attempted, rung)


def _exhausted(state: SupportResolverState, deps: LadderDependencies) -> bool:
    """Whether the next invocation would exceed the released budget.

    Checked *before* the call. A budget checked afterwards has already spent
    the thing it was protecting.
    """
    return _budget_spent(state) >= deps.configuration.per_case_llm_budget


def _resolve_payload(
    state: SupportResolverState,
    *,
    rung: str,
    context: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The bounded key set one resolve call may see.

    Enumerated rather than assembled from the state, so a field added to the
    checkpoint does not silently become a prompt input -- `allowedInputKeys` on
    the gateway task is the released half of the same rule, and this is the
    code-side half.
    """
    payload = {
        "rung": rung,
        "intent": state["intent"],
        "question": state["question_text"],
        "context": context,
    }
    if prior is not None:
        payload["priorAnswer"] = prior
    return payload


def make_resolve_from_facts_node(deps: LadderDependencies):
    async def resolve_from_facts(state: SupportResolverState) -> dict[str, Any]:
        """Rung one: assemble the case's own facts, and answer from them.

        Assembly and invocation are **one node**, not two. A separate assembly
        node would have to hand the assembled payload to the next node through
        the state -- and that payload is every fact value the prompt carries,
        which is precisely what `resolution_state.py` refuses to checkpoint. So
        the context is built inside the node that uses it and never outlives it;
        only `consumed_fact_ids` and `content_hash`, which are references, are
        written to the state.

        Nothing is lost by re-assembling on resume: `assemble_case_context` is
        pure, so the same fact log and the same policy give back the same bytes
        and the same hash.
        """
        if _exhausted(state, deps):
            return {"budget_exhausted": True, "rungs_attempted": _rungs(state, RUNG_FACTS)}
        facts = await deps.facts.fact_log(state["case_id"])
        assembled: AssembledContext = assemble_case_context(facts, deps.context_policy)
        raw = await deps.resolver.invoke(
            payload=_resolve_payload(
                state, rung=RUNG_FACTS, context=assembled.payload(), prior=None
            )
        )
        attempt = parse_resolution_attempt(raw)
        return {
            "fact_answer": attempt.as_state(),
            "consumed_fact_ids": tuple(assembled.consumed_fact_ids),
            "context_hash": assembled.content_hash,
            "llm_invocations_used": _budget_spent(state) + 1,
            "rungs_attempted": _rungs(state, RUNG_FACTS),
        }

    return resolve_from_facts


def make_sync_graph_node(deps: LadderDependencies):
    async def sync_graph(state: SupportResolverState) -> dict[str, Any]:
        """The graph rung's own on-demand sync. A node of its own, deliberately.

        Separate from the read so a resume that already synced does not sync
        again -- a targeted source read is the most expensive thing this ladder
        does, and acceptance 23's "resume at the last completed node" is exactly
        the guarantee that stops it happening twice.
        """
        if state.get("graph_synced"):
            return {}
        assert deps.graph_sync is not None  # the node is not compiled in without it
        receipt_id = await deps.graph_sync.synchronize_for_case(case_id=state["case_id"])
        return {"graph_synced": True, "graph_sync_receipt_id": receipt_id}

    return sync_graph


def make_resolve_from_graph_node(deps: LadderDependencies):
    async def resolve_from_graph(state: SupportResolverState) -> dict[str, Any]:
        """Rung two: answer from the graph, and say whether it agrees with rung one."""
        if _exhausted(state, deps):
            return {"budget_exhausted": True, "rungs_attempted": _rungs(state, RUNG_GRAPH)}
        assert deps.graph_read is not None  # the node is not compiled in without it
        graph_view = await deps.graph_read.read_case_graph(case_id=state["case_id"])
        raw = await deps.resolver.invoke(
            payload=_resolve_payload(
                state,
                rung=RUNG_GRAPH,
                context={"graph": dict(graph_view)},
                prior=state.get("fact_answer"),
            )
        )
        attempt = parse_resolution_attempt(raw)
        return {
            "graph_answer": attempt.as_state(),
            "llm_invocations_used": _budget_spent(state) + 1,
            "rungs_attempted": _rungs(state, RUNG_GRAPH),
        }

    return resolve_from_graph


def make_route_tool_node(deps: LadderDependencies):
    async def route_tool(state: SupportResolverState) -> dict[str, Any]:
        """Rung three: a registered tool, selected by intent and filled from trust.

        Every guarantee here belongs to `tool_router.py` and is *used* rather
        than re-implemented. Note what this node does **not** pass:
        `state["question_text"]` never reaches `plan_tool_invocation`, which has
        no parameter for it, nor `trusted_entities_from`, whose inputs are
        keyword-only and are the case facts and the graph results.
        """
        rungs = _rungs(state, RUNG_TOOL)
        intent = validate_intent(state["intent"], deps.intent_taxonomy)
        if intent is None:
            return {
                "tool_refusal": {
                    "reason": RefusalReason.NO_ELIGIBLE_BINDING.value,
                    "intent": state["intent"],
                    "detail": "classification is not a member of the released taxonomy",
                    "missingEntities": [],
                },
                "rungs_attempted": rungs,
            }
        assert deps.trusted_facts is not None  # the node is not compiled in without it
        assert deps.tools is not None
        assert deps.principal_id is not None
        case_facts, fact_ids = await deps.trusted_facts.trusted_entities(state["case_id"])
        # `None` when there is no graph rung. `trusted_entities_from` treats
        # `None` and `{}` identically today, so this is a statement of intent
        # rather than a behavioural difference -- said plainly because a comment
        # claiming a distinction the code does not make is worse than no comment.
        graph_view = (
            dict(await deps.graph_read.read_case_graph(case_id=state["case_id"]))
            if deps.graph_read is not None
            else None
        )
        trusted = trusted_entities_from(
            case_facts=case_facts, fact_ids=fact_ids, graph_results=graph_view
        )
        outcome = plan_tool_invocation(
            intent, deps.configuration.bindings_for_intent(intent.value), trusted
        )
        if isinstance(outcome, ToolRefusal):
            return {"tool_refusal": _refusal_state(outcome), "rungs_attempted": rungs}

        plan_state = {
            "toolId": outcome.tool_id,
            "capability": outcome.capability,
            "contract": outcome.contract,
            "inputSchemaRef": outcome.input_schema_ref,
            "arguments": dict(outcome.arguments),
            "argumentProvenance": {
                name: list(entry) for name, entry in outcome.argument_provenance.items()
            },
            # An id. The value behind it is resolved inside the executor and is
            # never returned to this graph -- see `resolution_state.py`.
            "credentialBindingId": outcome.credential_binding_id,
        }
        executed = await deps.tools.execute(
            outcome, principal_id=deps.principal_id, case_id=state["case_id"]
        )
        if isinstance(executed, ToolRefusal):
            return {
                "tool_plan": plan_state,
                "tool_refusal": _refusal_state(executed),
                "rungs_attempted": rungs,
            }
        if _exhausted(state, deps):
            return {
                "tool_plan": plan_state,
                "tool_result_ref": f"tool:{executed.tool_id}:{state['support_event_id']}",
                "budget_exhausted": True,
                "rungs_attempted": rungs,
            }
        raw = await deps.resolver.invoke(
            payload=_resolve_payload(
                state,
                rung=RUNG_TOOL,
                context={"toolId": executed.tool_id, "result": dict(executed.result)},
                prior=state.get("graph_answer") or state.get("fact_answer"),
            )
        )
        attempt = parse_resolution_attempt(raw)
        return {
            "tool_plan": plan_state,
            # A reference, never the read's contents.
            "tool_result_ref": f"tool:{executed.tool_id}:{state['support_event_id']}",
            "tool_answer": attempt.as_state(),
            "llm_invocations_used": _budget_spent(state) + 1,
            "rungs_attempted": rungs,
        }

    return route_tool


def _refusal_state(refusal: ToolRefusal) -> dict[str, Any]:
    return {
        "reason": refusal.reason.value,
        "intent": refusal.intent,
        "detail": refusal.detail,
        "toolId": refusal.tool_id,
        "missingEntities": list(refusal.missing_entities),
    }


def make_finalize_node(deps: LadderDependencies):
    async def finalize(state: SupportResolverState) -> dict[str, Any]:
        """An answer cleared its rung's threshold. Record it, and its gate.

        The gate decision is a pure read of `reply_gate` against the intent, and
        it is taken **here** rather than at the send site so that the state a
        reviewer inspects already says whether this answer was ever allowed to
        go out on its own.
        """
        answer, rung = _answering_rung(state, deps)
        assert answer is not None  # the router only routes here when one cleared
        requires_review = deps.configuration.reply_gate.requires_review(state["intent"])
        return {
            "resolution": {
                "answerText": answer["answerText"],
                "confidenceMillionths": answer["confidenceMillionths"],
                "citedFactIds": list(answer.get("citedFactIds") or ()),
                "resolvedByRung": rung,
                "requiresReview": requires_review,
                "gateMode": deps.configuration.reply_gate.mode_for(state["intent"]),
                "consumedFactIds": list(state.get("consumed_fact_ids", ()) or ()),
                "contextHash": state.get("context_hash", ""),
                "toolResultRef": state.get("tool_result_ref"),
            }
        }

    return finalize


def make_escalate_node(deps: LadderDependencies):
    async def escalate(state: SupportResolverState) -> dict[str, Any]:
        """No answer. Say why, in the form the clarification fact needs.

        Budget exhaustion additionally writes `support_resolver_budget_exhausted`
        (contracts.md sect. 9) -- through the append-once path, keyed on the
        support event, so a retried attempt that exhausts the same budget writes
        one fact rather than one per retry.
        """
        reason = _escalation_reason(state)
        if reason is EscalationReason.BUDGET_EXHAUSTED:
            await deps.append_scoped_fact_once(
                record_scope=None,
                fact_id=f"{SUPPORT_RESOLVER_BUDGET_EXHAUSTED}-{state['support_event_id']}",
                case_id=state["case_id"],
                fact_name=SUPPORT_RESOLVER_BUDGET_EXHAUSTED,
                value={
                    "supportEventId": state["support_event_id"],
                    "invocationsUsed": _budget_spent(state),
                    "perCaseLlmBudget": deps.configuration.per_case_llm_budget,
                },
                agent_id=AGENT_ID,
                channel=FactChannel.CHANNEL_A,
                # `DERIVED`: it is computed from the platform's own counters,
                # not observed anywhere and not a model's opinion.
                acquisition_method=FactAcquisition.DERIVED,
                source_system="RETURN_SUPPORT",
                source_path="SUPPORT_RESOLVER_BUDGET",
            )
        refusal = state.get("tool_refusal") or {}
        return {
            "escalation": {
                "reason": reason.value,
                "resolutionAttempts": list(state.get("rungs_attempted", ()) or ()),
                "neededField": _needed_field(state),
                "missingEntities": list(refusal.get("missingEntities") or ()),
                "consumedFactIds": list(state.get("consumed_fact_ids", ()) or ()),
                "contextHash": state.get("context_hash", ""),
                "invocationsUsed": _budget_spent(state),
            }
        }

    return escalate


def _answering_rung(
    state: SupportResolverState, deps: LadderDependencies
) -> tuple[Mapping[str, Any] | None, str]:
    """The first rung whose answer cleared its own threshold, in descent order.

    The tool rung is measured against `graph_confidence_millionths`: a tool read
    is a *graph* read behind a released binding, so holding it to a different
    tolerance than the graph rung would be a third threshold nobody configured.
    """
    fact = state.get("fact_answer")
    if fact is not None and fact["confidenceMillionths"] >= (
        deps.configuration.fact_confidence_millionths
    ):
        return fact, RUNG_FACTS
    graph = state.get("graph_answer")
    threshold = deps.configuration.graph_confidence_millionths
    if graph is not None and graph["confidenceMillionths"] >= threshold:
        return graph, RUNG_GRAPH
    tool = state.get("tool_answer")
    if tool is not None and tool["confidenceMillionths"] >= threshold:
        return tool, RUNG_TOOL
    return None, ""


def _conflicting(state: SupportResolverState) -> bool:
    """Whether a later rung reported disagreeing with the fact rung.

    Read from the model's own `agreesWithPrior` rather than compared as text:
    two sentences saying the same thing differently are not a conflict, and a
    string comparison would call them one. `None` -- the rung did not answer the
    question, or the response omitted it -- is **not** a conflict; only an
    explicit `False` is, and only when there was a prior answer to disagree
    with.
    """
    prior = state.get("fact_answer")
    if prior is None or not prior.get("answerText"):
        return False
    for later in (state.get("graph_answer"), state.get("tool_answer")):
        if later is not None and later.get("agreesWithPrior") is False:
            return True
    return False


def _escalation_reason(state: SupportResolverState) -> EscalationReason:
    if state.get("budget_exhausted"):
        return EscalationReason.BUDGET_EXHAUSTED
    if _conflicting(state):
        return EscalationReason.CONFLICTING_SOURCES
    refusal = state.get("tool_refusal")
    if refusal is not None:
        mapped = {
            RefusalReason.MISSING_REQUIRED_ENTITY.value: (EscalationReason.MISSING_REQUIRED_ENTITY),
            RefusalReason.NO_ELIGIBLE_BINDING.value: EscalationReason.NO_ELIGIBLE_TOOL,
            RefusalReason.CAPABILITY_UNAVAILABLE.value: EscalationReason.TOOL_UNAVAILABLE,
            RefusalReason.NOT_AUTHORIZED.value: EscalationReason.TOOL_UNAVAILABLE,
        }.get(str(refusal.get("reason")))
        if mapped is not None:
            return mapped
    return EscalationReason.SUB_THRESHOLD


def _needed_field(state: SupportResolverState) -> str | None:
    """What the associate would have to supply, if any rung said.

    The tool refusal's first missing entity outranks a model's guess: it is the
    schema's own name for the thing that was absent, and the model's
    `neededField` is prose about the same gap at best.
    """
    refusal = state.get("tool_refusal") or {}
    missing = refusal.get("missingEntities") or ()
    if missing:
        return str(missing[0])
    for answer in (state.get("tool_answer"), state.get("graph_answer"), state.get("fact_answer")):
        if answer is not None and answer.get("neededField"):
            return str(answer["neededField"])
    return None


# ----------------------------------------------------------------- the routers


def _cleared(answer: Mapping[str, Any] | None, threshold: int) -> bool:
    return answer is not None and int(answer["confidenceMillionths"]) >= threshold


def _next_rung_after_facts(deps: LadderDependencies) -> str:
    """Where an uncleared fact answer descends to, given what is compiled in.

    The descent order of sect. 9 with the unserviceable rungs removed, rather
    than a fixed `"sync_graph"` that would name a node the graph does not have.
    """
    if deps.graph_rung_available:
        return "sync_graph"
    if deps.tool_rung_available:
        return "route_tool"
    return "escalate"


def _next_rung_after_graph(deps: LadderDependencies) -> str:
    return "route_tool" if deps.tool_rung_available else "escalate"


def make_route_after_facts(deps: LadderDependencies):
    def route_after_facts(state: SupportResolverState) -> str:
        if state.get("budget_exhausted"):
            return "escalate"
        if _cleared(state.get("fact_answer"), deps.configuration.fact_confidence_millionths):
            return "finalize"
        return _next_rung_after_facts(deps)

    return route_after_facts


def make_route_after_graph(deps: LadderDependencies):
    def route_after_graph(state: SupportResolverState) -> str:
        """Conflict is checked **before** the threshold, and that order matters.

        A graph answer that is both confident and in disagreement with the facts
        is the most dangerous thing this ladder can produce: it would be sent to
        Support, under the platform's name, contradicting the case's own record.
        Checking the threshold first would send exactly that.
        """
        if state.get("budget_exhausted"):
            return "escalate"
        if _conflicting(state):
            return "escalate"
        if _cleared(state.get("graph_answer"), deps.configuration.graph_confidence_millionths):
            return "finalize"
        return _next_rung_after_graph(deps)

    return route_after_graph


def make_route_after_tool(deps: LadderDependencies):
    def route_after_tool(state: SupportResolverState) -> str:
        if state.get("budget_exhausted"):
            return "escalate"
        if _conflicting(state):
            return "escalate"
        if _cleared(state.get("tool_answer"), deps.configuration.graph_confidence_millionths):
            return "finalize"
        return "escalate"

    return route_after_tool


_AFTER_TOOL_TARGETS: dict[Hashable, str] = {"finalize": "finalize", "escalate": "escalate"}


def compiled_rungs(deps: LadderDependencies) -> tuple[str, ...]:
    """Which rungs a ladder built from these dependencies actually has.

    Exported so a composition site, a test and an operator can all read the same
    answer to "is the tool rung reachable in this deployment?" -- and so that
    answer comes from the dependencies rather than from a comment.
    """
    rungs = [RUNG_FACTS]
    if deps.graph_rung_available:
        rungs.append(RUNG_GRAPH)
    if deps.tool_rung_available:
        rungs.append(RUNG_TOOL)
    return tuple(rungs)


def build_resolution_ladder(
    deps: LadderDependencies, *, checkpointer: BaseCheckpointSaver[str] | None = None
) -> CompiledStateGraph[SupportResolverState, None]:
    """Compile the ladder. Topology only -- behaviour lives in the node factories.

    **The topology is the rung inventory.** A rung whose ports the deployment
    could not supply is not compiled in, so it cannot be entered, cannot appear
    in `rungs_attempted`, and cannot answer nothing convincingly. The conditional
    target maps are built from the same availability the routers read, because a
    map naming a node that was never added is a compile-time error in LangGraph
    and a router returning a name absent from the map is a runtime one -- keeping
    both from one source is what stops the two drifting.

    `checkpointer` is a `SystemStoreCheckpointSaver` in every non-unit context
    (contracts.md sect. 9); `InMemorySaver`/`MemorySaver` are forbidden outside
    unit tests by `tests/reasoning/test_no_langchain_provider_packages.py`.
    """
    graph: StateGraph[SupportResolverState, None] = StateGraph(SupportResolverState)

    graph.add_node("resolve_from_facts", make_resolve_from_facts_node(deps))
    graph.add_node("finalize", make_finalize_node(deps))
    graph.add_node("escalate", make_escalate_node(deps))

    after_facts: dict[Hashable, str] = {"finalize": "finalize", "escalate": "escalate"}
    after_graph: dict[Hashable, str] = {"finalize": "finalize", "escalate": "escalate"}

    if deps.graph_rung_available:
        graph.add_node("sync_graph", make_sync_graph_node(deps))
        graph.add_node("resolve_from_graph", make_resolve_from_graph_node(deps))
        after_facts["sync_graph"] = "sync_graph"
    if deps.tool_rung_available:
        graph.add_node("route_tool", make_route_tool_node(deps))
        after_facts["route_tool"] = "route_tool"
        after_graph["route_tool"] = "route_tool"

    graph.set_entry_point("resolve_from_facts")
    graph.add_conditional_edges("resolve_from_facts", make_route_after_facts(deps), after_facts)
    if deps.graph_rung_available:
        graph.add_edge("sync_graph", "resolve_from_graph")
        graph.add_conditional_edges("resolve_from_graph", make_route_after_graph(deps), after_graph)
    if deps.tool_rung_available:
        graph.add_conditional_edges("route_tool", make_route_after_tool(deps), _AFTER_TOOL_TARGETS)
    graph.add_edge("finalize", END)
    graph.add_edge("escalate", END)

    return graph.compile(checkpointer=checkpointer)
