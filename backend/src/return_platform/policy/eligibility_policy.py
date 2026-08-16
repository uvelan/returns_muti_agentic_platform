"""`ReturnEligibilityPolicy` -- the versioned rule set the evaluator executes (3A.2).

This is the shape of baseline section 9, as a `StrictConfigModel` so that it
inherits the same release, activation, checksum and audit machinery every other
block of `ReturnPlatformConfiguration` already has. It lives in its own module
so that wiring it into the root configuration is one import and one field.

**A malformed release refuses activation.** Baseline section 20 is explicit
that an empty or malformed rule set must fail operationally rather than deploy
and turn every case into `REVIEW_REQUIRED` -- an outage that looks exactly like
correct caution. Every check section 20 lists (rule priorities, required
fields, window values, fee-source references, condition matrices, reason-code
references, manufacturer rule references) is enforced by `validate_release`
below, which pydantic runs at construction. There is no separate validation
pass to forget to call.

**Nothing here can express a fee amount.** `percentage` and `amount` are typed
`None`, not `Decimal | None`. A release that writes `percentage: 15` fails to
parse. That is the strongest available statement of "Ferguson publishes no
universal restocking percentage and this application must not invent one": it
is not a lint rule or a code review convention, it is a type error.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator

from return_platform.policy.vocabulary import (
    CurrencyCode,
    EligibilityDecision,
    FeeAmountSource,
    NonBlank,
    PolicyCondition,
    PolicyPrecedence,
    PolicyReasonCode,
    ReturnReason,
    ReturnWindowBasis,
    StockClassificationDefault,
    StrictConfigModel,
    UnstatedConditionFacts,
)

__all__ = [
    "DeliveryClaimConfiguration",
    "DeliveryClaimReportingWindow",
    "OutsideStandardWindowConfiguration",
    "ProhibitedStateRequirements",
    "PurchaseWindowConfiguration",
    "ResaleConditionRequirements",
    "RestockingFeeConfiguration",
    "ReturnEligibilityPolicy",
    "SpecialOrNonStockConfiguration",
    "SpecialOrderDecisions",
    "StandardStockRequirements",
    "StandardStockReturnConfiguration",
    "WarrantyIssueConfiguration",
]


class PurchaseWindowConfiguration(StrictConfigModel):
    """How long the standard return window runs, and from what.

    `days` is calendar days, counted between *local* dates in the customer's
    business zone -- never a `timedelta` over instants, which is 24 hours short
    or long on the two days a year a zone shifts.
    """

    days: int = Field(ge=1, le=3_650)
    basis: ReturnWindowBasis = ReturnWindowBasis.PURCHASE_DATE


class ResaleConditionRequirements(StrictConfigModel):
    """The value each resale-condition fact must hold for a standard return.

    Every field is "the value the fact is required to have", which is exactly
    how the YAML reads. A deployment that does not require original packaging
    says `original_packaging: false` and the check stops being applied.
    """

    new: bool = True
    suitable_for_resale: bool = True
    original_packaging: bool = True
    packaging_undamaged: bool = True
    all_original_parts: bool = True


class ProhibitedStateRequirements(StrictConfigModel):
    """The value each prohibited-state fact must hold for a standard return.

    All `False` in the public baseline: a standard-return item has not been
    used, installed, modified, rebuilt, reconditioned, repaired, altered or
    damaged. `damaged` is here *and* routed by cause -- a damaged item fails
    the standard condition only once the cause has been established as customer
    or use damage, which is what keeps a carrier claim from being rejected as
    an ordinary return.
    """

    used: bool = False
    installed: bool = False
    modified: bool = False
    rebuilt: bool = False
    reconditioned: bool = False
    repaired: bool = False
    altered: bool = False
    damaged: bool = False


class StandardStockRequirements(StrictConfigModel):
    """What makes an item eligible for the standard stocked path at all."""

    seller_stocked: bool = True
    special_order: bool = False
    condition: ResaleConditionRequirements = Field(default_factory=ResaleConditionRequirements)
    prohibited_states: ProhibitedStateRequirements = Field(
        default_factory=ProhibitedStateRequirements
    )


class StandardStockReturnConfiguration(StrictConfigModel):
    """The Ferguson standard stocked-item return rule."""

    purchase_window: PurchaseWindowConfiguration
    requirements: StandardStockRequirements = Field(default_factory=StandardStockRequirements)
    decision_when_satisfied: EligibilityDecision = EligibilityDecision.APPROVE
    conditions: tuple[PolicyCondition, ...] = (PolicyCondition.RESTOCKING_FEE_APPLIES,)
    #: What a condition or prohibited-state fact nobody stated is allowed to
    #: decide. Defaulted to `REVIEW_REQUIRED`, which is this evaluator's
    #: historical behaviour and the baseline's safety rule; a release that sets
    #: `NOT_EVALUATED` narrows the standard path to the checks the platform can
    #: source, which in practice is the return window.
    #:
    #: It is a setting rather than a branch so that switching the other checks
    #: back on is a released configuration change and not a deployment. The
    #: requirements above are untouched by it: they still reject a stated
    #: failure, and they resume gating the moment this returns to
    #: `REVIEW_REQUIRED`.
    unstated_condition_facts: UnstatedConditionFacts = UnstatedConditionFacts.REVIEW_REQUIRED

    @model_validator(mode="after")
    def validate_rule(self) -> StandardStockReturnConfiguration:
        if self.decision_when_satisfied is EligibilityDecision.REJECT:
            raise ValueError(
                "a satisfied standard stocked return cannot be configured to REJECT: "
                "a rule set that can never approve is the malformed release baseline "
                "section 20 refuses to activate"
            )
        if len(set(self.conditions)) != len(self.conditions):
            raise ValueError("standard stocked return conditions must be unique")
        return self


class SellerRestockingFeeSchedule(StrictConfigModel):
    """The seller's own restocking rate, in basis points.

    Basis points, not a float and not a percent string: money arithmetic in this
    package is `Decimal` or integer minor units, never binary floating point, and
    an integer rate keeps that true from configuration all the way to the credit.
    1500 = 15.00%.

    `authority` is fixed to `SELLER_CONFIGURATION` and cannot be set to anything
    else. A rate the seller chose must never be able to present itself as a
    manufacturer's fee or as published Ferguson policy -- those come from the
    Support outcome and the public terms respectively, and conflating them is how
    a number acquires an authority it never had.
    """

    default_rate_basis_points: int = Field(ge=0, le=10_000)
    currency: CurrencyCode = "USD"

    @property
    def authority(self) -> FeeAmountSource:
        return FeeAmountSource.SELLER_CONFIGURATION

    def rate(self) -> Decimal:
        """The rate as a decimal fraction, e.g. 1500 -> Decimal('0.15')."""
        return Decimal(self.default_rate_basis_points) / Decimal(10_000)


class RestockingFeeConfiguration(StrictConfigModel):
    """That a fee applies is policy; what it is, is not (baseline 3).

    `percentage` and `amount` are typed `None` and stay that way. Ferguson's
    public policy establishes that returns are subject to a restocking fee and
    publishes no universal percentage, so a number written *there* would be
    fabricated -- it would claim Ferguson's authority for a rate Ferguson has
    not published.

    `seller_schedule` is the other thing. It is the **seller's own** rate, and it
    carries `FeeAmountSource.SELLER_CONFIGURATION` on every determination it
    produces, so a figure sourced from it can never be mistaken in an audit for
    one Ferguson published. That is exactly the route this model always intended:
    the docstring said "the amount reaches a case from `amount_source`", and
    `SELLER_CONFIGURATION` is the first entry in it -- there was simply nowhere
    to put the seller's value.
    """

    applies_by_default: bool = True
    percentage: None = None
    amount: None = None
    seller_schedule: SellerRestockingFeeSchedule | None = None
    amount_source: tuple[FeeAmountSource, ...] = (
        FeeAmountSource.SELLER_CONFIGURATION,
        FeeAmountSource.SELLER_OVERRIDE,
        FeeAmountSource.MANUFACTURER,
    )
    seller_can_waive: bool = True
    invent_default_amount: bool = False

    @model_validator(mode="after")
    def validate_fee(self) -> RestockingFeeConfiguration:
        if self.invent_default_amount:
            raise ValueError(
                "invent_default_amount cannot be enabled: there is no published Ferguson "
                "restocking-fee percentage for the platform to default to"
            )
        if not self.amount_source:
            raise ValueError(
                "a restocking fee that applies needs at least one permitted amount source"
            )
        if len(set(self.amount_source)) != len(self.amount_source):
            raise ValueError("restocking fee amount sources must be unique")
        return self


class StockClassificationConfiguration(StrictConfigModel):
    """Where the stocked-vs-special-order answer comes from.

    It does not come from the source. Ferguson's `salesInv` extract carries no
    such field, and the one candidate (`lineType`) is undocumented and unsafe --
    see `StockClassificationDefault`. So an operator supplies it here, in a
    released policy, and can change it without code.

    Two mechanisms, checked in this order:

    1. **Explicit designation.** A SKU, product id, or SKU prefix listed below is
       special-order regardless of anything the source says. This is how a
       deployment marks the product families it knows are bought to order.
    2. **The default for everything else**, when the source left it unknown.

    A line the source *does* classify is never overridden -- configuration fills
    silence, it does not contradict evidence.

    The observed `SP`-prefixed master product ids in the reference data look like
    special orders (8 lines, all with `taggedPoId`, all `cntrtCostType: P`), but
    the same token appears as `origProdStatus: SP` on four ordinary catalogue
    products, and no vendor document defines either. The prefix list is left
    empty deliberately: adding `"SP"` is an operator's assertion to make, not a
    guess for this file to ship.
    """

    unresolved_default: StockClassificationDefault = StockClassificationDefault.REVIEW_REQUIRED
    special_order_skus: tuple[NonBlank, ...] = ()
    special_order_sku_prefixes: tuple[NonBlank, ...] = ()
    special_order_product_ids: tuple[NonBlank, ...] = ()

    def designates_special_order(self, *, sku: str | None, product_id: str | None) -> bool:
        """True when the operator has named this line special-order."""
        if sku is not None:
            if sku in self.special_order_skus:
                return True
            if any(sku.startswith(prefix) for prefix in self.special_order_sku_prefixes):
                return True
        return product_id is not None and product_id in self.special_order_product_ids


class SpecialOrderDecisions(StrictConfigModel):
    """The five special-order outcomes of baseline section 4, as configuration.

    Configured rather than branched in Python so a manufacturer-specific
    release can move one of them without a deployment -- and validated here so
    that no release can move one of them to `APPROVE`, which is the only
    direction that could manufacture an approval out of an absent fact.
    """

    manufacturer_acceptance_unknown: EligibilityDecision = EligibilityDecision.REVIEW_REQUIRED
    manufacturer_acceptance_rejected: EligibilityDecision = EligibilityDecision.REJECT
    manufacturer_acceptance_accepted_buyer_fee_unknown: EligibilityDecision = (
        EligibilityDecision.REVIEW_REQUIRED
    )
    manufacturer_acceptance_accepted_buyer_fee_rejected: EligibilityDecision = (
        EligibilityDecision.REJECT
    )
    manufacturer_acceptance_accepted_buyer_fee_accepted: EligibilityDecision = (
        EligibilityDecision.APPROVE
    )

    @model_validator(mode="after")
    def validate_decisions(self) -> SpecialOrderDecisions:
        never_approvable = {
            "manufacturer_acceptance_unknown": self.manufacturer_acceptance_unknown,
            "manufacturer_acceptance_rejected": self.manufacturer_acceptance_rejected,
            "manufacturer_acceptance_accepted_buyer_fee_unknown": (
                self.manufacturer_acceptance_accepted_buyer_fee_unknown
            ),
            "manufacturer_acceptance_accepted_buyer_fee_rejected": (
                self.manufacturer_acceptance_accepted_buyer_fee_rejected
            ),
        }
        offending = sorted(
            name
            for name, decision in never_approvable.items()
            if decision is EligibilityDecision.APPROVE
        )
        if offending:
            raise ValueError(
                "a special-order case with an unknown or refused fact cannot be configured "
                f"to APPROVE: {', '.join(offending)}"
            )
        return self


class SpecialOrNonStockConfiguration(StrictConfigModel):
    """Special-order and non-stock items need the manufacturer's agreement."""

    manufacturer_acceptance_required: bool = True
    buyer_fee_acceptance_required: bool = True
    decisions: SpecialOrderDecisions = Field(default_factory=SpecialOrderDecisions)

    @model_validator(mode="after")
    def validate_path(self) -> SpecialOrNonStockConfiguration:
        if not self.manufacturer_acceptance_required:
            raise ValueError(
                "manufacturer acceptance cannot be switched off for special-order returns: "
                "no public Ferguson rule permits approving one without it"
            )
        return self


