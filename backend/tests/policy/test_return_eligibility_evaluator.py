"""The deterministic return-eligibility evaluator, against its own baseline.

Baseline Examples A-G are the acceptance suite and they are the first seven
tests here. Everything after them is a boundary the baseline names in sections
12, 18 and 19 -- day 30, day 31, 23:50 local, both DST shifts, the
two-business-day claim boundary, every prohibited state unknown one at a time,
an unknown damage cause, an unknown special-order status, a fee that applies
with no amount, and the `{WARRANTY, APPROVE}` outcome that must not be
constructable.

The policy under test is parsed from the YAML block below rather than built in
Python. That is deliberate: the block is the one an operator pastes into the
return configuration, so a release that would not load is a test failure here
rather than a startup failure in a container.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml
from pydantic import ValidationError

from return_platform.operations.business_calendar import BusinessCalendar, WorkingPeriod
from return_platform.policy import (
    APPROVAL_FORBIDDING_REASON_CODES,
    DamageCause,
    DeliveryClaimConfiguration,
    DeliveryClaimWindow,
    EligibilityDecision,
    EvidenceState,
    FeeAmountSource,
    FeeDeclaration,
    FeeDetermination,
    ManufacturerAcceptance,
    MonetaryAmount,
    OutsideStandardWindowConfiguration,
    PolicyClock,
    PolicyCondition,
    PolicyEvaluationInput,
    PolicyFactEnvelope,
    PolicyOutcome,
    PolicyProvenance,
    PolicyReasonCode,
    PolicyReleaseError,
    PolicyRoute,
    PolicyRule,
    ReportingWindowState,
    RestockingFeeConfiguration,
    ReturnEligibilityPolicy,
    ReturnReason,
    SpecialOrderDecisions,
    StandardStockReturnConfiguration,
    TriState,
    WarrantyIssueConfiguration,
    admitted_value,
    evaluate_return_eligibility,
    reporting_window_deadline,
)

# ---------------------------------------------------------------------------
# The release under test
# ---------------------------------------------------------------------------

#: Baseline section 9, as the operator writes it.
#:
#: Four keys of the baseline's illustrative YAML are deliberately absent and
#: would be rejected by `extra="forbid"` if written:
#: `outside_standard_window.condition: PURCHASE_AGE_DAYS > 30` restates
#: `purchase_window.days` in a second dialect, and `action` /
#: `standard_return_decision` on the two routed paths restate what
#: `PolicyOutcome`'s route/decision invariant already makes unconstructable.
#: A rule expressed twice is a rule that can disagree with itself.
BASELINE_POLICY_YAML = """
id: ferguson-standard-return-policy
version: "2026-08-15"
authority: FERGUSON_PUBLIC_TERMS
source_document: Ferguson Terms and Conditions of Sale
source_revision: "Terms and Conditions of Sale - Rev. May 2025"

precedence:
  - CUSTOMER_CONTRACT_OVERRIDE
  - SPECIAL_ORDER_MANUFACTURER_POLICY
  - FERGUSON_STANDARD_RETURN

standard_stock_return:
  purchase_window:
    days: 30
    basis: PURCHASE_DATE
  requirements:
    seller_stocked: true
    special_order: false
    condition:
      new: true
      suitable_for_resale: true
      original_packaging: true
      packaging_undamaged: true
      all_original_parts: true
    prohibited_states:
      used: false
      installed: false
      modified: false
      rebuilt: false
      reconditioned: false
      repaired: false
      altered: false
      damaged: false
  decision_when_satisfied: APPROVE
  conditions:
    - RESTOCKING_FEE_APPLIES

restocking_fee:
  applies_by_default: true
  percentage: null
  amount: null
  amount_source:
    - SELLER_CONFIGURATION
    - SELLER_OVERRIDE
    - MANUFACTURER
  seller_can_waive: true
  invent_default_amount: false

special_or_nonstock:
  manufacturer_acceptance_required: true
  buyer_fee_acceptance_required: true
  decisions:
    manufacturer_acceptance_unknown: REVIEW_REQUIRED
    manufacturer_acceptance_rejected: REJECT
    manufacturer_acceptance_accepted_buyer_fee_unknown: REVIEW_REQUIRED
    manufacturer_acceptance_accepted_buyer_fee_rejected: REJECT
    manufacturer_acceptance_accepted_buyer_fee_accepted: APPROVE

outside_standard_window:
  decision: REVIEW_REQUIRED
  reason_code: OUTSIDE_STANDARD_RETURN_WINDOW

delivery_claim:
  conditions:
    - SHIPPING_DAMAGE
    - SHORTAGE
    - SHIPMENT_ERROR
    - IMPROPER_DELIVERY
  reporting_window:
    business_days: 2
    basis: DELIVERY_DATE

warranty_issue:
  reasons:
    - MANUFACTURING_DEFECT
    - PRODUCT_FAILURE_AFTER_INSTALLATION
    - COVERED_PRIVATE_LABEL_DEFECT
    - MANUFACTURER_WARRANTY_ISSUE
"""


def _load_policy(source: str = BASELINE_POLICY_YAML) -> ReturnEligibilityPolicy:
    parsed: Any = yaml.safe_load(source)
    return ReturnEligibilityPolicy.model_validate(parsed)


POLICY = _load_policy()

NEW_YORK = ZoneInfo("America/New_York")

#: 09:00-17:00, Monday to Friday, declared rather than assumed.
NINE_TO_FIVE = tuple(
    WorkingPeriod(weekday=day, start_minute=9 * 60, end_minute=17 * 60) for day in range(5)
)


def _calendar(holidays: frozenset[date] = frozenset()) -> BusinessCalendar:
    return BusinessCalendar(
        calendar_id="test",
        timezone="America/New_York",
        working_periods=NINE_TO_FIVE,
        holidays=holidays,
    )


def _local(
    year: int, month: int, day: int, hour: int = 12, minute: int = 0, second: int = 0
) -> datetime:
    """A wall-clock instant in the customer's business zone."""
    return datetime(year, month, day, hour, minute, second, tzinfo=NEW_YORK)


