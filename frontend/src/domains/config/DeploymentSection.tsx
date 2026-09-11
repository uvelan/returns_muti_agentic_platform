import { useQuery } from "@tanstack/react-query";

import { configApi } from "../../api/configuration";
import { EnumSelect, type EnumOption } from "../../components/forms/EnumSelect";
import { FieldGroup } from "../../components/forms/FieldGroup";
import { NumberField } from "../../components/forms/NumberField";
import { OrderedList } from "../../components/forms/OrderedList";
import { TagListInput } from "../../components/forms/TagListInput";
import { Toggle } from "../../components/forms/Toggle";
import { useCapabilities } from "../../hooks/capabilityContext";
import type { Json } from "./DocumentEditor";
import { asArray, asBoolean, asNumber, asObject, asString, asStringArray, sliceOf } from "./jsonPath";
import { runtimeSliceOf, type RuntimeSlice } from "./runtimeSlice";
import { TextField } from "./TextField";
import { TypedSectionScreen } from "./TypedSectionScreen";

/**
 * `/config/deployment` -- CFG-6 (D-CFG-4): the eight env-held business
 * switches, now released and hot-adopted like every other section here.
 * Provider order, per-provider model pools, the two GOOGLE extras, the four
 * dependency-simulation modes, feedback learning, and the support-ticket
 * mode/base URL.
 *
 * **Precedence with the AI Control Center is visible here, not just enforced
 * on the backend.** `runtime_integrations.ai_providers` (read straight off
 * the active release, not the editable slice) still governs *availability* --
 * credentials and the model pool -- for a provider it enables; this screen
 * renders that provider's pool read-only with the reason, because
 * `deployment.ai.model_pools` for it would be published but silently ignored
 * on adoption (design §4). Provider order is always this screen's, for every
 * provider.
 *
 * **Production-refused options render disabled with the reason, not
 * hidden** (design §6): `SIMULATED` on a dependency mode, `SIMULATOR`/
 * `MANUAL` in provider order. An operator must be able to see a control
 * exists and why it is unavailable here -- the same property the backend's
 * `validate_deployment_for_environment` enforces at publish, made visible
 * before a publish is even attempted. `environment` comes from
 * `GET /api/config/runtime` (added there in this lease); `null` (a payload
 * predating the field, or a read that failed) is treated as "unknown", never
 * as production, so this screen never disables an option production would
 * actually allow.
 */

const SECTION_KEYS = ["deployment"] as const;

const ALL_PROVIDERS = ["GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA"] as const;

const DEPENDENCY_MODE_OPTIONS: readonly EnumOption[] = [
  { value: "REAL", label: "Real" },
  { value: "SIMULATED", label: "Simulated" },
  { value: "MANUAL", label: "Manual" },
  { value: "BLOCKED", label: "Blocked" },
];

const SUPPORT_TICKET_MODE_OPTIONS: readonly EnumOption[] = [
  { value: "INTERNAL", label: "Internal" },
  { value: "INTERNAL_WITH_EXTERNAL_MIRROR", label: "Internal, mirrored externally" },
  { value: "EXTERNAL_AUTHORITY", label: "External authority" },
];

/** `SIMULATED` disabled in production -- `validate_deployment_for_environment`'s dependency-mode rule. */
function dependencyModeOptions(environment: string | null): readonly EnumOption[] {
  if (environment !== "production") return DEPENDENCY_MODE_OPTIONS;
  return DEPENDENCY_MODE_OPTIONS.map((option) =>
    option.value === "SIMULATED"
      ? { ...option, disabled: true, disabledReason: "forbidden in production" }
      : option,
  );
}

/** The provider keys `runtime_integrations` currently enables. */
function governedProviders(snapshot: Readonly<Record<string, unknown>>): ReadonlySet<string> {
  const configuration = asObject(snapshot.configuration as Json | undefined);
  const runtimeIntegrations = asObject(configuration.runtime_integrations);
  const providers = asArray(runtimeIntegrations.ai_providers);
  return new Set(
    providers
      .map(asObject)
      .filter((provider) => asBoolean(provider.enabled))
      .map((provider) => asString(provider.provider_key)),
  );
}

