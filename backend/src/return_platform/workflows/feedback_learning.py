"""Deterministic feedback construction from persisted bay evidence."""

from datetime import datetime

from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.workflows.stage_results import (
    BayAssignmentStatus,
    FeedbackLearningActivityResult,
    FeedbackLearningStatus,
    StageContextBinding,
    bay_assignment_result_from_binding,
    bind_stage_activity_result,
)

__all__ = ["build_feedback_learning_result"]


def build_feedback_learning_result(
    *,
    bay_assignment: ContextSnapshot,
    feedback_reference: str | None,
    learning_signal_reference: str | None,
    configuration_version: str,
    observed_at: datetime,
) -> FeedbackLearningActivityResult:
    """Map authoritative bay evidence to one bounded feedback disposition."""
    assignment = bay_assignment_result_from_binding(
        StageContextBinding(
            completed_stage=WorkflowStage.BAY_ASSIGNMENT,
            schema_version=bay_assignment.schema_version,
            payload_json=bay_assignment.payload_json,
            payload_digest=bay_assignment.payload_digest,
        )
    )
    if assignment.status is BayAssignmentStatus.ASSIGNED:
        if feedback_reference is None or learning_signal_reference is None:
            raise ValueError("Assigned returns require complete feedback evidence.")
        status = FeedbackLearningStatus.RECORDED
    elif assignment.status is BayAssignmentStatus.PENDING:
        if feedback_reference is not None or learning_signal_reference is not None:
            raise ValueError("Pending assignments cannot claim feedback evidence.")
        status = FeedbackLearningStatus.DEFERRED
    else:
        if feedback_reference is not None or learning_signal_reference is not None:
            raise ValueError("Inactive assignments cannot claim feedback evidence.")
        status = FeedbackLearningStatus.NOT_APPLICABLE
    result = FeedbackLearningActivityResult(
        schema_version="feedback-learning-v1",
        bay_assignment_status=assignment.status,
        status=status,
        request_reference=assignment.request_reference,
        return_reference=assignment.return_reference,
        warehouse_reference=assignment.warehouse_reference,
        bay_reference=assignment.bay_reference,
        feedback_reference=feedback_reference,
        learning_signal_reference=learning_signal_reference,
        bay_assignment_context_digest=bay_assignment.payload_digest,
        evidence_references=(
            *assignment.evidence_references,
            f"CONTEXT_SHA256:{bay_assignment.payload_digest}",
        ),
        configuration_version=configuration_version,
        observed_at=observed_at,
    )
    bind_stage_activity_result(WorkflowStage.FEEDBACK_LEARNING, result)
    return result
