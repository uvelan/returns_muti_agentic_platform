import { useQuery } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";

import { agentConfigApi } from "../../api/agentConfig";
import { configApi } from "../../api/configuration";
import { EnumSelect, type EnumOption } from "../../components/forms/EnumSelect";
import { FieldGroup } from "../../components/forms/FieldGroup";
import { OrderedList } from "../../components/forms/OrderedList";
import { TagListInput } from "../../components/forms/TagListInput";
import { Toggle } from "../../components/forms/Toggle";
import { useCapabilities } from "../../hooks/capabilityContext";
import type { Json, JsonObject } from "./DocumentEditor";
import { asArray, asBoolean, asObject, asString, asStringArray, sliceOf } from "./jsonPath";
import { runtimeSliceOf, type RuntimeSlice } from "./runtimeSlice";
import { TextField } from "./TextField";
import { TypedSectionScreen } from "./TypedSectionScreen";

/**
 * `/config/integrations` -- `integrations` and `copilot`, replacing the
 * read-only tab (`UNBACKED.Integrations` in `ConfigurationPage.tsx`, which
 * pointed at the Runtime tab's raw JSON; that entry is removed here).
 *
 * **`integrations` is four *named* topics, not a data-keyed map.**
 * `IntegrationConfiguration` (`return_configuration.py:1405`) declares
 * `omc_return_create`, `external_support_mirror`, `carrier_booking` and
 * `customer_notification` as four fixed fields, each an
 * `IntegrationTopicConfiguration` -- `extra="forbid"` on the strict base
 * model refuses a fifth. The brief's own design table says `KeyValueTable`,
 * which is for keys that are *data* (`ship_via_methods`, `dependencies`);
 * offering add/rename/delete over a fixed four-field model would build
 * controls that always 422. Four fixed rows instead, one per topic.
 *
 * **`copilot.candidate_columns` is not a flat string list either.**
 * `CandidateColumnConfiguration` is `{label, fields}` -- `fields` is an
 * alias chain (the first name the row actually carries supplies the value),
 * not one column name. An `OrderedList` of small cards, not a
 * `TagListInput`.
 *
 * **No "poll intervals" field exists on `CopilotConfiguration`.** The brief's
 * design table asked for one; the model has none, and `RuntimeIntegrationsConfiguration`
 * (the AI provider/data-source runtime block, already pointed at "AI Control
 * Center -- Providers & Models" in `BusinessSection.tsx`) has none either. Not
 * invented here, the same choice `WorkflowSection.tsx` made for the design
 * table's handler/agent-ref fields.
 */

const SECTION_KEYS = ["integrations", "copilot"] as const;

const TOPICS = [
  { key: "omc_return_create", label: "OMC return create" },
  { key: "external_support_mirror", label: "External support mirror" },
  { key: "carrier_booking", label: "Carrier booking" },
  { key: "customer_notification", label: "Customer notification" },
] as const;

export function IntegrationsSection() {
  const { can } = useCapabilities();
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });
  const agents = useQuery({ queryKey: ["config", "agents"], queryFn: () => agentConfigApi.list() });

  if (runtime.isPending) return <p className="text-sm text-on-surface-variant">Loading...</p>;
  if (runtime.error !== null) {
    return <p role="alert" className="text-sm text-error">{runtime.error.message}</p>;
  }

  const active = runtimeSliceOf(runtime.data);
  const agentOptions: EnumOption[] = (agents.data ?? []).map((agent) => ({
    value: agent.manifestId,
    label: agent.name,
  }));

  return (
    <IntegrationsEditor
      key={active.releaseId}
      active={active}
      agentOptions={agentOptions}
      canWrite={can("config.release.write")}
      canPublish={can("config.release.promote")}
    />
  );
}

