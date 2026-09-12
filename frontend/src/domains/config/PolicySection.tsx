import { useId, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { configApi, type PolicyPreviewResult } from "../../api/configuration";
import { EnumSelect, type EnumOption } from "../../components/forms/EnumSelect";
import { FieldGroup } from "../../components/forms/FieldGroup";
import { KeyValueTable, type KeyValueEntry } from "../../components/forms/KeyValueTable";
import { NumberField } from "../../components/forms/NumberField";
import { OrderedList } from "../../components/forms/OrderedList";
import { TagListInput } from "../../components/forms/TagListInput";
import { Toggle } from "../../components/forms/Toggle";
import { useCapabilities } from "../../hooks/capabilityContext";
import type { JsonObject } from "./DocumentEditor";
import {
  asBoolean,
  asNumber,
  asObject,
  asString,
  asStringArray,
  sliceOf,
} from "./jsonPath";
import { runtimeSliceOf, type RuntimeSlice } from "./runtimeSlice";
import { TypedSectionScreen } from "./TypedSectionScreen";

/**
 * `/config/policy` -- one switch, defaults for all products, exceptions, and
 * a decision preview (CFG-8).
 *
 * Every field here already existed inside `/config/return-policy`
 * (`ReturnPolicySection.tsx`), rendered below the method-derivation groups.
 * This screen is the same two domain keys -- `return_eligibility_policy`,
 * `policy_evaluation` -- shaped around how an operator thinks about policy
 * ("a 30-day return window, for all products, with exceptions") rather than
 * around the return-method-derivation groups that stay behind.
 * `return_policy` and `return_method_requirements` are `ReturnPolicySection`'s
 * alone now; nothing here reads or writes them.
 */

const SECTION_KEYS = ["return_eligibility_policy", "policy_evaluation"] as const;

const ELIGIBILITY_DECISIONS: readonly EnumOption[] = [
  { value: "APPROVE", label: "Approve" },
  { value: "REJECT", label: "Reject" },
  { value: "REVIEW_REQUIRED", label: "Review required" },
];

const OUTSIDE_WINDOW_DECISIONS: readonly EnumOption[] = [
  { value: "REJECT", label: "Reject" },
  { value: "REVIEW_REQUIRED", label: "Review required" },
];

const RETURN_WINDOW_BASES: readonly EnumOption[] = [
  { value: "PURCHASE_DATE", label: "Purchase date" },
  { value: "DELIVERY_DATE", label: "Delivery date" },
];

const UNSTATED_CONDITION_FACTS_OPTIONS: readonly EnumOption[] = [
  { value: "REVIEW_REQUIRED", label: "Review required" },
  { value: "NOT_EVALUATED", label: "Not evaluated" },
];

const STOCK_CLASSIFICATION_DEFAULTS: readonly EnumOption[] = [
  { value: "STANDARD_STOCK", label: "Standard stock" },
  { value: "SPECIAL_ORDER", label: "Special order" },
  { value: "REVIEW_REQUIRED", label: "Review required" },
];

const POLICY_CONDITION_SUGGESTIONS = [
  "RESTOCKING_FEE_APPLIES",
  "RESTOCKING_FEE_WAIVED",
  "MANUFACTURER_FEE_ACCEPTED",
  "MANUFACTURER_FEE_NOT_APPLICABLE",
];

const FEE_AMOUNT_SOURCE_LABEL: Readonly<Record<string, string>> = {
  SELLER_CONFIGURATION: "Seller's own schedule",
  SELLER_OVERRIDE: "Seller override (this case)",
  MANUFACTURER: "Manufacturer",
};

const RETURN_REASON_SUGGESTIONS = [
  "SHIPPING_DAMAGE",
  "SHORTAGE",
  "SHIPMENT_ERROR",
  "IMPROPER_DELIVERY",
  "MANUFACTURING_DEFECT",
  "PRODUCT_FAILURE_AFTER_INSTALLATION",
  "COVERED_PRIVATE_LABEL_DEFECT",
  "MANUFACTURER_WARRANTY_ISSUE",
  "CHANGED_MIND",
  "ORDERED_IN_ERROR",
  "NO_LONGER_NEEDED",
  "OTHER",
];

/** Block 3's checklist, in the baseline's own order (`resale_condition_facts`/`prohibited_state_facts`). */
const CONDITION_CHECKS: readonly { field: string; label: string }[] = [
  { field: "new", label: "New" },
  { field: "suitable_for_resale", label: "Suitable for resale" },
  { field: "original_packaging", label: "In original packaging" },
  { field: "packaging_undamaged", label: "Packaging undamaged" },
  { field: "all_original_parts", label: "All original parts present" },
];

const PROHIBITED_CHECKS: readonly { field: string; label: string }[] = [
  { field: "used", label: "Used" },
  { field: "installed", label: "Installed" },
  { field: "modified", label: "Modified" },
  { field: "rebuilt", label: "Rebuilt" },
  { field: "reconditioned", label: "Reconditioned" },
  { field: "repaired", label: "Repaired" },
  { field: "altered", label: "Altered" },
  { field: "damaged", label: "Damaged" },
];

export function PolicySection() {
  const { can } = useCapabilities();
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });

  if (runtime.isPending) return <p className="text-sm text-on-surface-variant">Loading...</p>;
  if (runtime.error !== null) {
    return <p role="alert" className="text-sm text-error">{runtime.error.message}</p>;
  }

  const active = runtimeSliceOf(runtime.data);
  return (
    <PolicyEditor
      key={active.releaseId}
      active={active}
      canWrite={can("config.release.write")}
      canPublish={can("config.release.promote")}
    />
  );
}

