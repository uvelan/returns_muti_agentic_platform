import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { configApi } from "../../api/configuration";
import { DurationField } from "../../components/forms/DurationField";
import { EnumSelect } from "../../components/forms/EnumSelect";
import { FieldGroup } from "../../components/forms/FieldGroup";
import { KeyValueTable, type KeyValueEntry } from "../../components/forms/KeyValueTable";
import { NumberField } from "../../components/forms/NumberField";
import { TagListInput } from "../../components/forms/TagListInput";
import { Toggle } from "../../components/forms/Toggle";
import { useCapabilities } from "../../hooks/capabilityContext";
import type { JsonObject } from "./DocumentEditor";
import { asBoolean, asNumber, asObject, asString, asStringArray, sliceOf } from "./jsonPath";
import { runtimeSliceOf, type RuntimeSlice } from "./runtimeSlice";
import { SupportTemplateSection } from "./SupportTemplateSection";
import { TextField } from "./TextField";
import { TypedSectionScreen } from "./TypedSectionScreen";

/**
 * `/config/support` -- the Channel B bridge, six tabs over six independent
 * `RETURN_PLATFORM` keys, each published on its own (contracts.md sects.
 * 5, 6, 9, 10):
 *
 * - **Template** is `SupportTemplateSection`, moved here unchanged (its own
 *   route stays a redirect for one release -- see `ConfigurationPage.tsx`).
 *   It is not on `TypedSectionScreen`: `support_template` publishes through
 *   the four-round-trip draft/patch/promote pipeline every other CFG-3/4
 *   screen used before `POST /api/config/publish` existed, and moving it
 *   onto the single-call path is not this lease's change to make.
 * - **Gate** (`support_gate`) -- the review gate over an outbound request.
 * - **Ingress** (`support_ingress`) -- what the platform accepts from
 *   Support and how it answers back.
 * - **Resolver** (`support_resolver`) -- how the platform answers a
 *   question Support asked. `tool_bindings` (five references per binding,
 *   one of them validated against the tool-schema registry at parse time)
 *   has no typed control here -- Advanced mode is where it is edited, the
 *   same partial-coverage choice CFG-4 made for `source_resolution`'s
 *   nineteen path lists.
 * - **Context assembly** (`context_assembly`) -- the reasoning-context
 *   budget and pinned facts.
 * - **Queues** (`support`) -- `SupportConfiguration`: authority mode,
 *   external mirror, default priority, the queue list, the outbox topic.
 *
 * Six tabs, one screen: the tab is local state, not a URL segment -- the
 * brief's own design table lists `/config/support` as one row, and a
 * section-within-a-section route would be the first place in this codebase
 * two things both claimed to be "the URL for this screen".
 */

type Tab = "Template" | "Gate" | "Ingress" | "Resolver" | "Context assembly" | "Queues";
const TABS: readonly Tab[] = ["Template", "Gate", "Ingress", "Resolver", "Context assembly", "Queues"];

export function SupportSection() {
  const [tab, setTab] = useState<Tab>("Template");
  const { can } = useCapabilities();
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });

  return (
    <div className="flex flex-col gap-4">
      <div role="tablist" aria-label="Support" className="flex flex-wrap gap-1.5 border-b border-outline-variant/70 pb-2">
        {TABS.map((candidate) => (
          <button
            key={candidate}
            type="button"
            role="tab"
            aria-selected={candidate === tab}
            onClick={() => { setTab(candidate); }}
            className={[
              "rounded-lg px-3 py-1.5 text-xs font-medium transition",
              candidate === tab
                ? "bg-secondary-container text-on-secondary-container"
                : "text-on-surface-variant hover:bg-surface-container-low",
            ].join(" ")}
          >
            {candidate}
          </button>
        ))}
      </div>

      {tab === "Template" ? <SupportTemplateSection /> : null}
      {tab !== "Template" ? (
        runtime.isPending ? (
          <p className="text-sm text-on-surface-variant">Loading...</p>
        ) : runtime.error !== null ? (
          <p role="alert" className="text-sm text-error">{runtime.error.message}</p>
        ) : (
          <SupportTab
            key={`${tab}-${runtimeSliceOf(runtime.data).releaseId}`}
            tab={tab}
            active={runtimeSliceOf(runtime.data)}
            canWrite={can("config.release.write")}
            canPublish={can("config.release.promote")}
          />
        )
      ) : null}
    </div>
  );
}

