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
    # Bay is `best_effort`. It used to raise on all three of these, and
    # `orchestrator._handle` calls this inline -- so an unavailable bay failed
    # the return, which is the exact inverse of the policy. A bay that cannot be
    # assigned yet is a state, not an error, and the case carries on without it.
    #
    # The three cases are still distinguished, because "not yet" and "never"
    # mean different things to an operator looking at a parked return.
    if warehouse_reference is not None and bay_reference is not None:
        # A bay was found. This no longer requires IN_TRANSIT fulfillment:
        # assignment starts on order confirmation and runs alongside the rest
        # of the return, so the shipment usually does not exist yet.
        status = BayAssignmentStatus.ASSIGNED
    elif fulfillment_result.status is FulfillmentTrackingStatus.NOT_APPLICABLE:
        # Nothing is coming back physically, so no bay will ever be needed.
        status = BayAssignmentStatus.NOT_APPLICABLE
    else:
        # Sought and not found, or not sought yet. Either way the return
        # proceeds and the bay may be filled in later.
        status = BayAssignmentStatus.PENDING
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