def _clock(
    evaluated_at: datetime | None = None,
    calendar: BusinessCalendar | None = None,
) -> PolicyClock:
    return PolicyClock(
        evaluated_at=evaluated_at or _local(2026, 8, 15, 9, 0),
        local_zone=NEW_YORK,
        business_calendar=calendar if calendar is not None else _calendar(),
    )


#: A stocked item nobody has touched. Every fact known, none of them defaulted.
PRISTINE: dict[str, TriState] = {
    "seller_stocked": TriState.TRUE,
    "special_order": TriState.FALSE,
    "non_stock": TriState.FALSE,
    "condition_new": TriState.TRUE,
    "suitable_for_resale": TriState.TRUE,
    "original_packaging": TriState.TRUE,
    "packaging_undamaged": TriState.TRUE,
    "all_original_parts": TriState.TRUE,
    "used": TriState.FALSE,
    "installed": TriState.FALSE,
    "modified": TriState.FALSE,
    "rebuilt": TriState.FALSE,
    "reconditioned": TriState.FALSE,
    "repaired": TriState.FALSE,
    "altered": TriState.FALSE,
    "damaged": TriState.FALSE,
}

PROHIBITED_STATE_FIELDS = (
    "used",
    "installed",
    "modified",
    "rebuilt",
    "reconditioned",
    "repaired",
    "altered",
    "damaged",
)

RESALE_CONDITION_FIELDS = (
    "condition_new",
    "suitable_for_resale",
    "original_packaging",
    "packaging_undamaged",
    "all_original_parts",
)


def _facts(**overrides: Any) -> PolicyEvaluationInput:
    """A pristine stocked return, with the facts under test overridden."""
    payload: dict[str, Any] = {
        "request_date": _local(2026, 7, 31, 9, 0),
        "purchase_date": _local(2026, 7, 1, 9, 0),
        "damage_cause": DamageCause.NOT_APPLICABLE,
        "return_reason": ReturnReason.CHANGED_MIND,
        **PRISTINE,
    }
    payload.update(overrides)
    return PolicyEvaluationInput(**payload)


def _evaluate(**overrides: Any) -> PolicyOutcome:
    return evaluate_return_eligibility(POLICY, _facts(**overrides), _clock())


# ---------------------------------------------------------------------------
# Baseline Examples A-G -- the acceptance suite
# ---------------------------------------------------------------------------


def test_example_a_stocked_item_inside_the_window_approves_with_a_fee_condition() -> None:
    """Stock item, purchased 18 days ago, new, undamaged packaging, never touched."""
    outcome = _evaluate(
        purchase_date=_local(2026, 7, 13, 9, 0),
        request_date=_local(2026, 7, 31, 9, 0),
    )

    assert outcome.route is PolicyRoute.STANDARD_RETURN
    assert outcome.decision is EligibilityDecision.APPROVE
    assert outcome.conditions == (PolicyCondition.RESTOCKING_FEE_APPLIES,)
    assert PolicyRule.WITHIN_30_DAYS in outcome.applied_rules
    assert outcome.restocking_fee is not None
    assert outcome.restocking_fee.applies is True
    # "fee amount supplied separately" -- the engine determines applicability
    # and refuses to name a figure, because Ferguson publishes none.
    assert outcome.restocking_fee.amount is None
    assert outcome.restocking_fee.permitted_amount_sources == (
        FeeAmountSource.SELLER_CONFIGURATION,
        FeeAmountSource.SELLER_OVERRIDE,
        FeeAmountSource.MANUFACTURER,
    )


def test_example_b_outside_the_standard_window_is_reviewed_never_auto_rejected() -> None:
    """Stock item, purchased 42 days ago, otherwise perfect condition."""
    outcome = _evaluate(
        purchase_date=_local(2026, 6, 19, 9, 0),
        request_date=_local(2026, 7, 31, 9, 0),
    )

    # Neither an automatic approve nor an automatic reject: Ferguson's public
    # standard establishes the window, not an inability to authorise exceptions.
    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.OUTSIDE_STANDARD_RETURN_WINDOW,)


def test_example_c_special_order_with_unknown_manufacturer_acceptance_is_reviewed() -> None:
    """Special-order valve, manufacturer acceptance unknown."""
    outcome = _evaluate(
        special_order=TriState.TRUE,
        seller_stocked=TriState.FALSE,
        manufacturer_return_acceptance=ManufacturerAcceptance.UNKNOWN,
    )

    assert outcome.route is PolicyRoute.STANDARD_RETURN
    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.MANUFACTURER_ACCEPTANCE_REQUIRED,)


def test_example_d_special_order_accepted_with_a_fee_the_buyer_accepts_approves() -> None:
    """Special-order valve, manufacturer accepts, restocking fee $75, customer accepts."""
    outcome = _evaluate(
        special_order=TriState.TRUE,
        seller_stocked=TriState.FALSE,
        manufacturer_return_acceptance=ManufacturerAcceptance.ACCEPTED,
        manufacturer_restocking_fee=FeeDeclaration(
            applicability=TriState.TRUE,
            amount=MonetaryAmount(amount=Decimal("75.00"), currency="USD"),
            source=FeeAmountSource.MANUFACTURER,
        ),
        manufacturer_cancellation_fee=FeeDeclaration(applicability=TriState.FALSE),
        buyer_accepts_manufacturer_fees=TriState.TRUE,
    )

    assert outcome.decision is EligibilityDecision.APPROVE
    assert PolicyCondition.MANUFACTURER_FEE_ACCEPTED in outcome.conditions
    assert outcome.restocking_fee is not None
    assert outcome.restocking_fee.amount == MonetaryAmount(amount=Decimal("75.00"), currency="USD")
    assert outcome.restocking_fee.amount_source is FeeAmountSource.MANUFACTURER
    # The amount is Decimal, never a binary float, all the way through.
    assert isinstance(outcome.restocking_fee.amount.amount, Decimal)


