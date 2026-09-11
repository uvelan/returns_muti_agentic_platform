import { useQuery } from "@tanstack/react-query";

import { configApi } from "../../api/configuration";
import { EnumSelect } from "../../components/forms/EnumSelect";
import { FieldGroup } from "../../components/forms/FieldGroup";
import { KeyValueTable, type KeyValueEntry } from "../../components/forms/KeyValueTable";
import { NumberField } from "../../components/forms/NumberField";
import { OrderedList } from "../../components/forms/OrderedList";
import { TagListInput } from "../../components/forms/TagListInput";
import { Toggle } from "../../components/forms/Toggle";
import { useCapabilities } from "../../hooks/capabilityContext";
import type { Json, JsonObject } from "./DocumentEditor";
import {
  asArray,
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
 * `/config/return-policy` -- what may be returned, how, and what the
 * platform decides on its own: `return_policy.return_method_derivation`,
 * `return_method_requirements`, `return_eligibility_policy`,
 * `policy_evaluation`.
 *
 * **Coverage, deliberately partial (see `DiscoverySection`'s own note on
 * the same tradeoff).** `return_eligibility_policy` is not a flat `rules`
 * list -- the actual model (`eligibility_policy.py:402`) is seven distinct,
 * differently-shaped sub-blocks (`standard_stock_return`, `restocking_fee`,
 * `stock_classification`, `special_or_nonstock`, `outside_standard_window`,
 * `delivery_claim`, `warranty_issue`) plus a `precedence` ordering. This
 * screen renders one `FieldGroup` per block for the ones with an `EnumSelect`
 * outcome worth surfacing (`standard_stock_return`, `outside_standard_window`,
 * `stock_classification`) plus the two reason-driven blocks
 * (`delivery_claim`, `warranty_issue`) and `precedence` itself; `restocking_fee`
 * and `special_or_nonstock` render on no typed control -- Advanced mode edits
 * the same slice as JSON with nothing held back.
 */

const SECTION_KEYS = ["return_policy", "return_eligibility_policy", "policy_evaluation"] as const;

const REQUIREMENT_DIMENSIONS = ["RMA", "LABEL", "TRACKING", "BOL", "PICKUP", "RETURN_LOCATION", "RECEIPT"] as const;

const ELIGIBILITY_DECISIONS = [
  { value: "APPROVE", label: "Approve" },
  { value: "REJECT", label: "Reject" },
  { value: "REVIEW_REQUIRED", label: "Review required" },
];

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

export function ReturnPolicySection() {
  const { can } = useCapabilities();
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });

  if (runtime.isPending) return <p className="text-sm text-on-surface-variant">Loading...</p>;
  if (runtime.error !== null) {
    return <p role="alert" className="text-sm text-error">{runtime.error.message}</p>;
  }

  const active = runtimeSliceOf(runtime.data);
  return (
    <ReturnPolicyEditor
      key={active.releaseId}
      active={active}
      canWrite={can("config.release.write")}
      canPublish={can("config.release.promote")}
    />
  );
}

