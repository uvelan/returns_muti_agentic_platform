"""The closed vocabularies the deterministic return-eligibility evaluator speaks.

Every enum here is code-owned and closed. That is deliberate: the audit's
defect was a *string* -- `policyCode: "POL-STD-30D"` typed into a React
component -- and a closed enum is the cheapest structural answer to "where did
this value come from?".

**No boolean facts.** `TriState` exists because `used: bool = False` converts
*unknown* into policy evidence. A model that finds no mention of installation
must be able to say so, and `known false != not mentioned` is the difference
between a return that is approved and one that is looked at.

**No binary floats.** `MonetaryDecimal` refuses a `float` outright rather than
quietly widening it, because a restocking fee that is `0.1 + 0.2` is a defect
nobody notices until a credit memo is a cent short.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

# Re-exported rather than redefined. `EligibilityDecision` is exactly
# three-valued and already shared with the session orchestrator; a parallel
# enum here is how "APPROVED" ends up meaning two different things in one
# codebase.
from return_platform.workflows.stage_results import EligibilityDecision

__all__ = [
    "APPROVAL_FORBIDDING_REASON_CODES",
    "CurrencyCode",
    "DamageCause",
    "EligibilityDecision",
    "FeeAmountSource",
    "ManufacturerAcceptance",
    "MonetaryAmount",
    "MonetaryDecimal",
    "NonBlank",
    "OverrideAuthority",
    "PolicyCondition",
    "PolicyExceptionCode",
    "PolicyPrecedence",
    "PolicyReasonCode",
    "PolicyRoute",
    "PolicyRule",
    "ReportingWindowState",
    "ReturnReason",
    "ReturnWindowBasis",
    "StrictConfigModel",
    "StrictModel",
    "TriState",
    "UnstatedConditionFacts",
]

#: Deliberately mirrored from `configuration/return_configuration.py` rather
#: than imported from it.
#:
#: `ReturnPlatformConfiguration` holds the eligibility policy as a field, so the
#: dependency has to run configuration -> policy. Importing the base class back
#: the other way makes that a cycle, and the failure is not subtle: the first
#: process to import `return_configuration` dies on a partially initialised
#: module. Two identical `ConfigDict`s and one `Annotated` alias are a much
#: cheaper price than a lazy import nobody can safely move.
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]


class StrictConfigModel(BaseModel):
    """Frozen and extra-forbidding, matching the root configuration's base."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class StrictModel(StrictConfigModel):
    """Frozen, extra-forbidding base for every policy contract."""


def _reject_binary_float(value: object) -> object:
    """Refuse a `float` before pydantic can coerce it into a `Decimal`.

    Pydantic will happily build `Decimal` from `float`, which is precisely the
    silent path this rule exists to close: `Decimal(0.1)` is not one tenth.
    Callers pass `Decimal`, `int`, or a decimal string.
    """
    if isinstance(value, float):
        raise ValueError(
            "monetary policy values must be Decimal, int, or a decimal string -- never a float"
        )
    return value


#: A non-negative money magnitude. Binary floats are refused, not rounded.
MonetaryDecimal = Annotated[
    Decimal,
    BeforeValidator(_reject_binary_float),
    Field(ge=0, max_digits=18, decimal_places=4),
]

#: ISO 4217, normalised to upper case. The pattern accepts either case because
#: pydantic checks it before applying `to_upper`, and a lower-case `usd` from a
#: source system is a formatting difference rather than a bad currency.
CurrencyCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z]{3}$"),
]


class MonetaryAmount(StrictModel):
    """One money value and its currency. Never a bare number."""

    amount: MonetaryDecimal
    currency: CurrencyCode


class TriState(StrEnum):
    """A policy-critical fact: known true, known false, or not established.

    `UNKNOWN` is the default everywhere a fact appears, and it is the whole
    point. A fact nobody stated is not a fact stated to be false.
    """

    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class PolicyRoute(StrEnum):
    """Which business path the eligibility evaluation hands the case to.

    Warranty and delivery claim are *routes*, not decisions and not terminal
    states: Support verifies both inside this application and approved cases
    rejoin the ordinary RMA lifecycle.
    """

    STANDARD_RETURN = "STANDARD_RETURN"
    WARRANTY = "WARRANTY"
    DELIVERY_CLAIM = "DELIVERY_CLAIM"


class PolicyPrecedence(StrEnum):
    """The authorities a release may place in its precedence chain (baseline 17)."""

    CUSTOMER_CONTRACT_OVERRIDE = "CUSTOMER_CONTRACT_OVERRIDE"
    SPECIAL_ORDER_MANUFACTURER_POLICY = "SPECIAL_ORDER_MANUFACTURER_POLICY"
    FERGUSON_STANDARD_RETURN = "FERGUSON_STANDARD_RETURN"