def test_example_e_an_installed_item_and_a_changed_mind_is_rejected() -> None:
    """Product installed, customer changed mind, no shipping damage, no defect."""
    outcome = _evaluate(installed=TriState.TRUE)

    assert outcome.route is PolicyRoute.STANDARD_RETURN
    assert outcome.decision is EligibilityDecision.REJECT
    assert outcome.reason_codes == (PolicyReasonCode.STANDARD_RETURN_CONDITION_FAILED,)


def test_example_f_shipping_damage_routes_to_a_delivery_claim() -> None:
    """Product arrived damaged yesterday."""
    outcome = _evaluate(
        return_reason=ReturnReason.SHIPPING_DAMAGE,
        damaged=TriState.TRUE,
        damage_cause=DamageCause.SHIPPING,
        delivery_date=_local(2026, 7, 30, 10, 0),
        request_date=_local(2026, 7, 31, 9, 0),
    )

    assert outcome.route is PolicyRoute.DELIVERY_CLAIM
    # "standard return decision = NOT_APPLICABLE" -- expressed as the absence
    # of a decision, not as a fourth EligibilityDecision value.
    assert outcome.decision is None
    assert outcome.delivery_claim_window is not None
    assert outcome.delivery_claim_window.state is ReportingWindowState.WITHIN


def test_example_g_a_pump_that_failed_after_installation_routes_to_warranty() -> None:
    """Pump failed after installation. Not a failed return -- a warranty remedy."""
    outcome = _evaluate(
        return_reason=ReturnReason.PRODUCT_FAILURE_AFTER_INSTALLATION,
        installed=TriState.TRUE,
        used=TriState.TRUE,
    )

    assert outcome.route is PolicyRoute.WARRANTY
    # Installed and used, and still not rejected: the item's prohibited states
    # were never even reached. That is the whole point of baseline section 7.
    assert outcome.decision is None
    assert outcome.reason_codes == (PolicyReasonCode.WARRANTY_VERIFICATION_REQUIRED,)


# ---------------------------------------------------------------------------
# Return-window boundaries (baseline section 19)
# ---------------------------------------------------------------------------


def test_exactly_day_thirty_is_inside_the_window() -> None:
    outcome = _evaluate(
        purchase_date=_local(2026, 7, 1, 9, 0),
        request_date=_local(2026, 7, 31, 9, 0),
    )

    assert outcome.decision is EligibilityDecision.APPROVE
    assert outcome.reason_codes == (PolicyReasonCode.WITHIN_STANDARD_RETURN_WINDOW,)


def test_day_thirty_one_is_outside_the_window() -> None:
    outcome = _evaluate(
        purchase_date=_local(2026, 7, 1, 9, 0),
        request_date=_local(2026, 8, 1, 9, 0),
    )

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.OUTSIDE_STANDARD_RETURN_WINDOW,)


def test_a_return_raised_at_2350_local_on_day_thirty_is_still_day_thirty() -> None:
    """The case naive UTC subtraction gets wrong, and the reason for section 19.

    23:50 in New York on 31 July is 03:50 UTC on 1 August. An evaluator that
    counts UTC dates makes this day 31 and sends a perfectly good return to
    review; one that counts local calendar dates approves it.
    """
    purchase = _local(2026, 7, 1, 9, 0)
    request = _local(2026, 7, 31, 23, 50)

    outcome = _evaluate(purchase_date=purchase, request_date=request)

    assert outcome.decision is EligibilityDecision.APPROVE
    # The disagreement, spelled out rather than asserted about in the abstract.
    naive_utc_days = (request.astimezone(UTC).date() - purchase.astimezone(UTC).date()).days
    assert naive_utc_days == 31
    local_days = (request.astimezone(NEW_YORK).date() - purchase.astimezone(NEW_YORK).date()).days
    assert local_days == 30


def test_the_window_counts_local_days_across_the_autumn_dst_boundary() -> None:
    """Thirty local calendar days spanning the 25-hour day in November.

    Real elapsed time is 30 days *and an hour*, so an implementation comparing
    `purchase + timedelta(days=30)` against the request instant rejects this
    return for being one hour late.
    """
    purchase = _local(2026, 10, 18, 10, 0)  # EDT
    request = _local(2026, 11, 17, 10, 0)  # EST, exactly 30 local days later

    outcome = _evaluate(purchase_date=purchase, request_date=request)

    assert outcome.decision is EligibilityDecision.APPROVE
    # Real elapsed time, not wall-clock: subtracting two datetimes that share a
    # tzinfo ignores the zone entirely, which is its own small trap.
    assert request.astimezone(UTC) - purchase.astimezone(UTC) == timedelta(days=30, hours=1)


def test_day_thirty_one_across_the_autumn_dst_boundary_is_still_outside() -> None:
    outcome = _evaluate(
        purchase_date=_local(2026, 10, 18, 10, 0),
        request_date=_local(2026, 11, 18, 10, 0),
    )

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.OUTSIDE_STANDARD_RETURN_WINDOW,)


