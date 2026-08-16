"""A turn that asks the associate a question has not finished.

Observed on 2026-08-16, in the first manual run that ever reached case
creation. The model confirmed order CO803471 and asked "What is coming back off
it, and what went wrong with it?" in the same response, under
``status: COMPLETE``. Every reader downstream believed the status:

* no clarification thread was opened -- ``pending_clarification_thread_id``
  came back ``null``, so the associate's answer had nothing to attach to;
* discovery closed and the case was raised;
* the case ran policy evaluation with no return reason and no line selected,
  which correctly produced ``REQUIRED_FACT_UNKNOWN`` and failed safe to
  ``REVIEW_REQUIRED``.

The associate was then looking at "Policy Exception Review Required", with a
supervisor override offered, beside a question nobody had answered yet -- and
the evaluator carried the blame for a decision it was never in a position to
make. Nothing had gone wrong with the evaluator. The turn had simply claimed to
be finished while it was still asking.

`AgentAction` already refuses the mirror image of this -- a CLARIFY whose
`requested_input` is empty. This is the direction that loses an answer rather
than merely omitting a question, and it was unguarded.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from return_platform.dynamic_knowledge.knowledge.evidence import (
    ResponseStatement,
    StatementType,
    StructuredAgentResponse,
    TERMINAL_STATUSES,
)


def _question(text: str = "What went wrong with it?") -> ResponseStatement:
    return ResponseStatement(
        statement_id="s1",
        statement_type=StatementType.CLARIFICATION_QUESTION,
        text=text,
        evidence_refs=(),
    )


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
def test_a_terminal_status_may_not_carry_a_clarification_question(status: str) -> None:
    """The exact shape of the observed turn."""
    with pytest.raises(ValidationError, match="still asks the associate a question"):
        StructuredAgentResponse(
            status=status,
            business_capability="return-context-collection",
            statements=(_question(),),
        )


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
def test_a_terminal_status_may_not_carry_requested_input(status: str) -> None:
    """`requested_input` alone is an open question too.

    Checked separately because the two travel independently: the observed turn
    carried both, but a response can populate `requested_input` with no
    statement typed as a question, and it is just as unanswered.
    """
    with pytest.raises(ValidationError, match="still asks the associate a question"):
        StructuredAgentResponse(
            status=status,
            business_capability="return-context-collection",
            statements=(),
            requested_input="What is coming back off it?",
        )


def test_the_status_is_matched_regardless_of_case_or_padding() -> None:
    """`status` is the model's own word, and models are inconsistent about it."""
    with pytest.raises(ValidationError, match="still asks the associate a question"):
        StructuredAgentResponse(
            status="  complete  ",
            business_capability="return-context-collection",
            statements=(_question(),),
        )


def test_asking_under_a_non_terminal_status_is_the_normal_path() -> None:
    """The overwhelmingly common turn, and it must stay unremarkable."""
    response = StructuredAgentResponse(
        status="NEEDS_CLARIFICATION",
        business_capability="return-context-collection",
        statements=(_question(),),
        requested_input="What is coming back off it?",
    )

    assert response.requested_input == "What is coming back off it?"


def test_finishing_without_asking_anything_is_still_allowed() -> None:
    """The guard must not make a completed turn impossible to express."""
    response = StructuredAgentResponse(
        status="COMPLETE",
        business_capability="order-discovery",
        statements=(
            ResponseStatement(
                statement_id="s1",
                statement_type=StatementType.REASONED_SUGGESTION,
                text="CO803471 it is - that is the one we will raise the return against.",
                evidence_refs=(),
            ),
        ),
    )

    assert response.status == "COMPLETE"


def test_blank_requested_input_does_not_count_as_asking() -> None:
    """An empty string is not a question.

    Whitespace rather than `None` because a model that has nothing to ask
    frequently emits `""` -- treating that as an open question would refuse
    every completed turn from such a model.
    """
    response = StructuredAgentResponse(
        status="COMPLETE",
        business_capability="order-discovery",
        statements=(),
        requested_input="   ",
    )

    assert response.status == "COMPLETE"
