import { useQuery } from "@tanstack/react-query";
import { Minus, Plus, X } from "lucide-react";
import type { ReactNode } from "react";

import { configApi } from "../../api/configuration";
import { DurationField } from "../../components/forms/DurationField";
import { EnumSelect } from "../../components/forms/EnumSelect";
import { FieldGroup } from "../../components/forms/FieldGroup";
import { KeyValueTable, type KeyValueEntry } from "../../components/forms/KeyValueTable";
import { NumberField } from "../../components/forms/NumberField";
import { OrderedList } from "../../components/forms/OrderedList";
import { TagListInput } from "../../components/forms/TagListInput";
import { Toggle } from "../../components/forms/Toggle";
import { useCapabilities } from "../../hooks/capabilityContext";
import type { Json, JsonObject } from "./DocumentEditor";
import { asArray, asBoolean, asNumber, asObject, asString, asStringArray, sliceOf } from "./jsonPath";
import { runtimeSliceOf, type RuntimeSlice } from "./runtimeSlice";
import { TextField } from "./TextField";
import { TypedSectionScreen } from "./TypedSectionScreen";

/**
 * `/config/workflow` -- the case workflow's stage sequence and SLAs, how long
 * a case waits before it chases Support or parks for operations, the
 * business calendars those waits run against, and scheduled housekeeping.
 *
 * **`workflow`'s shape is not what CFG-5's own brief described.** The design
 * table asked for "stage id, handler type `EnumSelect`, agent ref where the
 * handler is an agent" -- that is `domain/workflow.py`'s `WorkflowConfig`
 * (`WorkflowStageEntry.handler`), a model nothing in `ReturnPlatformConfiguration`
 * actually uses. The real `workflow:` key here is `return_configuration.py`'s
 * `WorkflowConfiguration`: `version`, a flat `stages: tuple[str, ...]` (min 2,
 * unique), `sla_minutes: dict[str, int]` and `completion_dimensions`. There is
 * no per-stage handler and no agent reference to bind -- `WorkflowStageHandlerType`
 * itself lost its `AGENT` member when AGT-02 removed agent dispatch, so even
 * the model the brief was describing has had nothing to point at an agent
 * with for a while. Built against the model that is actually live rather than
 * inventing the handler/agent-ref fields the brief asked for on a model that
 * is not the one released.
 *
 * `housekeeping` *is* present on `ReturnPlatformConfiguration` (checked, per
 * the brief's own instruction), so it is included -- collapsed by default,
 * since its seven reclaimer blocks are the section an operator tunes least
 * often.
 */

const SECTION_KEYS = ["workflow", "return_case", "business_calendars", "housekeeping"] as const;

const WEEKDAYS = [
  { value: "0", label: "Monday" },
  { value: "1", label: "Tuesday" },
  { value: "2", label: "Wednesday" },
  { value: "3", label: "Thursday" },
  { value: "4", label: "Friday" },
  { value: "5", label: "Saturday" },
  { value: "6", label: "Sunday" },
] as const;

export function WorkflowSection() {
  const { can } = useCapabilities();
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });

  if (runtime.isPending) return <p className="text-sm text-on-surface-variant">Loading...</p>;
  if (runtime.error !== null) {
    return <p role="alert" className="text-sm text-error">{runtime.error.message}</p>;
  }

  const active = runtimeSliceOf(runtime.data);
  return (
    <WorkflowEditor
      key={active.releaseId}
      active={active}
      canWrite={can("config.release.write")}
      canPublish={can("config.release.promote")}
    />
  );
}