class OutsideStandardWindowConfiguration(StrictConfigModel):
    """What happens past the standard window (baseline 5).

    Neither an automatic approve nor an automatic reject. Ferguson's public
    standard establishes the window; it does not establish that Ferguson can
    never authorise an exception, so a late return is a question for a human.
    """

    decision: EligibilityDecision = EligibilityDecision.REVIEW_REQUIRED
    reason_code: PolicyReasonCode = PolicyReasonCode.OUTSIDE_STANDARD_RETURN_WINDOW

    @model_validator(mode="after")
    def validate_decision(self) -> OutsideStandardWindowConfiguration:
        if self.decision is EligibilityDecision.APPROVE:
            raise ValueError("a return past the standard window cannot be configured to APPROVE")
        return self


class DeliveryClaimReportingWindow(StrictConfigModel):
    """How long a delivery-related issue may be reported for.

    Business days, from delivery. Two in the current public Terms. This is
    what sets `slaDueAt` on the Support work item in 3A.6, which is why it is
    business days against a declared calendar rather than 48 hours: a Friday
    delivery reported on Tuesday morning is inside the window, and wall-clock
    arithmetic says it is not.
    """

    business_days: int = Field(default=2, ge=1, le=365)
    basis: ReturnWindowBasis = ReturnWindowBasis.DELIVERY_DATE

    @model_validator(mode="after")
    def validate_window(self) -> DeliveryClaimReportingWindow:
        if self.basis is not ReturnWindowBasis.DELIVERY_DATE:
            raise ValueError("a delivery-claim reporting window is counted from the delivery date")
        return self


