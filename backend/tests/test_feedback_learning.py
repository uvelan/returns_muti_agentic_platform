"""Deterministic feedback-learning construction tests."""

from datetime import UTC, datetime

import pytest

from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.workflows.feedback_learning import build_feedback_learning_result
from return_platform.workflows.stage_results import (
    BayAssignmentActivityResult,
    BayAssignmentStatus,
    FeedbackLearningActivityResult,
    FeedbackLearningStatus,
    FulfillmentTrackingStatus,
    StageResultValidationError,
    bind_stage_activity_result,
    feedback_learning_result_from_binding,
)

_AT = datetime(2026, 7, 22, 16, tzinfo=UTC)


def _assignment(status: BayAssignmentStatus) -> ContextSnapshot:
    active = status is not BayAssignmentStatus.NOT_APPLICABLE
    assigned = status is BayAssignmentStatus.ASSIGNED
    fulfillment_status = (
        FulfillmentTrackingStatus.IN_TRANSIT
        if assigned
        else FulfillmentTrackingStatus.AWAITING_HANDOFF
        if active
        else FulfillmentTrackingStatus.NOT_APPLICABLE
    )
    binding = bind_stage_activity_result(
        WorkflowStage.BAY_ASSIGNMENT,
        BayAssignmentActivityResult(
            "bay-assignment-v1",
            fulfillment_status,
            status,
            "REQUEST-1",
            "RETURN-1" if active else None,
            "WAREHOUSE-1" if assigned else None,
            "BAY-1" if assigned else None,
            "a" * 64,
            ("FIXTURE:BAY",),
            "return-v1",
            _AT,
        ),
    )
    return ContextSnapshot(
        schema_version=binding.schema_version,
        payload_json=binding.payload_json,
        payload_digest=binding.payload_digest,
    )


@pytest.mark.parametrize(
    ("assignment", "feedback", "signal", "status"),
    (
        (
            BayAssignmentStatus.NOT_APPLICABLE,
            None,
            None,
            FeedbackLearningStatus.NOT_APPLICABLE,
        ),
        (
            BayAssignmentStatus.PENDING,
            None,
            None,
            FeedbackLearningStatus.DEFERRED,
        ),
        (
            BayAssignmentStatus.ASSIGNED,
            "FEEDBACK-1",
            "LEARNING-SIGNAL-1",
            FeedbackLearningStatus.RECORDED,
        ),
    ),
)
def test_builder_maps_assignment_to_bounded_feedback(
    assignment: BayAssignmentStatus,
    feedback: str | None,
    signal: str | None,
    status: FeedbackLearningStatus,
) -> None:
    bay = _assignment(assignment)
    result = build_feedback_learning_result(
        bay_assignment=bay,
        feedback_reference=feedback,
        learning_signal_reference=signal,
        configuration_version="return-v1",
        observed_at=_AT,
    )
    binding = bind_stage_activity_result(WorkflowStage.FEEDBACK_LEARNING, result)

    assert result.status is status
    assert result.bay_assignment_context_digest == bay.payload_digest
    assert feedback_learning_result_from_binding(binding) == result


@pytest.mark.parametrize(
    ("assignment", "feedback", "signal"),
    (
        (BayAssignmentStatus.NOT_APPLICABLE, "FEEDBACK-1", None),
        (BayAssignmentStatus.PENDING, None, "LEARNING-SIGNAL-1"),
        (BayAssignmentStatus.ASSIGNED, "FEEDBACK-1", None),
    ),
)
def test_builder_rejects_illegal_feedback_references(
    assignment: BayAssignmentStatus,
    feedback: str | None,
    signal: str | None,
) -> None:
    with pytest.raises(ValueError):
        build_feedback_learning_result(
            bay_assignment=_assignment(assignment),
            feedback_reference=feedback,
            learning_signal_reference=signal,
            configuration_version="return-v1",
            observed_at=_AT,
        )


def test_binding_rejects_recorded_feedback_without_learning_signal() -> None:
    with pytest.raises(StageResultValidationError):
        bind_stage_activity_result(
            WorkflowStage.FEEDBACK_LEARNING,
            FeedbackLearningActivityResult(
                "feedback-learning-v1",
                BayAssignmentStatus.ASSIGNED,
                FeedbackLearningStatus.RECORDED,
                "REQUEST-1",
                "RETURN-1",
                "WAREHOUSE-1",
                "BAY-1",
                "FEEDBACK-1",
                None,
                "b" * 64,
                ("FIXTURE:FEEDBACK",),
                "return-v1",
                _AT,
            ),
        )
