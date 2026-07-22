"""Deterministic return-request construction from persisted eligibility evidence."""

from datetime import datetime

from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.workflows.stage_results import (
    EligibilityDecision,
    ReturnRequestActivityResult,
    ReturnRequestOutcome,
    StageContextBinding,
    bind_stage_activity_result,
    eligibility_result_from_binding,
)

__all__ = ["build_return_request_result"]


def build_return_request_result(
    *,
    eligibility: ContextSnapshot,
    request_reference: str,
    return_reference: str | None,
    configuration_version: str,
    observed_at: datetime,
) -> ReturnRequestActivityResult:
    """Map authoritative eligibility evidence to one consistent request outcome."""
    eligibility_result = eligibility_result_from_binding(
        StageContextBinding(
            completed_stage=WorkflowStage.ELIGIBILITY_EVALUATION,
            schema_version=eligibility.schema_version,
            payload_json=eligibility.payload_json,
            payload_digest=eligibility.payload_digest,
        )
    )
    if eligibility_result.decision is EligibilityDecision.APPROVE:
        outcome = ReturnRequestOutcome.CREATED
        if return_reference is None:
            raise ValueError("Approved eligibility requires a return reference.")
    elif eligibility_result.decision is EligibilityDecision.REJECT:
        outcome = ReturnRequestOutcome.DECLINED
        if return_reference is not None:
            raise ValueError("Rejected eligibility cannot create a return reference.")
    else:
        outcome = ReturnRequestOutcome.REVIEW_PENDING
        if return_reference is not None:
            raise ValueError("Review-required eligibility cannot create a return reference.")
    result = ReturnRequestActivityResult(
        schema_version="return-request-v1",
        eligibility_decision=eligibility_result.decision,
        outcome=outcome,
        request_reference=request_reference,
        return_reference=return_reference,
        eligibility_context_digest=eligibility.payload_digest,
        evidence_references=(
            *eligibility_result.evidence_references,
            f"CONTEXT_SHA256:{eligibility.payload_digest}",
        ),
        configuration_version=configuration_version,
        observed_at=observed_at,
    )
    bind_stage_activity_result(WorkflowStage.RETURN_REQUEST, result)
    return result