function IntegrationsEditor({
  active,
  agentOptions,
  canWrite,
  canPublish,
}: {
  active: RuntimeSlice;
  agentOptions: readonly EnumOption[];
  canWrite: boolean;
  canPublish: boolean;
}) {
  const loaded = sliceOf(active.configuration, SECTION_KEYS);

  return (
    <TypedSectionScreen
      kicker="Integrations"
      title="Integrations"
      description="Topic bindings for the platform's four outbound integrations, and the return-discovery Copilot's own settings: which agent it routes to and which candidate columns lead the match table."
      active={active}
      loaded={loaded}
      canWrite={canWrite}
      canPublish={canPublish}
      jsonLabel="Integrations section JSON"
      notObjectMessage="Each of integrations and copilot must be an object."
      renderTyped={({ get, set, errorMap }) => {
        const integrations = asObject(get(["integrations"]));
        const copilot = asObject(get(["copilot"]));
        const currentAgentId = asString(copilot.order_discovery_agent_id);
        // `EnumSelect` always keeps the release's current value selectable
        // even once the option list no longer names it (the primitive's own
        // contract) -- so a `None` copilot (no agent chosen yet) is offered
        // as an explicit "Not set" entry rather than silently snapping to
        // whichever agent happens to be first.
        const options: readonly EnumOption[] =
          currentAgentId === ""
            ? [{ value: "", label: "Not set" }, ...agentOptions]
            : agentOptions;

        return (
          <div className="flex flex-col gap-4">
            <FieldGroup kicker="Integrations" title="Topic bindings" description="Four fixed outbound topics; a deployment configures where each publishes and whether AI may claim success on it.">
              {TOPICS.map((topic) => {
                const value = asObject(integrations[topic.key]);
                return (
                  <div key={topic.key} className="flex flex-col gap-2 rounded-lg border border-outline-variant/70 bg-surface-container-low p-3">
                    <p className="text-sm font-semibold text-on-surface">{topic.label}</p>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <Toggle
                        label="Enabled"
                        value={asBoolean(value.enabled)}
                        onChange={(next) => { set(["integrations", topic.key, "enabled"], next); }}
                      />
                      <AiMayFabricateSuccessStatus value={asBoolean(value.ai_may_fabricate_success)} />
                      <TextField
                        label="Topic"
                        value={asString(value.topic)}
                        onChange={(next) => { set(["integrations", topic.key, "topic"], next); }}
                        error={errorMap.get(`integrations.${topic.key}.topic`)}
                        required
                      />
                      <TextField
                        label="Authority"
                        value={asString(value.authority)}
                        onChange={(next) => { set(["integrations", topic.key, "authority"], next); }}
                        error={errorMap.get(`integrations.${topic.key}.authority`)}
                        required
                      />
                    </div>
                  </div>
                );
              })}
            </FieldGroup>

            <FieldGroup kicker="Copilot" title="Order discovery routing" description="Which registered agent policy the Copilot's conversation turns are routed to, and which candidate fields its match table leads with.">
              <EnumSelect
                label="Order discovery agent"
                hint="The agent policy published in the active schema. Not set fails closed: the Copilot disables its composer and names the missing setting rather than guessing."
                value={currentAgentId}
                onChange={(next) => { set(["copilot", "order_discovery_agent_id"], next === "" ? null : next); }}
                options={options}
                error={errorMap.get("copilot.order_discovery_agent_id")}
              />
              <CandidateColumns
                columns={asArray(copilot.candidate_columns)}
                onChange={(next) => { set(["copilot", "candidate_columns"], next); }}
                error={errorMap.get("copilot.candidate_columns")}
              />
            </FieldGroup>
          </div>
        );
      }}
    />
  );
}

/**
 * `ai_may_fabricate_success`, read-only.
 *
 * RV round 1, F3: a `Toggle` here can never be switched *on* --
 * `validate_required_agents` (`return_configuration.py:1878-1879`) refuses
 * `true` on any of the four topics unconditionally, not only in production
 * -- so an interactive control whose only reachable transition always 422s
 * is exactly what this file's own module docstring already rejects
 * `KeyValueTable` for on this same section ("offering add/rename/delete
 * over a fixed four-field model would build controls that always 422").
 * Rendered as a status line instead: the release's actual current value
 * (never assumed to be `false` -- a release predating the validator could
 * in principle still carry `true`), stated as fact, with the refusal
 * reason next to it rather than as a hint on a control nothing can change.
 * Not a `Toggle` prop addition (`components/forms/**` is outside CFG-5's
 * Owns) -- a local, non-interactive readout instead.
 */
function AiMayFabricateSuccessStatus({ value }: { value: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <p className="premium-kicker">AI may fabricate success</p>
      <p className="text-sm text-on-surface">{value ? "On" : "Off"} -- read-only</p>
      <p className="text-xs text-on-surface-variant">
        Whether an AI-authored message may claim this integration succeeded before it is
        confirmed. The release validator refuses <code>true</code> on any of the four topics
        unconditionally (not only in production), so there is no reachable state for a control
        here to switch to.
      </p>
    </div>
  );
}

function newColumn(): JsonObject {
  return { label: "", fields: [] };
}

function CandidateColumns({
  columns,
  onChange,
  error,
}: {
  columns: readonly Json[];
  onChange: (next: Json[]) => void;
  error?: string;
}) {
  return (
    <div className="flex flex-col gap-2">
      <OrderedList
        label="Candidate columns"
        error={error}
        items={columns.map((column, index) => ({ column: asObject(column), index }))}
        keyOf={({ column, index }) => asString(column.label) || `column-${String(index)}`}
        onChange={(next) => { onChange(next.map(({ column }) => column)); }}
        renderItem={({ column, index }) => (
          <div className="flex items-start justify-between gap-2 py-1">
            <div className="flex flex-1 flex-col gap-2">
              <TextField
                label="Label"
                value={asString(column.label)}
                onChange={(next) => {
                  onChange(columns.map((entry, at) => (at === index ? { ...asObject(entry), label: next } : entry)));
                }}
                required
              />
              <TagListInput
                label="Fields"
                hint="An alias chain -- the first name the row actually carries supplies the value."
                values={asStringArray(column.fields)}
                onChange={(next) => {
                  onChange(columns.map((entry, at) => (at === index ? { ...asObject(entry), fields: next } : entry)));
                }}
              />
            </div>
            <button
              type="button"
              aria-label={`Remove candidate column ${asString(column.label) || String(index + 1)}`}
              onClick={() => { onChange(columns.filter((_, at) => at !== index)); }}
              className="flex size-8 shrink-0 items-center justify-center rounded-md text-outline transition hover:bg-error-container hover:text-error"
            >
              <X size={14} aria-hidden="true" />
            </button>
          </div>
        )}
      />
      <button
        type="button"
        onClick={() => { onChange([...columns, newColumn()]); }}
        className="flex w-fit items-center gap-1 rounded-lg border border-dashed border-outline-control bg-surface-container-low/60 px-2.5 py-1.5 text-[11px] font-medium text-on-surface-variant transition hover:border-primary hover:text-primary"
      >
        <Plus size={12} aria-hidden="true" />
        Add candidate column
      </button>
    </div>
  );
}