class ReturnWindowBasis(StrEnum):
    """What a return window is counted from."""

    PURCHASE_DATE = "PURCHASE_DATE"
    DELIVERY_DATE = "DELIVERY_DATE"


class DamageCause(StrEnum):
    """Why the product is damaged -- the fact that decides the route (baseline 8).

    `damaged = true -> REJECT` is the implementation the baseline explicitly
    forbids, because the same observation is an ordinary rejection, a carrier
    claim or a warranty remedy depending only on this value.
    """

    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CUSTOMER_OR_USE = "CUSTOMER_OR_USE"
    SHIPPING = "SHIPPING"
    MANUFACTURER_DEFECT = "MANUFACTURER_DEFECT"


class ReturnReason(StrEnum):
    """Why the customer is returning the item.

    Which of these route to a delivery claim and which to warranty is
    *configuration* (`delivery_claim.conditions`, `warranty_issue.reasons`),
    not a branch in the evaluator. The enum is only the vocabulary.
    """

    UNKNOWN = "UNKNOWN"

    # Delivery-related (baseline 6)
    SHIPPING_DAMAGE = "SHIPPING_DAMAGE"
    SHORTAGE = "SHORTAGE"
    SHIPMENT_ERROR = "SHIPMENT_ERROR"
    IMPROPER_DELIVERY = "IMPROPER_DELIVERY"

    # Warranty-related (baseline 7)
    MANUFACTURING_DEFECT = "MANUFACTURING_DEFECT"
    PRODUCT_FAILURE_AFTER_INSTALLATION = "PRODUCT_FAILURE_AFTER_INSTALLATION"
    COVERED_PRIVATE_LABEL_DEFECT = "COVERED_PRIVATE_LABEL_DEFECT"
    MANUFACTURER_WARRANTY_ISSUE = "MANUFACTURER_WARRANTY_ISSUE"

    # Ordinary return reasons
    CHANGED_MIND = "CHANGED_MIND"
    ORDERED_IN_ERROR = "ORDERED_IN_ERROR"
    NO_LONGER_NEEDED = "NO_LONGER_NEEDED"
    OTHER = "OTHER"


class ManufacturerAcceptance(StrEnum):
    """Whether the manufacturer will take a special-order or non-stock item back."""

    UNKNOWN = "UNKNOWN"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class FeeAmountSource(StrEnum):
    """Where a fee amount may legitimately come from (baseline 3).

    There is no fourth member and there is deliberately no `POLICY_DEFAULT`:
    Ferguson publishes no universal restocking percentage, so the engine has
    nowhere to read one from and must not invent one.
    """

    SELLER_CONFIGURATION = "SELLER_CONFIGURATION"
    SELLER_OVERRIDE = "SELLER_OVERRIDE"
    MANUFACTURER = "MANUFACTURER"


class OverrideAuthority(StrEnum):
    """Who authorised a departure from the standard public rule (baseline 1, 17)."""

    CUSTOMER_CONTRACT = "CUSTOMER_CONTRACT"
    MANUFACTURER = "MANUFACTURER"
    SELLER_OVERRIDE = "SELLER_OVERRIDE"


class PolicyExceptionCode(StrEnum):
    """The kinds of exception a decision may carry."""

    CUSTOMER_CONTRACT_OVERRIDE = "CUSTOMER_CONTRACT_OVERRIDE"
    MANUFACTURER_POLICY = "MANUFACTURER_POLICY"
    SELLER_FEE_WAIVER = "SELLER_FEE_WAIVER"