function WorkflowEditor({
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
      kicker="Workflow"
      title="Workflow"
      description="The case workflow's stage sequence and SLAs, how long a case waits before it chases Support or parks for operations, the business calendars those waits run against, and scheduled operational cleanup."
      active={active}
      loaded={loaded}
      canWrite={canWrite}
      canPublish={canPublish}
      jsonLabel="Workflow section JSON"
      notObjectMessage="Each of workflow, return_case, business_calendars and housekeeping must be an object."
      renderTyped={({ get, set, errorMap }) => {
        const workflow = asObject(get(["workflow"]));
        const returnCase = asObject(get(["return_case"]));
        const housekeeping = asObject(get(["housekeeping"]));
        const calendars = asArray(get(["business_calendars"]));
        const stages = asStringArray(workflow.stages);

        return (
          <div className="flex flex-col gap-4">
            <FieldGroup
              kicker="Case workflow"
              title="Stage sequence"
              description="Every stage the case workflow runs through, in order, and the SLA clock each one is held to."
            >
              <TextField
                label="Version"
                value={asString(workflow.version)}
                onChange={(next) => { set(["workflow", "version"], next); }}
                error={errorMap.get("workflow.version")}
                required
              />
              <WorkflowStages
                stages={stages}
                onChange={(next) => { set(["workflow", "stages"], next); }}
                error={errorMap.get("workflow.stages")}
              />
              <SlaMinutesTable
                slaMinutes={asObject(workflow.sla_minutes)}
                onChange={(next) => { set(["workflow", "sla_minutes"], next); }}
                error={errorMap.get("workflow.sla_minutes")}
              />
              <TagListInput
                label="Completion dimensions"
                hint="What must all be true for the case to count as complete."
                values={asStringArray(workflow.completion_dimensions)}
                onChange={(next) => { set(["workflow", "completion_dimensions"], next); }}
                error={errorMap.get("workflow.completion_dimensions")}
              />
            </FieldGroup>

            <FieldGroup
              kicker="Return case"
              title="Waits and timeouts"
              description="How long the case waits for the associate and for Support before it chases again or parks for operations."
            >
              <DurationField
                label="Bay wait"
                hint="Dead time on the critical path while an associate waits at the bay. Wall clock, not a business duration."
                seconds={asNumber(returnCase.bay_wait_seconds, 120)}
                onChange={(next) => { set(["return_case", "bay_wait_seconds"], next); }}
                error={errorMap.get("return_case.bay_wait_seconds")}
              />
              <DurationField
                label="Item reservation TTL"
                hint="How long a selected quantity is held before the hold expires. Wall clock."
                seconds={asNumber(returnCase.item_reservation_ttl_seconds, 1_800)}
                onChange={(next) => { set(["return_case", "item_reservation_ttl_seconds"], next); }}
                error={errorMap.get("return_case.item_reservation_ttl_seconds")}
              />
              <Toggle
                label="Return details required"
                hint="Refuses the Support handoff until a return has been described, rather than opening the thread as soon as the case clears."
                value={asBoolean(returnCase.return_details_required)}
                onChange={(next) => { set(["return_case", "return_details_required"], next); }}
              />
              <DurationField
                label="Return details wait"
                hint="How long the case waits for a selection before the details-required rule parks it (or the handoff proceeds without it)."
                seconds={asNumber(returnCase.return_details_wait_seconds, 1_800)}
                onChange={(next) => { set(["return_case", "return_details_wait_seconds"], next); }}
                error={errorMap.get("return_case.return_details_wait_seconds")}
              />
              <DurationField
                label="Support response wait"
                hint="Business-calendar duration -- eight hours means eight working hours against the calendar named below, not wall clock."
                seconds={asNumber(returnCase.support_response_wait_seconds, 28_800)}
                onChange={(next) => { set(["return_case", "support_response_wait_seconds"], next); }}
                error={errorMap.get("return_case.support_response_wait_seconds")}
              />
              <DurationField
                label="Reminder interval"
                hint="Business-calendar duration."
                seconds={asNumber(returnCase.reminder_interval_seconds, 7_200)}
                onChange={(next) => { set(["return_case", "reminder_interval_seconds"], next); }}
                error={errorMap.get("return_case.reminder_interval_seconds")}
              />
              <NumberField
                label="Max reminders"
                value={asNumber(returnCase.max_reminders, 3)}
                onChange={(next) => { set(["return_case", "max_reminders"], next); }}
                min={0}
                max={50}
                error={errorMap.get("return_case.max_reminders")}
              />
              <EnumSelect
                label="On reminders exhausted"
                value={asString(returnCase.on_reminders_exhausted, "PARK_FOR_OPERATIONS")}
                onChange={(next) => { set(["return_case", "on_reminders_exhausted"], next); }}
                options={[
                  { value: "PARK_FOR_OPERATIONS", label: "Park for operations" },
                  { value: "ESCALATE", label: "Escalate" },
                ]}
                allowUnknown={false}
                error={errorMap.get("return_case.on_reminders_exhausted")}
              />
              <TextField
                label="Business calendar"
                hint="Which calendar below the business-duration waits above run against."
                value={asString(returnCase.business_calendar_id, "default")}
                onChange={(next) => { set(["return_case", "business_calendar_id"], next); }}
                error={errorMap.get("return_case.business_calendar_id")}
              />
              <TextField
                label="Timezone"
                hint="Fallback zone used when the named calendar does not declare one."
                value={asString(returnCase.timezone, "UTC")}
                onChange={(next) => { set(["return_case", "timezone"], next); }}
                error={errorMap.get("return_case.timezone")}
              />
            </FieldGroup>

            <BusinessCalendars
              calendars={calendars}
              onChange={(next) => { set(["business_calendars"], next); }}
              error={errorMap.get("business_calendars")}
            />

            <HousekeepingFields housekeeping={housekeeping} set={set} />
          </div>
        );
      }}
    />
  );
}