export function DeploymentSection() {
  const { can } = useCapabilities();
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });

  if (runtime.isPending) return <p className="text-sm text-on-surface-variant">Loading...</p>;
  if (runtime.error !== null) {
    return (
      <p role="alert" className="text-sm text-error">
        {runtime.error.message}
      </p>
    );
  }

  const active = runtimeSliceOf(runtime.data);
  return (
    <DeploymentEditor
      key={active.releaseId}
      active={active}
      environment={active.environment}
      governed={governedProviders(runtime.data)}
      canWrite={can("config.release.write")}
      canPublish={can("config.release.promote")}
    />
  );
}

function DeploymentEditor({
  active,
  environment,
  governed,
  canWrite,
  canPublish,
}: {
  active: RuntimeSlice;
  environment: string | null;
  governed: ReadonlySet<string>;
  canWrite: boolean;
  canPublish: boolean;
}) {
  const loaded = sliceOf(active.configuration, SECTION_KEYS);

  return (
    <TypedSectionScreen
      kicker="Deployment"
      title="Deployment"
      description="AI provider order and model pools, dependency-simulation modes, feedback learning, and the support-ticket mode -- the switches a deployment used to set once in .env and never touch again."
      active={active}
      loaded={loaded}
      canWrite={canWrite}
      canPublish={canPublish}
      jsonLabel="Deployment section JSON"
      notObjectMessage="deployment must be an object."
      renderTyped={({ get, set, errorMap }) => {
        const deployment = asObject(get(["deployment"]));
        const ai = asObject(deployment.ai);
        const google = asObject(ai.google);
        const modelPools = asObject(ai.model_pools);
        const dependencies = asObject(deployment.dependencies);
        const feedbackLearning = asObject(deployment.feedback_learning);
        const supportTicket = asObject(deployment.support_ticket);
        const providerOrder = asStringArray(ai.provider_order);
        const modeOptions = dependencyModeOptions(environment);

        return (
          <div className="flex flex-col gap-4">
            <FieldGroup
              kicker="AI"
              title="Provider order"
              description="Reordering and filtering the enabled set -- the only source of provider order, and the only way to name SIMULATOR or MANUAL. A provider named here without credentials contributes no routes."
            >
              <OrderedList
                label="Provider order"
                error={errorMap.get("deployment.ai.provider_order")}
                items={providerOrder}
                keyOf={(value) => value}
                onChange={(next) => { set(["deployment", "ai", "provider_order"], next); }}
                renderItem={(value) => (
                  <span className="flex items-center gap-2">
                    <span className="font-mono text-xs">{value}</span>
                    {environment === "production" && (value === "SIMULATOR" || value === "MANUAL") ? (
                      <span
                        className="rounded-full bg-error-container px-2 py-0.5 text-[10px] font-semibold text-on-error-container"
                        title={`${value} cannot be configured in production`}
                      >
                        forbidden in production
                      </span>
                    ) : null}
                  </span>
                )}
              />
            </FieldGroup>

            <FieldGroup kicker="AI" title="Model pools" description="Lightweight and standard tiers, per provider. A provider the AI Control Center governs renders read-only here -- its pool is that mechanism's answer, and a value published here would be silently ignored on adoption.">
              <div className="flex flex-col gap-4">
                {ALL_PROVIDERS.map((provider) => {
                  const pool = asObject(modelPools[provider]);
                  const isGoverned = governed.has(provider);
                  if (isGoverned) {
                    return (
                      <div key={provider} className="rounded-lg border border-outline-variant bg-surface-container-low p-3">
                        <p className="text-xs font-semibold text-on-surface">{provider}</p>
                        <p className="mt-1 text-xs text-on-surface-variant">
                          Governed by the AI Control Center provider bindings. Lightweight and standard
                          models come from there, not from this screen.
                        </p>
                      </div>
                    );
                  }
                  return (
                    <div key={provider} className="flex flex-col gap-2 rounded-lg border border-outline-variant bg-surface-container-lowest p-3">
                      <p className="text-xs font-semibold text-on-surface">{provider}</p>
                      <TagListInput
                        label={`${provider} lightweight models`}
                        values={asStringArray(pool.lightweight)}
                        onChange={(next) => { set(["deployment", "ai", "model_pools", provider, "lightweight"], next); }}
                        error={errorMap.get(`deployment.ai.model_pools.${provider}.lightweight`)}
                      />
                      <TagListInput
                        label={`${provider} standard models`}
                        values={asStringArray(pool.standard)}
                        onChange={(next) => { set(["deployment", "ai", "model_pools", provider, "standard"], next); }}
                        error={errorMap.get(`deployment.ai.model_pools.${provider}.standard`)}
                      />
                    </div>
                  );
                })}
              </div>
            </FieldGroup>

            <FieldGroup kicker="AI" title="Google" description="Thinking budget and constrained decoding -- always this screen's, regardless of which provider bindings the AI Control Center governs.">
              <NumberField
                label="Thinking budget"
                hint="How many tokens Gemini may spend thinking before it answers. Advisory, not absolute."
                value={asNumber(google.thinking_budget, 2048)}
                onChange={(next) => { set(["deployment", "ai", "google", "thinking_budget"], next); }}
                min={0}
                max={24576}
                error={errorMap.get("deployment.ai.google.thinking_budget")}
              />
              <Toggle
                label="Response schema"
                hint="Constrained decoding (Gemini responseSchema). Off lets the model follow the schema in the system prompt instead."
                value={asBoolean(google.response_schema)}
                onChange={(next) => { set(["deployment", "ai", "google", "response_schema"], next); }}
              />
            </FieldGroup>

            <FieldGroup kicker="Dependencies" title="Dependency simulation" description="How each external dependency is served. Simulated is forbidden in production.">
              <EnumSelect
                label="OMC"
                value={asString(dependencies.omc, "SIMULATED")}
                options={modeOptions}
                onChange={(next) => { set(["deployment", "dependencies", "omc"], next); }}
                error={errorMap.get("deployment.dependencies.omc")}
              />
              <EnumSelect
                label="Parcel"
                value={asString(dependencies.parcel, "SIMULATED")}
                options={modeOptions}
                onChange={(next) => { set(["deployment", "dependencies", "parcel"], next); }}
                error={errorMap.get("deployment.dependencies.parcel")}
              />
              <EnumSelect
                label="Freight"
                value={asString(dependencies.freight, "SIMULATED")}
                options={modeOptions}
                onChange={(next) => { set(["deployment", "dependencies", "freight"], next); }}
                error={errorMap.get("deployment.dependencies.freight")}
              />
              <EnumSelect
                label="LSI"
                value={asString(dependencies.lsi, "SIMULATED")}
                options={modeOptions}
                onChange={(next) => { set(["deployment", "dependencies", "lsi"], next); }}
                error={errorMap.get("deployment.dependencies.lsi")}
              />
            </FieldGroup>

            <FieldGroup kicker="Feedback learning" title="Feedback learning" description="Whether governed feedback-learning recommendations are produced at all.">
              <Toggle
                label="Feedback learning enabled"
                value={asBoolean(feedbackLearning.enabled, true)}
                onChange={(next) => { set(["deployment", "feedback_learning", "enabled"], next); }}
              />
            </FieldGroup>

            <FieldGroup kicker="Support ticket" title="Support ticket" description="Where a Return Support ticket is created and mirrored. The API key stays outside the release.">
              <EnumSelect
                label="Mode"
                value={asString(supportTicket.mode, "INTERNAL")}
                options={SUPPORT_TICKET_MODE_OPTIONS}
                onChange={(next) => { set(["deployment", "support_ticket", "mode"], next); }}
                error={errorMap.get("deployment.support_ticket.mode")}
              />
              <TextField
                label="Base URL"
                hint="Required when mode is not Internal."
                value={asString(supportTicket.base_url)}
                onChange={(next) => {
                  set(["deployment", "support_ticket", "base_url"], next.trim() === "" ? null : next);
                }}
                error={errorMap.get("deployment.support_ticket.base_url")}
              />
            </FieldGroup>
          </div>
        );
      }}
    />
  );
}