class StockClassificationDefault(StrEnum):
    """How to resolve stocked-vs-special-order when the source cannot say.

    Verified 2026-08-15: no field in Ferguson's `salesInv` extract carries the
    distinction. `lineType` is undocumented in all four vendor dictionaries, and
    its `MP` majority contains 89 procure-to-order lines (`taggedPoId` with
    `prodAvailQty == 0`) sitting exactly on the "normally stocked" seam -- so
    `MP` cannot be read as "stocked" without converting 89 unknowns into
    approval evidence.

    The classification therefore comes from the operator, not from the data, and
    this enum is how they say it. `REVIEW_REQUIRED` is the model default because
    it is what the baseline's own safety rule specifies; a deployment that would
    rather move returns than queue them sets `STANDARD_STOCK` knowingly, in a
    released policy, and every decision taken that way records
    `STOCK_CLASS_FROM_CONFIGURATION` so an auditor can tell an assumption from a
    fact.
    """

    #: Unclassified lines are ordinary stocked goods. Returns flow.
    STANDARD_STOCK = "STANDARD_STOCK"
    #: Unclassified lines take the manufacturer-acceptance path.
    SPECIAL_ORDER = "SPECIAL_ORDER"
    #: Leave it unknown, so the fail-safe queues the return for a human.
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class UnstatedConditionFacts(StrEnum):
    """What a resale-condition or prohibited-state fact nobody stated may decide.

    The sibling of `StockClassificationDefault`, and for the same reason: the
    question is not what the rule says, it is what silence is allowed to mean,
    and only an operator can answer that for a deployment.

    Neither member weakens a fact that *was* stated. A known-failing condition
    -- an item the customer said was installed -- still rejects under both, and
    still rejects before the window is looked at, because "it was installed" is
    a better answer than "it was also late".

    `REVIEW_REQUIRED` is the model default because it is the baseline's own
    safety rule and because it is what this evaluator has always done.
    `NOT_EVALUATED` is the operator narrowing the decision to the facts the
    platform can actually source: every check left unevaluated travels on the
    outcome, and `PolicyRule.CONDITION_FACTS_NOT_EVALUATED` travels with it, so
    an approval taken without condition evidence is never indistinguishable
    from one taken with it.
    """

    #: Silence queues the return. Nothing approves until the facts are captured.
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    #: Silence decides nothing. The check is recorded as unevaluated and the
    #: remaining rules -- in practice, the return window -- decide the case.
    NOT_EVALUATED = "NOT_EVALUATED"


class PolicyCondition(StrEnum):
    """Something that must hold for the decision to stand as issued.

    `RESTOCKING_FEE_APPLIES` is the whole reason this list exists: the engine
    can determine *that* a fee applies without being able to determine what it
    is, and saying so is honest where naming a percentage would be fabrication.
    """

    RESTOCKING_FEE_APPLIES = "RESTOCKING_FEE_APPLIES"
    RESTOCKING_FEE_WAIVED = "RESTOCKING_FEE_WAIVED"
    MANUFACTURER_FEE_ACCEPTED = "MANUFACTURER_FEE_ACCEPTED"
    MANUFACTURER_FEE_NOT_APPLICABLE = "MANUFACTURER_FEE_NOT_APPLICABLE"


class PolicyRule(StrEnum):
    """The named rules an evaluation applied, persisted as provenance (baseline 16)."""

    POLICY_RELEASE_VALIDATED = "POLICY_RELEASE_VALIDATED"
    CUSTOMER_CONTRACT_OVERRIDE = "CUSTOMER_CONTRACT_OVERRIDE"
    DELIVERY_CLAIM_ROUTING = "DELIVERY_CLAIM_ROUTING"
    WARRANTY_ROUTING = "WARRANTY_ROUTING"
    DAMAGE_CAUSE_ROUTING = "DAMAGE_CAUSE_ROUTING"
    SPECIAL_ORDER_MANUFACTURER_POLICY = "SPECIAL_ORDER_MANUFACTURER_POLICY"
    STANDARD_STOCK_ITEM = "STANDARD_STOCK_ITEM"
    NEW_RESALEABLE_CONDITION = "NEW_RESALEABLE_CONDITION"
    WITHIN_30_DAYS = "WITHIN_30_DAYS"
    OUTSIDE_STANDARD_WINDOW = "OUTSIDE_STANDARD_WINDOW"
    RESTOCKING_FEE_APPLIES = "RESTOCKING_FEE_APPLIES"
    STOCK_CLASS_FROM_CONFIGURATION = "STOCK_CLASS_FROM_CONFIGURATION"
    #: One or more resale-condition or prohibited-state checks were not applied,
    #: because the facts they read were never stated and the release configures
    #: `UnstatedConditionFacts.NOT_EVALUATED`. The names of those checks are on
    #: `PolicyOutcome.unevaluated_checks`, and this rule may not appear without
    #: them -- the marker and the list are constructed together or not at all.
    CONDITION_FACTS_NOT_EVALUATED = "CONDITION_FACTS_NOT_EVALUATED"
    FAIL_SAFE_REVIEW = "FAIL_SAFE_REVIEW"


