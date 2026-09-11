import { useQuery } from "@tanstack/react-query";

import { configApi } from "../../api/configuration";
import { FieldGroup } from "../../components/forms/FieldGroup";
import { NumberField } from "../../components/forms/NumberField";
import { TagListInput } from "../../components/forms/TagListInput";
import { Toggle } from "../../components/forms/Toggle";
import { useCapabilities } from "../../hooks/capabilityContext";
import type { JsonObject } from "./DocumentEditor";
import { asBoolean, asNumber, asObject, asString, asStringArray } from "./jsonPath";
import type { RuntimeSlice } from "./runtimeSlice";
import { TextField } from "./TextField";
import { TypedSectionScreen } from "./TypedSectionScreen";

/**
 * `/config/simulation` -- `DEPENDENCY_SIMULATION`, the whole domain document
 * (`DependencySimulationConfiguration`): enabled, the operator-facing
 * banner, AI narration settings, and the four dependencies' operations and
 * status sequences.
 *
 * **A sibling of `configuration`, not a key inside it.** Every other CFG-4/5
 * screen edits a slice of `RETURN_PLATFORM`'s `configuration` object; this
 * domain has always been separate (`BusinessSection.tsx`'s own `simulation`
 * group reads `snapshot.dependency_simulation_configuration`, not
 * `snapshot.configuration.dependency_simulation`), so `loaded` here is the
 * *whole* domain document rather than a `sliceOf` a shared one, and
 * `domainKey="DEPENDENCY_SIMULATION"` is passed through to
 * `TypedSectionScreen` rather than left at its `RETURN_PLATFORM` default.
 *
 * **`dependencies` is four *required, fixed* keys, not a data-keyed map.**
 * `DependencySimulationConfiguration.validate_dependencies`
 * (`dependency_simulation/configuration.py:59-67`) refuses any document
 * whose `dependencies` are not exactly `{OMC, PARCEL, FREIGHT, LSI}` -- the
 * same shape `IntegrationsSection.tsx`'s four topics are in, for the same
 * reason a `KeyValueTable` (the brief's own design table) would be wrong: it
 * would offer renaming or removing a key the model requires. Four fixed
 * rows instead.
 *
 * **`ai.fallbackAlwaysEnabled` has no control.** The model's own validator
 * refuses `false` unconditionally ("Deterministic fallback must always be
 * enabled for simulation") -- there is no state for a toggle to represent
 * other than "on", so none is offered. `ai.providerOrder` and
 * `ai.pricingMicrousdPerMillionTokens` are Advanced-only, the same partial-
 * coverage choice CFG-4 made for `source_resolution`'s path lists.
 */

const DEPENDENCIES = ["OMC", "PARCEL", "FREIGHT", "LSI"] as const;

export function SimulationSection() {
  const { can } = useCapabilities();
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });

  if (runtime.isPending) return <p className="text-sm text-on-surface-variant">Loading...</p>;
  if (runtime.error !== null) {
    return <p role="alert" className="text-sm text-error">{runtime.error.message}</p>;
  }

  const active = simulationSliceOf(runtime.data);
  return (
    <SimulationEditor
      key={active.releaseId}
      active={active}
      canWrite={can("config.release.write")}
      canPublish={can("config.release.promote")}
    />
  );
}

/**
 * `runtimeSliceOf` reads `snapshot.configuration` (the `RETURN_PLATFORM`
 * document); this domain's document lives at
 * `snapshot.dependency_simulation_configuration` instead, so it gets its own
 * adapter rather than a `RuntimeSlice.configuration` that would lie about
 * which domain it came from. `releaseId`/`headRevision` are shared across
 * every domain on one release, so those two fields are read the same way.
 */
function simulationSliceOf(snapshot: Readonly<Record<string, unknown>>): RuntimeSlice {
  const releaseId = snapshot.release_id;
  const head = snapshot.head_revision;
  const environment = snapshot.environment;
  return {
    releaseId: typeof releaseId === "string" ? releaseId : "unknown",
    headRevision: typeof head === "number" ? head : null,
    configuration: asObject(snapshot.dependency_simulation_configuration as JsonObject | undefined),
    // CFG-6: `RuntimeSlice.environment` (`GET /api/config/runtime`'s own
    // field, not this domain's) -- read the same way `runtimeSliceOf` does,
    // for the same reason `releaseId`/`headRevision` are: it describes the
    // release/process, not the dependency-simulation document.
    environment: typeof environment === "string" ? environment : null,
  };
}