def test_the_window_counts_local_days_across_the_spring_dst_boundary() -> None:
    """The 23-hour day in March. Thirty local days, 30 days minus an hour of real time."""
    purchase = _local(2026, 2, 15, 10, 0)  # EST
    request = _local(2026, 3, 17, 10, 0)  # EDT, exactly 30 local days later

    outcome = _evaluate(purchase_date=purchase, request_date=request)

    assert outcome.decision is EligibilityDecision.APPROVE
    assert request.astimezone(UTC) - purchase.astimezone(UTC) == timedelta(days=29, hours=23)

    late = _evaluate(purchase_date=purchase, request_date=_local(2026, 3, 18, 10, 0))
    assert late.decision is EligibilityDecision.REVIEW_REQUIRED


def test_a_missing_purchase_date_is_reviewed_never_approved() -> None:
    outcome = _evaluate(purchase_date=None)

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.PURCHASE_DATE_UNKNOWN,)


# ---------------------------------------------------------------------------
# The two-business-day delivery-claim boundary
# ---------------------------------------------------------------------------


def test_two_business_days_from_a_friday_delivery_close_on_tuesday() -> None:
    """A Friday delivery against a Monday-to-Friday desk is due at close on Tuesday.

    Wall-clock arithmetic makes it Sunday, which is a deadline nobody can meet
    and nobody is watching.
    """
    delivered = _local(2026, 8, 14, 10, 0)  # Friday

    deadline = reporting_window_deadline(_calendar(), delivered, 2)

    assert deadline is not None
    assert deadline.astimezone(NEW_YORK) == _local(2026, 8, 18, 17, 0)  # Tuesday close


def test_the_claim_window_is_within_at_the_closing_instant_and_elapsed_after() -> None:
    delivered = _local(2026, 8, 14, 10, 0)
    clock = _clock(evaluated_at=_local(2026, 8, 19, 9, 0))

    def _state(request_at: datetime) -> ReportingWindowState:
        outcome = evaluate_return_eligibility(
            POLICY,
            _facts(
                return_reason=ReturnReason.SHIPPING_DAMAGE,
                damaged=TriState.TRUE,
                damage_cause=DamageCause.SHIPPING,
                delivery_date=delivered,
                request_date=request_at,
            ),
            clock,
        )
        assert outcome.delivery_claim_window is not None
        return outcome.delivery_claim_window.state

    assert _state(_local(2026, 8, 18, 16, 59)) is ReportingWindowState.WITHIN
    assert _state(_local(2026, 8, 18, 17, 0)) is ReportingWindowState.WITHIN
    assert _state(_local(2026, 8, 18, 17, 0, 1)) is ReportingWindowState.ELAPSED


def test_the_claim_window_walks_over_a_declared_holiday() -> None:
    """No hardcoded Monday-to-Friday, and no hardcoded working year either."""
    delivered = _local(2026, 8, 14, 10, 0)  # Friday
    calendar = _calendar(holidays=frozenset({date(2026, 8, 17)}))  # Monday shut

    deadline = reporting_window_deadline(calendar, delivered, 2)

    assert deadline is not None
    assert deadline.astimezone(NEW_YORK) == _local(2026, 8, 19, 17, 0)  # Wednesday close


def test_an_elapsed_claim_window_still_routes_to_the_claim_queue() -> None:
    """The window annotates the hand-off; it never rejects the claim here.

    Support verifies a late claim, and a late claim is exactly the one a human
    should see. An evaluator that rejected it would be deciding a question the
    baseline reserves for Support.
    """
    outcome = evaluate_return_eligibility(
        POLICY,
        _facts(
            return_reason=ReturnReason.SHORTAGE,
            delivery_date=_local(2026, 8, 14, 10, 0),
            request_date=_local(2026, 8, 28, 10, 0),
        ),
        _clock(evaluated_at=_local(2026, 8, 28, 10, 0)),
    )

    assert outcome.route is PolicyRoute.DELIVERY_CLAIM
    assert outcome.decision is None
    assert outcome.delivery_claim_window is not None
    assert outcome.delivery_claim_window.state is ReportingWindowState.ELAPSED


def test_a_claim_with_no_delivery_date_reports_an_undetermined_window() -> None:
    outcome = _evaluate(
        return_reason=ReturnReason.IMPROPER_DELIVERY,
        delivery_date=None,
    )

    assert outcome.route is PolicyRoute.DELIVERY_CLAIM
    assert outcome.delivery_claim_window is not None
    assert outcome.delivery_claim_window.state is ReportingWindowState.UNDETERMINED
    assert outcome.delivery_claim_window.reporting_deadline is None
    assert outcome.reason_codes == (PolicyReasonCode.DELIVERY_CLAIM_REPORTING_WINDOW_UNDETERMINED,)


def test_a_claim_with_no_configured_calendar_reports_an_undetermined_window() -> None:
    """A release that forgot its calendar is visible, not silently wall-clocked."""
    clock = PolicyClock(
        evaluated_at=_local(2026, 8, 15, 9, 0),
        local_zone=NEW_YORK,
        business_calendar=None,
    )

    outcome = evaluate_return_eligibility(
        POLICY,
        _facts(
            return_reason=ReturnReason.SHIPMENT_ERROR,
            delivery_date=_local(2026, 8, 14, 10, 0),
            request_date=_local(2026, 8, 15, 9, 0),
        ),
        clock,
    )

    assert outcome.delivery_claim_window is not None
    assert outcome.delivery_claim_window.state is ReportingWindowState.UNDETERMINED


def test_a_calendar_that_never_opens_yields_no_deadline_rather_than_a_wrong_one() -> None:
    never_open = BusinessCalendar(
        calendar_id="closed",
        timezone="UTC",
        working_periods=(WorkingPeriod(weekday=0, start_minute=0, end_minute=1_440),),
        holidays=frozenset(date(2026, 1, 1) + timedelta(days=offset) for offset in range(1_500)),
    )

    assert reporting_window_deadline(never_open, _local(2026, 8, 14, 10, 0), 2) is None


