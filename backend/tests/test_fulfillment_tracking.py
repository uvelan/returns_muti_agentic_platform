"""Deterministic fulfillment-tracking construction tests.

The state under test is the one W2.6 exists to correct: `IN_TRANSIT` used to
follow from `tracking_reference is not None` -- from the platform having written
a number into its own store, never from anything having been seen to move. These
assert the separation: a number is necessary and no longer sufficient, and the
three readings of the shipment evidence stay distinguishable afterwards.
"""

from datetime import UTC, datetime

import pytest

from return_platform.canonical.operations import ContextSnapshot, WorkflowStage
from return_platform.workflows.fulfillment_tracking import (
    ShipmentEvidence,
    ShipmentObservation,
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
_TRACKING = "TRACKING-1"
_GENERATION = "gen-7"


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


def _observation(
    evidence: ShipmentEvidence,
    *,
    tracking_reference: str = _TRACKING,
    current_status: str | None = None,
    unavailable_reason: str | None = None,
) -> ShipmentObservation:
    return ShipmentObservation(
        tracking_reference=tracking_reference,
        evidence=evidence,
        graph_generation_id=_GENERATION,
        current_status=current_status,
        unavailable_reason=unavailable_reason,
    )


def _build(
    outcome: ReturnRequestOutcome,
    *,
    fulfillment: str | None,
    tracking: str | None,
    shipment: ShipmentObservation | None = None,
) -> FulfillmentTrackingActivityResult:
    return build_fulfillment_tracking_result(
        return_request=_return_request(outcome),
        fulfillment_reference=fulfillment,
        tracking_reference=tracking,
        configuration_version="return-v1",
        observed_at=_AT,
        shipment=shipment,
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
    """The states that do not depend on a shipment at all still hold."""
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


def test_a_tracking_number_alone_no_longer_makes_a_return_in_transit() -> None:
    """The whole of W2.6, as one assertion.

    Before this, a label printed and left on the counter and a parcel the
    carrier had collected produced the same fulfillment state, because the only
    input was whether the platform had written a number into its own store.
    """
    result = _build(
        ReturnRequestOutcome.CREATED,
        fulfillment="FULFILLMENT-1",
        tracking=_TRACKING,
        shipment=None,
    )

    assert result.status is FulfillmentTrackingStatus.AWAITING_HANDOFF
    assert result.tracking_reference == _TRACKING
    assert "SHIPMENT_UNAVAILABLE:NOT_ATTEMPTED" in result.evidence_references


def test_an_observed_shipment_is_what_makes_a_return_in_transit() -> None:
    """`IN_TRANSIT` requires evidence of a shipment, not evidence of a number."""
    result = _build(
        ReturnRequestOutcome.CREATED,
        fulfillment="FULFILLMENT-1",
        tracking=_TRACKING,
        shipment=_observation(ShipmentEvidence.OBSERVED, current_status="PICKED_UP"),
    )

    assert result.status is FulfillmentTrackingStatus.IN_TRANSIT
    # The generation and the observed status both land on the audit trail, so a
    # state derived from a generation since retired can still be explained.
    assert f"SHIPMENT_OBSERVED:{_GENERATION}:PICKED_UP" in result.evidence_references


def test_an_observed_shipment_with_no_status_is_still_movement() -> None:
    """A shipment record existing is the handoff signal.

    `current_status` is nullable in the descriptor, and refusing `IN_TRANSIT`
    without one would make a carrier that reports late look identical to a
    carrier that never collected.
    """
    result = _build(
        ReturnRequestOutcome.CREATED,
        fulfillment="FULFILLMENT-1",
        tracking=_TRACKING,
        shipment=_observation(ShipmentEvidence.OBSERVED),
    )

    assert result.status is FulfillmentTrackingStatus.IN_TRANSIT
    assert f"SHIPMENT_OBSERVED:{_GENERATION}:STATUS_UNKNOWN" in result.evidence_references


@pytest.mark.parametrize(
    ("evidence", "reason", "expected_reference"),
    (
        (ShipmentEvidence.ABSENT, None, f"SHIPMENT_ABSENT:{_GENERATION}"),
        (
            ShipmentEvidence.UNAVAILABLE,
            "SERVICEUNAVAILABLE",
            "SHIPMENT_UNAVAILABLE:SERVICEUNAVAILABLE",
        ),
    ),
)
def test_a_reader_can_tell_no_shipment_from_no_lookup(
    evidence: ShipmentEvidence, reason: str | None, expected_reference: str
) -> None:
    """Both degrade to `AWAITING_HANDOFF`, and they are not the same fact.

    Collapsing them is what let the previous implementation's single inference
    stay invisible: a return nobody has collected and a graph nobody could reach
    need different people to act.
    """
    result = _build(
        ReturnRequestOutcome.CREATED,
        fulfillment="FULFILLMENT-1",
        tracking=_TRACKING,
        shipment=_observation(evidence, unavailable_reason=reason),
    )

    assert result.status is FulfillmentTrackingStatus.AWAITING_HANDOFF
    assert expected_reference in result.evidence_references


def test_awaiting_handoff_with_a_tracking_number_is_a_bindable_state() -> None:
    """The binding used to forbid exactly this combination.

    `_bind_fulfillment_tracking` required `tracking_reference is None` for
    `AWAITING_HANDOFF`, which made "we have a number" and "it is moving" the same
    statement by construction -- so the builder could not have concluded
    otherwise even had it read a shipment.
    """
    result = _build(
        ReturnRequestOutcome.CREATED,
        fulfillment="FULFILLMENT-1",
        tracking=_TRACKING,
        shipment=_observation(ShipmentEvidence.ABSENT),
    )

    binding = bind_stage_activity_result(WorkflowStage.FULFILLMENT_TRACKING, result)

    assert fulfillment_tracking_result_from_binding(binding) == result


def test_an_observation_for_a_different_parcel_is_refused() -> None:
    """A mismatched observation is a wiring bug, not a state.

    Accepting it would let one return's shipment decide another's fulfillment
    status -- silently, and in the direction that claims more than is known.
    """
    with pytest.raises(ValueError):
        _build(
            ReturnRequestOutcome.CREATED,
            fulfillment="FULFILLMENT-1",
            tracking=_TRACKING,
            shipment=_observation(ShipmentEvidence.OBSERVED, tracking_reference="TRACKING-OTHER"),
        )


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
        _build(outcome, fulfillment=fulfillment, tracking=tracking)


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


def test_binding_still_requires_a_tracking_number_for_in_transit() -> None:
    """Relaxing one direction must not relax the other.

    `IN_TRANSIT` without a tracking reference would be a shipment nobody can
    look up, which is a state the source data cannot produce.
    """
    with pytest.raises(StageResultValidationError):
        bind_stage_activity_result(
            WorkflowStage.FULFILLMENT_TRACKING,
            FulfillmentTrackingActivityResult(
                "fulfillment-tracking-v1",
                ReturnRequestOutcome.CREATED,
                FulfillmentTrackingStatus.IN_TRANSIT,
                "REQUEST-1",
                "RETURN-1",
                "FULFILLMENT-1",
                None,
                "b" * 64,
                ("FIXTURE:FULFILLMENT",),
                "return-v1",
                _AT,
            ),
        )
