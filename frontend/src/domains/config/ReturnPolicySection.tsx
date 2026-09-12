import { useQuery } from "@tanstack/react-query";

import { configApi } from "../../api/configuration";
import { EnumSelect } from "../../components/forms/EnumSelect";
import { FieldGroup } from "../../components/forms/FieldGroup";
import { KeyValueTable, type KeyValueEntry } from "../../components/forms/KeyValueTable";
import { TagListInput } from "../../components/forms/TagListInput";
import { useCapabilities } from "../../hooks/capabilityContext";
import type { Json, JsonObject } from "./DocumentEditor";
import { asArray, asObject, asString, asStringArray, sliceOf } from "./jsonPath";
import { runtimeSliceOf, type RuntimeSlice } from "./runtimeSlice";
import { TypedSectionScreen } from "./TypedSectionScreen";

/**
 * `/config/return-policy` -- what may be returned, and how:
 * `return_policy.return_method_derivation` and `return_method_requirements`.
 *
 * **CFG-8 moved the eligibility and policy-evaluation groups out.**
 * `return_eligibility_policy` and `policy_evaluation` used to render here,
 * below these two groups; they are now `/config/policy`
 * (`PolicySection.tsx`), a screen shaped around how an operator thinks about
 * eligibility rather than around the two domain keys it happens to touch.
 * This screen keeps exactly the two groups its own name still describes --
 * how a return method is derived, and what each method requires.
 */

const SECTION_KEYS = ["return_policy"] as const;

const REQUIREMENT_DIMENSIONS = ["RMA", "LABEL", "TRACKING", "BOL", "PICKUP", "RETURN_LOCATION", "RECEIPT"] as const;

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
      description="How a return method is derived, and what each method requires."
      active={active}
      loaded={loaded}
      canWrite={canWrite}
      canPublish={canPublish}
      jsonLabel="Return policy section JSON"
      notObjectMessage="return_policy must be an object."
      renderTyped={({ get, set, errorMap }) => {
        const returnPolicy = asObject(get(["return_policy"]));
        const derivation = asObject(returnPolicy.return_method_derivation);
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
                error={errorMap.get("return_policy.return_method_derivation.ship_via_methods")}
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