# ---------------------------------------------------------------------------
# Tri-state facts: unknown is never evidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", PROHIBITED_STATE_FIELDS)
def test_every_prohibited_state_unknown_on_its_own_forces_review(field: str) -> None:
    """One unknown among fifteen known-good facts is enough to stop an approval.

    This is the test that would have caught `used: bool = False`. Under a
    defaulted boolean every one of these cases approves, and each approval
    looks exactly like a correct one.
    """
    outcome = _evaluate(**{field: TriState.UNKNOWN})

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.REQUIRED_FACT_UNKNOWN,)


@pytest.mark.parametrize("field", RESALE_CONDITION_FIELDS)
def test_every_resale_condition_unknown_on_its_own_forces_review(field: str) -> None:
    outcome = _evaluate(**{field: TriState.UNKNOWN})

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.REQUIRED_FACT_UNKNOWN,)


@pytest.mark.parametrize("field", PROHIBITED_STATE_FIELDS)
def test_every_prohibited_state_known_true_rejects(field: str) -> None:
    overrides: dict[str, Any] = {field: TriState.TRUE}
    if field == "damaged":
        overrides["damage_cause"] = DamageCause.CUSTOMER_OR_USE

    outcome = _evaluate(**overrides)

    assert outcome.decision is EligibilityDecision.REJECT
    assert outcome.reason_codes == (PolicyReasonCode.STANDARD_RETURN_CONDITION_FAILED,)


def test_an_unknown_damage_cause_is_reviewed_not_rejected() -> None:
    """`damaged = true -> REJECT` is the implementation the baseline forbids."""
    outcome = _evaluate(damaged=TriState.TRUE, damage_cause=DamageCause.UNKNOWN)

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.DAMAGE_CAUSE_UNKNOWN,)
    assert PolicyRule.DAMAGE_CAUSE_ROUTING in outcome.applied_rules


def test_customer_damage_fails_the_standard_condition() -> None:
    outcome = _evaluate(damaged=TriState.TRUE, damage_cause=DamageCause.CUSTOMER_OR_USE)

    assert outcome.decision is EligibilityDecision.REJECT


def test_a_manufacturer_defect_cause_routes_to_warranty_whatever_the_reason_says() -> None:
    outcome = _evaluate(
        return_reason=ReturnReason.OTHER,
        damaged=TriState.TRUE,
        damage_cause=DamageCause.MANUFACTURER_DEFECT,
    )

    assert outcome.route is PolicyRoute.WARRANTY
    assert outcome.decision is None


def test_a_shipping_cause_routes_to_a_claim_whatever_the_reason_says() -> None:
    outcome = _evaluate(
        return_reason=ReturnReason.OTHER,
        damaged=TriState.TRUE,
        damage_cause=DamageCause.SHIPPING,
        delivery_date=_local(2026, 7, 30, 10, 0),
    )

    assert outcome.route is PolicyRoute.DELIVERY_CLAIM


def test_an_unknown_special_order_status_is_reviewed() -> None:
    outcome = _evaluate(special_order=TriState.UNKNOWN)

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.SPECIAL_ORDER_STATUS_UNKNOWN,)


def test_an_unknown_non_stock_status_is_reviewed() -> None:
    outcome = _evaluate(non_stock=TriState.UNKNOWN)

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.SPECIAL_ORDER_STATUS_UNKNOWN,)


def test_an_unknown_stock_status_is_reviewed() -> None:
    outcome = _evaluate(seller_stocked=TriState.UNKNOWN)

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.STOCK_STATUS_UNKNOWN,)


def test_an_input_with_no_facts_at_all_reviews_and_never_approves() -> None:
    """The expected baseline behaviour while extraction is incomplete.

    Every tri-state field defaults to UNKNOWN, so a bare input carries no
    evidence whatsoever -- and produces the answer that costs nothing to be
    wrong about.
    """
    outcome = evaluate_return_eligibility(
        POLICY,
        PolicyEvaluationInput(request_date=_local(2026, 8, 15, 9, 0)),
        _clock(),
    )

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.SPECIAL_ORDER_STATUS_UNKNOWN,)


def test_no_policy_critical_fact_defaults_to_a_boolean() -> None:
    """The structural half of 3A.3, asserted on the model rather than a decision."""
    bare = PolicyEvaluationInput(request_date=_local(2026, 8, 15, 9, 0))

    for field in (*PROHIBITED_STATE_FIELDS, *RESALE_CONDITION_FIELDS):
        value = getattr(bare, field)
        assert value is TriState.UNKNOWN, field
        assert not isinstance(value, bool), field


# ---------------------------------------------------------------------------
# Evidence admission (3A.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [EvidenceState.PENDING_VALIDATION, EvidenceState.REJECTED, EvidenceState.SUPERSEDED],
)
def test_only_accepted_evidence_reaches_the_evaluator(state: EvidenceState) -> None:
    envelope = PolicyFactEnvelope(
        value=TriState.FALSE,
        provenance="CUSTOMER_STATEMENT",
        acquisition_method="AI_EXTRACTED",
        validation_state=state,
        captured_at=_local(2026, 8, 15, 9, 0),
    )

    assert admitted_value(envelope) is TriState.UNKNOWN


def test_accepted_evidence_passes_through_unchanged() -> None:
    envelope = PolicyFactEnvelope(
        value=TriState.FALSE,
        provenance="CUSTOMER_STATEMENT",
        acquisition_method="ASSOCIATE_CONFIRMED",
        validation_state=EvidenceState.ACCEPTED,
        captured_at=_local(2026, 8, 15, 9, 0),
    )

    assert admitted_value(envelope) is TriState.FALSE
    assert admitted_value(None) is TriState.UNKNOWN


# ---------------------------------------------------------------------------
# Special-order / non-stock path (baseline section 4)
# ---------------------------------------------------------------------------