function SupportTab({
  tab,
  active,
  canWrite,
  canPublish,
}: {
  tab: Exclude<Tab, "Template">;
  active: RuntimeSlice;
  canWrite: boolean;
  canPublish: boolean;
}) {
  if (tab === "Gate") return <GateTab active={active} canWrite={canWrite} canPublish={canPublish} />;
  if (tab === "Ingress") return <IngressTab active={active} canWrite={canWrite} canPublish={canPublish} />;
  if (tab === "Resolver") return <ResolverTab active={active} canWrite={canWrite} canPublish={canPublish} />;
  if (tab === "Context assembly") return <ContextAssemblyTab active={active} canWrite={canWrite} canPublish={canPublish} />;
  return <QueuesTab active={active} canWrite={canWrite} canPublish={canPublish} />;
}

const ON_TIMEOUT_OPTIONS = [
  { value: "auto_send", label: "Auto-send" },
  { value: "hold", label: "Hold" },
  { value: "escalate", label: "Escalate" },
];

function GateTab({ active, canWrite, canPublish }: { active: RuntimeSlice; canWrite: boolean; canPublish: boolean }) {
  const loaded = sliceOf(active.configuration, ["support_gate"]);
  return (
    <TypedSectionScreen
      kicker="Support gate"
      title="Review gate"
      description="How many support requests one case produces, and what happens to each while a person is looking at it."
      active={active}
      loaded={loaded}
      canWrite={canWrite}
      canPublish={canPublish}
      jsonLabel="Support gate JSON"
      notObjectMessage="support_gate must be an object."
      renderTyped={({ get, set, errorMap }) => {
        const gate = asObject(get(["support_gate"]));
        const review = asObject(gate.template_review);
        return (
          <FieldGroup kicker="Support gate" title="Request grouping and review cadence">
            <EnumSelect
              label="Request grouping"
              hint="How many support requests one case produces, and therefore how many reviews."
              value={asString(gate.request_grouping, "one_per_case")}
              onChange={(next) => { set(["support_gate", "request_grouping"], next); }}
              options={[
                { value: "one_per_case", label: "One per case" },
                { value: "by_shipping_mode", label: "By shipping mode" },
                { value: "by_ship_from", label: "By ship-from" },
              ]}
              allowUnknown={false}
              error={errorMap.get("support_gate.request_grouping")}
            />
            <Toggle
              label="Gate enabled"
              hint="False takes the pre-gate path exactly: the handoff composes and sends with no review."
              value={asBoolean(review.enabled, true)}
              onChange={(next) => { set(["support_gate", "template_review", "enabled"], next); }}
            />
            <DurationField
              label="Review wait"
              hint="Working seconds -- resolved against the business calendar, the same as the support-response wait."
              seconds={asNumber(review.review_wait_seconds, 28_800)}
              onChange={(next) => { set(["support_gate", "template_review", "review_wait_seconds"], next); }}
              error={errorMap.get("support_gate.template_review.review_wait_seconds")}
            />
            <DurationField
              label="Reminder interval"
              hint="Working seconds."
              seconds={asNumber(review.reminder_interval_seconds, 7_200)}
              onChange={(next) => { set(["support_gate", "template_review", "reminder_interval_seconds"], next); }}
              error={errorMap.get("support_gate.template_review.reminder_interval_seconds")}
            />
            <NumberField
              label="Max reminders"
              hint="A case total, not a per-review one. 0 to 20."
              value={asNumber(review.max_reminders, 3)}
              onChange={(next) => { set(["support_gate", "template_review", "max_reminders"], next); }}
              min={0}
              max={20}
              error={errorMap.get("support_gate.template_review.max_reminders")}
            />
            <EnumSelect
              label="On timeout"
              hint="An unresolved required gap forces hold or escalate regardless of this setting."
              value={asString(review.on_timeout, "hold")}
              onChange={(next) => { set(["support_gate", "template_review", "on_timeout"], next); }}
              options={ON_TIMEOUT_OPTIONS}
              allowUnknown={false}
              error={errorMap.get("support_gate.template_review.on_timeout")}
            />
          </FieldGroup>
        );
      }}
    />
  );
}