function ReturnPolicyEditor({
  active,
  canWrite,
  canPublish,
}: {
  active: RuntimeSlice;
  canWrite: boolean;
  canPublish: boolean;
}) {
  const loaded = sliceOf(active.configuration, SECTION_KEYS);

  return (
    <TypedSectionScreen
      kicker="Return policy"
      title="Return policy"
      description="How a return method is derived, what each method requires, the rules eligibility is judged by, and whether eligibility is evaluated at all."
      active={active}
      loaded={loaded}
      canWrite={canWrite}
      canPublish={canPublish}
      jsonLabel="Return policy section JSON"
      notObjectMessage="Each of return_policy, return_eligibility_policy and policy_evaluation must be an object."
      renderTyped={({ get, set, errorMap }) => {
        const returnPolicy = asObject(get(["return_policy"]));
        const derivation = asObject(returnPolicy.return_method_derivation);
        const eligibility = asObject(get(["return_eligibility_policy"]));
        const policyEvaluation = asObject(get(["policy_evaluation"]));
        const methods = asStringArray(returnPolicy.normalized_return_methods);
        const methodOptions = methods.map((method) => ({ value: method, label: method }));

        return (
          <div className="flex flex-col gap-4">
            <FieldGroup kicker="Return policy" title="Return method derivation" description="How the platform picks a return method for an item, and which ship-via codes imply which method.">
              <EnumSelect
                label="Default method"
                value={asString(derivation.default_method)}
                options={methodOptions}
                onChange={(next) => { set(["return_policy", "return_method_derivation", "default_method"], next); }}
                error={errorMap.get("return_policy.return_method_derivation.default_method")}
              />
              <EnumSelect
                label="Freight method"
                value={asString(derivation.freight_method)}
                options={methodOptions}
                onChange={(next) => { set(["return_policy", "return_method_derivation", "freight_method"], next); }}
                error={errorMap.get("return_policy.return_method_derivation.freight_method")}
              />
              <TagListInput
                label="Freight keywords"
                hint="Matched case-insensitively against product descriptions and SKUs to steer an item toward the freight method."
                values={asStringArray(derivation.freight_keywords)}
                onChange={(next) => { set(["return_policy", "return_method_derivation", "freight_keywords"], next); }}
              />
              <KeyValueTable
                label="Ship-via methods"
                hint={`Ship-via code -> return method. A value should be one of: ${methods.join(", ") || "(no methods declared yet)"}.`}
                entries={Object.entries(asObject(derivation.ship_via_methods)).map(
                  ([key, value]): KeyValueEntry => ({ key, value }),
                )}
                onChange={(next) => {
                  const record: JsonObject = {};
                  for (const entry of next) record[entry.key] = entry.value;
                  set(["return_policy", "return_method_derivation", "ship_via_methods"], record);
                }}
                valueKind="string"
              />
              <TagListInput
                label="BOL tendering instruction types"
                hint="No default exists on the backend -- an operator must answer this before a freight return can be tendered."
                values={asStringArray(returnPolicy.bol_tendering_instruction_types)}
                onChange={(next) => { set(["return_policy", "bol_tendering_instruction_types"], next); }}
                error={errorMap.get("return_policy.bol_tendering_instruction_types")}
              />
            </FieldGroup>

            <ReturnMethodRequirementsMatrix
              methods={methods}
              requirements={asArray(returnPolicy.return_method_requirements)}
              onChange={(next) => { set(["return_policy", "return_method_requirements"], next); }}
            />

            <FieldGroup kicker="Eligibility" title="Precedence" description="Which policy wins when more than one applies. FERGUSON_STANDARD_RETURN must be last -- it is the platform's own fallback.">
              <OrderedList
                label="Precedence order"
                items={asStringArray(eligibility.precedence)}
                keyOf={(value) => value}
                onChange={(next) => { set(["return_eligibility_policy", "precedence"], next); }}
                renderItem={(value) => <span className="font-mono text-xs">{value}</span>}
              />
            </FieldGroup>

            <FieldGroup kicker="Eligibility" title="Standard stock return" description="The default rule: an item in policy-satisfying condition, inside the purchase window.">
              <NumberField
                label="Purchase window (days)"
                value={asNumber(asObject(asObject(eligibility.standard_stock_return).purchase_window).days, 30)}
                onChange={(next) => { set(["return_eligibility_policy", "standard_stock_return", "purchase_window", "days"], next); }}
                min={1}
                max={3650}
                unit="days"
              />
              <EnumSelect
                label="Purchase window basis"
                value={asString(asObject(asObject(eligibility.standard_stock_return).purchase_window).basis, "PURCHASE_DATE")}
                options={[
                  { value: "PURCHASE_DATE", label: "Purchase date" },
                  { value: "DELIVERY_DATE", label: "Delivery date" },
                ]}
                onChange={(next) => { set(["return_eligibility_policy", "standard_stock_return", "purchase_window", "basis"], next); }}
              />
              <EnumSelect
                label="Decision when satisfied"
                hint="The model refuses REJECT here -- a satisfied standard return is never an outright rejection."
                value={asString(asObject(eligibility.standard_stock_return).decision_when_satisfied, "APPROVE")}
                options={ELIGIBILITY_DECISIONS}
                onChange={(next) => { set(["return_eligibility_policy", "standard_stock_return", "decision_when_satisfied"], next); }}
                error={errorMap.get("return_eligibility_policy.standard_stock_return.decision_when_satisfied")}
              />
            </FieldGroup>

            <FieldGroup kicker="Eligibility" title="Outside the standard window" description="What happens when a return is judged eligible in every way except timing.">
              <EnumSelect
                label="Decision"
                hint="The model refuses APPROVE here -- outside-window is never auto-approved."
                value={asString(asObject(eligibility.outside_standard_window).decision, "REVIEW_REQUIRED")}
                options={ELIGIBILITY_DECISIONS}
                onChange={(next) => { set(["return_eligibility_policy", "outside_standard_window", "decision"], next); }}
                error={errorMap.get("return_eligibility_policy.outside_standard_window.decision")}
              />
            </FieldGroup>

            <FieldGroup kicker="Eligibility" title="Stock classification" description="What the platform decides when it cannot tell standard stock from special order on its own.">
              <EnumSelect
                label="Unresolved default"
                value={asString(asObject(eligibility.stock_classification).unresolved_default, "REVIEW_REQUIRED")}
                options={[
                  { value: "STANDARD_STOCK", label: "Standard stock" },
                  { value: "SPECIAL_ORDER", label: "Special order" },
                  { value: "REVIEW_REQUIRED", label: "Review required" },
                ]}
                onChange={(next) => { set(["return_eligibility_policy", "stock_classification", "unresolved_default"], next); }}
              />
            </FieldGroup>

            <FieldGroup kicker="Eligibility" title="Delivery claim" description="Reasons treated as a delivery claim, and how long a customer has to report one.">
              <TagListInput
                label="Conditions"
                hint="Which return reasons count as a delivery claim -- must not overlap with Warranty issue's reasons below."
                values={asStringArray(asObject(eligibility.delivery_claim).conditions)}
                onChange={(next) => { set(["return_eligibility_policy", "delivery_claim", "conditions"], next); }}
                suggestions={RETURN_REASON_SUGGESTIONS}
                error={errorMap.get("return_eligibility_policy.delivery_claim.conditions")}
              />
              <NumberField
                label="Reporting window"
                hint="Business days from delivery"
                value={asNumber(asObject(asObject(eligibility.delivery_claim).reporting_window).business_days, 2)}
                onChange={(next) => { set(["return_eligibility_policy", "delivery_claim", "reporting_window", "business_days"], next); }}
                min={1}
                max={365}
                unit="business days"
              />
            </FieldGroup>

            <FieldGroup kicker="Eligibility" title="Warranty issue" description="Reasons treated as a manufacturer warranty issue.">
              <TagListInput
                label="Reasons"
                values={asStringArray(asObject(eligibility.warranty_issue).reasons)}
                onChange={(next) => { set(["return_eligibility_policy", "warranty_issue", "reasons"], next); }}
                suggestions={RETURN_REASON_SUGGESTIONS}
                error={errorMap.get("return_eligibility_policy.warranty_issue.reasons")}
              />
            </FieldGroup>

            <FieldGroup kicker="Policy evaluation" title="Policy evaluation" description="Whether eligibility is evaluated at all. Disabling it requires a reason.">
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
            </FieldGroup>
          </div>
        );
      }}
    />
  );
}

