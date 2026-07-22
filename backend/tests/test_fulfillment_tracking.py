"""Deterministic fulfillment-tracking construction tests."""

from datetime import UTC, datetime

import pytest

from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.workflows.fulfillment_tracking import (
    build_fulfillment_tracking_result,
)
from return_platform.workflows.stage_results import (
    EligibilityDecision,
    FulfillmentTrackingActivityResult,
    FulfillmentTrackingStatus,
    ReturnRequestActivityResult,
    ReturnRequestOutcome,
    StageResultValidationError,
    bind_stage_activity_result,
    fulfillment_tracking_result_from_binding,
)

_AT = datetime(2026, 7, 22, 14, tzinfo=UTC)


def _return_request(outcome: ReturnRequestOutcome) -> ContextSnapshot:
    created = outcome is ReturnRequestOutcome.CREATED
    decision = (
        EligibilityDecision.APPROVE
        if created
        else EligibilityDecision.REJECT
        if outcome is ReturnRequestOutcome.DECLINED
        else EligibilityDecision.REVIEW_REQUIRED
    )
    binding = bind_stage_activity_result(
        WorkflowStage.RETURN_REQUEST,
        ReturnRequestActivityResult(
            "return-request-v1",
            decision,
            outcome,
            "REQUEST-1",
            "RETURN-1" if created else None,
            "a" * 64,
            ("FIXTURE:RETURN-REQUEST",),
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
    ("outcome", "fulfillment", "tracking", "status"),
    (
        (
            ReturnRequestOutcome.CREATED,
            "FULFILLMENT-1",
            None,
            FulfillmentTrackingStatus.AWAITING_HANDOFF,
        ),
        (
            ReturnRequestOutcome.CREATED,
            "FULFILLMENT-1",
            "TRACKING-1",
            FulfillmentTrackingStatus.IN_TRANSIT,
        ),
        (
            ReturnRequestOutcome.DECLINED,
            None,
            None,
            FulfillmentTrackingStatus.NOT_APPLICABLE,
        ),
        (
            ReturnRequestOutcome.REVIEW_PENDING,
            None,
            None,
            FulfillmentTrackingStatus.NOT_APPLICABLE,
        ),
    ),
)
def test_builder_maps_return_request_to_legal_tracking_state(
    outcome: ReturnRequestOutcome,
    fulfillment: str | None,
    tracking: str | None,
    status: FulfillmentTrackingStatus,
) -> None:
    request = _return_request(outcome)
    result = build_fulfillment_tracking_result(
        return_request=request,
        fulfillment_reference=fulfillment,
        tracking_reference=tracking,
        configuration_version="return-v1",
        observed_at=_AT,
    )
    binding = bind_stage_activity_result(WorkflowStage.FULFILLMENT_TRACKING, result)

    assert result.status is status
    assert result.return_request_context_digest == request.payload_digest
    assert fulfillment_tracking_result_from_binding(binding) == result


@pytest.mark.parametrize(
    ("outcome", "fulfillment", "tracking"),
    (
        (ReturnRequestOutcome.CREATED, None, None),
        (ReturnRequestOutcome.DECLINED, "FULFILLMENT-1", None),
        (ReturnRequestOutcome.REVIEW_PENDING, None, "TRACKING-1"),
    ),
)
def test_builder_rejects_illegal_reference_combinations(
    outcome: ReturnRequestOutcome,
    fulfillment: str | None,
    tracking: str | None,
) -> None:
    with pytest.raises(ValueError):
        build_fulfillment_tracking_result(
            return_request=_return_request(outcome),
            fulfillment_reference=fulfillment,
            tracking_reference=tracking,
            configuration_version="return-v1",
            observed_at=_AT,
        )


def test_binding_rejects_active_tracking_for_inactive_return() -> None:
    with pytest.raises(StageResultValidationError):
        bind_stage_activity_result(
            WorkflowStage.FULFILLMENT_TRACKING,
            FulfillmentTrackingActivityResult(
                "fulfillment-tracking-v1",
                ReturnRequestOutcome.DECLINED,
                FulfillmentTrackingStatus.IN_TRANSIT,
                "REQUEST-1",
                None,
                "FULFILLMENT-1",
                "TRACKING-1",
                "b" * 64,
                ("FIXTURE:FULFILLMENT",),
                "return-v1",
                _AT,
            ),
        )
