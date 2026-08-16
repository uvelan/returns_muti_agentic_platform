"""`PolicyEvaluationInput` -- the facts the evaluator is allowed to see (3A.3).

Baseline section 10, minus everything section 11 strikes. There is no
`customer_tier`, no `prior_return_count`, no product-class window and no value
threshold on this model, and their absence is the point: they were inferred
from what a returns policy usually contains rather than from any Ferguson
source, so a field for one would be an invitation to write the rule.

**Every policy-critical fact is `TriState` and defaults to `UNKNOWN`.** Not one
of them is a `bool`. A `bool` has no way to say "nobody mentioned this", so the
extraction step would have to choose between `False` and refusing to build the
input at all -- and `False` is evidence. `used = False` means the customer told
us the item is unused; `used = UNKNOWN` means we do not know, and the two must
reach different decisions.

`FeeDeclaration` applies the same rule to money: "no fee applies" and "we have
not established whether a fee applies" are different states, and only the first
one may contribute to an approval.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from return_platform.policy.vocabulary import (
    DamageCause,
    FeeAmountSource,
    ManufacturerAcceptance,
    MonetaryAmount,
    NonBlank,
    ReturnReason,
    StrictModel,
    TriState,
)

__all__ = [
    "EvidenceState",
    "FeeDeclaration",
    "PolicyEvaluationInput",
    "PolicyFactEnvelope",
    "admitted_value",
]


class EvidenceState(StrEnum):
    """Whether a captured fact is in a state that may enter policy evaluation.

    The case fact log already carries `acquisitionMethod`, `sourceSystem` and
    supersession. The evaluator's contribution is to *enforce* them as an
    admission rule: a fact awaiting validation, rejected, or superseded by a
    later capture is not evidence, and reading it as evidence is how a stale
    "unused" from a first turn survives the customer's correction on a second.
    """

    ACCEPTED = "ACCEPTED"
    PENDING_VALIDATION = "PENDING_VALIDATION"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class PolicyFactEnvelope(StrictModel):
    """One tri-state fact with the provenance that decides whether it counts.

    Deliberately not part of `PolicyEvaluationInput`. The evaluator stays a
    pure function over resolved values; this is the shape the *adapter* from
    the case projection uses to resolve them, and `admitted_value` is the whole
    admission rule in one place so that both the adapter and its tests read the
    same statement of it.
    """

    value: TriState
    provenance: NonBlank
    acquisition_method: NonBlank
    validation_state: EvidenceState
    captured_at: AwareDatetime


def admitted_value(envelope: PolicyFactEnvelope | None) -> TriState:
    """The value the evaluator may use, which is `UNKNOWN` unless it is accepted.

    A missing envelope is `UNKNOWN` for the same reason a pending one is: the
    absence of admissible evidence is not evidence of absence.
    """
    if envelope is None or envelope.validation_state is not EvidenceState.ACCEPTED:
        return TriState.UNKNOWN
    return envelope.value


class FeeDeclaration(StrictModel):
    """Whether a fee applies, and -- only if someone authoritative said so -- how much.

    `applicability` is tri-state because "the manufacturer charges nothing" and
    "we have not asked the manufacturer" are different facts, and baseline
    section 4 sends the second one to review while letting the first one
    approve.

    An amount without a source is unconstructable. That is the invariant that
    stops a figure from appearing on a screen with nothing behind it.
    """

    applicability: TriState = TriState.UNKNOWN
    amount: MonetaryAmount | None = None
    source: FeeAmountSource | None = None

    @model_validator(mode="after")
    def validate_declaration(self) -> FeeDeclaration:
        if self.amount is not None and self.source is None:
            raise ValueError(
                "a fee amount must name the authority it came from: "
                "SELLER_CONFIGURATION, SELLER_OVERRIDE or MANUFACTURER"
            )
        if self.applicability is TriState.FALSE and self.amount is not None:
            raise ValueError("a fee that does not apply cannot carry an amount")
        if self.applicability is TriState.UNKNOWN and self.amount is not None:
            raise ValueError(
                "a fee whose applicability is unknown cannot carry an amount: "
                "the amount would be asserting the applicability"
            )
        return self

    @property
    def is_resolved(self) -> bool:
        """Whether this declaration says something the evaluator can act on.

        Resolved means either "no fee applies" or "this fee applies and here is
        the authoritative amount". A fee that applies with no amount is *not*
        resolved -- baseline section 4 sends "fee applicability/amount unknown"
        to review, and an amount nobody supplied is exactly that.
        """
        if self.applicability is TriState.FALSE:
            return True
        return self.applicability is TriState.TRUE and self.amount is not None


class PolicyEvaluationInput(StrictModel):
    """The resolved return-policy facts for one case, at one instant."""

    # --- Dates. Instants, converted to local calendar dates by the evaluator.
    #     `request_date` is required: an evaluation with no "as of" cannot
    #     answer a window question at all, and defaulting it to now() would put
    #     a clock read inside a function documented as pure.
    request_date: AwareDatetime
    purchase_date: AwareDatetime | None = None
    delivery_date: AwareDatetime | None = None

    # --- Path discrimination. Baseline section 12's safety rule names an
    #     unknown special-order status explicitly: it is REVIEW_REQUIRED, not
    #     an assumed stock item.
    seller_stocked: TriState = TriState.UNKNOWN
    special_order: TriState = TriState.UNKNOWN
    non_stock: TriState = TriState.UNKNOWN

    #: Product identity, carried only so `stock_classification` can designate a
    #: line special-order by SKU or product id. No rule reads these directly --
    #: the public policy establishes no product-class eligibility, and baseline
    #: section 11 strikes product-class windows by name. They are an operator
    #: lookup key, not evidence.
    sku: str | None = None
    product_id: str | None = None

    #: Carried because baseline section 10 lists it, and read by no rule. The
    #: public policy establishes no quantity or value threshold, and section 11
    #: strikes "arbitrary value thresholds" by name -- so this reaches the
    #: refund calculation downstream, never the eligibility decision.
    quantity: int | None = Field(default=None, ge=1)

    # --- Resale condition.
    condition_new: TriState = TriState.UNKNOWN
    suitable_for_resale: TriState = TriState.UNKNOWN
    original_packaging: TriState = TriState.UNKNOWN
    packaging_undamaged: TriState = TriState.UNKNOWN
    all_original_parts: TriState = TriState.UNKNOWN

    # --- Prohibited states.
    used: TriState = TriState.UNKNOWN
    installed: TriState = TriState.UNKNOWN
    modified: TriState = TriState.UNKNOWN
    rebuilt: TriState = TriState.UNKNOWN
    reconditioned: TriState = TriState.UNKNOWN
    repaired: TriState = TriState.UNKNOWN
    altered: TriState = TriState.UNKNOWN
    damaged: TriState = TriState.UNKNOWN

    damage_cause: DamageCause = DamageCause.UNKNOWN
    return_reason: ReturnReason = ReturnReason.UNKNOWN

    # --- Special-order / non-stock manufacturer path.
    manufacturer_return_acceptance: ManufacturerAcceptance = ManufacturerAcceptance.UNKNOWN
    manufacturer_restocking_fee: FeeDeclaration = Field(default_factory=FeeDeclaration)
    manufacturer_cancellation_fee: FeeDeclaration = Field(default_factory=FeeDeclaration)
    buyer_accepts_manufacturer_fees: TriState = TriState.UNKNOWN

    # --- Seller fee position.
    seller_restocking_fee: FeeDeclaration = Field(default_factory=FeeDeclaration)
    seller_fee_waiver: TriState = TriState.UNKNOWN

    #: A negotiated customer agreement outranks the public standard (baseline
    #: 17). Only its *reference* travels here -- the evaluator cannot read a
    #: contract, so a case that has one is a case a human decides.
    contract_override_reference: NonBlank | None = None

    # --- Provenance echo. Recorded on the outcome, never used as a rule input.
    policy_version: NonBlank | None = None
    configuration_release_id: NonBlank | None = None

    @model_validator(mode="after")
    def validate_facts(self) -> PolicyEvaluationInput:
        if self.purchase_date is not None and self.purchase_date > self.request_date:
            raise ValueError("a return cannot be requested before the purchase it concerns")
        if self.delivery_date is not None and self.delivery_date > self.request_date:
            raise ValueError("a return cannot be requested before the delivery it concerns")
        if (
            self.damage_cause is not DamageCause.UNKNOWN
            and self.damage_cause is not DamageCause.NOT_APPLICABLE
            and self.damaged is TriState.FALSE
        ):
            raise ValueError("a damage cause cannot be stated for an item established as undamaged")
        return self

    def resale_condition_facts(self) -> tuple[tuple[str, TriState], ...]:
        """The resale-condition facts, named, in the baseline's order."""
        return (
            ("new", self.condition_new),
            ("suitable_for_resale", self.suitable_for_resale),
            ("original_packaging", self.original_packaging),
            ("packaging_undamaged", self.packaging_undamaged),
            ("all_original_parts", self.all_original_parts),
        )

    def prohibited_state_facts(self) -> tuple[tuple[str, TriState], ...]:
        """The prohibited-state facts, named, in the baseline's order."""
        return (
            ("used", self.used),
            ("installed", self.installed),
            ("modified", self.modified),
            ("rebuilt", self.rebuilt),
            ("reconditioned", self.reconditioned),
            ("repaired", self.repaired),
            ("altered", self.altered),
            ("damaged", self.damaged),
        )