def _special_order(**overrides: Any) -> PolicyOutcome:
    payload: dict[str, Any] = {
        "special_order": TriState.TRUE,
        "seller_stocked": TriState.FALSE,
        "manufacturer_return_acceptance": ManufacturerAcceptance.ACCEPTED,
    }
    payload.update(overrides)
    return _evaluate(**payload)


def test_a_manufacturer_that_refuses_the_return_rejects_it() -> None:
    outcome = _special_order(manufacturer_return_acceptance=ManufacturerAcceptance.REJECTED)

    assert outcome.decision is EligibilityDecision.REJECT
    assert outcome.reason_codes == (PolicyReasonCode.MANUFACTURER_REJECTED_RETURN,)


def test_an_accepted_return_with_an_unknown_fee_is_reviewed() -> None:
    outcome = _special_order(
        manufacturer_restocking_fee=FeeDeclaration(applicability=TriState.UNKNOWN),
        manufacturer_cancellation_fee=FeeDeclaration(applicability=TriState.FALSE),
    )

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.MANUFACTURER_FEE_UNKNOWN,)


def test_a_fee_that_applies_with_no_amount_is_an_unknown_amount() -> None:
    """ "Applicability *or* amount unknown" -- both halves send the case to review."""
    outcome = _special_order(
        manufacturer_restocking_fee=FeeDeclaration(applicability=TriState.TRUE),
        manufacturer_cancellation_fee=FeeDeclaration(applicability=TriState.FALSE),
    )

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.MANUFACTURER_FEE_UNKNOWN,)


def test_an_unknown_buyer_fee_position_is_reviewed() -> None:
    outcome = _special_order(
        manufacturer_restocking_fee=FeeDeclaration(
            applicability=TriState.TRUE,
            amount=MonetaryAmount(amount=Decimal("75.00"), currency="USD"),
            source=FeeAmountSource.MANUFACTURER,
        ),
        manufacturer_cancellation_fee=FeeDeclaration(applicability=TriState.FALSE),
        buyer_accepts_manufacturer_fees=TriState.UNKNOWN,
    )

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.BUYER_FEE_ACCEPTANCE_UNKNOWN,)


def test_a_buyer_who_refuses_the_manufacturer_fee_is_rejected() -> None:
    outcome = _special_order(
        manufacturer_restocking_fee=FeeDeclaration(
            applicability=TriState.TRUE,
            amount=MonetaryAmount(amount=Decimal("75.00"), currency="USD"),
            source=FeeAmountSource.MANUFACTURER,
        ),
        manufacturer_cancellation_fee=FeeDeclaration(applicability=TriState.FALSE),
        buyer_accepts_manufacturer_fees=TriState.FALSE,
    )

    assert outcome.decision is EligibilityDecision.REJECT
    assert outcome.reason_codes == (PolicyReasonCode.BUYER_REJECTED_MANUFACTURER_FEE,)


def test_an_accepted_return_with_no_manufacturer_fee_approves() -> None:
    outcome = _special_order(
        manufacturer_restocking_fee=FeeDeclaration(applicability=TriState.FALSE),
        manufacturer_cancellation_fee=FeeDeclaration(applicability=TriState.FALSE),
    )

    assert outcome.decision is EligibilityDecision.APPROVE
    assert outcome.conditions == (PolicyCondition.MANUFACTURER_FEE_NOT_APPLICABLE,)
    assert outcome.restocking_fee is not None
    assert outcome.restocking_fee.applies is False
    assert outcome.restocking_fee.amount is None


def test_an_item_the_seller_does_not_stock_takes_the_manufacturer_path() -> None:
    outcome = _evaluate(
        seller_stocked=TriState.FALSE,
        special_order=TriState.FALSE,
        non_stock=TriState.FALSE,
        manufacturer_return_acceptance=ManufacturerAcceptance.UNKNOWN,
    )

    assert PolicyRule.SPECIAL_ORDER_MANUFACTURER_POLICY in outcome.applied_rules
    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED


# ---------------------------------------------------------------------------
# Restocking fee: applicability without an amount (baseline section 3)
# ---------------------------------------------------------------------------


def test_the_engine_determines_applicability_and_never_an_amount() -> None:
    outcome = _evaluate()

    assert outcome.restocking_fee is not None
    assert outcome.restocking_fee.applies is True
    assert outcome.restocking_fee.amount is None
    assert outcome.restocking_fee.amount_source is None
    assert PolicyCondition.RESTOCKING_FEE_APPLIES in outcome.conditions


def test_a_seller_supplied_amount_is_echoed_with_the_authority_that_set_it() -> None:
    outcome = _evaluate(
        seller_restocking_fee=FeeDeclaration(
            applicability=TriState.TRUE,
            amount=MonetaryAmount(amount=Decimal("22.50"), currency="USD"),
            source=FeeAmountSource.SELLER_CONFIGURATION,
        )
    )

    assert outcome.restocking_fee is not None
    assert outcome.restocking_fee.amount is not None
    assert outcome.restocking_fee.amount.amount == Decimal("22.50")
    assert outcome.restocking_fee.amount_source is FeeAmountSource.SELLER_CONFIGURATION


def test_a_waived_fee_does_not_apply_and_records_the_waiver() -> None:
    outcome = _evaluate(seller_fee_waiver=TriState.TRUE)

    assert outcome.decision is EligibilityDecision.APPROVE
    assert outcome.restocking_fee is not None
    assert outcome.restocking_fee.applies is False
    assert outcome.restocking_fee.waived is True
    assert outcome.conditions == (PolicyCondition.RESTOCKING_FEE_WAIVED,)
    assert len(outcome.exceptions) == 1