function IngressTab({ active, canWrite, canPublish }: { active: RuntimeSlice; canWrite: boolean; canPublish: boolean }) {
  const loaded = sliceOf(active.configuration, ["support_ingress"]);
  return (
    <TypedSectionScreen
      kicker="Support ingress"
      title="Ingress"
      description="What the platform accepts from Support -- whether the natural-language door is open, which intents it classifies, and what happens to a message that arrives while it is shut."
      active={active}
      loaded={loaded}
      canWrite={canWrite}
      canPublish={canPublish}
      jsonLabel="Support ingress JSON"
      notObjectMessage="support_ingress must be an object."
      renderTyped={({ get, set, errorMap }) => {
        const ingress = asObject(get(["support_ingress"]));
        const parking = asObject(ingress.parking);
        const disclosure = asObject(ingress.agent_disclosure);
        const limits = asObject(ingress.limits);
        const outboundEntries: KeyValueEntry[] = Object.entries(asObject(ingress.outbound_templates)).map(
          ([key, value]) => ({ key, value: typeof value === "string" ? value : "" }),
        );

        return (
          <div className="flex flex-col gap-4">
            <FieldGroup kicker="Ingress" title="Natural-language door" description="The structured return-outcome path is unaffected by this and is always on.">
              <Toggle
                label="Natural-language ingress enabled"
                hint="A runtime switch, not a deploy: a message that arrives while this is off is parked and reprocessed once it is on, so flipping it loses nothing."
                value={asBoolean(ingress.nl_enabled)}
                onChange={(next) => { set(["support_ingress", "nl_enabled"], next); }}
              />
              <TagListInput
                label="Intents"
                hint="The closed classification taxonomy. An out-of-set answer always becomes 'other' in code, whatever this list says."
                values={asStringArray(ingress.intents)}
                onChange={(next) => { set(["support_ingress", "intents"], next); }}
                suggestions={[
                  "info_request",
                  "rma_issued",
                  "label_issued",
                  "shipping_instruction",
                  "tracking_provided",
                  "partial_fulfillment",
                  "rejection",
                  "acknowledgement",
                  "other",
                ]}
                error={errorMap.get("support_ingress.intents")}
              />
              <TextField
                label="Multi-record framing prompt key"
                hint="Which prompt section carries the do-not-mix framing when one inbound message fans out to several return records."
                value={asString(ingress.multi_record_framing_prompt_key)}
                onChange={(next) => { set(["support_ingress", "multi_record_framing_prompt_key"], next); }}
                error={errorMap.get("support_ingress.multi_record_framing_prompt_key")}
              />
            </FieldGroup>

            <FieldGroup kicker="Parking" title="Messages that arrive while the door is shut">
              <DurationField
                label="Retention"
                seconds={asNumber(parking.retention_seconds, 30 * 24 * 3_600)}
                onChange={(next) => { set(["support_ingress", "parking", "retention_seconds"], next); }}
              />
              <NumberField
                label="Per-case quota"
                hint="How many parked messages one case may hold before the parking itself is escalated."
                value={asNumber(parking.per_case_quota, 50)}
                onChange={(next) => { set(["support_ingress", "parking", "per_case_quota"], next); }}
                min={1}
              />
              <DurationField
                label="Alert dedupe window"
                hint="One alert per case per window, not one per message."
                seconds={asNumber(parking.alert_dedupe_window_seconds, 900)}
                onChange={(next) => { set(["support_ingress", "parking", "alert_dedupe_window_seconds"], next); }}
              />
            </FieldGroup>

            <FieldGroup kicker="Agent disclosure" title="Who the agent says it is" description="Attached to every agent-authored Channel B message.">
              <TextField
                label="Display name"
                value={asString(disclosure.display_name, "Returns Assistant")}
                onChange={(next) => { set(["support_ingress", "agent_disclosure", "display_name"], next); }}
              />
              <TextField
                label="Disclosure line"
                value={asString(disclosure.disclosure_line)}
                onChange={(next) => { set(["support_ingress", "agent_disclosure", "disclosure_line"], next); }}
              />
            </FieldGroup>

            <FieldGroup kicker="Outbound templates" title="Text by template id" description="Under sect. 8's interpolation-only grammar -- the config-templated default composition path for Channel B text.">
              <KeyValueTable
                label="Outbound templates"
                entries={outboundEntries}
                onChange={(next) => {
                  const record: JsonObject = {};
                  for (const entry of next) record[entry.key] = entry.value;
                  set(["support_ingress", "outbound_templates"], record);
                }}
                valueKind="string"
                keyLabel="Template id"
                valueLabel="Text"
              />
            </FieldGroup>

            <FieldGroup kicker="Limits" title="What the endpoint refuses before anything is persisted" collapsible defaultOpen={false}>
              <NumberField
                label="Max body characters"
                hint="Refused with 413 rather than truncated."
                value={asNumber(limits.max_body_characters, 16_000)}
                onChange={(next) => { set(["support_ingress", "limits", "max_body_characters"], next); }}
                min={1}
              />
              <NumberField
                label="Max messages per case per window"
                value={asNumber(limits.max_messages_per_case_per_window, 60)}
                onChange={(next) => { set(["support_ingress", "limits", "max_messages_per_case_per_window"], next); }}
                min={1}
              />
              <DurationField
                label="Rate window"
                seconds={asNumber(limits.rate_window_seconds, 60)}
                onChange={(next) => { set(["support_ingress", "limits", "rate_window_seconds"], next); }}
              />
              <NumberField
                label="Max identifier characters"
                hint="Longest external_message_id / transport_id accepted."
                value={asNumber(limits.max_identifier_characters, 256)}
                onChange={(next) => { set(["support_ingress", "limits", "max_identifier_characters"], next); }}
                min={1}
              />
            </FieldGroup>
          </div>
        );
      }}
    />
  );
}