class PolicyReasonCode(StrEnum):
    """Why the evaluation reached the answer it did."""

    # Standard stocked path
    WITHIN_STANDARD_RETURN_WINDOW = "WITHIN_STANDARD_RETURN_WINDOW"
    OUTSIDE_STANDARD_RETURN_WINDOW = "OUTSIDE_STANDARD_RETURN_WINDOW"
    PURCHASE_DATE_UNKNOWN = "PURCHASE_DATE_UNKNOWN"
    DELIVERY_DATE_UNKNOWN = "DELIVERY_DATE_UNKNOWN"
    STANDARD_RETURN_CONDITION_FAILED = "STANDARD_RETURN_CONDITION_FAILED"
    REQUIRED_FACT_UNKNOWN = "REQUIRED_FACT_UNKNOWN"

    # Path discrimination
    SPECIAL_ORDER_STATUS_UNKNOWN = "SPECIAL_ORDER_STATUS_UNKNOWN"
    STOCK_STATUS_UNKNOWN = "STOCK_STATUS_UNKNOWN"

    # Damage cause
    DAMAGE_CAUSE_UNKNOWN = "DAMAGE_CAUSE_UNKNOWN"

    # Special-order / non-stock path
    MANUFACTURER_ACCEPTANCE_REQUIRED = "MANUFACTURER_ACCEPTANCE_REQUIRED"
    MANUFACTURER_ACCEPTED_RETURN = "MANUFACTURER_ACCEPTED_RETURN"
    MANUFACTURER_REJECTED_RETURN = "MANUFACTURER_REJECTED_RETURN"
    MANUFACTURER_FEE_UNKNOWN = "MANUFACTURER_FEE_UNKNOWN"
    BUYER_FEE_ACCEPTANCE_UNKNOWN = "BUYER_FEE_ACCEPTANCE_UNKNOWN"
    BUYER_REJECTED_MANUFACTURER_FEE = "BUYER_REJECTED_MANUFACTURER_FEE"

    # Routed paths
    DELIVERY_CLAIM_WITHIN_REPORTING_WINDOW = "DELIVERY_CLAIM_WITHIN_REPORTING_WINDOW"
    DELIVERY_CLAIM_REPORTING_WINDOW_ELAPSED = "DELIVERY_CLAIM_REPORTING_WINDOW_ELAPSED"
    DELIVERY_CLAIM_REPORTING_WINDOW_UNDETERMINED = "DELIVERY_CLAIM_REPORTING_WINDOW_UNDETERMINED"
    WARRANTY_VERIFICATION_REQUIRED = "WARRANTY_VERIFICATION_REQUIRED"

    # Override and fallthrough
    CUSTOMER_CONTRACT_OVERRIDE_REQUIRES_REVIEW = "CUSTOMER_CONTRACT_OVERRIDE_REQUIRES_REVIEW"
    NO_RULE_MATCHED = "NO_RULE_MATCHED"


class ReportingWindowState(StrEnum):
    """Whether a delivery claim was raised inside its reporting window."""

    WITHIN = "WITHIN"
    ELAPSED = "ELAPSED"
    UNDETERMINED = "UNDETERMINED"


#: Reason codes that make `APPROVE` unconstructable.
#:
#: This is the structural half of "never infer APPROVE". The evaluator is
#: written not to produce these together, and `PolicyOutcome` refuses the
#: combination anyway -- so a later edit that reorders a branch fails a
#: constructor rather than shipping an approval nobody can justify.
APPROVAL_FORBIDDING_REASON_CODES: frozenset[PolicyReasonCode] = frozenset(
    {
        PolicyReasonCode.OUTSIDE_STANDARD_RETURN_WINDOW,
        PolicyReasonCode.PURCHASE_DATE_UNKNOWN,
        PolicyReasonCode.DELIVERY_DATE_UNKNOWN,
        PolicyReasonCode.STANDARD_RETURN_CONDITION_FAILED,
        PolicyReasonCode.REQUIRED_FACT_UNKNOWN,
        PolicyReasonCode.SPECIAL_ORDER_STATUS_UNKNOWN,
        PolicyReasonCode.STOCK_STATUS_UNKNOWN,
        PolicyReasonCode.DAMAGE_CAUSE_UNKNOWN,
        PolicyReasonCode.MANUFACTURER_ACCEPTANCE_REQUIRED,
        PolicyReasonCode.MANUFACTURER_REJECTED_RETURN,
        PolicyReasonCode.MANUFACTURER_FEE_UNKNOWN,
        PolicyReasonCode.BUYER_FEE_ACCEPTANCE_UNKNOWN,
        PolicyReasonCode.BUYER_REJECTED_MANUFACTURER_FEE,
        PolicyReasonCode.CUSTOMER_CONTRACT_OVERRIDE_REQUIRES_REVIEW,
        PolicyReasonCode.NO_RULE_MATCHED,
    }
)