def test_an_unknown_waiver_leaves_the_configured_default_standing() -> None:
    outcome = _evaluate(seller_fee_waiver=TriState.UNKNOWN)

    assert outcome.restocking_fee is not None
    assert outcome.restocking_fee.applies is True
    assert outcome.restocking_fee.waived is False


# ---------------------------------------------------------------------------
# Money is never a binary float (baseline section 18)
# ---------------------------------------------------------------------------


def test_a_monetary_amount_refuses_a_binary_float() -> None:
    with pytest.raises(ValidationError, match="never a float"):
        MonetaryAmount(amount=75.0, currency="USD")


@pytest.mark.parametrize("value", [Decimal("75.00"), 75, "75.00"])
def test_a_monetary_amount_accepts_decimal_int_and_decimal_text(value: object) -> None:
    amount = MonetaryAmount(amount=value, currency="usd")

    assert amount.amount == Decimal("75")
    assert amount.currency == "USD"


def test_a_fee_amount_without_an_authority_is_unconstructable() -> None:
    with pytest.raises(ValidationError, match="authority"):
        FeeDeclaration(
            applicability=TriState.TRUE,
            amount=MonetaryAmount(amount=Decimal("75.00"), currency="USD"),
        )


def test_a_fee_that_does_not_apply_cannot_carry_an_amount() -> None:
    with pytest.raises(ValidationError):
        FeeDeclaration(
            applicability=TriState.FALSE,
            amount=MonetaryAmount(amount=Decimal("75.00"), currency="USD"),
            source=FeeAmountSource.MANUFACTURER,
        )


# ---------------------------------------------------------------------------
# PolicyOutcome structural invariants (3A.5)
# ---------------------------------------------------------------------------


def _provenance() -> PolicyProvenance:
    return PolicyProvenance(
        policy_id="ferguson-standard-return-policy",
        policy_version="2026-08-15",
        authority="FERGUSON_PUBLIC_TERMS",
        source_document="Ferguson Terms and Conditions of Sale",
        source_revision="Rev. May 2025",
        evaluated_at=_local(2026, 8, 15, 9, 0),
    )


def test_a_warranty_route_carrying_an_approval_is_unconstructable() -> None:
    """`{"route": "WARRANTY", "decision": "APPROVE"}` -- the invariant, literally."""
    with pytest.raises(ValidationError, match="Support verifies it"):
        PolicyOutcome(
            route=PolicyRoute.WARRANTY,
            decision=EligibilityDecision.APPROVE,
            applied_rules=(PolicyRule.WARRANTY_ROUTING,),
            provenance=_provenance(),
        )


@pytest.mark.parametrize(
    "decision",
    [
        EligibilityDecision.APPROVE,
        EligibilityDecision.REJECT,
        EligibilityDecision.REVIEW_REQUIRED,
    ],
)
def test_no_decision_of_any_value_may_sit_on_a_routed_path(
    decision: EligibilityDecision,
) -> None:
    for route in (PolicyRoute.WARRANTY, PolicyRoute.DELIVERY_CLAIM):
        with pytest.raises(ValidationError):
            PolicyOutcome(
                route=route,
                decision=decision,
                applied_rules=(PolicyRule.WARRANTY_ROUTING,),
                provenance=_provenance(),
            )


def test_a_standard_return_without_a_decision_is_unconstructable() -> None:
    with pytest.raises(ValidationError, match="must carry one of"):
        PolicyOutcome(
            route=PolicyRoute.STANDARD_RETURN,
            decision=None,
            applied_rules=(PolicyRule.STANDARD_STOCK_ITEM,),
            provenance=_provenance(),
        )


@pytest.mark.parametrize("reason", sorted(APPROVAL_FORBIDDING_REASON_CODES))
def test_an_approval_cannot_rest_on_an_unknown_or_refused_fact(reason: PolicyReasonCode) -> None:
    """The structural half of "never infer APPROVE".

    The evaluator's branches already avoid this. The constructor refusing it as
    well is what keeps a future reordering from quietly shipping an approval.
    """
    with pytest.raises(ValidationError, match="cannot rest on"):
        PolicyOutcome(
            route=PolicyRoute.STANDARD_RETURN,
            decision=EligibilityDecision.APPROVE,
            reason_codes=(reason,),
            applied_rules=(PolicyRule.STANDARD_STOCK_ITEM,),
            provenance=_provenance(),
        )


def test_only_a_delivery_claim_may_carry_a_reporting_window() -> None:
    window = DeliveryClaimWindow(
        business_days=2,
        delivery_date=_local(2026, 8, 14, 10, 0),
        reporting_deadline=_local(2026, 8, 18, 17, 0),
        state=ReportingWindowState.WITHIN,
    )

    with pytest.raises(ValidationError, match="only a delivery claim"):
        PolicyOutcome(
            route=PolicyRoute.WARRANTY,
            applied_rules=(PolicyRule.WARRANTY_ROUTING,),
            delivery_claim_window=window,
            provenance=_provenance(),
        )


def test_an_undetermined_window_cannot_carry_a_deadline() -> None:
    with pytest.raises(ValidationError, match="undetermined reporting window"):
        DeliveryClaimWindow(
            business_days=2,
            delivery_date=_local(2026, 8, 14, 10, 0),
            reporting_deadline=_local(2026, 8, 18, 17, 0),
            state=ReportingWindowState.UNDETERMINED,
        )


def test_an_outcome_fee_amount_must_name_its_authority() -> None:
    with pytest.raises(ValidationError, match="must name the authority"):
        FeeDetermination(
            applies=True,
            amount=MonetaryAmount(amount=Decimal("10.00"), currency="USD"),
        )


