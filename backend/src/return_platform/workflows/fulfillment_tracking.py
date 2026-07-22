"""Deterministic fulfillment construction from persisted return-request evidence."""

from datetime import datetime

from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.workflows.stage_results import (
    FulfillmentTrackingActivityResult,
    FulfillmentTrackingStatus,
    ReturnRequestOutcome,
    StageContextBinding,
    bind_stage_activity_result,
    return_request_result_from_binding,
)

__all__ = ["build_fulfillment_tracking_result"]


def build_fulfillment_tracking_result(
    *,
    return_request: ContextSnapshot,
    fulfillment_reference: str | None,
    tracking_reference: str | None,
    configuration_version: str,
    observed_at: datetime,
) -> FulfillmentTrackingActivityResult:
    """Map authoritative return-request evidence to one legal tracking state."""
    request_result = return_request_result_from_binding(
        StageContextBinding(
            completed_stage=WorkflowStage.RETURN_REQUEST,
            schema_version=return_request.schema_version,
            payload_json=return_request.payload_json,
            payload_digest=return_request.payload_digest,
        )
    )
    if request_result.outcome is ReturnRequestOutcome.CREATED:
        if fulfillment_reference is None:
            raise ValueError("Created returns require a fulfillment reference.")
        status = (
            FulfillmentTrackingStatus.AWAITING_HANDOFF
            if tracking_reference is None
            else FulfillmentTrackingStatus.IN_TRANSIT
        )
    else:
        if fulfillment_reference is not None or tracking_reference is not None:
            raise ValueError("Inactive returns cannot carry fulfillment references.")
        status = FulfillmentTrackingStatus.NOT_APPLICABLE
    result = FulfillmentTrackingActivityResult(
        schema_version="fulfillment-tracking-v1",
        return_request_outcome=request_result.outcome,
        status=status,
        request_reference=request_result.request_reference,
        return_reference=request_result.return_reference,
        fulfillment_reference=fulfillment_reference,
        tracking_reference=tracking_reference,
        return_request_context_digest=return_request.payload_digest,
        evidence_references=(
            *request_result.evidence_references,
            f"CONTEXT_SHA256:{return_request.payload_digest}",
        ),
        configuration_version=configuration_version,
        observed_at=observed_at,
    )
    bind_stage_activity_result(WorkflowStage.FULFILLMENT_TRACKING, result)
    return result