function WorkflowStages({
  stages,
  onChange,
  error,
}: {
  stages: readonly string[];
  onChange: (next: string[]) => void;
  error?: string;
}) {
  return (
    <div className="flex flex-col gap-2">
      <OrderedList
        label="Stages"
        error={error}
        items={stages.map((stage, index) => ({ stage, index }))}
        keyOf={({ stage, index }) => stage || `stage-${String(index)}`}
        onChange={(next) => { onChange(next.map(({ stage }) => stage)); }}
        renderItem={({ stage, index }) => (
          <div className="flex items-center gap-2">
            <TextField
              label={`Stage ${String(index + 1)}`}
              value={stage}
              onChange={(next) => { onChange(stages.map((entry, at) => (at === index ? next : entry))); }}
              required
            />
            <button
              type="button"
              aria-label={`Remove stage ${stage || String(index + 1)}`}
              onClick={() => { onChange(stages.filter((_, at) => at !== index)); }}
              className="flex size-8 shrink-0 items-center justify-center rounded-md text-outline transition hover:bg-error-container hover:text-error"
            >
              <X size={14} aria-hidden="true" />
            </button>
          </div>
        )}
      />
      <button
        type="button"
        onClick={() => { onChange([...stages, ""]); }}
        className="flex w-fit items-center gap-1 rounded-lg border border-dashed border-outline-control bg-surface-container-low/60 px-2.5 py-1.5 text-[11px] font-medium text-on-surface-variant transition hover:border-primary hover:text-primary"
      >
        <Plus size={12} aria-hidden="true" />
        Add stage
      </button>
    </div>
  );
}

/** `sla_minutes` is data-keyed (a stage name -> its SLA), so `KeyValueTable`, not a per-stage control. */
function SlaMinutesTable({
  slaMinutes,
  onChange,
  error,
}: {
  slaMinutes: JsonObject;
  onChange: (next: JsonObject) => void;
  error?: string;
}) {
  const entries: KeyValueEntry[] = Object.entries(slaMinutes).map(([key, value]) => ({
    key,
    value: typeof value === "number" ? value : 0,
  }));
  return (
    <KeyValueTable
      label="SLA minutes per stage"
      hint="How many minutes each stage is held to before it is late. Key is the stage id from the sequence above."
      error={error}
      entries={entries}
      onChange={(next) => {
        const record: JsonObject = {};
        for (const entry of next) record[entry.key] = entry.value;
        onChange(record);
      }}
      valueKind="number"
      // Not "Stage": `KeyValueTable` labels each row's key input
      // `"${keyLabel} ${index + 1}"`, which at "Stage" would collide with the
      // stage-sequence `OrderedList` above (both would render "Stage 1").
      keyLabel="SLA stage"
      valueLabel="Minutes"
    />
  );
}

function newCalendar(): JsonObject {
  return { calendar_id: "", timezone: "UTC", working_periods: [newWorkingPeriod()], holidays: [] };
}

