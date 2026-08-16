"""Carrier, service level, ETA and the restocking rate: producer or honest absence.

Two audit findings meet here, and both were findings about a *producer* rather
than about a shape. Both producers now exist; what these tests hold in place is
which fields have one and which deliberately do not.

**#9 -- carrier, service level and ETA.** The Copilot rendered
`session.orderSource` as "Carrier & Service" and
`session.shippingPathExpectation` as "Est. Delivery": an order's source system
and a return-method enum, presented as a carrier and a date. `carrier` now has a
real chain -- `ReturnOutcomeRecord.carrier -> SupportReturnRecord.carrier ->
RETURN_RECORD_MERGED_FIELDS -> ReturnRecordView.carrier -> project_shipments` --
and `tests/operations/test_carrier_reaches_the_case.py` drives it from the HTTP
edge. The tests here hold the *reader* half: the carrier comes off the return
record and off nothing else, and `serviceLevel` and `estimatedDeliveryAt` stay
`None` because neither has a producer. The only service level in the platform is
`PickupRequest.serviceLevel`, a freight collection booking rather than a parcel
service, and nothing computes a return-leg estimate at all.

**#12/§11 -- the restocking rate.** `FeeDetermination.rate_basis_points` and
`.rate_source` are real values on `PolicyOutcome`, produced from the seller
schedule in the active release. `PolicyEvaluationProjection` carries them,
`ReturnCaseWorkflowActivities._record_policy_outcome` appends both facts beside
`policy_restocking_fee_applies` / `policy_restocking_fee_waived`, and
`assembly._restocking_rate` reads them back. The rate is recorded **as
evaluated** and never re-read from the live release; `tests/policy/
test_restocking_rate_reaches_the_case.py` drives that through the shipped
workflow. The tests here hold the reader's own rules: both facts or neither, an
unreadable authority drops the rate with it, and an out-of-range rate is not
believed.

**No currency figure, under any branch.** The evaluator is pure and holds no
line prices; the rate is policy and the money is arithmetic that belongs where
prices live. `test_the_projection_carries_no_currency_figure_for_the_fee` is the
standing guard on that.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from return_platform.operations.case_projection.assembly import (
    CaseAggregateDocuments,
    assemble_case_projection_state,
    project_policy_evaluation,
    project_shipments,
)
from return_platform.operations.case_projection.contract import (
    PolicyEvaluationProjection,
    ShipmentProjection,
)
from return_platform.policy import EligibilityDecision, FeeAmountSource, PolicyRoute

NOW = datetime(2026, 8, 15, 9, 30, tzinfo=UTC)


def _fact(name: str, value: Any) -> dict[str, Any]:
    return {
        "factId": f"fact-{name}",
        "caseId": "case-1",
        "factName": name,
        "value": value,
        "agentId": "return-eligibility-policy",
        "channel": "SYSTEM",
        "acquisitionMethod": "DERIVED",
        "sourceSystem": "RETURN_ELIGIBILITY_POLICY",
        "sourcePath": "DETERMINISTIC_POLICY_EVALUATION",
        "observedAt": NOW,
        "recordedAt": NOW,
        "supersedesFactId": None,
    }


def _facts(**values: Any) -> dict[str, dict[str, Any]]:
    return {name: _fact(name, value) for name, value in values.items()}


def _evaluated(**extra: Any) -> dict[str, dict[str, Any]]:
    """A standard return the evaluator approved, plus whatever the test adds."""
    return _facts(
        policy_route="STANDARD_RETURN",
        policy_decision="APPROVE",
        policy_conditions="RESTOCKING_FEE_APPLIES",
        **extra,
    )


def _record(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "returnRecordId": "rec-1",
        "caseId": "case-1",
        "returnReference": "RMA-OPS01-CD4364",
        "status": "ISSUED",
        "returnLocation": None,
        "trackingReference": "1Z999AA1",
        "labelReference": "LBL-OPS01",
        "shippingInstructionReference": None,
        "sourceSystem": "RETURN_SUPPORT",
        "version": 1,
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    document.update(overrides)
    return document


# ---------------------------------------------------------------------------
# Carrier: produced from the record. Service level and ETA: still unproduced.
# ---------------------------------------------------------------------------


def test_a_package_projects_no_service_level_and_no_eta() -> None:
    """The two fields of audit finding #9 that still have no producer.

    A record with a tracking reference is a package, and the package is real:
    `shipmentId` and `trackingNumber` are populated from persistence. This
    record names no carrier, so `carrier` is `None` too -- Support said nothing
    about one. `serviceLevel` and `estimatedDeliveryAt` are `None` for a
    different and permanent reason: nothing produces them.
    `project_shipments` names what is missing for each. `None` is not a defect
    here; substituting `orderSource` and `shippingPathExpectation` for two of
    them is the defect, and it is what this replaced.
    """
    shipments = project_shipments(_record())

    assert shipments is not None
    (shipment,) = shipments
    assert shipment.trackingNumber == "1Z999AA1"
    assert shipment.carrier is None
    assert shipment.serviceLevel is None
    assert shipment.estimatedDeliveryAt is None
    assert shipment.shipmentStatus is None


def test_the_carrier_is_read_from_the_record_and_reaches_the_package() -> None:
    """The reader half of the chain audit finding #9 asked for.

    `ReturnRecordView.carrier` is where a Support-supplied carrier lands, and
    the assembler reads it from there. The write half -- HTTP request through
    workflow dataclass through merged field -- is
    `tests/operations/test_carrier_reaches_the_case.py`.
    """
    shipments = project_shipments(_record(carrier="UPS"))

    assert shipments is not None
    (shipment,) = shipments
    assert shipment.carrier == "UPS"


def test_a_blank_carrier_is_absence_and_not_a_carrier_called_nothing() -> None:
    """`_text` drops blanks, so an empty column never renders as a carrier."""
    shipments = project_shipments(_record(carrier="   "))

    assert shipments is not None
    (shipment,) = shipments
    assert shipment.carrier is None


def test_no_column_on_the_return_record_supplies_a_service_level_or_an_eta() -> None:
    """The absence is in persistence, not in the reader.

    A document carrying every plausible spelling still projects nothing for
    these two, because the assembler reads neither -- and it reads neither
    because `ReturnRecordView` declares neither and no writer sets either. If a
    producer ever lands, this test fails, which is the point: it should be
    edited by the change that adds one and by nothing else.
    """
    from return_platform.operations.models import ReturnRecordView

    shipments = project_shipments(
        _record(
            serviceLevel="GROUND",
            estimatedDeliveryAt=NOW,
            sourceSystem="OMC",
        )
    )

    assert shipments is not None
    (shipment,) = shipments
    assert shipment.serviceLevel is None
    assert shipment.estimatedDeliveryAt is None
    assert "serviceLevel" not in ReturnRecordView.model_fields
    assert "estimatedDeliveryAt" not in ReturnRecordView.model_fields


def test_the_order_source_can_never_reach_the_carrier_field() -> None:
    """The live defect, made unsayable.

    `orderSource` was rendered as "Carrier & Service". The shipment model has no
    such field to borrow from, and the assembler passes `carrier` exactly one
    value -- the return record's own column -- so there is no arrangement of the
    case documents that puts an order's source system in front of an operator as
    a carrier.
    """
    fields = set(ShipmentProjection.model_fields)

    assert {"carrier", "serviceLevel", "estimatedDeliveryAt"} <= fields
    assert "orderSource" not in fields
    assert "shippingPathExpectation" not in fields

    shipments = project_shipments(_record(orderSource="OMC"))
    assert shipments is not None
    assert shipments[0].carrier is None


def test_the_case_path_now_carries_a_carrier_end_to_end() -> None:
    """The four links that were missing, asserted as a chain.

    `SupportActionRequest.carrier` was the only carrier on the platform, and it
    travels the **session** path, guarded by `sessionId is not None`. A Copilot
    case has no session, so the case path had to grow its own: every model below
    is one link, and a chain missing any one of them puts `None` on the screen
    while the value sits at the other end. This is the shape of test the
    `return_method` gap needed and did not have.
    """
    import dataclasses

    from return_platform.api.return_support import ReturnOutcomeRecord
    from return_platform.operations.models import ReturnRecordView
    from return_platform.operations.return_support.service import SupportActionRequest
    from return_platform.operations.sql_business_state import ReturnRecordWrite
    from return_platform.workflows.return_case_activities import RETURN_RECORD_MERGED_FIELDS
    from return_platform.workflows.return_case_workflow import SupportReturnRecord

    assert "carrier" in SupportActionRequest.model_fields
    assert "carrier" in ReturnOutcomeRecord.model_fields
    assert "carrier" in {field.name for field in dataclasses.fields(SupportReturnRecord)}
    assert ("carrier", "carrier", "carrier") in RETURN_RECORD_MERGED_FIELDS
    assert "carrier" in ReturnRecordView.model_fields
    assert "carrier" in {field.name for field in dataclasses.fields(ReturnRecordWrite)}


def test_the_carrier_is_not_pinned_by_a_field_pattern() -> None:
    """No operator-owned carrier catalogue exists, so nothing may pin one.

    The realistic regression is someone adding `pattern=r"^(UPS|FEDEX|...)$"`
    "for the contract". It would refuse a carrier a deployment starts using
    tomorrow and would advertise the stale set through the generated client as
    authoritative -- the CFG-03 defect that `shippingPathExpectation` had.
    """
    from return_platform.api.return_support import ReturnOutcomeRecord

    field = ReturnOutcomeRecord.model_fields["carrier"]
    patterns = [item for item in field.metadata if getattr(item, "pattern", None) is not None]
    assert patterns == [], f"carrier is pinned by a pattern: {patterns}"


# ---------------------------------------------------------------------------
# The restocking rate reaches the projection
# ---------------------------------------------------------------------------


def test_the_rate_and_its_authority_reach_the_projection_together() -> None:
    """Given the facts, the configured rate arrives with its source beside it.

    1500 basis points is the seller schedule in the shipped release, and
    `SELLER_CONFIGURATION` is the authority `SellerRestockingFeeSchedule` fixes
    it to. Both travel, because a rate the screen cannot attribute is a rate the
    screen must not show.
    """
    evaluation = project_policy_evaluation(
        _evaluated(
            policy_restocking_fee_rate_basis_points=1500,
            policy_restocking_fee_rate_source="SELLER_CONFIGURATION",
        )
    )

    assert evaluation is not None
    assert evaluation.rateBasisPoints == 1500
    assert evaluation.rateSource is FeeAmountSource.SELLER_CONFIGURATION


def test_the_rate_is_read_from_its_string_form_too() -> None:
    """`append_case_fact` stores what the writer passed and coerces nothing."""
    evaluation = project_policy_evaluation(
        _evaluated(
            policy_restocking_fee_rate_basis_points="1500",
            policy_restocking_fee_rate_source="SELLER_CONFIGURATION",
        )
    )

    assert evaluation is not None
    assert evaluation.rateBasisPoints == 1500


def test_the_rate_travels_beside_the_applicability_facts_it_belongs_with() -> None:
    """The positive assertion that replaced the pinned absence.

    `ReturnCaseWorkflowActivities._record_policy_outcome` appends all four facts
    from one `FeeDetermination`: applicability, waiver, rate and rate source. An
    evaluated case therefore projects the rate *and* keeps applicability where it
    already lived. This test used to assert the last two were `None`, and it was
    written to be deleted by the change that added their producer -- which is
    this one. `tests/policy/test_restocking_rate_reaches_the_case.py` proves the
    writer; this proves the reader agrees with it.
    """
    evaluation = project_policy_evaluation(
        _evaluated(
            policy_restocking_fee_applies=True,
            policy_restocking_fee_waived=False,
            policy_restocking_fee_rate_basis_points=1500,
            policy_restocking_fee_rate_source="SELLER_CONFIGURATION",
        )
    )

    assert evaluation is not None
    assert evaluation.rateBasisPoints == 1500
    assert evaluation.rateSource is FeeAmountSource.SELLER_CONFIGURATION
    # Applicability is not repeated as a field: it already travels in
    # `conditions` -- one home for the fact, not a second field beside it.
    assert evaluation.conditions is not None
    assert "RESTOCKING_FEE_APPLIES" in [condition.value for condition in evaluation.conditions]


def test_a_case_evaluated_under_a_schedule_less_policy_projects_no_rate() -> None:
    """The original behaviour, restored exactly by removing the schedule.

    A release with no `restocking_fee.seller_schedule` produces a
    `FeeDetermination` with no rate, the recorder appends neither fact, and the
    projection reports `None` for both -- while applicability still travels in
    `conditions`. Asserted here on the reader; the writer half is
    `test_restocking_rate_reaches_the_case.py`.
    """
    evaluation = project_policy_evaluation(
        _evaluated(
            policy_restocking_fee_applies=True,
            policy_restocking_fee_waived=False,
        )
    )

    assert evaluation is not None
    assert evaluation.rateBasisPoints is None
    assert evaluation.rateSource is None
    assert evaluation.conditions is not None
    assert "RESTOCKING_FEE_APPLIES" in [condition.value for condition in evaluation.conditions]


def test_a_rate_with_no_named_authority_is_dropped_rather_than_shown() -> None:
    """A percentage nobody can attribute is indistinguishable from an invented one.

    Dropped, not raised on: the rate is one annotation on an evaluation, and
    failing the whole case read over a half-written pair would take the
    decision and the return down with it.
    """
    evaluation = project_policy_evaluation(_evaluated(policy_restocking_fee_rate_basis_points=1500))

    assert evaluation is not None
    assert evaluation.rateBasisPoints is None
    assert evaluation.rateSource is None


def test_an_unreadable_authority_drops_the_rate_with_it() -> None:
    """`FeeAmountSource` has no `POLICY_DEFAULT`, and a fact claiming one is not read."""
    evaluation = project_policy_evaluation(
        _evaluated(
            policy_restocking_fee_rate_basis_points=1500,
            policy_restocking_fee_rate_source="POLICY_DEFAULT",
        )
    )

    assert evaluation is not None
    assert evaluation.rateBasisPoints is None


@pytest.mark.parametrize("rate", [-1, 10_001, 150_000, "fifteen percent", True])
def test_an_out_of_range_or_unreadable_rate_is_not_believed(rate: Any) -> None:
    """`150000` is a corrupt fact, not a 1500% fee.

    Declined by the assembler rather than passed to the model, which would fail
    the entire case read on a value the projection can simply not believe.
    """
    evaluation = project_policy_evaluation(
        _evaluated(
            policy_restocking_fee_rate_basis_points=rate,
            policy_restocking_fee_rate_source="SELLER_CONFIGURATION",
        )
    )

    assert evaluation is not None
    assert evaluation.rateBasisPoints is None


def test_the_contract_refuses_a_rate_with_no_source() -> None:
    """Mirrors `FeeDetermination`'s own invariant, so the two cannot disagree."""
    with pytest.raises(ValidationError, match="authority that set it"):
        PolicyEvaluationProjection(
            route=PolicyRoute.STANDARD_RETURN,
            originalDecision=EligibilityDecision.APPROVE,
            effectiveDecision=EligibilityDecision.APPROVE,
            rateBasisPoints=1500,
        )