function ResolverTab({ active, canWrite, canPublish }: { active: RuntimeSlice; canWrite: boolean; canPublish: boolean }) {
  const loaded = sliceOf(active.configuration, ["support_resolver"]);
  return (
    <TypedSectionScreen
      kicker="Support resolver"
      title="Resolver"
      description="How sure the resolver must be before it answers, whether an answer goes out on its own or waits for review, and how much model spend one case may consume."
      active={active}
      loaded={loaded}
      canWrite={canWrite}
      canPublish={canPublish}
      jsonLabel="Support resolver JSON"
      notObjectMessage="support_resolver must be an object."
      renderTyped={({ get, set, errorMap }) => {
        const resolver = asObject(get(["support_resolver"]));
        const replyGate = asObject(resolver.reply_gate);
        const perIntentEntries: KeyValueEntry[] = Object.entries(asObject(replyGate.per_intent)).map(
          ([key, value]) => ({ key, value: typeof value === "string" ? value : "review_required" }),
        );

        return (
          <div className="flex flex-col gap-4">
            <FieldGroup kicker="Resolver" title="Confidence and budget">
              <NumberField
                label="Fact confidence threshold"
                hint="Millionths -- how sure a fact-derived answer must be. Defaults high (900,000) on purpose."
                value={asNumber(resolver.fact_confidence_millionths, 900_000)}
                onChange={(next) => { set(["support_resolver", "fact_confidence_millionths"], next); }}
                min={0}
                max={1_000_000}
                error={errorMap.get("support_resolver.fact_confidence_millionths")}
              />
              <NumberField
                label="Graph confidence threshold"
                hint="Millionths -- the same tolerance for a graph-derived answer."
                value={asNumber(resolver.graph_confidence_millionths, 900_000)}
                onChange={(next) => { set(["support_resolver", "graph_confidence_millionths"], next); }}
                min={0}
                max={1_000_000}
                error={errorMap.get("support_resolver.graph_confidence_millionths")}
              />
              <Toggle
                label="Clarification resets deadline"
                hint="An answered clarification pushes the review deadline out again."
                value={asBoolean(resolver.clarification_resets_deadline, true)}
                onChange={(next) => { set(["support_resolver", "clarification_resets_deadline"], next); }}
              />
              <NumberField
                label="Per-case LLM budget"
                hint="Model invocations one case may spend on resolution. Exhaustion writes a fact and escalates."
                value={asNumber(resolver.per_case_llm_budget, 12)}
                onChange={(next) => { set(["support_resolver", "per_case_llm_budget"], next); }}
                min={1}
                error={errorMap.get("support_resolver.per_case_llm_budget")}
              />
              <TagListInput
                label="Trigger intents"
                hint="Which classified intents reach the resolution ladder. 'other' may never be one -- it is the sink for everything unclassifiable."
                values={asStringArray(resolver.trigger_intents)}
                onChange={(next) => { set(["support_resolver", "trigger_intents"], next); }}
                suggestions={["info_request"]}
                error={errorMap.get("support_resolver.trigger_intents")}
              />
            </FieldGroup>

            <FieldGroup kicker="Reply gate" title="Auto-reply or review, per intent" description="`default` is the floor for every intent nobody has named below, including ones a later release adds to the taxonomy.">
              <EnumSelect
                label="Default"
                value={asString(replyGate.default, "review_required")}
                onChange={(next) => { set(["support_resolver", "reply_gate", "default"], next); }}
                options={[
                  { value: "review_required", label: "Review required" },
                  { value: "auto_reply", label: "Auto-reply" },
                ]}
                allowUnknown={false}
                error={errorMap.get("support_resolver.reply_gate.default")}
              />
              <KeyValueTable
                label="Per-intent overrides"
                hint="Value must be review_required or auto_reply -- not enforced by this table, checked on Validate."
                entries={perIntentEntries}
                onChange={(next) => {
                  const record: JsonObject = {};
                  for (const entry of next) record[entry.key] = entry.value;
                  set(["support_resolver", "reply_gate", "per_intent"], record);
                }}
                valueKind="string"
                keyLabel="Intent"
                valueLabel="Mode"
              />
            </FieldGroup>

            <p className="text-xs text-on-surface-variant">
              Tool bindings (which capability a validated intent makes eligible) are not edited here
              -- five references per binding, one validated against the tool-schema registry at parse
              time. Switch to Advanced to edit <code>tool_bindings</code>.
            </p>
          </div>
        );
      }}
    />
  );
}