function PolicyEditor({
  active,
  canWrite,
  canPublish,
}: {
  active: RuntimeSlice;
  canWrite: boolean;
  canPublish: boolean;
}) {
  const loaded = sliceOf(active.configuration, SECTION_KEYS);
  // Item B: the packaged RETURN_PLATFORM document, for "Reset to packaged
  // default". A read query, not tied to `active` -- the packaged file does
  // not change with the release, and a failed or slow fetch simply hides the
  // reset links rather than blocking the screen (`packaged.data` stays
  // `undefined`).
  const packaged = useQuery({
    queryKey: ["config", "packaged", "RETURN_PLATFORM"],
    queryFn: () => configApi.packagedDomain("RETURN_PLATFORM"),
  });
  const packagedStandard = asObject(
    asObject(asObject(packaged.data as JsonObject | undefined).return_eligibility_policy).standard_stock_return,
  );

  return (
    <TypedSectionScreen
      kicker="Policy"
      title="Policy"
      description="Whether eligibility is evaluated, the default rule for every product, what the item must be, and the exceptions."
      active={active}
      loaded={loaded}
      canWrite={canWrite}
      canPublish={canPublish}
      jsonLabel="Policy section JSON"
      notObjectMessage="Each of return_eligibility_policy and policy_evaluation must be an object."
      renderTyped={({ get, set, errorMap }) => {
        const eligibility = asObject(get(["return_eligibility_policy"]));
        const policyEvaluation = asObject(get(["policy_evaluation"]));
        const standard = asObject(eligibility.standard_stock_return);
        const purchaseWindow = asObject(standard.purchase_window);
        const requirements = asObject(standard.requirements);
        const condition = asObject(requirements.condition);
        const prohibited = asObject(requirements.prohibited_states);
        const restockingFee = asObject(eligibility.restocking_fee);
        const sellerSchedule = asObject(restockingFee.seller_schedule);
        const stockClassification = asObject(eligibility.stock_classification);
        const specialOrNonstock = asObject(eligibility.special_or_nonstock);
        const decisions = asObject(specialOrNonstock.decisions);
        const outsideWindow = asObject(eligibility.outside_standard_window);
        const deliveryClaim = asObject(eligibility.delivery_claim);
        const warrantyIssue = asObject(eligibility.warranty_issue);

        return (
          <div className="flex flex-col gap-4">
            {/* 1. Policy evaluation */}
            <FieldGroup
              kicker="Policy"
              title="Policy evaluation"
              description="Whether every return is judged against the rules below before the case proceeds."
            >
              <Toggle
                label="Policy evaluation enabled"
                value={asBoolean(policyEvaluation.enabled, true)}
                onChange={(next) => {
                  set(["policy_evaluation", "enabled"], next);
                  if (next) set(["policy_evaluation", "disabled_reason"], null);
                }}
                reasonField={{
                  value: asString(policyEvaluation.disabled_reason),
                  onChange: (next) => { set(["policy_evaluation", "disabled_reason"], next); },
                  requiredWhen: "off",
                }}
              />
              <p className="text-sm text-on-surface-variant">
                {asBoolean(policyEvaluation.enabled, true)
                  ? "Every return is judged against the rules below before the case proceeds."
                  : /*
                     * The exact fact names `evaluate_case_eligibility`
                     * (`workflows/return_case_activities.py`) writes when the
                     * gate is off: `policy_evaluation_state =
                     * SKIPPED_BY_CONFIGURATION` and
                     * `policy_evaluation_skip_reason` -- never
                     * `CONDITION_FACTS_NOT_EVALUATED`, which is a different
                     * mechanism entirely (an *enabled* gate whose window
                     * decides a case over unstated facts, below).
                     */
                    "No eligibility decision is recorded; the case proceeds and carries policy_evaluation_state = SKIPPED_BY_CONFIGURATION, with the stated reason attached as policy_evaluation_skip_reason."}
              </p>
            </FieldGroup>

            {/* 2. Defaults for all products */}
            <FieldGroup
              kicker="Standard stock return"
              title="Defaults for all products"
              description="The default rule every product is judged against: an item in policy-satisfying condition, inside the purchase window."
            >
              <div className="flex flex-wrap items-end gap-3">
                <NumberField
                  label="Return window"
                  value={asNumber(purchaseWindow.days, 30)}
                  onChange={(next) => {
                    set(["return_eligibility_policy", "standard_stock_return", "purchase_window", "days"], next);
                  }}
                  min={1}
                  max={3650}
                  unit="days"
                  error={errorMap.get("return_eligibility_policy.standard_stock_return.purchase_window.days")}
                />
                <ResetToPackagedLink
                  visible={
                    packaged.data !== undefined
                    && asNumber(purchaseWindow.days, 30)
                      !== asNumber(asObject(packagedStandard.purchase_window).days, 30)
                  }
                  onClick={() => {
                    set(
                      ["return_eligibility_policy", "standard_stock_return", "purchase_window", "days"],
                      asNumber(asObject(packagedStandard.purchase_window).days, 30),
                    );
                  }}
                />
              </div>
              <div className="flex flex-wrap items-end gap-3">
                <EnumSelect
                  label="Return window basis"
                  value={asString(purchaseWindow.basis, "PURCHASE_DATE")}
                  options={RETURN_WINDOW_BASES}
                  onChange={(next) => {
                    set(["return_eligibility_policy", "standard_stock_return", "purchase_window", "basis"], next);
                  }}
                />
                <ResetToPackagedLink
                  visible={
                    packaged.data !== undefined
                    && asString(purchaseWindow.basis, "PURCHASE_DATE")
                      !== asString(asObject(packagedStandard.purchase_window).basis, "PURCHASE_DATE")
                  }
                  onClick={() => {
                    set(
                      ["return_eligibility_policy", "standard_stock_return", "purchase_window", "basis"],
                      asString(asObject(packagedStandard.purchase_window).basis, "PURCHASE_DATE"),
                    );
                  }}
                />
              </div>
              <div className="flex flex-wrap items-end gap-3">
                <EnumSelect
                  label="Decision when satisfied"
                  hint="The model refuses REJECT here -- a satisfied standard return is never an outright rejection."
                  value={asString(standard.decision_when_satisfied, "APPROVE")}
                  options={ELIGIBILITY_DECISIONS}
                  onChange={(next) => {
                    set(["return_eligibility_policy", "standard_stock_return", "decision_when_satisfied"], next);
                  }}
                  error={errorMap.get("return_eligibility_policy.standard_stock_return.decision_when_satisfied")}
                />
                <ResetToPackagedLink
                  visible={
                    packaged.data !== undefined
                    && asString(standard.decision_when_satisfied, "APPROVE")
                      !== asString(packagedStandard.decision_when_satisfied, "APPROVE")
                  }
                  onClick={() => {
                    set(
                      ["return_eligibility_policy", "standard_stock_return", "decision_when_satisfied"],
                      asString(packagedStandard.decision_when_satisfied, "APPROVE"),
                    );
                  }}
                />
              </div>
              <div className="flex flex-wrap items-end gap-3">
                <EnumSelect
                  label="What an unstated condition fact decides"
                  hint="Review required: silence queues the return until every check above is answered. Not evaluated: silence decides nothing and the return window decides instead -- the outcome carries CONDITION_FACTS_NOT_EVALUATED and names the checks it skipped. A fact somebody DID state still decides, either way."
                  value={asString(standard.unstated_condition_facts, "REVIEW_REQUIRED")}
                  options={UNSTATED_CONDITION_FACTS_OPTIONS}
                  onChange={(next) => {
                    set(["return_eligibility_policy", "standard_stock_return", "unstated_condition_facts"], next);
                  }}
                />
                <ResetToPackagedLink
                  visible={
                    packaged.data !== undefined
                    && asString(standard.unstated_condition_facts, "REVIEW_REQUIRED")
                      !== asString(packagedStandard.unstated_condition_facts, "REVIEW_REQUIRED")
                  }
                  onClick={() => {
                    set(
                      ["return_eligibility_policy", "standard_stock_return", "unstated_condition_facts"],
                      asString(packagedStandard.unstated_condition_facts, "REVIEW_REQUIRED"),
                    );
                  }}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <TagListInput
                  label="Conditions attached to approval"
                  hint="What travels alongside an approval -- from the model's own vocabulary (PolicyCondition); an unrecognised value is refused at Validate."
                  values={asStringArray(standard.conditions)}
                  onChange={(next) => {
                    set(["return_eligibility_policy", "standard_stock_return", "conditions"], next);
                  }}
                  suggestions={POLICY_CONDITION_SUGGESTIONS}
                  error={errorMap.get("return_eligibility_policy.standard_stock_return.conditions")}
                />
                <ResetToPackagedLink
                  visible={
                    packaged.data !== undefined
                    && JSON.stringify(asStringArray(standard.conditions))
                      !== JSON.stringify(asStringArray(packagedStandard.conditions))
                  }
                  onClick={() => {
                    set(
                      ["return_eligibility_policy", "standard_stock_return", "conditions"],
                      asStringArray(packagedStandard.conditions),
                    );
                  }}
                />
              </div>
            </FieldGroup>

            {/* 3. What the item must be */}
            <FieldGroup
              kicker="Standard stock return"
              title="What the item must be"
              description="Every check a standard return is judged against. Turning one off stops the check being applied at all -- it does not relax it."
            >
              <div>
                <p className="premium-kicker">Condition</p>
                <div className="mt-1.5 flex flex-col gap-2">
                  {CONDITION_CHECKS.map(({ field, label }) => (
                    <Toggle
                      key={field}
                      label={label}
                      hint={
                        asBoolean(condition[field], true)
                          ? "Required for a standard return."
                          : "Not required -- this check is skipped."
                      }
                      value={asBoolean(condition[field], true)}
                      onChange={(next) => {
                        set(
                          ["return_eligibility_policy", "standard_stock_return", "requirements", "condition", field],
                          next,
                        );
                      }}
                    />
                  ))}
                </div>
              </div>
              <div>
                <p className="premium-kicker">Prohibited states</p>
                <div className="mt-1.5 flex flex-col gap-2">
                  {PROHIBITED_CHECKS.map(({ field, label }) => (
                    <Toggle
                      key={field}
                      label={label}
                      hint={
                        asBoolean(prohibited[field], false)
                          ? `Required to be true -- unusual: a standard return would have to be ${label.toLowerCase()}.`
                          : `Required to be false -- a standard return must not be ${label.toLowerCase()}.`
                      }
                      value={asBoolean(prohibited[field], false)}
                      onChange={(next) => {
                        set(
                          [
                            "return_eligibility_policy",
                            "standard_stock_return",
                            "requirements",
                            "prohibited_states",
                            field,
                          ],
                          next,
                        );
                      }}
                    />
                  ))}
                </div>
              </div>
              <div>
                <p className="premium-kicker">Stock and order type</p>
                <div className="mt-1.5 flex flex-col gap-2">
                  <Toggle
                    label="Seller-stocked"
                    hint={
                      asBoolean(requirements.seller_stocked, true)
                        ? "Required -- the item must be one the seller stocks."
                        : "Not required."
                    }
                    value={asBoolean(requirements.seller_stocked, true)}
                    onChange={(next) => {
                      set(
                        ["return_eligibility_policy", "standard_stock_return", "requirements", "seller_stocked"],
                        next,
                      );
                    }}
                  />
                  <Toggle
                    label="Special order"
                    hint={
                      asBoolean(requirements.special_order, false)
                        ? "Required to be a special order."
                        : "Required to NOT be a special order (the ordinary case)."
                    }
                    value={asBoolean(requirements.special_order, false)}
                    onChange={(next) => {
                      set(
                        ["return_eligibility_policy", "standard_stock_return", "requirements", "special_order"],
                        next,
                      );
                    }}
                  />
                </div>
              </div>
            </FieldGroup>

            {/* 4. Outside the window, and the restocking fee */}
            <FieldGroup
              kicker="Eligibility"
              title="Outside the standard window"
              description="What happens when a return is judged eligible in every way except timing. The model refuses APPROVE here -- outside-window is never auto-approved."
            >
              <EnumSelect
                label="Decision"
                value={asString(outsideWindow.decision, "REVIEW_REQUIRED")}
                options={OUTSIDE_WINDOW_DECISIONS}
                onChange={(next) => { set(["return_eligibility_policy", "outside_standard_window", "decision"], next); }}
                error={errorMap.get("return_eligibility_policy.outside_standard_window.decision")}
              />
            </FieldGroup>

            <FieldGroup
              kicker="Eligibility"
              title="Restocking fee"
              description="That a fee applies is policy; what it is, is not -- Ferguson publishes no universal percentage, so the model cannot express one."
            >
              <Toggle
                label="Applies by default"
                value={asBoolean(restockingFee.applies_by_default, true)}
                onChange={(next) => { set(["return_eligibility_policy", "restocking_fee", "applies_by_default"], next); }}
              />
              <Toggle
                label="Seller can waive"
                value={asBoolean(restockingFee.seller_can_waive, true)}
                onChange={(next) => { set(["return_eligibility_policy", "restocking_fee", "seller_can_waive"], next); }}
              />
              <OrderedList
                label="Permitted amount sources, in priority order"
                items={asStringArray(restockingFee.amount_source)}
                keyOf={(value) => value}
                onChange={(next) => { set(["return_eligibility_policy", "restocking_fee", "amount_source"], next); }}
                renderItem={(value) => (
                  <span className="text-xs text-on-surface">{FEE_AMOUNT_SOURCE_LABEL[value] ?? value}</span>
                )}
                error={errorMap.get("return_eligibility_policy.restocking_fee.amount_source")}
              />
              <div className="flex flex-col gap-1 text-xs text-on-surface-variant">
                <p>Percentage: not published by Ferguson.</p>
                <p>Amount: not published by Ferguson.</p>
              </div>
              <KeyValueTable
                label="Seller's own rate schedule"
                hint="The seller's own standing rate, in basis points (1500 = 15.00%). Every figure it produces is tagged SELLER_CONFIGURATION, never Ferguson's own policy. Leave empty to emit applicability with no figure attached."
                entries={Object.entries(sellerSchedule).map(([key, value]): KeyValueEntry => ({
                  key,
                  value:
                    typeof value === "number" || typeof value === "string"
                      ? String(value)
                      : "",
                }))}
                onChange={(next) => {
                  if (next.length === 0) {
                    set(["return_eligibility_policy", "restocking_fee", "seller_schedule"], null);
                    return;
                  }
                  const record: JsonObject = {};
                  for (const entry of next) {
                    record[entry.key] =
                      entry.key === "default_rate_basis_points" ? Number(entry.value) || 0 : entry.value;
                  }
                  set(["return_eligibility_policy", "restocking_fee", "seller_schedule"], record);
                }}
                valueKind="string"
                keyLabel="Field"
              />
            </FieldGroup>

            {/* 5. Exceptions */}
            <FieldGroup
              kicker="Eligibility"
              title="Stock classification"
              description="Where stocked-vs-special-order comes from when the source cannot say. A line the source DOES classify is never overridden."
            >
              <EnumSelect
                label="Unresolved default"
                value={asString(stockClassification.unresolved_default, "REVIEW_REQUIRED")}
                options={STOCK_CLASSIFICATION_DEFAULTS}
                onChange={(next) => { set(["return_eligibility_policy", "stock_classification", "unresolved_default"], next); }}
              />
              <TagListInput
                label="Special-order SKUs"
                values={asStringArray(stockClassification.special_order_skus)}
                onChange={(next) => { set(["return_eligibility_policy", "stock_classification", "special_order_skus"], next); }}
              />
              <TagListInput
                label="Special-order SKU prefixes"
                values={asStringArray(stockClassification.special_order_sku_prefixes)}
                onChange={(next) => {
                  set(["return_eligibility_policy", "stock_classification", "special_order_sku_prefixes"], next);
                }}
              />
              <TagListInput
                label="Special-order product ids"
                values={asStringArray(stockClassification.special_order_product_ids)}
                onChange={(next) => {
                  set(["return_eligibility_policy", "stock_classification", "special_order_product_ids"], next);
                }}
              />
            </FieldGroup>

            <FieldGroup
              kicker="Eligibility"
              title="Special order and non-stock"
              description="An item the seller does not stock, or does not carry, needs the manufacturer's agreement. Manufacturer acceptance is always required -- the model refuses to disable it."
            >
              <Toggle
                label="Buyer fee acceptance required"
                value={asBoolean(specialOrNonstock.buyer_fee_acceptance_required, true)}
                onChange={(next) => { set(["return_eligibility_policy", "special_or_nonstock", "buyer_fee_acceptance_required"], next); }}
              />
              <EnumSelect
                label="Manufacturer acceptance unknown"
                hint="The model refuses APPROVE here -- an unknown fact cannot decide an approval."
                value={asString(decisions.manufacturer_acceptance_unknown, "REVIEW_REQUIRED")}
                options={ELIGIBILITY_DECISIONS}
                onChange={(next) => { set(["return_eligibility_policy", "special_or_nonstock", "decisions", "manufacturer_acceptance_unknown"], next); }}
              />
              <EnumSelect
                label="Manufacturer acceptance rejected"
                hint="The model refuses APPROVE here."
                value={asString(decisions.manufacturer_acceptance_rejected, "REJECT")}
                options={ELIGIBILITY_DECISIONS}
                onChange={(next) => { set(["return_eligibility_policy", "special_or_nonstock", "decisions", "manufacturer_acceptance_rejected"], next); }}
              />
              <EnumSelect
                label="Accepted, buyer fee unknown"
                hint="The model refuses APPROVE here."
                value={asString(decisions.manufacturer_acceptance_accepted_buyer_fee_unknown, "REVIEW_REQUIRED")}
                options={ELIGIBILITY_DECISIONS}
                onChange={(next) => { set(["return_eligibility_policy", "special_or_nonstock", "decisions", "manufacturer_acceptance_accepted_buyer_fee_unknown"], next); }}
              />
              <EnumSelect
                label="Accepted, buyer fee rejected"
                hint="The model refuses APPROVE here."
                value={asString(decisions.manufacturer_acceptance_accepted_buyer_fee_rejected, "REJECT")}
                options={ELIGIBILITY_DECISIONS}
                onChange={(next) => { set(["return_eligibility_policy", "special_or_nonstock", "decisions", "manufacturer_acceptance_accepted_buyer_fee_rejected"], next); }}
              />
              <EnumSelect
                label="Accepted, buyer fee accepted"
                value={asString(decisions.manufacturer_acceptance_accepted_buyer_fee_accepted, "APPROVE")}
                options={ELIGIBILITY_DECISIONS}
                onChange={(next) => { set(["return_eligibility_policy", "special_or_nonstock", "decisions", "manufacturer_acceptance_accepted_buyer_fee_accepted"], next); }}
              />
            </FieldGroup>

            <FieldGroup
              kicker="Eligibility"
              title="Delivery claim"
              description="Reasons treated as a delivery claim, and how long a customer has to report one -- must not overlap with Warranty issue's reasons below."
            >
              <TagListInput
                label="Conditions"
                values={asStringArray(deliveryClaim.conditions)}
                onChange={(next) => { set(["return_eligibility_policy", "delivery_claim", "conditions"], next); }}
                suggestions={RETURN_REASON_SUGGESTIONS}
                error={errorMap.get("return_eligibility_policy.delivery_claim.conditions")}
              />
              <NumberField
                label="Reporting window"
                hint="Business days from delivery"
                value={asNumber(asObject(deliveryClaim.reporting_window).business_days, 2)}
                onChange={(next) => { set(["return_eligibility_policy", "delivery_claim", "reporting_window", "business_days"], next); }}
                min={1}
                max={365}
                unit="business days"
              />
            </FieldGroup>

            <FieldGroup kicker="Eligibility" title="Warranty issue" description="Reasons treated as a manufacturer warranty issue.">
              <TagListInput
                label="Reasons"
                values={asStringArray(warrantyIssue.reasons)}
                onChange={(next) => { set(["return_eligibility_policy", "warranty_issue", "reasons"], next); }}
                suggestions={RETURN_REASON_SUGGESTIONS}
                error={errorMap.get("return_eligibility_policy.warranty_issue.reasons")}
              />
            </FieldGroup>

            {/* 6. Precedence */}
            <FieldGroup
              kicker="Eligibility"
              title="Precedence"
              description="Which policy wins when more than one applies. FERGUSON_STANDARD_RETURN is the platform's own fallback and is pinned last -- reorder the rows above it."
            >
              <OrderedList
                label="Precedence order"
                // CFG-8 A1: the model-level validator that pins the fallback
                // last reports at the parent path (`return_eligibility_policy`),
                // not `…precedence`, because it is a cross-field rule, not a
                // per-field one -- so the inline slot at `…precedence` alone
                // could never fill. Read both: whichever the backend actually
                // used, the operator sees it on the control they just dragged.
                error={
                  errorMap.get("return_eligibility_policy.precedence") ??
                  errorMap.get("return_eligibility_policy")
                }
                items={asStringArray(eligibility.precedence)}
                keyOf={(value) => value}
                // CFG-8 A1: `FERGUSON_STANDARD_RETURN` is the platform's own
                // fallback and the model refuses a release where it is not
                // last -- rendering it non-movable keeps the control itself
                // honest about the constraint instead of letting an operator
                // drag it, run Validate, and learn only from the page-level
                // error list.
                fixedTrailing={(value) => value === "FERGUSON_STANDARD_RETURN"}
                onChange={(next) => { set(["return_eligibility_policy", "precedence"], next); }}
                renderItem={(value) => <span className="font-mono text-xs">{value}</span>}
              />
            </FieldGroup>
          </div>
        );
      }}
      renderOutsideFieldset={({ draft }) => (
        // CFG-8 A3: a sibling of the fieldset above, not a child of it -- see
        // `TypedSectionScreen`'s own note on `renderOutsideFieldset`. Evaluate
        // writes nothing into the draft and the route is deliberately scoped
        // to `config.runtime.read`, so a read-only operator must reach it.
        <PolicyPreviewPanel
          returnEligibilityPolicy={asObject(draft.return_eligibility_policy)}
          policyEvaluation={asObject(draft.policy_evaluation)}
        />
      )}
    />
  );
}

