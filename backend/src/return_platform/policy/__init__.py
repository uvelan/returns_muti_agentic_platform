"""Deterministic return-eligibility policy: the only producer of an executable decision.

```text
conversation -> LLM extraction -> structured return facts
             -> deterministic Ferguson policy evaluator
             -> APPROVE / REJECT / REVIEW_REQUIRED  (or a verification route)
             -> optional AI explanation, which carries no decision-shaped field
```

The AI gateway is advisory. It may say what is missing and what is ambiguous;
it may not decide. Everything in this package is pure -- no IO, no database, no
model call -- so the rule set is testable on its own, which is what the audit
found nothing had been.
"""

from return_platform.policy.eligibility_policy import (
    DeliveryClaimConfiguration,
    DeliveryClaimReportingWindow,
    OutsideStandardWindowConfiguration,
    ProhibitedStateRequirements,
    PurchaseWindowConfiguration,
    ResaleConditionRequirements,
    RestockingFeeConfiguration,
    ReturnEligibilityPolicy,
    SpecialOrderDecisions,
    SpecialOrNonStockConfiguration,
    StandardStockRequirements,
    StandardStockReturnConfiguration,
    WarrantyIssueConfiguration,
)
from return_platform.policy.evaluation_input import (
    EvidenceState,
    FeeDeclaration,
    PolicyEvaluationInput,
    PolicyFactEnvelope,
    admitted_value,
)
from return_platform.policy.evaluator import (
    PolicyClock,
    PolicyReleaseError,
    evaluate_return_eligibility,
    reporting_window_deadline,
)
from return_platform.policy.outcome import (
    DeliveryClaimWindow,
    FeeDetermination,
    PolicyException,
    PolicyOutcome,
    PolicyProvenance,
)
from return_platform.policy.vocabulary import (
    APPROVAL_FORBIDDING_REASON_CODES,
    DamageCause,
    EligibilityDecision,
    FeeAmountSource,
    ManufacturerAcceptance,
    MonetaryAmount,
    OverrideAuthority,
    PolicyCondition,
    PolicyExceptionCode,
    PolicyPrecedence,
    PolicyReasonCode,
    PolicyRoute,
    PolicyRule,
    ReportingWindowState,
    ReturnReason,
    ReturnWindowBasis,
    TriState,
    UnstatedConditionFacts,
)

__all__ = [
    "APPROVAL_FORBIDDING_REASON_CODES",
    "DamageCause",
    "DeliveryClaimConfiguration",
    "DeliveryClaimReportingWindow",
    "DeliveryClaimWindow",
    "EligibilityDecision",
    "EvidenceState",
    "FeeAmountSource",
    "FeeDeclaration",
    "FeeDetermination",
    "ManufacturerAcceptance",
    "MonetaryAmount",
    "OutsideStandardWindowConfiguration",
    "OverrideAuthority",
    "PolicyClock",
    "PolicyCondition",
    "PolicyEvaluationInput",
    "PolicyException",
    "PolicyExceptionCode",
    "PolicyFactEnvelope",
    "PolicyOutcome",
    "PolicyPrecedence",
    "PolicyProvenance",
    "PolicyReasonCode",
    "PolicyReleaseError",
    "PolicyRoute",
    "PolicyRule",
    "ProhibitedStateRequirements",
    "PurchaseWindowConfiguration",
    "ReportingWindowState",
    "ResaleConditionRequirements",
    "RestockingFeeConfiguration",
    "ReturnEligibilityPolicy",
    "ReturnReason",
    "ReturnWindowBasis",
    "SpecialOrNonStockConfiguration",
    "SpecialOrderDecisions",
    "StandardStockRequirements",
    "StandardStockReturnConfiguration",
    "TriState",
    "UnstatedConditionFacts",
    "WarrantyIssueConfiguration",
    "admitted_value",
    "evaluate_return_eligibility",
    "reporting_window_deadline",
]