function newWorkingPeriod(): JsonObject {
  return { weekday: 0, start_minute: 480, end_minute: 1_020 };
}

function BusinessCalendars({
  calendars,
  onChange,
  error,
}: {
  calendars: readonly Json[];
  onChange: (next: Json[]) => void;
  error?: string;
}) {
  return (
    <FieldGroup
      kicker="Business calendars"
      title="When each calendar is open"
      description="A calendar's own working periods and holidays -- what the business-calendar waits above run against. No calendar declared means wall-clock behaviour for every wait that asks for one; there is no implicit Monday-Friday."
    >
      <OrderedList
        label="Calendars"
        error={error}
        items={calendars.map((calendar, index) => ({ calendar: asObject(calendar), index }))}
        keyOf={({ calendar, index }) => asString(calendar.calendar_id) || `calendar-${String(index)}`}
        onChange={(next) => { onChange(next.map(({ calendar }) => calendar)); }}
        renderItem={({ calendar, index }) => (
          <BusinessCalendarCard
            calendar={calendar}
            onChange={(next) => { onChange(calendars.map((entry, at) => (at === index ? next : entry))); }}
            onRemove={() => { onChange(calendars.filter((_, at) => at !== index)); }}
          />
        )}
      />
      <button
        type="button"
        onClick={() => { onChange([...calendars, newCalendar()]); }}
        className="flex w-fit items-center gap-1 rounded-lg border border-dashed border-outline-control bg-surface-container-low/60 px-2.5 py-1.5 text-[11px] font-medium text-on-surface-variant transition hover:border-primary hover:text-primary"
      >
        <Plus size={12} aria-hidden="true" />
        Add calendar
      </button>
    </FieldGroup>
  );
}

function BusinessCalendarCard({
  calendar,
  onChange,
  onRemove,
}: {
  calendar: JsonObject;
  onChange: (next: JsonObject) => void;
  onRemove: () => void;
}) {
  function update(key: string, value: Json) {
    onChange({ ...calendar, [key]: value });
  }
  const periods = asArray(calendar.working_periods);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-outline-variant bg-surface-container-lowest p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-2">
          <TextField
            label="Calendar id"
            value={asString(calendar.calendar_id)}
            onChange={(next) => { update("calendar_id", next); }}
            required
          />
          <TextField
            label="Timezone"
            value={asString(calendar.timezone, "UTC")}
            onChange={(next) => { update("timezone", next); }}
          />
        </div>
        <button
          type="button"
          aria-label={`Remove calendar ${asString(calendar.calendar_id) || "unnamed"}`}
          onClick={onRemove}
          className="flex size-8 shrink-0 items-center justify-center rounded-md text-outline transition hover:bg-error-container hover:text-error"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>
      <WorkingPeriods periods={periods} onChange={(next) => { update("working_periods", next); }} />
      <TagListInput
        label="Holidays"
        hint="Whole non-working days in this calendar's own zone, as YYYY-MM-DD."
        values={asStringArray(calendar.holidays)}
        onChange={(next) => { update("holidays", next); }}
      />
    </div>
  );
}

