"""`SupportRequestDraft` is declared twice, and both copies must agree.

The workflow sandbox may not import `return_case_activities`, so the shape that
crosses the activity boundary is written out on both sides. That is a deliberate
duplication and not a mistake -- but it is a duplication nothing was checking,
and a field added to one side only is a value silently dropped in transit.

Found the hard way: `subject` was added to the workflow's copy, and thirty tests
failed on `SupportRequestDraft.__init__() got an unexpected keyword argument
'subject'` -- which is the *good* outcome. The bad one is a field that is
accepted on both sides and carries nothing, which is what a serialized
round-trip does with a shape mismatch nobody asserted.
"""

from __future__ import annotations

from dataclasses import fields

from return_platform.workflows.return_case_activities import (
    SupportRequestDraft as ActivitySide,
)
from return_platform.workflows.return_case_workflow import (
    SupportRequestDraft as WorkflowSide,
)


def _shape(declaration: type) -> dict[str, str]:
    return {field.name: str(field.type) for field in fields(declaration)}


def test_both_declarations_carry_the_same_fields() -> None:
    assert _shape(ActivitySide) == _shape(WorkflowSide)


def test_the_shape_still_carries_all_three_halves_of_the_handoff() -> None:
    """Named rather than derived, so adding a fourth is a decision somebody
    makes here rather than something the test absorbs."""
    assert set(_shape(WorkflowSide)) == {"text", "payload", "subject"}
