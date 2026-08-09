"""Conditional edges. Every loop here is bounded by `limits.py`.

Routing reads `next_action` rather than inferring intent from which state fields
happen to be populated: the nodes already decided, and re-deriving that decision
in the router is how the two drift apart.
"""

from __future__ import annotations

from typing import Any

from return_platform.graph_schema_analyzer.reasoning.state import NextAction

__all__ = [
    "route_after_identify_gaps",
    "route_after_propose_schema",
    "route_after_validate_proposal",
]


def route_after_propose_schema(state: dict[str, Any]) -> str:
    """validate / reconsider.

    The design's diagram draws PROPOSE_SCHEMA straight into VALIDATE_PROPOSAL,
    with IDENTIFY_GAPS deciding earlier whether a clarification is needed. Taken
    literally that cannot work: the gap signal is the model's own
    `open_questions`, which do not exist until it has proposed, so on a first
    pass IDENTIFY_GAPS always sees none and the clarification branch is
    unreachable. (Caught by a test asserting the graph suspends -- it never did.)

    So a proposal that arrives *with* open questions routes back to
    IDENTIFY_GAPS instead of onward: the model has told us it guessed, and
    validating a guess wastes an attempt from the validation budget on a
    question a human could answer directly. IDENTIFY_GAPS remains the single
    place that decides to clarify.
    """
    proposal = state.get("proposal") or {}
    if proposal.get("open_questions"):
        return "reconsider"
    return "validate"


def route_after_identify_gaps(state: dict[str, Any]) -> str:
    """propose / clarify / escalate. There is no 'ask again immediately' edge --
    a clarification always returns through ANALYZE_STRUCTURE first, so a second
    question can only be asked after the model has reconsidered with the answer.
    """
    action = state.get("next_action")
    if action == NextAction.ESCALATE:
        return "escalate"
    if action == NextAction.CLARIFY:
        return "clarify"
    return "propose"


def route_after_validate_proposal(state: dict[str, Any]) -> str:
    """complete / revise / escalate."""
    action = state.get("next_action")
    if action == NextAction.ESCALATE:
        return "escalate"
    if action == NextAction.REVISE:
        return "revise"
    return "complete"