function ResetToPackagedLink({ visible, onClick }: { visible: boolean; onClick: () => void }) {
  if (!visible) return null;
  return (
    <button
      type="button"
      onClick={onClick}
      className="mb-1 text-[11px] font-medium text-primary underline-offset-2 hover:underline"
    >
      Reset to packaged default
    </button>
  );
}

type TriState = "TRUE" | "FALSE" | "UNKNOWN";

/** yes / no / not stated -- the checklist's own tri-state, as a preview fact. */
function TriStateRadioGroup({
  label,
  value,
  onChange,
}: {
  label: string;
  value: TriState;
  onChange: (next: TriState) => void;
}) {
  const name = useId();
  const options: readonly { value: TriState; label: string }[] = [
    { value: "TRUE", label: "Yes" },
    { value: "FALSE", label: "No" },
    { value: "UNKNOWN", label: "Not stated" },
  ];
  return (
    <fieldset className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <legend className="w-full text-xs text-on-surface">{label}</legend>
      {options.map((option) => (
        <label key={option.value} className="flex items-center gap-1 text-[11px] text-on-surface-variant">
          <input
            type="radio"
            name={`${name}-${label}`}
            checked={value === option.value}
            onChange={() => { onChange(option.value); }}
            className="accent-primary"
          />
          {option.label}
        </label>
      ))}
    </fieldset>
  );
}

