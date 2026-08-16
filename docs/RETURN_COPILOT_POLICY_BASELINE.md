# Return Copilot — Return Eligibility Policy Baseline

**Status:** Authoritative input for Phase 3A. Supersedes nothing; there was no prior rule set.
**Authority:** Ferguson's current public Returns and Cancellations Policy and Terms and
Conditions of Sale (Rev. May 2025)
**Consumed by:** [`RETURN_COPILOT_REMEDIATION_PLAN.md`](RETURN_COPILOT_REMEDIATION_PLAN.md) §7
**Authored:** 2026-08-15

**Purpose:** Close the Return Copilot Phase 3A policy-rule dependency using Ferguson's current
public standard return policy as the baseline authority.

**Two corrections applied since this was written**, both recorded in the remediation plan and
authoritative over the text below:

1. **Warranty is not a terminal route.** Support verifies warranty inside this application; the
   case goes to `AWAITING_SUPPORT` and rejoins the normal RMA lifecycle. §7 below describes only
   the *eligibility* routing, not the fulfilment path.
2. **Delivery claims are likewise verified by Support**, in the same shape. §6's
   `standard_return_decision: NOT_APPLICABLE` remains correct — it is expressed as
   `PolicyOutcome.route` with a null decision, not as a fourth `EligibilityDecision` value.

---

## 1. Policy Authority

Use Ferguson's current public:

- **Returns and Cancellations Policy**
- **Terms and Conditions of Sale**

as the initial authoritative baseline for deterministic return eligibility.

Internal negotiated customer agreements, manufacturer-specific rules, and approved Ferguson exceptions must remain configurable higher-priority overrides.

No unsupported business rule may be invented.

---

## 2. Confirmed Ferguson Standard Return Rules

### 2.1 Standard stocked-item return

A standard stocked, non-special-order product can be returned within **30 days of purchase** when all applicable conditions are satisfied:

- Product is new.
- Product is suitable for resale.
- Product is in original packaging.
- Original packaging is undamaged.
- All original parts are present.
- Product has not been used.
- Product has not been installed.
- Product has not been modified.
- Product has not been rebuilt.
- Product has not been reconditioned.
- Product has not been repaired.
- Product has not been altered.
- Product has not been damaged.

When these conditions are satisfied, the deterministic policy result can be:

```text
APPROVE
```

subject to the applicable restocking-fee rule.

---

## 3. Restocking Fee

Ferguson's public policy states that returns are subject to a restocking fee unless Ferguson agrees otherwise.

However, Ferguson's public policy does **not** publish a universal standard restocking-fee percentage.

Therefore the application must never invent values such as:

```text
10%
15%
20%
25%
```

or any fixed amount.

The deterministic engine may determine:

```text
RESTOCKING_FEE_APPLIES
```

but the actual fee amount must come from one of:

```text
SELLER_CONFIGURATION
SELLER_OVERRIDE
MANUFACTURER
```

The seller may waive the fee where an authorized Ferguson rule or override permits it.

---

## 4. Special-Order / Non-Stock Products

Special-order or non-stock products have a different eligibility path.

Return requires manufacturer acceptance.

If the manufacturer imposes restocking or cancellation fees, the buyer must accept the applicable fee before the return can be automatically approved.

Decision model:

```text
manufacturer acceptance unknown
→ REVIEW_REQUIRED

manufacturer rejects return
→ REJECT

manufacturer accepts return
+ fee applicability/amount unknown
→ REVIEW_REQUIRED

manufacturer accepts return
+ required fee presented
+ buyer rejects fee
→ REJECT

manufacturer accepts return
+ buyer accepts required fee
→ APPROVE
```

---

## 5. Outside the Standard 30-Day Window

Do not automatically reject every return after 30 days.

Ferguson's public standard establishes the normal 30-day return window, but does not establish that Ferguson can never authorize an exception.

Therefore:

```text
purchase age > 30 days
→ REVIEW_REQUIRED
→ OUTSIDE_STANDARD_RETURN_WINDOW
```

Do not:

```text
> 30 days
→ automatic APPROVE
```

and do not:

```text
> 30 days
→ automatic REJECT
```

unless a more specific authoritative Ferguson/customer contract rule explicitly requires it.

---

## 6. Delivery Claims Are Not Standard Returns

Damage, shortage, shipment errors, and improper delivery are a separate business path.

Ferguson's current Terms of Sale provide a separate reporting window for these delivery-related issues.

Examples:

```text
SHIPPING_DAMAGE
SHORTAGE
SHIPMENT_ERROR
IMPROPER_DELIVERY
```

These should route to:

```text
DELIVERY_CLAIM
```

rather than being evaluated as a standard 30-day return.

The standard return decision for such cases should be:

```text
NOT_APPLICABLE
```

The public Ferguson Terms state a reporting window of **2 business days from delivery** for these claims.

---

## 7. Warranty Issues Are Not Standard Returns

A product defect after use or installation must not automatically be treated as a failed standard return.

Examples:

```text
product failed after installation
manufacturing defect
covered private-label defect
manufacturer warranty issue
```

These should route to:

```text
WARRANTY
```