function requirementsFor(requirements: readonly Json[], method: string): Set<string> {
  const row = requirements.map(asObject).find((entry) => asString(entry.method) === method);
  return new Set(row !== undefined ? asStringArray(row.requires) : []);
}

/**
 * `return_method_requirements`: a list of `{method, requires}`, not a
 * `method -> {requirement: bool}` mapping (confirmed against
 * `ReturnMethodRequirementConfiguration`) -- rendered as the brief's own
 * "matrix (method x requirement checkboxes)" by reading/writing that list
 * through the matrix shape rather than storing the matrix as the model.
 */
function ReturnMethodRequirementsMatrix({
  methods,
  requirements,
  onChange,
}: {
  methods: readonly string[];
  requirements: readonly Json[];
  onChange: (next: Json[]) => void;
}) {
  function toggle(method: string, dimension: string, checked: boolean) {
    const current = requirementsFor(requirements, method);
    if (checked) current.add(dimension);
    else current.delete(dimension);
    const others = requirements.map(asObject).filter((entry) => asString(entry.method) !== method);
    const updated = current.size > 0 ? [...others, { method, requires: [...current] }] : others;
    onChange(updated);
  }

  return (
    <FieldGroup
      kicker="Return policy"
      title="Return method requirements"
      description="What each method requires before it can complete. A method with no box checked has no row and is unmapped, not refused."
    >
      {methods.length === 0 ? (
        <p className="text-sm text-on-surface-variant">
          No return methods are declared yet (return_policy.normalized_return_methods is empty).
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-max border-collapse text-xs">
            <thead className="bg-surface-container-low">
              <tr>
                <th scope="col" className="px-2 py-1.5 text-left font-semibold text-on-surface-variant">Method</th>
                {REQUIREMENT_DIMENSIONS.map((dimension) => (
                  <th key={dimension} scope="col" className="px-2 py-1.5 text-left font-semibold text-on-surface-variant">
                    {dimension}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {methods.map((method) => {
                const requires = requirementsFor(requirements, method);
                return (
                  <tr key={method} className="border-t border-outline-variant/60">
                    <td className="px-2 py-1.5 font-mono text-on-surface">{method}</td>
                    {REQUIREMENT_DIMENSIONS.map((dimension) => (
                      <td key={dimension} className="px-2 py-1.5">
                        <input
                          type="checkbox"
                          aria-label={`${method} requires ${dimension}`}
                          checked={requires.has(dimension)}
                          onChange={(event) => { toggle(method, dimension, event.target.checked); }}
                          className="size-4 accent-primary"
                        />
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </FieldGroup>
  );
}