function ContextAssemblyTab({ active, canWrite, canPublish }: { active: RuntimeSlice; canWrite: boolean; canPublish: boolean }) {
  const loaded = sliceOf(active.configuration, ["context_assembly"]);
  return (
    <TypedSectionScreen
      kicker="Context assembly"
      title="Context assembly"
      description="How a case's facts become the context a model reasons over: which facts are always included, how large the budget is, and when it should be summarised."
      active={active}
      loaded={loaded}
      canWrite={canWrite}
      canPublish={canPublish}
      jsonLabel="Context assembly JSON"
      notObjectMessage="context_assembly must be an object."
      renderTyped={({ get, set, errorMap }) => {
        const assembly = asObject(get(["context_assembly"]));
        const compaction = asObject(assembly.compaction);
        return (
          <FieldGroup kicker="Context assembly" title="Budget and pinned facts">
            <TagListInput
              label="Pinned fact names"
              hint="Always in the assembled context, whatever the budget. A pinned name that gets trimmed is the failure this list exists to prevent."
              values={asStringArray(assembly.pinned_fact_names)}
              onChange={(next) => { set(["context_assembly", "pinned_fact_names"], next); }}
              error={errorMap.get("context_assembly.pinned_fact_names")}
            />
            <NumberField
              label="Token budget"
              hint="Measured by the tokenizer version below."
              value={asNumber(assembly.token_budget, 8_000)}
              onChange={(next) => { set(["context_assembly", "token_budget"], next); }}
              min={1}
              error={errorMap.get("context_assembly.token_budget")}
            />
            <TextField
              label="Tokenizer version"
              hint="Pinned with promptVersion. The assembler refuses a version it cannot measure rather than falling back to a different estimator."
              value={asString(assembly.tokenizer_version, "wordpiece-approx.v1")}
              onChange={(next) => { set(["context_assembly", "tokenizer_version"], next); }}
              error={errorMap.get("context_assembly.tokenizer_version")}
            />
            <NumberField
              label="Compaction trigger"
              hint="Millionths of the token budget at which a compaction summary should be requested."
              value={asNumber(compaction.trigger_fraction_millionths, 800_000)}
              onChange={(next) => { set(["context_assembly", "compaction", "trigger_fraction_millionths"], next); }}
              min={0}
              max={1_000_000}
              error={errorMap.get("context_assembly.compaction.trigger_fraction_millionths")}
            />
            <TextField
              label="Compaction summary task"
              hint="The ai_gateway task that writes the summary."
              value={asString(compaction.summary_task_id, "support.context.summarize.v1")}
              onChange={(next) => { set(["context_assembly", "compaction", "summary_task_id"], next); }}
              error={errorMap.get("context_assembly.compaction.summary_task_id")}
            />
          </FieldGroup>
        );
      }}
    />
  );
}