type PreviewSampleForm = {
  daysSincePurchase: number;
  stockClassification: "STANDARD_STOCK" | "SPECIAL_ORDER" | "UNRESOLVED";
  facts: Record<string, TriState>;
  reason: string;
};

function defaultSampleForm(): PreviewSampleForm {
  return { daysSincePurchase: 10, stockClassification: "STANDARD_STOCK", facts: {}, reason: "" };
}

/**
 * Block 7: a small form evaluating `POST /api/config/policy/preview` against
 * the **draft** in the editor -- never the loaded document, and never a real
 * case or the graph, exactly as the backend route documents itself.
 */
function PolicyPreviewPanel({
  returnEligibilityPolicy,
  policyEvaluation,
}: {
  returnEligibilityPolicy: JsonObject;
  policyEvaluation: JsonObject;
}) {
  const [form, setForm] = useState<PreviewSampleForm>(defaultSampleForm());

  const preview = useMutation({
    mutationFn: () =>
      configApi.previewPolicy({
        returnEligibilityPolicy,
        policyEvaluation,
        sample: {
          daysSincePurchase: form.daysSincePurchase,
          stockClassification: form.stockClassification,
          facts: form.facts,
          reason: form.reason.trim() === "" ? null : form.reason,
        },
      }),
  });

  return (
    <section className="rounded-xl border border-outline-variant bg-surface-container-lowest">
      <header className="flex flex-wrap items-end gap-3 border-b border-outline-variant/80 px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="premium-kicker">Preview</p>
          <h3 className="mt-0.5 text-sm font-semibold text-on-surface">Evaluate a sample against this draft</h3>
          <p className="mt-1 max-w-2xl text-xs text-on-surface-variant">
            The sample is fabricated, never a real case, and no graph read is spent on a preview. This
            evaluates the values above as you have edited them, not the published release.
          </p>
        </div>
        <button
          type="button"
          onClick={() => { preview.mutate(); }}
          disabled={preview.isPending}
          // RV round 1, F1 (BLOCKING, CFG-8 A2): `disabled:opacity-40` on
          // `text-on-surface-variant` composited to 2.046:1 -- the same
          // family CFG-5's F1 was blocking on and AgentsSection.tsx's Save
          // button already carries the fix for (`disabled:bg-*`/
          // `disabled:border-*`, no opacity). Dimmed through the
          // background/border channel instead, so the label stays at full
          // contrast in both states; `disabled:hover:*` re-asserts the
          // resting colours so a disabled button never reads as interactive
          // (CFG-5's own H1: differ by fill/border, not only cursor).
          className="flex items-center gap-1.5 rounded-lg border border-outline-control bg-surface-container-lowest px-3 py-2 text-xs font-semibold text-on-surface-variant transition hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:border-outline-variant disabled:bg-surface-container-low disabled:hover:border-outline-variant disabled:hover:text-on-surface-variant"
        >
          {preview.isPending ? "Evaluating..." : "Evaluate"}
        </button>
      </header>

      <div className="grid grid-cols-1 gap-3 px-4 py-3 sm:grid-cols-2 xl:grid-cols-4">
        <NumberField
          label="Days since purchase"
          value={form.daysSincePurchase}
          onChange={(next) => { setForm({ ...form, daysSincePurchase: next }); }}
          min={0}
          max={36_500}
          unit="days"
        />
        <EnumSelect
          label="Stock classification"
          value={form.stockClassification}
          options={[
            { value: "STANDARD_STOCK", label: "Standard stock" },
            { value: "SPECIAL_ORDER", label: "Special order" },
            { value: "UNRESOLVED", label: "Unresolved" },
          ]}
          onChange={(next) => {
            setForm({ ...form, stockClassification: next as PreviewSampleForm["stockClassification"] });
          }}
        />
        <TextInputField
          label="Return reason"
          hint="Optional -- a ReturnReason value, e.g. CHANGED_MIND"
          value={form.reason}
          onChange={(reason) => { setForm({ ...form, reason }); }}
        />
      </div>

      <div className="flex flex-col gap-1.5 border-t border-outline-variant/80 px-4 py-3">
        <p className="premium-kicker">What the item must be</p>
        <div className="grid grid-cols-1 gap-x-4 gap-y-1.5 sm:grid-cols-2">
          {[...CONDITION_CHECKS, ...PROHIBITED_CHECKS].map(({ field, label }) => (
            <TriStateRadioGroup
              key={field}
              label={label}
              value={form.facts[field] ?? "UNKNOWN"}
              onChange={(next) => { setForm({ ...form, facts: { ...form.facts, [field]: next } }); }}
            />
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-2 border-t border-outline-variant/80 px-4 py-3">
        {preview.error !== null ? (
          <p role="alert" className="rounded-lg border border-error/20 bg-error-container px-3 py-2 text-sm text-on-error-container">
            {preview.error.message}
          </p>
        ) : null}
        <p role="status" className="text-sm text-on-surface">
          {preview.data === undefined ? "Nothing evaluated yet. Set the sample above, then Evaluate." : summarize(preview.data)}
        </p>
        {preview.data !== undefined ? <PreviewResult result={preview.data} /> : null}
      </div>
    </section>
  );
}

function summarize(result: PolicyPreviewResult): string {
  if (!result.evaluation_enabled) {
    return `Policy evaluation is off. ${result.policy_evaluation_skip_reason ?? "No reason was stated."}`;
  }
  if (result.decision === null) {
    return `Routed to ${result.route ?? "an unnamed route"}, verified by Support -- not an eligibility decision.`;
  }
  return `Decision: ${result.decision}`;
}

function PreviewResult({ result }: { result: PolicyPreviewResult }) {
  if (!result.evaluation_enabled) {
    return (
      <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
        <Fact label="Policy evaluation state" value={result.policy_evaluation_state} />
        <Fact label="Skip reason" value={result.policy_evaluation_skip_reason ?? "(none stated)"} />
      </dl>
    );
  }
  return (
    <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
      <Fact label="Decision" value={result.decision ?? "(none -- a hand-off route)"} />
      <Fact label="Route" value={result.route ?? "-"} />
      <Fact label="Conditions" value={result.conditions.length > 0 ? result.conditions.join(", ") : "(none)"} />
      <Fact
        label="Applied rules"
        value={result.applied_rules.length > 0 ? result.applied_rules.join(", ") : "(none)"}
      />
      <Fact
        label="Unanswered checks"
        value={result.unanswered_checks.length > 0 ? result.unanswered_checks.join(", ") : "(none)"}
      />
      <Fact
        label="Reason codes"
        value={result.reason_codes.length > 0 ? result.reason_codes.join(", ") : "(none)"}
      />
    </dl>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="premium-kicker">{label}</dt>
      <dd className="mt-0.5 text-on-surface">{value}</dd>
    </div>
  );
}

function TextInputField({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (next: string) => void;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-[11px] font-semibold text-on-surface-variant">{label}</label>
      <input
        id={id}
        type="text"
        value={value}
        onChange={(event) => { onChange(event.target.value); }}
        className="premium-field py-1.5 text-xs"
      />
      {hint !== undefined ? <p className="text-[10px] text-outline">{hint}</p> : null}
    </div>
  );
}