rather than being rejected simply because the product has been used or installed.

Ferguson's current Terms distinguish warranty remedies from ordinary returns.

---

## 8. Damage-Cause Routing

Do not implement:

```text
damaged = true
→ REJECT
```

because the cause matters.

Use:

```text
customer/use damage
→ fails standard return condition
→ REJECT

shipping damage
→ DELIVERY_CLAIM

manufacturer/product defect
→ WARRANTY

damage cause unknown
→ REVIEW_REQUIRED
```

This prevents incorrectly rejecting shipping claims or warranty issues as ordinary returns.

---

# 9. Deterministic Policy Configuration

Use a versioned policy configuration.

```yaml
return_eligibility_policy:
  id: ferguson-standard-return-policy
  version: "2026-08-15"
  authority: FERGUSON_PUBLIC_TERMS
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
    condition: PURCHASE_AGE_DAYS > 30
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

    action: ROUTE_TO_DELIVERY_CLAIM
    standard_return_decision: NOT_APPLICABLE

  warranty_issue:
    action: ROUTE_TO_WARRANTY
    standard_return_decision: NOT_APPLICABLE
```

---

# 10. Required Policy Evaluation Input

The deterministic evaluator needs actual return-policy facts.

```python
PolicyEvaluationInput(
    purchase_date,
    delivery_date,
    request_date,

    seller_stocked,
    special_order,
    non_stock,

    quantity,

    condition_new,
    suitable_for_resale,
    original_packaging,
    packaging_undamaged,
    all_original_parts,

    used,
    installed,
    modified,
    rebuilt,
    reconditioned,
    repaired,
    altered,
    damaged,

    damage_cause,

    return_reason,

    manufacturer_return_acceptance,
    manufacturer_restocking_fee,
    manufacturer_cancellation_fee,
    buyer_accepts_manufacturer_fees,

    seller_restocking_fee,
    seller_fee_waiver,

    contract_override_reference,

    policy_version,
    configuration_release_id,
)
```

---

# 11. Unsupported Baseline Factors — Remove

Do not include unsupported rules in the baseline evaluator unless an authoritative Ferguson source or internal contract supplies them.

Remove/avoid:

```text
customerTier
priorReturnCount
product-class-specific return windows
arbitrary value thresholds
hardcoded restocking percentages
unsupported reason-code eligibility matrices
```

The public standard policy does not establish these as general eligibility rules.

---

# 12. Deterministic Evaluator Precedence

Evaluate in this order:

```text
1. Validate policy release.
2. Apply explicit customer/contract override, if one exists.
3. Detect delivery-claim path.
4. Detect warranty path.
5. Detect special-order/non-stock path.
6. Evaluate standard stocked-item 30-day policy.
7. Resolve restocking-fee applicability.
8. Otherwise REVIEW_REQUIRED.
```

Important safety rule:

```text
NO MATCH
MISSING REQUIRED FACT
AMBIGUOUS DAMAGE CAUSE
UNKNOWN MANUFACTURER ACCEPTANCE
UNKNOWN SPECIAL-ORDER STATUS

→ REVIEW_REQUIRED
```

Never infer `APPROVE`.

---

# 13. Deterministic Policy Authority

The deterministic evaluator is the only producer of executable eligibility decisions:

```text
APPROVE
REJECT
REVIEW_REQUIRED
```

Correct boundary:

```text
conversation
    ↓
LLM extraction
    ↓
structured return facts
    ↓
deterministic Ferguson policy evaluator
    ↓
APPROVE / REJECT / REVIEW_REQUIRED
    ↓
optional AI explanation/advisory
```

The LLM must not be the policy authority.

---

# 14. AI Advisory Contract

The AI advisory must contain no executable decision-shaped field.

Use:

```json
{
  "advisory": {
    "missingFacts": [],
    "ambiguities": [],
    "explanation": "...",
    "suggestedHumanReview": false,
    "modelProvider": "...",
    "modelName": "..."
  }
}
```

Do not include:

```text
recommendation = APPROVE
recommendation = REJECT
decision = APPROVE
decision = REJECT
```

The deterministic policy evaluator remains the only source of the final policy decision.

---

# 15. Example Outcomes

## Example A — Standard stocked item inside window

```text
Stock item
Purchased 18 days ago
New
Original undamaged packaging
All parts present
Never installed
Never used
Never modified
```

Result:

```text
APPROVE
RESTOCKING_FEE_APPLIES
fee amount supplied separately
```

## Example B — Outside standard window

```text
Stock item
Purchased 42 days ago
Otherwise perfect condition
```

Result:

```text
REVIEW_REQUIRED
OUTSIDE_STANDARD_RETURN_WINDOW
```

## Example C — Special-order product, manufacturer unknown

```text
Special-order valve
Manufacturer acceptance unknown
```

Result:

```text
REVIEW_REQUIRED
MANUFACTURER_ACCEPTANCE_REQUIRED
```

## Example D — Special-order product accepted with fee

```text
Special-order valve
Manufacturer accepts return
Manufacturer restocking fee = $75
Customer accepts fee
```

Result:

```text
APPROVE
MANUFACTURER_RESTOCKING_FEE = $75
```

## Example E — Used/installed normal return

```text
Product installed
Customer changed mind
No shipping damage
No warranty defect
```

Result:

```text
REJECT
STANDARD_RETURN_CONDITION_FAILED
```

## Example F — Shipping damage

```text
Product arrived damaged yesterday
```

Result:

```text
ROUTE_TO_DELIVERY_CLAIM
standard return decision = NOT_APPLICABLE
```

Apply the Ferguson delivery-claim reporting-window rules.

## Example G — Warranty issue

```text
Pump failed after installation
```

Result:

```text
ROUTE_TO_WARRANTY
standard return decision = NOT_APPLICABLE
```

Evaluate the applicable Ferguson/private-label or manufacturer warranty separately.

---

# 16. Policy Provenance

Every deterministic decision must persist the exact policy release and applied rules.

```json
{
  "decision": "APPROVE",
  "source": "FERGUSON_POLICY_ENGINE",
  "policyId": "ferguson-standard-return-policy",
  "policyVersion": "2026-08-15",
  "sourceDocument": "Ferguson Terms and Conditions of Sale",
  "sourceRevision": "May 2025",
  "evaluatedAt": "...",
  "appliedRules": [
    "STANDARD_STOCK_ITEM",
    "WITHIN_30_DAYS",
    "NEW_RESALEABLE_CONDITION",
    "RESTOCKING_FEE_APPLIES"
  ]
}
```

Also retain:

```text
configurationReleaseId
customerContractOverrideReference
manufacturerPolicyReference
override history
actor
effectiveDecision
originalDecision
```

where applicable.

---

# 17. Policy Override Precedence

Use this precedence:

```text
1. Explicit customer/contract override
2. Manufacturer-specific special-order rule
3. Ferguson standard public return policy
4. REVIEW_REQUIRED
```

An override must never silently destroy the original policy result.

Persist:

```text
originalDecision
effectiveDecision
override actor
override reasonCode
override reason
override timestamp
```

---

# 18. Money Handling

All monetary policy values must use:

```text
Decimal
```

or:

```text
integer minor units
```

Never use binary floating point for:

```text
restocking fees
refund amounts
credits
manufacturer fees
```

---

# 19. Date and Time Handling

Return-window evaluation must use the customer/business-local date context used by the platform.

Explicitly test:

```text
exactly day 30
day 31
return raised at 23:50 local
DST boundary
delivery claims exactly at 2 business-day boundary
```

Do not implement eligibility using naive UTC subtraction if Ferguson policy is expressed in calendar/business-day terms.

---

# 20. Release Validation

Policy configuration must be versioned and validated before activation.

Invalid policy release:

```text
→ refuse activation
```

Never deploy an empty or malformed rule set that causes every case to become:

```text
REVIEW_REQUIRED
```

without an explicit operational failure.

Validate at least:

```text
rule priorities
required fields
window values
return-method references
fee-source references
condition matrices
reason-code references
manufacturer rule references
```

---

# 21. Plan Decision — Closed

Replace the previous open dependency:

```text
Who authors the return eligibility rule set?
```

with:

```text
CLOSED for baseline implementation.
```

Baseline authority:

```text
Ferguson's current public Returns and Cancellations Policy
+
Ferguson's current Terms and Conditions of Sale
```

Higher-priority conditional authority:

```text
Internal/customer-specific written agreements
Manufacturer-specific special-order policies
Authorized Ferguson overrides
```

Rules:

```text
No unsupported business rule may be invented.
No hardcoded fee percentage may be invented.
No unsupported product-class window may be invented.
No customer-tier threshold may be invented.
No prior-return threshold may be invented.
```

---

# 22. Phase 3A Is No Longer Blocked

Engineering can now implement:

```text
ReturnEligibilityPolicy schema
policy-release validation
deterministic evaluator
rule-priority framework
required-fact validation
APPROVE / REJECT / REVIEW_REQUIRED outcomes
delivery-claim routing
warranty routing
special-order manufacturer path
restocking-fee applicability
policy provenance
policy override integration
unit tests
```

Additional internal Ferguson/customer-specific rules can be layered later through configuration without changing the evaluator architecture.

---

# 23. Source Notes

The baseline above was derived from Ferguson's current public:

- Returns and Cancellations policy.
- Terms and Conditions of Sale.
- Terms of Sale revision identified on the public site as **Rev. May 2025**.

The public sources support:

- 30-day standard return window.
- New/resalable condition requirements.
- Original undamaged packaging.
- Original parts requirement.
- Exclusion of used/installed/modified/rebuilt/reconditioned/repaired/altered/damaged goods from the standard return path.
- Restocking fee applicability without a published universal fee percentage.
- Manufacturer acceptance for special/non-stock return paths.
- Separate treatment for shipping damage/shortage/improper delivery.
- Separate warranty remedies.

The public sources do **not** establish a universal:

- Restocking-fee percentage.
- Customer-tier eligibility rule.
- Prior-return-count threshold.
- Generic product-class-specific return window.
- Arbitrary value threshold.

Those must not be fabricated.