function WorkingPeriods({
  periods,
  onChange,
}: {
  periods: readonly Json[];
  onChange: (next: Json[]) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <p className="premium-kicker">Working periods</p>
      <ul className="flex flex-col gap-2">
        {periods.map((raw, index) => {
          const period = asObject(raw);
          function update(key: string, value: Json) {
            onChange(periods.map((entry, at) => (at === index ? { ...asObject(entry), [key]: value } : entry)));
          }
          return (
            // Index as key: a period carries no stable id of its own.
            <li
              key={index}
              className="flex flex-col gap-2 rounded-lg border border-outline-variant/70 bg-surface-container-low p-2.5 sm:flex-row sm:items-end"
            >
              <div className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-3">
                <EnumSelect
                  label="Weekday"
                  value={String(asNumber(period.weekday))}
                  onChange={(next) => { update("weekday", Number(next)); }}
                  options={WEEKDAYS}
                  allowUnknown={false}
                />
                <NumberField
                  label="Start"
                  hint="Minutes after midnight, 0 to 1439"
                  value={asNumber(period.start_minute)}
                  onChange={(next) => { update("start_minute", next); }}
                  min={0}
                  max={1439}
                />
                <NumberField
                  label="End"
                  hint="Minutes after midnight, 1 to 1440 -- 1440 reaches midnight"
                  value={asNumber(period.end_minute)}
                  onChange={(next) => { update("end_minute", next); }}
                  min={1}
                  max={1440}
                />
              </div>
              <button
                type="button"
                aria-label={`Remove working period ${String(index + 1)}`}
                onClick={() => { onChange(periods.filter((_, at) => at !== index)); }}
                className="flex size-8 shrink-0 items-center justify-center rounded-md text-outline transition hover:bg-error-container hover:text-error"
              >
                <Minus size={13} aria-hidden="true" />
              </button>
            </li>
          );
        })}
      </ul>
      <button
        type="button"
        onClick={() => { onChange([...periods, newWorkingPeriod()]); }}
        className="flex w-fit items-center gap-1 rounded-lg border border-dashed border-outline-control bg-surface-container-low/60 px-2.5 py-1.5 text-[11px] font-medium text-on-surface-variant transition hover:border-primary hover:text-primary"
      >
        <Plus size={12} aria-hidden="true" />
        Add working period
      </button>
    </div>
  );
}

/**
 * `housekeeping`'s seven blocks, collapsed by default: this is the section of
 * the page an operator tunes least often, and expanding it on load would push
 * the stage sequence and the waits -- what a workflow change actually is --
 * below the fold behind reclaimer windows nobody is here to touch today.
 */
