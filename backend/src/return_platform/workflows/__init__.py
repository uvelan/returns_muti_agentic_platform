"""Durable workflow definitions with lazy imports to keep pure contracts importable."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_RETURN_WORKFLOW_EXPORTS = frozenset(
    {
        "AppliedStageCommand",
        "ReturnSessionActivityResult",
        "ReturnSessionInitializeActivityInput",
        "ReturnSessionTransitionActivityInput",
        "ReturnWorkflow",
        "ReturnWorkflowAdvanceCommand",
        "ReturnWorkflowConfigurationVersion",
        "ReturnWorkflowErrorCode",
        "ReturnWorkflowExecutionState",
        "ReturnWorkflowInput",
        "ReturnWorkflowStatus",
        "ReturnWorkflowTransitionError",
        "advance_return_workflow",
        "start_return_workflow_execution",
    }
)
_STAGE_RESULT_EXPORTS = frozenset(
    {
        "BayAssignmentActivityResult",
        "BayAssignmentStatus",
        "EligibilityActivityResult",
        "EligibilityDecision",
        "FeedbackLearningActivityResult",
        "FeedbackLearningStatus",
        "FulfillmentTrackingActivityResult",
        "FulfillmentTrackingStatus",
        "IntakeActivityResult",
        "IntakeChannel",
        "OrderDiscoveryActivityResult",
        "ReturnRequestActivityResult",
        "ReturnRequestOutcome",
        "StageContextBinding",
        "StageResultValidationError",
        "bay_assignment_result_from_binding",
        "bind_stage_activity_result",
        "empty_stage_context_binding",
        "feedback_learning_result_from_binding",
        "fulfillment_tracking_result_from_binding",
        "return_request_result_from_binding",
        "validate_stage_context_binding",
    }
)

__all__ = sorted(_RETURN_WORKFLOW_EXPORTS | _STAGE_RESULT_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _RETURN_WORKFLOW_EXPORTS:
        module = import_module("return_platform.workflows.return_workflow")
        return getattr(module, name)
    if name in _STAGE_RESULT_EXPORTS:
        module = import_module("return_platform.workflows.stage_results")
        return getattr(module, name)
    raise AttributeError(name)
