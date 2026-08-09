"""Topology only -- every node's behaviour lives in nodes.py.

    START -> LOAD_SOURCE_SNAPSHOT -> ANALYZE_STRUCTURE -> IDENTIFY_GAPS
                     ^                                        |
                     |            clarification_required / sufficient_context
                     |                       |                    |
                     +-- INTERRUPT_FOR_CLARIFICATION       PROPOSE_SCHEMA
                                                            |         |
                                              open_questions|         |no questions
                                                            |         v
                                        +---------------+---+   VALIDATE_PROPOSAL
                                        |               |        |          |
                                        |          IDENTIFY_GAPS |          |
                                 validation_failure            valid    budget out
                                        v                        v          v
                              REASON_ABOUT_FAILURES         USER_REVIEW   ESCALATE
                                        v                        v          v
                                 REVISE_PROPOSAL               END        END
                                        |
                                        +--> ANALYZE_STRUCTURE

The `open_questions` edge from PROPOSE_SCHEMA back to IDENTIFY_GAPS is a
deliberate departure from the design's diagram, which draws PROPOSE straight
into VALIDATE. See `routing.route_after_propose_schema` for why the literal
topology leaves the clarification branch unreachable.

APPLY_TYPED_MUTATION and the USER_REVIEW "modification" branch are C3.3 and are
absent rather than stubbed -- a node that exists and does nothing is worse than
one that is visibly missing.
"""

from __future__ import annotations

from collections.abc import Hashable

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from return_platform.graph_schema_analyzer.reasoning.nodes import (
    AnalyzerDependencies,
    make_analyze_structure_node,
    make_identify_gaps_node,
    make_interrupt_for_clarification_node,
    make_load_source_snapshot_node,
    make_propose_schema_node,
    make_reason_about_failures_node,
    make_revise_proposal_node,
    make_user_review_node,
    make_validate_proposal_node,
)
from return_platform.graph_schema_analyzer.reasoning.routing import (
    route_after_identify_gaps,
    route_after_propose_schema,
    route_after_validate_proposal,
)
from return_platform.graph_schema_analyzer.reasoning.state import AnalyzerState

__all__ = ["build_analyzer_graph"]

_AFTER_IDENTIFY_GAPS: dict[Hashable, str] = {
    "propose": "propose_schema",
    "clarify": "interrupt_for_clarification",
    "escalate": END,
}

_AFTER_PROPOSE: dict[Hashable, str] = {
    "validate": "validate_proposal",
    "reconsider": "identify_gaps",
}

_AFTER_VALIDATE: dict[Hashable, str] = {
    "complete": "user_review",
    "revise": "reason_about_failures",
    "escalate": END,
}


def build_analyzer_graph(
    deps: AnalyzerDependencies, *, checkpointer: BaseCheckpointSaver[str] | None = None
) -> CompiledStateGraph[AnalyzerState, None]:
    """Compile the reasoning graph.

    A checkpointer is required for real use, not optional decoration: the
    clarification pause is a LangGraph `interrupt()`, and without persistence
    there is nothing to resume from days later when the operator answers.
    """
    graph: StateGraph[AnalyzerState, None] = StateGraph(AnalyzerState)

    graph.add_node("load_source_snapshot", make_load_source_snapshot_node(deps))
    graph.add_node("analyze_structure", make_analyze_structure_node())
    graph.add_node("identify_gaps", make_identify_gaps_node(deps))
    graph.add_node("interrupt_for_clarification", make_interrupt_for_clarification_node())
    graph.add_node("propose_schema", make_propose_schema_node(deps))
    graph.add_node("validate_proposal", make_validate_proposal_node(deps))
    graph.add_node("reason_about_failures", make_reason_about_failures_node())
    graph.add_node("revise_proposal", make_revise_proposal_node())
    graph.add_node("user_review", make_user_review_node())

    graph.set_entry_point("load_source_snapshot")
    graph.add_edge("load_source_snapshot", "analyze_structure")
    graph.add_edge("analyze_structure", "identify_gaps")
    graph.add_conditional_edges("identify_gaps", route_after_identify_gaps, _AFTER_IDENTIFY_GAPS)
    # A resumed clarification re-enters analysis rather than jumping straight to
    # a proposal, so the answer actually informs the next attempt.
    graph.add_edge("interrupt_for_clarification", "analyze_structure")
    graph.add_conditional_edges("propose_schema", route_after_propose_schema, _AFTER_PROPOSE)
    graph.add_conditional_edges("validate_proposal", route_after_validate_proposal, _AFTER_VALIDATE)
    graph.add_edge("reason_about_failures", "revise_proposal")
    graph.add_edge("revise_proposal", "analyze_structure")
    graph.add_edge("user_review", END)

    return graph.compile(checkpointer=checkpointer)
