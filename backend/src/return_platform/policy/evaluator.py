"""The deterministic return-eligibility evaluator (3A.4).

A pure function. No IO, no database read, no model call, no clock read. The
evaluation instant, the local zone and the business calendar all arrive as an
injected `PolicyClock`, which is what makes the whole rule set unit-testable
today -- before the case projection exists, and without a running platform.

**Precedence, exactly baseline section 12:**

```text
1 validate policy release        5 special-order / non-stock path
2 customer/contract override     6 standard stocked 30-day policy
3 delivery-claim detection       7 restocking-fee applicability
4 warranty detection             8 otherwise REVIEW_REQUIRED
```

Damage-cause routing (baseline section 8) sits between 4 and 5: shipping damage
has already routed to a claim at step 3 and a manufacturer defect to warranty at
step 4, so what remains at that point is customer-or-use damage, which fails the
standard condition, and an *unestablished* cause, which is a question for a
human. `damaged = true -> REJECT` is the implementation the baseline forbids by
name, and it is forbidden because it rejects carrier claims as ordinary returns.

**What silence is allowed to decide is configuration, not code.** A condition or
prohibited-state fact nobody stated reaches `REVIEW_REQUIRED` under
`UnstatedConditionFacts.REVIEW_REQUIRED` and is recorded as unevaluated under
`NOT_EVALUATED`, leaving the return window as the determinative question. The
rules themselves are untouched by that setting -- a *stated* failure still
rejects, and still rejects ahead of the window -- so re-enabling them is a
released configuration change rather than a deployment. Every outcome that
skipped a check carries `PolicyRule.CONDITION_FACTS_NOT_EVALUATED` and the names
of the checks it skipped, for the same reason `STOCK_CLASS_FROM_CONFIGURATION`
exists: an approval taken on an assumption must never read like one taken on
evidence.

**Never infer APPROVE.** No rule matched, a missing required fact, an ambiguous
damage cause, unknown manufacturer acceptance and unknown special-order status
all reach `REVIEW_REQUIRED`. Two mechanisms rather than one: the branches below
are written that way, and `PolicyOutcome` refuses to construct an approval
carrying any of those reason codes, so a later reordering fails a constructor
instead of shipping an approval.

**Dates are calendar dates in the customer's business zone**, never a
`timedelta` over instants. Thirty days is thirty local calendar days: a return
raised at 23:50 local on day 30 is inside the window even though it is day 31 in
UTC, and the two days a year a zone shifts are 23 and 25 hours long without this
module knowing it. The delivery-claim window is business days walked against the
declared `BusinessCalendar`, for the same reason `business_calendar.py` exists:
a Friday delivery reported on Tuesday morning is inside a two-business-day
window and wall-clock arithmetic says it is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from return_platform.operations.business_calendar import (
    MAX_HORIZON_DAYS,
    BusinessCalendar,
    WorkingPeriod,
)
from return_platform.policy.eligibility_policy import ReturnEligibilityPolicy
from return_platform.policy.evaluation_input import FeeDeclaration, PolicyEvaluationInput
from return_platform.policy.outcome import (
    DeliveryClaimWindow,
    FeeDetermination,
    PolicyException,
    PolicyOutcome,
    PolicyProvenance,
)
from return_platform.policy.vocabulary import (
    DamageCause,
    EligibilityDecision,
    ManufacturerAcceptance,
    OverrideAuthority,
    PolicyCondition,
    PolicyExceptionCode,
    PolicyReasonCode,
    PolicyRoute,
    PolicyRule,
    ReportingWindowState,
    ReturnWindowBasis,
    StockClassificationDefault,
    TriState,
    UnstatedConditionFacts,
)

__all__ = [
    "PolicyClock",
    "PolicyReleaseError",
    "evaluate_return_eligibility",
    "reporting_window_deadline",
]


class PolicyReleaseError(RuntimeError):
    """The evaluator was handed something that is not a validated policy release.

    Baseline section 20: an invalid release refuses activation rather than
    deploying a rule set that turns every case into `REVIEW_REQUIRED` with no
    operational failure to point at. A `ReturnEligibilityPolicy` is valid by
    construction -- pydantic ran every section 20 check -- so reaching this is
    a wiring fault, and it is raised rather than absorbed into a decision.
    """


@dataclass(frozen=True, slots=True)
class PolicyClock:
    """Everything time-dependent the evaluation needs, resolved by the caller.

    Injected rather than read so the evaluator stays pure and every boundary in
    baseline section 19 -- day 30, day 31, 23:50 local, a DST shift, the
    two-business-day claim boundary -- is a value a test supplies rather than a
    condition a test has to wait for.

    `local_zone` is already a `ZoneInfo` and `business_calendar` is already a
    `BusinessCalendar`: resolving either touches the tz database or
    configuration, and both belong on the activity side of the workflow
    boundary that `business_calendar.py` documents.
    """

    evaluated_at: datetime
    local_zone: ZoneInfo
    business_calendar: BusinessCalendar | None = None

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("the evaluation instant must be timezone-aware")


def evaluate_return_eligibility(
    policy: ReturnEligibilityPolicy,
    facts: PolicyEvaluationInput,
    clock: PolicyClock,
) -> PolicyOutcome:
    """Decide one return against one policy release. Pure, total, deterministic."""
    if not isinstance(policy, ReturnEligibilityPolicy):
        raise PolicyReleaseError(
            "the eligibility evaluator requires a validated ReturnEligibilityPolicy release"
        )
    provenance = _provenance(policy, facts, clock)
    base: tuple[PolicyRule, ...] = (PolicyRule.POLICY_RELEASE_VALIDATED,)

    # 2. An explicit customer/contract override outranks the public standard.
    #    Only its reference reaches here -- the evaluator cannot read a
    #    negotiated agreement, so the honest deterministic answer is that a
    #    human must, and the exception is recorded rather than discarded.
    if facts.contract_override_reference is not None:
        return PolicyOutcome(
            route=PolicyRoute.STANDARD_RETURN,
            decision=EligibilityDecision.REVIEW_REQUIRED,
            exceptions=(
                PolicyException(
                    code=PolicyExceptionCode.CUSTOMER_CONTRACT_OVERRIDE,
                    authority=OverrideAuthority.CUSTOMER_CONTRACT,
                    reference=facts.contract_override_reference,
                ),
            ),
            reason_codes=(PolicyReasonCode.CUSTOMER_CONTRACT_OVERRIDE_REQUIRES_REVIEW,),
            applied_rules=(*base, PolicyRule.CUSTOMER_CONTRACT_OVERRIDE),
            provenance=provenance,
        )

    # 3. Delivery claims are a separate business path, not a failed return.
    if facts.return_reason in policy.delivery_claim.conditions or (
        facts.damage_cause is DamageCause.SHIPPING
    ):
        window = _delivery_claim_window(policy, facts, clock)
        return PolicyOutcome(
            route=PolicyRoute.DELIVERY_CLAIM,
            decision=None,
            reason_codes=(_WINDOW_REASON[window.state],),
            applied_rules=(*base, PolicyRule.DELIVERY_CLAIM_ROUTING),
            delivery_claim_window=window,
            provenance=provenance,
        )

    # 4. Warranty remedies are distinct from ordinary returns, and a defect
    #    after installation must not be rejected for having been installed.
    if facts.return_reason in policy.warranty_issue.reasons or (
        facts.damage_cause is DamageCause.MANUFACTURER_DEFECT
    ):
        return PolicyOutcome(
            route=PolicyRoute.WARRANTY,
            decision=None,
            reason_codes=(PolicyReasonCode.WARRANTY_VERIFICATION_REQUIRED,),
            applied_rules=(*base, PolicyRule.WARRANTY_ROUTING),
            provenance=provenance,
        )

    # 4b. Damage whose cause nobody has established. Shipping and manufacturer
    #     causes have already routed above, so an unknown cause here is exactly
    #     the ambiguity baseline section 12 sends to review.
    if facts.damaged is TriState.TRUE and facts.damage_cause is DamageCause.UNKNOWN:
        return _review(
            provenance,
            PolicyReasonCode.DAMAGE_CAUSE_UNKNOWN,
            (*base, PolicyRule.DAMAGE_CAUSE_ROUTING, PolicyRule.FAIL_SAFE_REVIEW),
        )

    # 5. Special-order / non-stock discrimination.
    #
    # The source cannot answer this (see `StockClassificationDefault`), so the
    # operator does, in configuration. `_classify_stock` fills silence only: a
    # line the source classified is returned untouched. When it does answer, it
    # says so, and `STOCK_CLASS_FROM_CONFIGURATION` travels on the outcome --
    # an approval taken on an assumption must never read like one taken on a
    # fact.
    requirements = policy.standard_stock_return.requirements
    facts, classified_by_configuration = _classify_stock(policy, facts)
    if classified_by_configuration:
        base = (*base, PolicyRule.STOCK_CLASS_FROM_CONFIGURATION)
    if facts.special_order is TriState.UNKNOWN or facts.non_stock is TriState.UNKNOWN:
        return _review(
            provenance,
            PolicyReasonCode.SPECIAL_ORDER_STATUS_UNKNOWN,
            (*base, PolicyRule.FAIL_SAFE_REVIEW),
        )
    diverges_from_stock_path = (
        facts.special_order is TriState.TRUE
    ) != requirements.special_order or (facts.non_stock is TriState.TRUE) != (
        requirements.special_order
    )
    if not diverges_from_stock_path:
        if facts.seller_stocked is TriState.UNKNOWN:
            return _review(
                provenance,
                PolicyReasonCode.STOCK_STATUS_UNKNOWN,
                (*base, PolicyRule.FAIL_SAFE_REVIEW),
            )
        # An item the seller does not stock takes the manufacturer path even
        # when nobody labelled it special-order: the seller has no standing to
        # accept a return of something it never carried.
        diverges_from_stock_path = (
            facts.seller_stocked is TriState.TRUE
        ) != requirements.seller_stocked

    if diverges_from_stock_path:
        return _evaluate_special_order(policy, facts, provenance, base)

    # 6 and 7. Standard stocked item, then restocking-fee applicability.
    return _evaluate_standard_stock(policy, facts, clock, provenance, base)


# --------------------------------------------------------------------------
# Step 5 -- special-order / non-stock manufacturer path (baseline section 4)
# --------------------------------------------------------------------------


def _evaluate_special_order(
    policy: ReturnEligibilityPolicy,
    facts: PolicyEvaluationInput,
    provenance: PolicyProvenance,
    base: tuple[PolicyRule, ...],
) -> PolicyOutcome:
    special = policy.special_or_nonstock
    decisions = special.decisions
    rules = (*base, PolicyRule.SPECIAL_ORDER_MANUFACTURER_POLICY)
    acceptance = facts.manufacturer_return_acceptance

    if acceptance is ManufacturerAcceptance.UNKNOWN:
        return PolicyOutcome(
            route=PolicyRoute.STANDARD_RETURN,
            decision=decisions.manufacturer_acceptance_unknown,
            reason_codes=(PolicyReasonCode.MANUFACTURER_ACCEPTANCE_REQUIRED,),
            applied_rules=(*rules, PolicyRule.FAIL_SAFE_REVIEW),
            provenance=provenance,
        )

    if acceptance is ManufacturerAcceptance.REJECTED:
        return PolicyOutcome(
            route=PolicyRoute.STANDARD_RETURN,
            decision=decisions.manufacturer_acceptance_rejected,
            reason_codes=(PolicyReasonCode.MANUFACTURER_REJECTED_RETURN,),
            applied_rules=rules,
            provenance=provenance,
        )

    restocking = facts.manufacturer_restocking_fee
    cancellation = facts.manufacturer_cancellation_fee
    if not restocking.is_resolved or not cancellation.is_resolved:
        return PolicyOutcome(
            route=PolicyRoute.STANDARD_RETURN,
            decision=decisions.manufacturer_acceptance_accepted_buyer_fee_unknown,
            reason_codes=(PolicyReasonCode.MANUFACTURER_FEE_UNKNOWN,),
            applied_rules=(*rules, PolicyRule.FAIL_SAFE_REVIEW),
            provenance=provenance,
        )

    fee_required = (
        restocking.applicability is TriState.TRUE or cancellation.applicability is TriState.TRUE
    )
    if fee_required and special.buyer_fee_acceptance_required:
        if facts.buyer_accepts_manufacturer_fees is TriState.UNKNOWN:
            return PolicyOutcome(
                route=PolicyRoute.STANDARD_RETURN,
                decision=decisions.manufacturer_acceptance_accepted_buyer_fee_unknown,
                reason_codes=(PolicyReasonCode.BUYER_FEE_ACCEPTANCE_UNKNOWN,),
                applied_rules=(*rules, PolicyRule.FAIL_SAFE_REVIEW),
                provenance=provenance,
            )
        if facts.buyer_accepts_manufacturer_fees is TriState.FALSE:
            return PolicyOutcome(
                route=PolicyRoute.STANDARD_RETURN,
                decision=decisions.manufacturer_acceptance_accepted_buyer_fee_rejected,
                reason_codes=(PolicyReasonCode.BUYER_REJECTED_MANUFACTURER_FEE,),
                applied_rules=rules,
                provenance=provenance,
            )

    conditions = (
        (PolicyCondition.MANUFACTURER_FEE_ACCEPTED,)
        if fee_required
        else (PolicyCondition.MANUFACTURER_FEE_NOT_APPLICABLE,)
    )
    return PolicyOutcome(
        route=PolicyRoute.STANDARD_RETURN,
        decision=decisions.manufacturer_acceptance_accepted_buyer_fee_accepted,
        conditions=conditions,
        reason_codes=(PolicyReasonCode.MANUFACTURER_ACCEPTED_RETURN,),
        applied_rules=(*rules, PolicyRule.RESTOCKING_FEE_APPLIES) if fee_required else rules,
        restocking_fee=_manufacturer_fee(policy, restocking),
        cancellation_fee=_manufacturer_fee(policy, cancellation),
        provenance=provenance,
    )


def _manufacturer_fee(
    policy: ReturnEligibilityPolicy, declaration: FeeDeclaration
) -> FeeDetermination:
    """Echo an authoritative manufacturer fee; never originate one."""
    applies = declaration.applicability is TriState.TRUE
    return FeeDetermination(
        applies=applies,
        waived=False,
        amount=declaration.amount if applies else None,
        amount_source=declaration.source if applies and declaration.amount else None,
        permitted_amount_sources=policy.restocking_fee.amount_source,
    )


# --------------------------------------------------------------------------
# Steps 6 and 7 -- standard stocked item, then restocking-fee applicability
# --------------------------------------------------------------------------


def _evaluate_standard_stock(
    policy: ReturnEligibilityPolicy,
    facts: PolicyEvaluationInput,
    clock: PolicyClock,
    provenance: PolicyProvenance,
    base: tuple[PolicyRule, ...],
) -> PolicyOutcome:
    rule = policy.standard_stock_return
    requirements = rule.requirements
    rules = (*base, PolicyRule.STANDARD_STOCK_ITEM)

    condition_required = requirements.condition
    prohibited_required = requirements.prohibited_states
    checks: tuple[tuple[str, TriState, bool], ...] = (
        *(
            (name, value, bool(getattr(condition_required, name)))
            for name, value in facts.resale_condition_facts()
        ),
        *(
            (name, value, bool(getattr(prohibited_required, name)))
            for name, value in facts.prohibited_state_facts()
        ),
    )

    # A known-failing condition is the strongest evidence available and answers
    # the case outright. Ordered before the window check on purpose: a return
    # of an installed item is rejected because it was installed, and saying so
    # is more useful than "it is also late".
    if any(
        value is not TriState.UNKNOWN and (value is TriState.TRUE) != required
        for _, value, required in checks
    ):
        return PolicyOutcome(
            route=PolicyRoute.STANDARD_RETURN,
            decision=EligibilityDecision.REJECT,
            reason_codes=(PolicyReasonCode.STANDARD_RETURN_CONDITION_FAILED,),
            applied_rules=(*rules, PolicyRule.NEW_RESALEABLE_CONDITION),
            provenance=provenance,
        )

    # A fact nobody stated. What it is allowed to decide is the operator's, in
    # configuration, and both answers are defensible for different deployments:
    # `REVIEW_REQUIRED` queues the return until the evidence exists, and
    # `NOT_EVALUATED` lets the window decide a case the conversation cannot
    # currently answer. Nothing here weakens a fact that *was* stated -- the
    # branch above has already rejected on one.
    unevaluated = tuple(name for name, value, _ in checks if value is TriState.UNKNOWN)
    if unevaluated and rule.unstated_condition_facts is UnstatedConditionFacts.REVIEW_REQUIRED:
        return _review(
            provenance,
            PolicyReasonCode.REQUIRED_FACT_UNKNOWN,
            (*rules, PolicyRule.NEW_RESALEABLE_CONDITION, PolicyRule.FAIL_SAFE_REVIEW),
        )
    if unevaluated:
        # Recorded, not silently dropped. Every outcome reachable from here
        # carries the marker and the names, and `PolicyOutcome` refuses one
        # carrying only half of the pair. `NEW_RESALEABLE_CONDITION` is
        # deliberately *not* claimed on any of them: the rule was not applied in
        # full, and an audit reading the applied rules must not be told it was.
        rules = (*rules, PolicyRule.CONDITION_FACTS_NOT_EVALUATED)

    basis_instant = (
        facts.purchase_date
        if rule.purchase_window.basis is ReturnWindowBasis.PURCHASE_DATE
        else facts.delivery_date
    )
    if basis_instant is None:
        missing = (
            PolicyReasonCode.PURCHASE_DATE_UNKNOWN
            if rule.purchase_window.basis is ReturnWindowBasis.PURCHASE_DATE
            else PolicyReasonCode.DELIVERY_DATE_UNKNOWN
        )
        # The window is the only determinative question left and its basis is
        # absent, so the honest answer is that nobody can say. Never a guessed
        # date and never an approval.
        return PolicyOutcome(
            route=PolicyRoute.STANDARD_RETURN,
            decision=EligibilityDecision.REVIEW_REQUIRED,
            reason_codes=(missing,),
            applied_rules=(*rules, PolicyRule.FAIL_SAFE_REVIEW),
            unevaluated_checks=unevaluated,
            provenance=provenance,
        )

    elapsed_days = _elapsed_calendar_days(basis_instant, facts.request_date, clock.local_zone)
    if elapsed_days > rule.purchase_window.days:
        return PolicyOutcome(
            route=PolicyRoute.STANDARD_RETURN,
            decision=policy.outside_standard_window.decision,
            reason_codes=(policy.outside_standard_window.reason_code,),
            applied_rules=(*rules, PolicyRule.OUTSIDE_STANDARD_WINDOW),
            unevaluated_checks=unevaluated,
            provenance=provenance,
        )

    fee = _seller_restocking_fee(policy, facts)
    conditions: tuple[PolicyCondition, ...] = ()
    if fee.applies and PolicyCondition.RESTOCKING_FEE_APPLIES in rule.conditions:
        conditions = (PolicyCondition.RESTOCKING_FEE_APPLIES,)
    elif fee.waived:
        conditions = (PolicyCondition.RESTOCKING_FEE_WAIVED,)

    return PolicyOutcome(
        route=PolicyRoute.STANDARD_RETURN,
        decision=rule.decision_when_satisfied,
        conditions=conditions,
        exceptions=_fee_waiver_exceptions(policy, facts),
        reason_codes=(PolicyReasonCode.WITHIN_STANDARD_RETURN_WINDOW,),
        applied_rules=(
            *rules,
            PolicyRule.WITHIN_30_DAYS,
            *(() if unevaluated else (PolicyRule.NEW_RESALEABLE_CONDITION,)),
            PolicyRule.RESTOCKING_FEE_APPLIES,
        ),
        unevaluated_checks=unevaluated,
        restocking_fee=fee,
        provenance=provenance,
    )


def _seller_restocking_fee(
    policy: ReturnEligibilityPolicy, facts: PolicyEvaluationInput
) -> FeeDetermination:
    """Applicability from policy; the amount only if a seller authority supplied one.

    An unknown waiver leaves the configured default standing, which is that the
    fee applies. That is safe in the only direction that matters here: the
    outcome is a condition Support resolves, never a figure on a screen. The
    evaluator originates no amount under any branch.
    """
    fee_policy = policy.restocking_fee
    waived = fee_policy.seller_can_waive and facts.seller_fee_waiver is TriState.TRUE
    applies = fee_policy.applies_by_default and not waived
    declared = facts.seller_restocking_fee
    amount = declared.amount if applies and declared.applicability is TriState.TRUE else None
    # A case-specific amount always wins: Support naming a figure for this return
    # is more specific than the seller's standing rate. The schedule fills the
    # gap, it does not override an answer somebody already gave.
    schedule = fee_policy.seller_schedule if applies and amount is None else None
    return FeeDetermination(
        applies=applies,
        waived=waived,
        amount=amount,
        amount_source=declared.source if amount is not None else None,
        permitted_amount_sources=fee_policy.amount_source,
        rate_basis_points=schedule.default_rate_basis_points if schedule else None,
        rate_source=schedule.authority if schedule else None,
    )


def _fee_waiver_exceptions(
    policy: ReturnEligibilityPolicy, facts: PolicyEvaluationInput
) -> tuple[PolicyException, ...]:
    if not (policy.restocking_fee.seller_can_waive and facts.seller_fee_waiver is TriState.TRUE):
        return ()
    reference = facts.contract_override_reference or "SELLER_FEE_WAIVER"
    return (
        PolicyException(
            code=PolicyExceptionCode.SELLER_FEE_WAIVER,
            authority=OverrideAuthority.SELLER_OVERRIDE,
            reference=reference,
        ),
    )


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------


def _elapsed_calendar_days(start: datetime, end: datetime, zone: ZoneInfo) -> int:
    """Local calendar days between two instants.

    Both instants are converted to a local date first and the dates subtracted,
    so the answer is a count of calendar days and never a count of 86,400-second
    blocks. That is what makes 23:50 local on day 30 read as day 30 rather than
    day 31, and what makes the 25-hour day in November not cost a customer a day
    of their window.
    """
    return (end.astimezone(zone).date() - start.astimezone(zone).date()).days


def _working_periods_on(calendar: BusinessCalendar, day: date) -> tuple[WorkingPeriod, ...]:
    """The calendar's declared working periods on one local day.

    A local mirror of the module-private helper in `business_calendar.py`,
    reading only that module's public fields. Duplicated rather than reached
    into because `business_calendar.py` belongs to another change and this
    package may not edit it; the shape is four lines and the fields are frozen.
    """
    if day in calendar.holidays:
        return ()
    return tuple(
        sorted(
            (period for period in calendar.working_periods if period.weekday == day.weekday()),
            key=lambda period: period.start_minute,
        )
    )


def _local_instant(day: date, minute: int, zone: ZoneInfo) -> datetime:
    """A local wall-clock minute on one day, as an absolute instant.

    `fold=0` resolves a repeated local hour to its first occurrence -- the
    earlier real instant, and therefore the conservative deadline. Minute 1440
    is midnight ending the day, expressed as the next day's zero so two whole
    consecutive days join without a seam. Both rules match
    `business_calendar.py` exactly, because a claim deadline and a support
    deadline disagreeing by an hour on one Sunday in November is not something
    anyone would find.
    """
    if minute >= 1_440:
        return datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=zone).astimezone(UTC)
    local = datetime.combine(day, time(hour=minute // 60, minute=minute % 60), tzinfo=zone).replace(
        fold=0
    )
    return local.astimezone(UTC)


def reporting_window_deadline(
    calendar: BusinessCalendar, delivered_at: datetime, business_days: int
) -> datetime | None:
    """Close of the Nth working day after delivery, or `None` if there is none.

    The delivery day itself is day zero: "two business days from delivery"
    gives the customer the next two days the business is open, and a Friday
    delivery against a Monday-to-Friday calendar is therefore due at close on
    Tuesday.

    `None` when the calendar declares no working day within
    `MAX_HORIZON_DAYS` -- a calendar whose every day is empty or swallowed by
    holidays would otherwise be walked forever looking for a day that does not
    exist. The caller reports that as an undetermined window rather than
    inventing a deadline.
    """
    if business_days < 1:
        raise ValueError("a reporting window must span at least one business day")
    zone = calendar.zone()
    day = delivered_at.astimezone(zone).date()
    horizon = day + timedelta(days=MAX_HORIZON_DAYS)
    counted = 0
    while day < horizon:
        day += timedelta(days=1)
        periods = _working_periods_on(calendar, day)
        if not periods:
            continue
        counted += 1
        if counted == business_days:
            return _local_instant(day, max(period.end_minute for period in periods), zone)
    return None


def _delivery_claim_window(
    policy: ReturnEligibilityPolicy, facts: PolicyEvaluationInput, clock: PolicyClock
) -> DeliveryClaimWindow:
    window = policy.delivery_claim.reporting_window
    if facts.delivery_date is None or clock.business_calendar is None:
        return DeliveryClaimWindow(
            business_days=window.business_days,
            delivery_date=facts.delivery_date,
            reporting_deadline=None,
            state=ReportingWindowState.UNDETERMINED,
        )
    deadline = reporting_window_deadline(
        clock.business_calendar, facts.delivery_date, window.business_days
    )
    if deadline is None:
        return DeliveryClaimWindow(
            business_days=window.business_days,
            delivery_date=facts.delivery_date,
            reporting_deadline=None,
            state=ReportingWindowState.UNDETERMINED,
        )
    return DeliveryClaimWindow(
        business_days=window.business_days,
        delivery_date=facts.delivery_date,
        reporting_deadline=deadline,
        state=(
            ReportingWindowState.WITHIN
            if facts.request_date <= deadline
            else ReportingWindowState.ELAPSED
        ),
    )


_WINDOW_REASON: dict[ReportingWindowState, PolicyReasonCode] = {
    ReportingWindowState.WITHIN: PolicyReasonCode.DELIVERY_CLAIM_WITHIN_REPORTING_WINDOW,
    ReportingWindowState.ELAPSED: PolicyReasonCode.DELIVERY_CLAIM_REPORTING_WINDOW_ELAPSED,
    ReportingWindowState.UNDETERMINED: (
        PolicyReasonCode.DELIVERY_CLAIM_REPORTING_WINDOW_UNDETERMINED
    ),
}


# --------------------------------------------------------------------------
# Shared builders
# --------------------------------------------------------------------------


def _classify_stock(
    policy: ReturnEligibilityPolicy,
    facts: PolicyEvaluationInput,
) -> tuple[PolicyEvaluationInput, bool]:
    """Resolve stocked-vs-special-order from configuration when the source is silent.

    Returns the facts to evaluate and whether configuration supplied any of them.

    Order matters. An explicit designation wins over the default, and both lose
    to the source: `known false != not mentioned` runs in this direction too, so
    a line the extract actually classified is never overwritten by a policy that
    merely has an opinion about lines in general.
    """
    classification = policy.stock_classification
    unknown = (
        facts.special_order is TriState.UNKNOWN
        or facts.non_stock is TriState.UNKNOWN
        or facts.seller_stocked is TriState.UNKNOWN
    )
    if not unknown:
        return facts, False

    if classification.designates_special_order(sku=facts.sku, product_id=facts.product_id):
        return (
            facts.model_copy(
                update={
                    field: value
                    for field, value in (
                        ("special_order", TriState.TRUE),
                        ("non_stock", TriState.TRUE),
                        ("seller_stocked", TriState.FALSE),
                    )
                    if getattr(facts, field) is TriState.UNKNOWN
                }
            ),
            True,
        )

    default = classification.unresolved_default
    if default is StockClassificationDefault.REVIEW_REQUIRED:
        return facts, False
    assumed = (
        (TriState.TRUE, TriState.TRUE, TriState.FALSE)
        if default is StockClassificationDefault.SPECIAL_ORDER
        else (TriState.FALSE, TriState.FALSE, TriState.TRUE)
    )
    return (
        facts.model_copy(
            update={
                field: value
                for field, value in zip(
                    ("special_order", "non_stock", "seller_stocked"), assumed, strict=True
                )
                if getattr(facts, field) is TriState.UNKNOWN
            }
        ),
        True,
    )


def _review(
    provenance: PolicyProvenance,
    reason: PolicyReasonCode,
    rules: tuple[PolicyRule, ...],
) -> PolicyOutcome:
    """Step 8. The answer whenever the facts do not establish anything else."""
    return PolicyOutcome(
        route=PolicyRoute.STANDARD_RETURN,
        decision=EligibilityDecision.REVIEW_REQUIRED,
        reason_codes=(reason,),
        applied_rules=rules,
        provenance=provenance,
    )


def _provenance(
    policy: ReturnEligibilityPolicy, facts: PolicyEvaluationInput, clock: PolicyClock
) -> PolicyProvenance:
    return PolicyProvenance(
        policy_id=policy.id,
        policy_version=policy.version,
        authority=policy.authority,
        source_document=policy.source_document,
        source_revision=policy.source_revision,
        evaluated_at=clock.evaluated_at,
        configuration_release_id=facts.configuration_release_id,
    )