def test_every_outcome_records_the_release_that_produced_it() -> None:
    outcome = _evaluate()

    assert outcome.provenance.source == "FERGUSON_POLICY_ENGINE"
    assert outcome.provenance.policy_id == "ferguson-standard-return-policy"
    assert outcome.provenance.policy_version == "2026-08-15"
    assert outcome.provenance.source_revision.startswith("Terms and Conditions of Sale")
    assert PolicyRule.POLICY_RELEASE_VALIDATED in outcome.applied_rules


# ---------------------------------------------------------------------------
# Contract override (baseline section 17)
# ---------------------------------------------------------------------------


def test_a_contract_override_reference_is_reviewed_and_recorded_never_guessed() -> None:
    """The evaluator cannot read a negotiated agreement, so it does not pretend to."""
    outcome = _evaluate(contract_override_reference="CONTRACT-4471")

    assert outcome.decision is EligibilityDecision.REVIEW_REQUIRED
    assert outcome.reason_codes == (PolicyReasonCode.CUSTOMER_CONTRACT_OVERRIDE_REQUIRES_REVIEW,)
    assert outcome.exceptions[0].reference == "CONTRACT-4471"
    assert PolicyRule.CUSTOMER_CONTRACT_OVERRIDE in outcome.applied_rules


# ---------------------------------------------------------------------------
# Release validation (baseline section 20)
# ---------------------------------------------------------------------------


def test_the_baseline_yaml_block_loads_as_a_valid_release() -> None:
    policy = _load_policy()

    assert policy.standard_stock_return.purchase_window.days == 30
    assert policy.delivery_claim.reporting_window.business_days == 2
    assert policy.restocking_fee.percentage is None
    assert policy.restocking_fee.amount is None


def test_a_release_cannot_declare_a_restocking_percentage() -> None:
    """No published Ferguson percentage exists, so the type cannot hold one."""
    with pytest.raises(ValidationError):
        RestockingFeeConfiguration(percentage=15)


def test_a_release_cannot_declare_a_restocking_amount() -> None:
    with pytest.raises(ValidationError):
        RestockingFeeConfiguration(amount=Decimal("25.00"))


def test_a_release_cannot_switch_on_inventing_a_default_amount() -> None:
    with pytest.raises(ValidationError, match="no published Ferguson"):
        RestockingFeeConfiguration(invent_default_amount=True)


def test_a_release_cannot_approve_an_unknown_manufacturer_acceptance() -> None:
    with pytest.raises(ValidationError, match="cannot be configured"):
        SpecialOrderDecisions(manufacturer_acceptance_unknown=EligibilityDecision.APPROVE)


def test_a_release_cannot_approve_a_return_past_the_standard_window() -> None:
    with pytest.raises(ValidationError, match="cannot be configured to APPROVE"):
        OutsideStandardWindowConfiguration(decision=EligibilityDecision.APPROVE)


def test_a_release_that_can_never_approve_is_refused() -> None:
    with pytest.raises(ValidationError, match="malformed release"):
        StandardStockReturnConfiguration(
            purchase_window={"days": 30},
            decision_when_satisfied=EligibilityDecision.REJECT,
        )


def test_the_ferguson_standard_must_be_last_in_the_precedence_chain() -> None:
    broken: dict[str, Any] = yaml.safe_load(BASELINE_POLICY_YAML)
    broken["precedence"] = ["FERGUSON_STANDARD_RETURN", "CUSTOMER_CONTRACT_OVERRIDE"]

    with pytest.raises(ValidationError, match="lowest-priority authority"):
        ReturnEligibilityPolicy.model_validate(broken)


def test_a_reason_cannot_route_to_both_warranty_and_a_delivery_claim() -> None:
    broken: dict[str, Any] = yaml.safe_load(BASELINE_POLICY_YAML)
    broken["warranty_issue"]["reasons"].append("SHIPPING_DAMAGE")

    with pytest.raises(ValidationError, match="cannot route to both"):
        ReturnEligibilityPolicy.model_validate(broken)


def test_an_empty_routing_vocabulary_is_refused() -> None:
    with pytest.raises(ValidationError):
        DeliveryClaimConfiguration(conditions=())
    with pytest.raises(ValidationError):
        WarrantyIssueConfiguration(reasons=())


def test_an_unknown_reason_cannot_be_a_routing_trigger() -> None:
    with pytest.raises(ValidationError, match="unknown return reason"):
        WarrantyIssueConfiguration(reasons=(ReturnReason.UNKNOWN,))


def test_the_evaluator_refuses_anything_that_is_not_a_validated_release() -> None:
    with pytest.raises(PolicyReleaseError):
        evaluate_return_eligibility(
            object(),  # type: ignore[arg-type]
            _facts(),
            _clock(),
        )


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------


def test_the_evaluator_is_a_pure_function_of_its_three_arguments() -> None:
    facts = _facts()
    clock = _clock()

    first = evaluate_return_eligibility(POLICY, facts, clock)
    second = evaluate_return_eligibility(POLICY, facts, clock)

    assert first == second
    assert first.provenance.evaluated_at == clock.evaluated_at


def test_the_clock_refuses_a_naive_evaluation_instant() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        PolicyClock(
            evaluated_at=datetime(2026, 8, 15, 9, 0),
            local_zone=NEW_YORK,
        )


def test_a_return_cannot_be_requested_before_the_purchase_it_concerns() -> None:
    with pytest.raises(ValidationError, match="before the purchase"):
        PolicyEvaluationInput(
            request_date=_local(2026, 7, 1, 9, 0),
            purchase_date=_local(2026, 7, 31, 9, 0),
        )


def test_a_damage_cause_cannot_be_stated_for_an_undamaged_item() -> None:
    with pytest.raises(ValidationError, match="established as undamaged"):
        PolicyEvaluationInput(
            request_date=_local(2026, 7, 31, 9, 0),
            damaged=TriState.FALSE,
            damage_cause=DamageCause.SHIPPING,
        )
