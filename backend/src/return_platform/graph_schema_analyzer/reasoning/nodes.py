"""The analyzer reasoning graph's nodes (design doc section 14.4).

**Reasoning stops at READY_FOR_APPROVAL.** Nothing here activates a generation,
CASes the active runtime snapshot, drains, retires, or emits source DDL/DML --
those belong to ApprovalService/BuildService/RebuildTrigger/the lifecycle
orchestrator. The split the design draws is:

    LangGraph       = think / inspect / clarify / propose / revise / validate
    Graph lifecycle = build / fence / activate / drain / retire

`tests/graph_schema_analyzer/test_reasoning_graph.py` asserts the boundary by
checking this module names no lifecycle operation at all -- a weaker check than
proving it never happens, but it catches the realistic mistake, which is someone
adding a convenient `build()` call here rather than routing through approval.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from langgraph.types import interrupt

from return_platform.graph_schema_analyzer.application.prompt_context import build_prompt_blocks
from return_platform.graph_schema_analyzer.ports.ai_port import SchemaReasoningPort
from return_platform.graph_schema_analyzer.ports.graph_target_port import GraphTargetPort
from return_platform.graph_schema_analyzer.ports.system_store_port import PersistencePort
from return_platform.graph_schema_analyzer.reasoning.limits import (
    AnalyzerBudgets,
    check_clarification_budget,
    check_validation_budget,
)
from return_platform.graph_schema_analyzer.reasoning.state import CompletionStatus, NextAction

__all__ = [
    "AnalyzerDependencies",
    "make_analyze_structure_node",
    "make_identify_gaps_node",
    "make_interrupt_for_clarification_node",
    "make_load_source_snapshot_node",
    "make_propose_schema_node",
    "make_reason_about_failures_node",
    "make_revise_proposal_node",
    "make_user_review_node",
    "make_validate_proposal_node",
]

logger = logging.getLogger(__name__)

_TASK_DEFINITION = (
    "Propose a property-graph schema that represents the entities and relationships "
    "present in the described source datasets. Ground every proposed node and "
    "relationship in a named source dataset."
)


@dataclass(frozen=True, slots=True)
class AnalyzerDependencies:
    """Static collaborators, closed over once at graph-compile time.

    All resolved ports. The graph never sees a concrete adapter, so it can be
    compiled and exercised without an AI provider or a graph database present.
    """

    persistence: PersistencePort
    reasoning: SchemaReasoningPort
    graph_target: GraphTargetPort
    budgets: AnalyzerBudgets


# `Any` rather than a precise Callable alias: LangGraph's add_node overloads
# bind the node's input to a TypedDict-like, and a structural Callable does not
# match them. Same accommodation as dynamic_knowledge/order_agent/graph_nodes.py.
NodeFn = Any


def make_load_source_snapshot_node(deps: AnalyzerDependencies) -> NodeFn:
    async def load_source_snapshot(state: dict[str, Any]) -> dict[str, Any]:
        """Bind the run to a specific, content-addressed snapshot.

        `source_schema_hash` is carried in state so every later step -- and the
        proposal itself -- is attributable to the exact source shape reasoned
        over. Without it a proposal could silently be re-interpreted against a
        later re-discovery of the same analysis.
        """
        snapshot = await deps.persistence.load_snapshot(state["source_snapshot_id"])
        return {
            "source_schema_hash": snapshot.content_hash,
            "next_action": NextAction.ANALYZE,
            "completion_status": CompletionStatus.IN_PROGRESS,
        }

    return load_source_snapshot


def make_analyze_structure_node() -> NodeFn:
    async def analyze_structure(state: dict[str, Any]) -> dict[str, Any]:
        """Records that a pass happened; takes no dependencies on purpose.

        Structural analysis is what PROPOSE_SCHEMA's model call actually performs
        -- inventing a second AI call here would double cost for no extra signal.
        The step still earns its place as a named node because both
        INTERRUPT_FOR_CLARIFICATION and REVISE_PROPOSAL route back through it,
        which is what makes "reconsider with new information" a single edge
        rather than two near-duplicate paths.
        """
        notes = tuple(state.get("reasoning_notes", ()))
        return {
            "reasoning_notes": (*notes, f"analyzed shape {state.get('source_schema_hash')}"),
            "next_action": NextAction.ANALYZE,
        }

    return analyze_structure


def make_identify_gaps_node(deps: AnalyzerDependencies) -> NodeFn:
    async def identify_gaps(state: dict[str, Any]) -> dict[str, Any]:
        """Decide between asking a human and proposing.

        The gap signal is the model's own `open_questions` from the previous
        proposal attempt; on the first pass there is none, so the graph proposes
        first and only clarifies once the model has said what it is missing.
        That ordering matters: clarifying before proposing would ask the operator
        to guess what the model needs.
        """
        open_questions = tuple((state.get("proposal") or {}).get("open_questions", ()) or ())
        if not open_questions:
            return {"next_action": NextAction.PROPOSE}

        verdict = check_clarification_budget(deps.budgets, state.get("clarification_count", 0))
        if verdict.exhausted:
            return {
                "next_action": NextAction.ESCALATE,
                "completion_status": CompletionStatus.NEEDS_HUMAN_REVIEW,
                "escalation_reason": verdict.reason,
            }
        return {"next_action": NextAction.CLARIFY}

    return identify_gaps


def make_interrupt_for_clarification_node() -> NodeFn:
    async def interrupt_for_clarification(state: dict[str, Any]) -> dict[str, Any]:
        """Suspend until a human answers.

        The interrupt payload carries references and the question only -- never a
        source sample (section 14.4). LangGraph re-executes this node from its
        first line on resume, so everything above `interrupt()` must stay free of
        side effects; today that is just reading state.
        """
        questions = tuple((state.get("proposal") or {}).get("open_questions", ()) or ())
        question = questions[0] if questions else "Additional information is required."
        answer = interrupt(
            {
                "analysis_id": state["analysis_id"],
                "draft_id": state.get("draft_id"),
                "revision_id": state.get("revision_id"),
                "question": question,
            }
        )
        exchanges = tuple(state.get("clarification_exchanges", ()))
        return {
            "clarification_exchanges": (*exchanges, {"question": question, "answer": str(answer)}),
            "clarification_count": state.get("clarification_count", 0) + 1,
            # Clear the answered questions so IDENTIFY_GAPS does not immediately
            # re-ask the same one on the way back through.
            "proposal": None,
            "next_action": NextAction.ANALYZE,
            "completion_status": CompletionStatus.IN_PROGRESS,
        }

    return interrupt_for_clarification


def make_propose_schema_node(deps: AnalyzerDependencies) -> NodeFn:
    async def propose_schema(state: dict[str, Any]) -> dict[str, Any]:
        """The single AI call. Everything it sees is six-block framed."""
        snapshot = await deps.persistence.load_snapshot(state["source_snapshot_id"])
        blocks = build_prompt_blocks(
            task_definition=_TASK_DEFINITION,
            source_metadata=[dataset.model_dump(mode="json") for dataset in snapshot.datasets],
            # Metadata-only by construction at this layer: retained samples live
            # in an encrypted structure behind their own access path, and the
            # reasoning graph deliberately does not hold them (section 14.4).
            untrusted_samples=None,
            user_requirements=state.get("requirements", ""),
            clarification_answers=list(state.get("clarification_exchanges", ())),
        )
        proposal = await deps.reasoning.propose_schema(
            analysis_id=state["analysis_id"],
            snapshot_content_hash=snapshot.content_hash,
            prompt_blocks=[block.model_dump(mode="json") for block in blocks],
        )
        return {
            "proposal": proposal.model_dump(mode="json"),
            "next_action": NextAction.VALIDATE,
        }

    return propose_schema


def make_validate_proposal_node(deps: AnalyzerDependencies) -> NodeFn:
    async def validate_proposal(state: dict[str, Any]) -> dict[str, Any]:
        """Graph-side feasibility. A proposal can be internally coherent and
        still be unbuildable against this target, so the target gets a say."""
        proposal = state.get("proposal") or {}
        findings = await deps.graph_target.validate_schema(draft=proposal)
        attempt = state.get("validation_attempt", 0) + 1
        if not findings:
            return {
                "validation_findings": (),
                "validation_attempt": attempt,
                "next_action": NextAction.COMPLETE,
            }
        verdict = check_validation_budget(deps.budgets, attempt)
        if verdict.exhausted:
            return {
                "validation_findings": tuple(dict(f) for f in findings),
                "validation_attempt": attempt,
                "next_action": NextAction.ESCALATE,
                "completion_status": CompletionStatus.NEEDS_HUMAN_REVIEW,
                "escalation_reason": verdict.reason,
            }
        return {
            "validation_findings": tuple(dict(f) for f in findings),
            "validation_attempt": attempt,
            "next_action": NextAction.REVISE,
        }

    return validate_proposal


def make_reason_about_failures_node() -> NodeFn:
    async def reason_about_failures(state: dict[str, Any]) -> dict[str, Any]:
        """Turn validation findings into a note the next proposal can use.

        Kept separate from REVISE_PROPOSAL so the reasoning is recorded in state
        even when the subsequent revision fails again -- otherwise the escalation
        a human eventually sees would have findings but no trail of what the
        graph made of them.
        """
        findings = state.get("validation_findings", ())
        summary = "; ".join(str(f.get("message", f)) for f in findings)
        notes = tuple(state.get("reasoning_notes", ()))
        return {
            "reasoning_notes": (*notes, f"validation failures: {summary}"),
            "next_action": NextAction.REVISE,
        }

    return reason_about_failures


def make_revise_proposal_node() -> NodeFn:
    async def revise_proposal(state: dict[str, Any]) -> dict[str, Any]:
        """Discard the rejected proposal and re-enter analysis.

        Budget counters are deliberately preserved: a revision must not be a way
        to reset the ceilings that bound the loop.
        """
        return {"proposal": None, "next_action": NextAction.ANALYZE}

    return revise_proposal


def make_user_review_node() -> NodeFn:
    async def user_review(state: dict[str, Any]) -> dict[str, Any]:
        """The reasoning graph's terminal state.

        Marks the proposal ready and stops. Approval, build, activation, and
        every other lifecycle operation happen outside the graph, on an explicit
        human action -- see this module's docstring.
        """
        return {
            "completion_status": CompletionStatus.READY_FOR_APPROVAL,
            "next_action": NextAction.COMPLETE,
        }

    return user_review