def test_the_projection_carries_no_currency_figure_for_the_fee() -> None:
    """The standing guard.

    The evaluator holds no line prices and neither does this contract. A
    `restockingFeeAmount` here would be filled by whoever needed a number, from
    whatever was nearest -- which is exactly how the audit's `18.75` appeared on
    a pane with no producer behind it. `SelectedItemProjection` and
    `ApprovedItemProjection` carry no unit price for the same reason, so there
    is no refund base in this contract to multiply either.
    """
    fields = PolicyEvaluationProjection.model_fields
    assert not [name for name in fields if "amount" in name.lower()]
    assert not [name for name in fields if "currenc" in name.lower()]

    from return_platform.operations.case_projection.contract import (
        ApprovedItemProjection,
        SelectedItemProjection,
    )

    for model in (SelectedItemProjection, ApprovedItemProjection):
        assert not [
            name
            for name in model.model_fields
            if "price" in name.lower() or "amount" in name.lower()
        ], f"{model.__name__} acquired a price, which makes a fee amount computable here"


# ---------------------------------------------------------------------------
# Through the whole assembler
# ---------------------------------------------------------------------------


def test_a_case_with_no_policy_evaluation_projects_null_not_an_empty_block() -> None:
    """`null` and `{}` are opposite answers, and the fee fields do not change that.

    Adding two nullable fields to `PolicyEvaluationProjection` must not turn "no
    evaluation" into a block full of nulls. The keying is unchanged: no
    `policy_route` fact, no block.
    """
    state = assemble_case_projection_state(
        CaseAggregateDocuments(
            case={
                "caseId": "case-1",
                "tenantId": "tenant-a",
                "principalId": "associate-1",
                "status": "GATHERING_INFO",
                "version": 1,
                "createdAt": NOW,
                "updatedAt": NOW,
            },
            facts=_facts(policy_restocking_fee_rate_basis_points=1500),
            return_records=(),
            return_items=(),
            support_work_item=None,
        )
    )

    assert state.policyEvaluation is None


def test_the_rate_survives_the_whole_assembler() -> None:
    """End to end from documents, so the wiring is proved and not only the helper."""
    state = assemble_case_projection_state(
        CaseAggregateDocuments(
            case={
                "caseId": "case-1",
                "tenantId": "tenant-a",
                "principalId": "associate-1",
                "status": "AWAITING_SUPPORT",
                "version": 1,
                "createdAt": NOW,
                "updatedAt": NOW,
            },
            facts=_evaluated(
                policy_restocking_fee_rate_basis_points=1500,
                policy_restocking_fee_rate_source="SELLER_CONFIGURATION",
            ),
            return_records=(_record(carrier="UPS"),),
            return_items=(),
            support_work_item=None,
        )
    )

    assert state.policyEvaluation is not None
    assert state.policyEvaluation.rateBasisPoints == 1500
    assert state.policyEvaluation.rateSource is FeeAmountSource.SELLER_CONFIGURATION

    # And the package beside it carries the carrier the record holds, while
    # staying honest about the two things nobody produces.
    (record,) = state.records()
    assert record.shipments is not None
    (shipment,) = record.shipments
    assert shipment.carrier == "UPS"
    assert (shipment.serviceLevel, shipment.estimatedDeliveryAt) == (None, None)
