"""`PolicyOutcome` -- what a deterministic evaluation produces (3A.5).

`EligibilityDecision` stays exactly three-valued and is imported, never
redefined. Routing is a wrapper around it so that "not applicable" is the
**absence** of a decision rather than a fourth decision value:

```text
route = STANDARD_RETURN -> decision in {APPROVE, REJECT, REVIEW_REQUIRED}
route = WARRANTY        -> decision is null
route = DELIVERY_CLAIM  -> decision is null
```

`{"route": "WARRANTY", "decision": "APPROVE"}` is unconstructable, and it is
unconstructable at the *constructor* rather than by convention -- the same
shape `AgentAction.validate_action_payload` already uses. Warranty and delivery
claims are verified by Support inside this application; the evaluator's job
ends at naming which queue verifies them.

Two further invariants are enforced here rather than left to the evaluator:

* An `APPROVE` may not carry a reason code from
  `APPROVAL_FORBIDDING_REASON_CODES`. "Never infer APPROVE" then survives a
  future edit that reorders a branch, because the reordered branch cannot
  build its own result.
* A fee determination may not carry an amount without an authority. The audit's
  `estimatedRefund: 149.99` was a number with nothing behind it; a number here
  has to name where it came from.
* An outcome that skipped checks names them, and an outcome that names none may
  not claim it skipped any. `unevaluated_checks` and
  `PolicyRule.CONDITION_FACTS_NOT_EVALUATED` are constructed together or the
  constructor refuses, so a window-only approval can always be told apart from
  a fully-evidenced one by a reader holding nothing but the outcome.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from return_platform.policy.vocabulary import (
    APPROVAL_FORBIDDING_REASON_CODES,
    EligibilityDecision,
    FeeAmountSource,
    MonetaryAmount,
    NonBlank,
    OverrideAuthority,
    PolicyCondition,
    PolicyExceptionCode,
    PolicyReasonCode,
    PolicyRoute,
    PolicyRule,
    ReportingWindowState,
    StrictModel,
)

__all__ = [
    "DeliveryClaimWindow",
    "FeeDetermination",
    "PolicyException",
    "PolicyOutcome",
    "PolicyProvenance",
]


class PolicyException(StrictModel):
    """A departure from the public standard, and who authorised it."""

    code: PolicyExceptionCode
    authority: OverrideAuthority
    reference: NonBlank


class FeeDetermination(StrictModel):
    """Whether a fee applies, and the amount only if an authority supplied one.

    `permitted_amount_sources` is copied from the policy release so that a
    consumer holding only the outcome can tell where the missing figure is
    meant to come from, rather than guessing or defaulting.
    """

    applies: bool
    waived: bool = False
    amount: MonetaryAmount | None = None
    amount_source: FeeAmountSource | None = None
    permitted_amount_sources: tuple[FeeAmountSource, ...] = ()

    #: The seller's configured rate, in basis points (1500 = 15.00%).
    #:
    #: A rate, not an amount, because the evaluator is pure and holds no line
    #: prices -- `quantity` is carried and deliberately read by no rule, and the
    #: public policy establishes no value threshold. So the rate is the policy
    #: answer and the currency figure is arithmetic performed downstream, where
    #: `order_line.net_price` actually exists. `rate_source` is required
    #: alongside it for the same reason `amount_source` is: a percentage with no
    #: named authority is indistinguishable from an invented one.
    rate_basis_points: int | None = Field(default=None, ge=0, le=10_000)
    rate_source: FeeAmountSource | None = None

    @model_validator(mode="after")
    def validate_determination(self) -> FeeDetermination:
        if self.amount is not None and self.amount_source is None:
            raise ValueError("a fee amount must name the authority that set it")
        if self.amount is not None and not self.applies:
            raise ValueError("a fee that does not apply cannot carry an amount")
        if self.waived and self.applies:
            raise ValueError("a waived fee does not apply")
        if self.rate_basis_points is not None and self.rate_source is None:
            raise ValueError("a fee rate must name the authority that set it")
        if self.rate_basis_points is not None and not self.applies:
            raise ValueError("a fee that does not apply cannot carry a rate")
        return self


class DeliveryClaimWindow(StrictModel):
    """The reporting window a delivery claim was raised against (baseline 6).

    Carried on the outcome rather than recomputed downstream because 3A.6 sets
    `slaDueAt` from `reporting_deadline`, and a deadline computed twice against
    two calendars is a deadline nobody can reconcile.

    `state` is annotation, never gating: an elapsed window does not reject the
    claim here. Support decides that, which is exactly the separation the
    baseline's `standard_return_decision: NOT_APPLICABLE` expresses.
    """

    business_days: int = Field(ge=1)
    delivery_date: AwareDatetime | None = None
    reporting_deadline: AwareDatetime | None = None
    state: ReportingWindowState

    @model_validator(mode="after")
    def validate_window(self) -> DeliveryClaimWindow:
        if self.state is ReportingWindowState.UNDETERMINED:
            if self.reporting_deadline is not None:
                raise ValueError("an undetermined reporting window cannot carry a deadline")
            return self
        if self.reporting_deadline is None or self.delivery_date is None:
            raise ValueError(
                "a resolved reporting window needs both the delivery instant and the deadline "
                "it produced"
            )
        return self


class PolicyProvenance(StrictModel):
    """Which policy release decided this, persisted with the decision (baseline 16)."""

    source: Literal["FERGUSON_POLICY_ENGINE"] = "FERGUSON_POLICY_ENGINE"
    policy_id: NonBlank
    policy_version: NonBlank
    authority: NonBlank
    source_document: NonBlank
    source_revision: NonBlank
    evaluated_at: AwareDatetime
    configuration_release_id: NonBlank | None = None


class PolicyOutcome(StrictModel):
    """The single executable result of one deterministic eligibility evaluation."""

    route: PolicyRoute
    decision: EligibilityDecision | None = None
    conditions: tuple[PolicyCondition, ...] = ()
    exceptions: tuple[PolicyException, ...] = ()
    reason_codes: tuple[PolicyReasonCode, ...] = ()
    applied_rules: tuple[PolicyRule, ...] = Field(min_length=1)

    #: The checks this evaluation did not apply, named exactly as
    #: `PolicyEvaluationInput.resale_condition_facts` and
    #: `prohibited_state_facts` name them, in the baseline's order.
    #:
    #: Non-empty only when the release configures
    #: `UnstatedConditionFacts.NOT_EVALUATED` and the case left one of those
    #: facts unstated. It is paired with `PolicyRule.CONDITION_FACTS_NOT_EVALUATED`
    #: by the validator below, in both directions, so an approval that skipped
    #: checks cannot be constructed without naming them and the marker cannot be
    #: claimed with nothing behind it. That is the same structural guarantee
    #: `STOCK_CLASS_FROM_CONFIGURATION` gives for an assumed stock class: an
    #: approval taken without evidence must never read like one taken on it.
    unevaluated_checks: tuple[NonBlank, ...] = ()

    restocking_fee: FeeDetermination | None = None
    #: Only the special-order manufacturer path can produce one. Separate from
    #: `restocking_fee` because baseline section 10 carries both manufacturer
    #: fees independently, and folding two authoritative amounts into one field
    #: is how one of them silently stops being shown.
    cancellation_fee: FeeDetermination | None = None
    delivery_claim_window: DeliveryClaimWindow | None = None
    provenance: PolicyProvenance

    @model_validator(mode="after")
    def validate_outcome(self) -> PolicyOutcome:
        if self.route is PolicyRoute.STANDARD_RETURN:
            if self.decision is None:
                raise ValueError(
                    "a standard return must carry one of APPROVE, REJECT or REVIEW_REQUIRED"
                )
        elif self.decision is not None:
            raise ValueError(
                f"route {self.route.value} is a verification hand-off, not a decision: "
                "Support verifies it, so the decision must be null"
            )

        if self.route is not PolicyRoute.DELIVERY_CLAIM and self.delivery_claim_window is not None:
            raise ValueError("only a delivery claim carries a delivery-claim reporting window")

        if self.decision is EligibilityDecision.APPROVE:
            offending = sorted(
                code.value for code in set(self.reason_codes) & APPROVAL_FORBIDDING_REASON_CODES
            )
            if offending:
                raise ValueError(
                    "an approval cannot rest on an unknown, refused or out-of-window fact: "
                    + ", ".join(offending)
                )

        marked = PolicyRule.CONDITION_FACTS_NOT_EVALUATED in self.applied_rules
        if self.unevaluated_checks and not marked:
            raise ValueError(
                "an outcome that skipped condition checks must carry "
                "CONDITION_FACTS_NOT_EVALUATED: a decision taken without evidence cannot be "
                "recorded as one taken on it"
            )
        if marked and not self.unevaluated_checks:
            raise ValueError(
                "CONDITION_FACTS_NOT_EVALUATED must name the checks it skipped"
            )
        if len(set(self.unevaluated_checks)) != len(self.unevaluated_checks):
            raise ValueError("outcome unevaluated checks must be unique")

        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("outcome reason codes must be unique")
        if len(set(self.conditions)) != len(self.conditions):
            raise ValueError("outcome conditions must be unique")
        if len(set(self.applied_rules)) != len(self.applied_rules):
            raise ValueError("outcome applied rules must be unique")
        return self