function HousekeepingFields({
  housekeeping,
  set,
}: {
  housekeeping: JsonObject;
  set: (path: readonly (string | number)[], value: Json) => void;
}) {
  const temporal = asObject(housekeeping.temporal_executions);
  const graphGenerations = asObject(housekeeping.graph_generations);
  const stalledSync = asObject(housekeeping.stalled_sync_runs);
  const probeDatabases = asObject(housekeeping.probe_databases);
  const orderLineReservations = asObject(housekeeping.order_line_reservations);
  const aiInterceptions = asObject(housekeeping.ai_interceptions);

  return (
    <FieldGroup
      kicker="Housekeeping"
      title="Operational debris reclamation"
      description="Scheduled cleanup: stranded workflow executions, retired graph generations, stalled sync runs, throwaway probe databases, and lapsed reservations and interceptions. Only the windows below are configuration -- what is reclaimable at all is structural and cannot be widened from here."
      collapsible
      defaultOpen={false}
    >
      <Toggle
        label="Housekeeping enabled"
        value={asBoolean(housekeeping.enabled, true)}
        onChange={(next) => { set(["housekeeping", "enabled"], next); }}
      />
      <DurationField
        label="Pass interval"
        hint="How often a housekeeping pass runs."
        seconds={asNumber(housekeeping.interval_seconds, 900)}
        onChange={(next) => { set(["housekeeping", "interval_seconds"], next); }}
      />

      <ReclaimerBlock title="Temporal executions" description="Stranded workflow executions on ephemeral test queues.">
        <Toggle label="Enabled" value={asBoolean(temporal.enabled, true)} onChange={(next) => { set(["housekeeping", "temporal_executions", "enabled"], next); }} />
        <DurationField label="Minimum age" seconds={asNumber(temporal.minimum_age_seconds, 3_600)} onChange={(next) => { set(["housekeeping", "temporal_executions", "minimum_age_seconds"], next); }} />
        <NumberField label="Batch limit" value={asNumber(temporal.batch_limit, 500)} onChange={(next) => { set(["housekeeping", "temporal_executions", "batch_limit"], next); }} min={1} max={10_000} />
      </ReclaimerBlock>

      <ReclaimerBlock title="Graph generations" description="RETIRED generations, once their retention window has passed.">
        <Toggle label="Enabled" value={asBoolean(graphGenerations.enabled, true)} onChange={(next) => { set(["housekeeping", "graph_generations", "enabled"], next); }} />
        <DurationField label="Retention" seconds={asNumber(graphGenerations.retention_seconds, 86_400)} onChange={(next) => { set(["housekeeping", "graph_generations", "retention_seconds"], next); }} />
        <NumberField label="Batch limit" hint="Generations reclaimed per pass" value={asNumber(graphGenerations.batch_limit, 5)} onChange={(next) => { set(["housekeeping", "graph_generations", "batch_limit"], next); }} min={1} max={1_000} />
        <NumberField label="Node delete batch size" value={asNumber(graphGenerations.node_delete_batch_size, 1_000)} onChange={(next) => { set(["housekeeping", "graph_generations", "node_delete_batch_size"], next); }} min={100} max={50_000} />
        <DurationField label="Abandoned build window" seconds={asNumber(graphGenerations.abandoned_build_seconds, 21_600)} onChange={(next) => { set(["housekeeping", "graph_generations", "abandoned_build_seconds"], next); }} />
        <DurationField label="Orphaned active window" seconds={asNumber(graphGenerations.orphaned_active_seconds, 86_400)} onChange={(next) => { set(["housekeeping", "graph_generations", "orphaned_active_seconds"], next); }} />
      </ReclaimerBlock>

      <ReclaimerBlock title="Stalled sync runs" description="A graph-sync run whose process stopped reporting.">
        <Toggle label="Enabled" value={asBoolean(stalledSync.enabled, true)} onChange={(next) => { set(["housekeeping", "stalled_sync_runs", "enabled"], next); }} />
        <DurationField label="Stall window" seconds={asNumber(stalledSync.stall_seconds, 150)} onChange={(next) => { set(["housekeeping", "stalled_sync_runs", "stall_seconds"], next); }} />
        <NumberField label="Batch limit" value={asNumber(stalledSync.batch_limit, 20)} onChange={(next) => { set(["housekeeping", "stalled_sync_runs", "batch_limit"], next); }} min={1} max={1_000} />
      </ReclaimerBlock>

      <ReclaimerBlock title="Probe databases" description="SQL Server databases test suites create and never drop.">
        <Toggle label="Enabled" value={asBoolean(probeDatabases.enabled, true)} onChange={(next) => { set(["housekeeping", "probe_databases", "enabled"], next); }} />
        <TagListInput label="Name suffixes" hint="A positive test -- only a database ending in one of these is a candidate." values={asStringArray(probeDatabases.name_suffixes)} onChange={(next) => { set(["housekeeping", "probe_databases", "name_suffixes"], next); }} />
        <DurationField label="Minimum age" seconds={asNumber(probeDatabases.minimum_age_seconds, 3_600)} onChange={(next) => { set(["housekeeping", "probe_databases", "minimum_age_seconds"], next); }} />
        <NumberField label="Batch limit" value={asNumber(probeDatabases.batch_limit, 50)} onChange={(next) => { set(["housekeeping", "probe_databases", "batch_limit"], next); }} min={1} max={500} />
      </ReclaimerBlock>

      {/* No `enabled` switch on these two: the model carries none (a lapsed
          hold's own expiry, not an age window, decides it -- see the model's
          own docstring), so there is nothing here to bind one to. */}
      <ReclaimerBlock title="Order line reservations" description="Lapsed item holds, settled from ACTIVE to EXPIRED. Always on -- there is no switch on this block in the model.">
        <NumberField label="Batch limit" value={asNumber(orderLineReservations.batch_limit, 200)} onChange={(next) => { set(["housekeeping", "order_line_reservations", "batch_limit"], next); }} min={1} max={5_000} />
      </ReclaimerBlock>

      <ReclaimerBlock title="AI interceptions" description="Lapsed interception holds, settled from PENDING to EXPIRED. Always on -- there is no switch on this block in the model.">
        <NumberField label="Batch limit" value={asNumber(aiInterceptions.batch_limit, 200)} onChange={(next) => { set(["housekeeping", "ai_interceptions", "batch_limit"], next); }} min={1} max={5_000} />
      </ReclaimerBlock>
    </FieldGroup>
  );
}

function ReclaimerBlock({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-outline-variant/70 bg-surface-container-low p-3">
      <div>
        <p className="text-sm font-semibold text-on-surface">{title}</p>
        <p className="mt-0.5 text-xs text-on-surface-variant">{description}</p>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">{children}</div>
    </div>
  );
}
