"""Deterministic bay assignment from persisted fulfillment evidence."""

from datetime import datetime

from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.workflows.stage_results import (
    BayAssignmentActivityResult,
    BayAssignmentStatus,
    FulfillmentTrackingStatus,
    StageContextBinding,
    bind_stage_activity_result,
    fulfillment_tracking_result_from_binding,
)

__all__ = ["build_bay_assignment_result"]


def build_bay_assignment_result(
    *,
    fulfillment: ContextSnapshot,
    warehouse_reference: str | None,
    bay_reference: str | None,
    configuration_version: str,
    observed_at: datetime,
) -> BayAssignmentActivityResult:
    """Map authoritative fulfillment evidence to one legal assignment state."""
    fulfillment_result = fulfillment_tracking_result_from_binding(
        StageContextBinding(
            completed_stage=WorkflowStage.FULFILLMENT_TRACKING,
            schema_version=fulfillment.schema_version,
            payload_json=fulfillment.payload_json,
            payload_digest=fulfillment.payload_digest,
        )
    )
    if fulfillment_result.status is FulfillmentTrackingStatus.IN_TRANSIT:
        if warehouse_reference is None or bay_reference is None:
            raise ValueError("In-transit fulfillment requires warehouse and bay references.")
        status = BayAssignmentStatus.ASSIGNED
    elif fulfillment_result.status is FulfillmentTrackingStatus.AWAITING_HANDOFF:
        if warehouse_reference is not None or bay_reference is not None:
            raise ValueError("Awaiting handoff cannot claim a bay assignment.")
        status = BayAssignmentStatus.PENDING
    else:
        if warehouse_reference is not None or bay_reference is not None:
            raise ValueError("Inactive fulfillment cannot claim a bay assignment.")
        status = BayAssignmentStatus.NOT_APPLICABLE
    result = BayAssignmentActivityResult(
        schema_version="bay-assignment-v1",
        fulfillment_status=fulfillment_result.status,
        status=status,
        request_reference=fulfillment_result.request_reference,
        return_reference=fulfillment_result.return_reference,
        warehouse_reference=warehouse_reference,
        bay_reference=bay_reference,
        fulfillment_context_digest=fulfillment.payload_digest,
        evidence_references=(
            *fulfillment_result.evidence_references,
            f"CONTEXT_SHA256:{fulfillment.payload_digest}",
        ),
        configuration_version=configuration_version,
        observed_at=observed_at,
    )
    bind_stage_activity_result(WorkflowStage.BAY_ASSIGNMENT, result)
    return result
