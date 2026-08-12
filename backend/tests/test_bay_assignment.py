"""Deterministic bay-assignment construction tests."""

from datetime import UTC, datetime

import pytest

from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.workflows.bay_assignment import build_bay_assignment_result
from return_platform.workflows.stage_results import (
    BayAssignmentActivityResult,
    BayAssignmentStatus,
    FulfillmentTrackingActivityResult,
    FulfillmentTrackingStatus,
    ReturnRequestOutcome,
    StageResultValidationError,
    bay_assignment_result_from_binding,
    bind_stage_activity_result,
)

_AT = datetime(2026, 7, 22, 15, tzinfo=UTC)


def _fulfillment(status: FulfillmentTrackingStatus) -> ContextSnapshot:
    active = status is not FulfillmentTrackingStatus.NOT_APPLICABLE
    binding = bind_stage_activity_result(
        WorkflowStage.FULFILLMENT_TRACKING,
        FulfillmentTrackingActivityResult(
            "fulfillment-tracking-v1",
            ReturnRequestOutcome.CREATED if active else ReturnRequestOutcome.REVIEW_PENDING,
            status,
            "REQUEST-1",
            "RETURN-1" if active else None,
            "FULFILLMENT-1" if active else None,
            "TRACKING-1" if status is FulfillmentTrackingStatus.IN_TRANSIT else None,
            "a" * 64,
            ("FIXTURE:FULFILLMENT",),
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
    ("fulfillment_status", "warehouse", "bay", "assignment_status"),
    (
        (
            FulfillmentTrackingStatus.NOT_APPLICABLE,
            None,
            None,
            BayAssignmentStatus.NOT_APPLICABLE,
        ),
        (
            FulfillmentTrackingStatus.AWAITING_HANDOFF,
            None,
            None,
            BayAssignmentStatus.PENDING,
        ),
        (
            FulfillmentTrackingStatus.IN_TRANSIT,
            "WAREHOUSE-1",
            "BAY-1",
            BayAssignmentStatus.ASSIGNED,
        ),
    ),
)
def test_builder_maps_fulfillment_to_legal_assignment(
    fulfillment_status: FulfillmentTrackingStatus,
    warehouse: str | None,
    bay: str | None,
    assignment_status: BayAssignmentStatus,
) -> None:
    fulfillment = _fulfillment(fulfillment_status)
    result = build_bay_assignment_result(
        fulfillment=fulfillment,
        warehouse_reference=warehouse,
        bay_reference=bay,
        configuration_version="return-v1",
        observed_at=_AT,
    )
    binding = bind_stage_activity_result(WorkflowStage.BAY_ASSIGNMENT, result)

    assert result.status is assignment_status
    assert result.fulfillment_context_digest == fulfillment.payload_digest
    assert bay_assignment_result_from_binding(binding) == result


@pytest.mark.parametrize(
    ("status", "warehouse", "bay"),
    (
        (FulfillmentTrackingStatus.NOT_APPLICABLE, "WAREHOUSE-1", "BAY-1"),
        (FulfillmentTrackingStatus.AWAITING_HANDOFF, None, "BAY-1"),
        (FulfillmentTrackingStatus.IN_TRANSIT, "WAREHOUSE-1", None),
    ),
)
def test_builder_rejects_illegal_assignment_references(
    status: FulfillmentTrackingStatus,
    warehouse: str | None,
    bay: str | None,
) -> None:
    with pytest.raises(ValueError):
        build_bay_assignment_result(
            fulfillment=_fulfillment(status),
            warehouse_reference=warehouse,
            bay_reference=bay,
            configuration_version="return-v1",
            observed_at=_AT,
        )


def test_binding_rejects_assigned_state_without_bay() -> None:
    with pytest.raises(StageResultValidationError):
        bind_stage_activity_result(
            WorkflowStage.BAY_ASSIGNMENT,
            BayAssignmentActivityResult(
                "bay-assignment-v1",
                FulfillmentTrackingStatus.IN_TRANSIT,
                BayAssignmentStatus.ASSIGNED,
                "REQUEST-1",
                "RETURN-1",
                "WAREHOUSE-1",
                None,
                "b" * 64,
                ("FIXTURE:BAY",),
                "return-v1",
                _AT,
            ),
        )


def test_a_bay_may_be_assigned_before_the_shipment_moves() -> None:
    """Bay assignment is concurrent with the return, not downstream of it.

    ASSIGNED used to be legal only alongside IN_TRANSIT fulfillment, so the bay
    had to wait for a shipment that does not exist yet -- the inverse of the
    intended ordering, and the reason an unavailable bay could stall a return.
    """
    result = build_bay_assignment_result(
        fulfillment=_fulfillment(FulfillmentTrackingStatus.AWAITING_HANDOFF),
        warehouse_reference="WAREHOUSE-1",
        bay_reference="BAY-1",
        configuration_version="return-v1",
        observed_at=_AT,
    )

    assert result.status is BayAssignmentStatus.ASSIGNED
    # Still bindable: the widening did not weaken the stage-result contract.
    bind_stage_activity_result(WorkflowStage.BAY_ASSIGNMENT, result)


def test_no_bay_found_is_a_state_not_a_failure() -> None:
    """Bay is best-effort. `orchestrator._handle` calls this inline, so raising
    here failed the whole return over an advisory step."""
    for status in (
        FulfillmentTrackingStatus.AWAITING_HANDOFF,
        FulfillmentTrackingStatus.IN_TRANSIT,
    ):
        result = build_bay_assignment_result(
            fulfillment=_fulfillment(status),
            warehouse_reference=None,
            bay_reference=None,
            configuration_version="return-v1",
            observed_at=_AT,
        )
        assert result.status is BayAssignmentStatus.PENDING
        bind_stage_activity_result(WorkflowStage.BAY_ASSIGNMENT, result)