function SimulationEditor({
  active,
  canWrite,
  canPublish,
}: {
  active: RuntimeSlice;
  canWrite: boolean;
  canPublish: boolean;
}) {
  return (
    <TypedSectionScreen
      kicker="Dependency simulation"
      title="Simulation"
      description="How simulated external systems (OMC, parcel and freight carriers, LSI) behave outside production, and the AI narration layered over the deterministic outcome."
      domainKey="DEPENDENCY_SIMULATION"
      active={active}
      loaded={active.configuration}
      canWrite={canWrite}
      canPublish={canPublish}
      jsonLabel="Dependency simulation JSON"
      notObjectMessage="The dependency simulation document must be an object."
      renderTyped={({ get, set, errorMap }) => {
        const ai = asObject(get(["ai"]));

        return (
          <div className="flex flex-col gap-4">
            <FieldGroup kicker="Dependency simulation" title="Banner and versioning">
              <Toggle
                label="Enabled"
                hint="Whether simulated dependencies run at all."
                value={asBoolean(get(["enabled"]), true)}
                onChange={(next) => { set(["enabled"], next); }}
              />
              <TextField
                label="Mode banner"
                hint="10 to 256 characters -- shown to the operator so a simulated outcome is never mistaken for a real one."
                value={asString(get(["modeBanner"]))}
                onChange={(next) => { set(["modeBanner"], next); }}
                error={errorMap.get("modeBanner")}
                required
              />
              <TextField
                label="Schema version"
                value={asString(get(["schemaVersion"]))}
                onChange={(next) => { set(["schemaVersion"], next); }}
                error={errorMap.get("schemaVersion")}
                required
              />
              <TextField
                label="Template version"
                value={asString(get(["templateVersion"]))}
                onChange={(next) => { set(["templateVersion"], next); }}
                error={errorMap.get("templateVersion")}
                required
              />
              <TextField
                label="Default scenario"
                value={asString(get(["defaultScenario"]), "SUCCESS")}
                onChange={(next) => { set(["defaultScenario"], next); }}
                error={errorMap.get("defaultScenario")}
              />
            </FieldGroup>

            <FieldGroup
              kicker="AI narration"
              title="Narrative layer"
              description="An AI-written narrative over the deterministic outcome. The deterministic fallback always runs underneath it -- there is no setting that turns that off."
            >
              <Toggle
                label="AI narration enabled"
                value={asBoolean(ai.enabled, true)}
                onChange={(next) => { set(["ai", "enabled"], next); }}
              />
              <NumberField
                label="Temperature"
                hint="0 to 1"
                value={asNumber(ai.temperature, 0)}
                onChange={(next) => { set(["ai", "temperature"], next); }}
                min={0}
                max={1}
                step={0.05}
                error={errorMap.get("ai.temperature")}
              />
              <NumberField
                label="Timeout"
                hint="Seconds, 0.25 to 30"
                value={asNumber(ai.timeoutSeconds, 4)}
                onChange={(next) => { set(["ai", "timeoutSeconds"], next); }}
                min={0.25}
                max={30}
                step={0.25}
                unit="s"
                error={errorMap.get("ai.timeoutSeconds")}
              />
              <NumberField
                label="Max output tokens"
                hint="32 to 2,048"
                value={asNumber(ai.maxOutputTokens, 256)}
                onChange={(next) => { set(["ai", "maxOutputTokens"], next); }}
                min={32}
                max={2_048}
                error={errorMap.get("ai.maxOutputTokens")}
              />
              <TextField
                label="Task id"
                value={asString(ai.taskId, "SIMULATOR_OPERATION_NARRATIVE_V1")}
                onChange={(next) => { set(["ai", "taskId"], next); }}
                error={errorMap.get("ai.taskId")}
              />
            </FieldGroup>

            <FieldGroup
              kicker="Dependencies"
              title="Operations and status sequences"
              description="Exactly four dependencies -- OMC, PARCEL, FREIGHT, LSI -- each with the operations it accepts and the status ladder a simulated call walks through."
            >
              {DEPENDENCIES.map((name) => {
                const dependency = asObject(get(["dependencies", name]));
                return (
                  <div key={name} className="flex flex-col gap-2 rounded-lg border border-outline-variant/70 bg-surface-container-low p-3">
                    <p className="text-sm font-semibold text-on-surface">{name}</p>
                    <TagListInput
                      label={`${name} operations`}
                      hint="At least one, no duplicates."
                      values={asStringArray(dependency.operations)}
                      onChange={(next) => { set(["dependencies", name, "operations"], next); }}
                      error={errorMap.get(`dependencies.${name}.operations`)}
                    />
                    <TagListInput
                      label={`${name} status sequence`}
                      hint="The order a simulated call's status moves through."
                      values={asStringArray(dependency.statusSequence)}
                      onChange={(next) => { set(["dependencies", name, "statusSequence"], next); }}
                    />
                  </div>
                );
              })}
            </FieldGroup>
          </div>
        );
      }}
    />
  );
}