function QueuesTab({ active, canWrite, canPublish }: { active: RuntimeSlice; canWrite: boolean; canPublish: boolean }) {
  const loaded = sliceOf(active.configuration, ["support"]);
  return (
    <TypedSectionScreen
      kicker="Support queues"
      title="Queues"
      description="SupportConfiguration -- the work-queue authority, whether an external mirror is kept, the default priority, and the queue list."
      active={active}
      loaded={loaded}
      canWrite={canWrite}
      canPublish={canPublish}
      jsonLabel="Support queues JSON"
      notObjectMessage="support must be an object."
      renderTyped={({ get, set, errorMap }) => {
        const support = asObject(get(["support"]));
        return (
          <FieldGroup kicker="Queues" title="Support work queues">
            <TextField
              label="Authority mode"
              value={asString(support.authority_mode)}
              onChange={(next) => { set(["support", "authority_mode"], next); }}
              error={errorMap.get("support.authority_mode")}
            />
            <Toggle
              label="External mirror enabled"
              value={asBoolean(support.external_mirror_enabled)}
              onChange={(next) => { set(["support", "external_mirror_enabled"], next); }}
            />
            <TextField
              label="Default priority"
              value={asString(support.default_priority)}
              onChange={(next) => { set(["support", "default_priority"], next); }}
              error={errorMap.get("support.default_priority")}
            />
            <TagListInput
              label="Queues"
              values={asStringArray(support.queues)}
              onChange={(next) => { set(["support", "queues"], next); }}
              error={errorMap.get("support.queues")}
            />
            <TextField
              label="External ticket outbox topic"
              value={asString(support.external_ticket_outbox_topic)}
              onChange={(next) => { set(["support", "external_ticket_outbox_topic"], next); }}
              error={errorMap.get("support.external_ticket_outbox_topic")}
            />
          </FieldGroup>
        );
      }}
    />
  );
}