class DeliveryClaimConfiguration(StrictConfigModel):
    """Damage, shortage, shipment error and improper delivery are not returns."""

    #: Named `conditions` to match the baseline YAML key exactly. These are
    #: return reasons, and the evaluator resolves them against `ReturnReason`.
    conditions: tuple[ReturnReason, ...] = Field(min_length=1)
    reporting_window: DeliveryClaimReportingWindow = Field(
        default_factory=DeliveryClaimReportingWindow
    )

    @model_validator(mode="after")
    def validate_reasons(self) -> DeliveryClaimConfiguration:
        if ReturnReason.UNKNOWN in self.conditions:
            raise ValueError("an unknown return reason cannot route to a delivery claim")
        if len(set(self.conditions)) != len(self.conditions):
            raise ValueError("delivery-claim return reasons must be unique")
        return self


class WarrantyIssueConfiguration(StrictConfigModel):
    """A defect after use or installation is a warranty remedy, not a failed return."""

    reasons: tuple[ReturnReason, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_reasons(self) -> WarrantyIssueConfiguration:
        if ReturnReason.UNKNOWN in self.reasons:
            raise ValueError("an unknown return reason cannot route to warranty")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("warranty return reasons must be unique")
        return self


class ReturnEligibilityPolicy(StrictConfigModel):
    """One versioned, validated deterministic return-eligibility release."""

    id: NonBlank
    version: NonBlank
    authority: NonBlank
    source_document: NonBlank
    source_revision: NonBlank

    precedence: tuple[PolicyPrecedence, ...] = Field(min_length=1)

    standard_stock_return: StandardStockReturnConfiguration
    restocking_fee: RestockingFeeConfiguration = Field(default_factory=RestockingFeeConfiguration)
    stock_classification: StockClassificationConfiguration = Field(
        default_factory=StockClassificationConfiguration,
        description=(
            "How stocked-vs-special-order is resolved when the source cannot say. "
            "Defaults to REVIEW_REQUIRED, which is the baseline's own safety rule."
        ),
    )
    special_or_nonstock: SpecialOrNonStockConfiguration = Field(
        default_factory=SpecialOrNonStockConfiguration
    )
    outside_standard_window: OutsideStandardWindowConfiguration = Field(
        default_factory=OutsideStandardWindowConfiguration
    )
    delivery_claim: DeliveryClaimConfiguration
    warranty_issue: WarrantyIssueConfiguration

    @model_validator(mode="after")
    def validate_release(self) -> ReturnEligibilityPolicy:
        """Every release check baseline section 20 enumerates, at construction."""
        if len(set(self.precedence)) != len(self.precedence):
            raise ValueError("policy precedence entries must be unique")
        if PolicyPrecedence.FERGUSON_STANDARD_RETURN not in self.precedence:
            raise ValueError(
                "the Ferguson standard return must appear in the precedence chain: it is the "
                "baseline authority every other entry overrides"
            )
        if self.precedence[-1] is not PolicyPrecedence.FERGUSON_STANDARD_RETURN:
            raise ValueError(
                "the Ferguson standard return is the lowest-priority authority and must be last "
                "in the precedence chain"
            )
        overlapping = sorted(
            reason.value
            for reason in set(self.delivery_claim.conditions) & set(self.warranty_issue.reasons)
        )
        if overlapping:
            raise ValueError(
                "a return reason cannot route to both a delivery claim and warranty: "
                + ", ".join(overlapping)
            )
        return self
