import { useQuery } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";

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
import { asArray, asBoolean, asNumber, asObject, asString, asStringArray, sliceOf } from "./jsonPath";
import { runtimeSliceOf, type RuntimeSlice } from "./runtimeSlice";
import { TextField } from "./TextField";
import { TypedSectionScreen } from "./TypedSectionScreen";

/**
 * `/config/fulfilment` -- shipments, bays and the order-management
 * connection: `shipment_tracking` (statuses ladder, `source_mirror`/
 * `source_constants`), `bay`, `omc`.
 */

const SECTION_KEYS = ["shipment_tracking", "bay", "omc"] as const;

const PROJECTION_STATUSES = [
  { value: "", label: "(none)" },
  { value: "AWAITING_HANDOFF", label: "Awaiting handoff" },
  { value: "IN_TRANSIT", label: "In transit" },
  { value: "DELIVERED", label: "Delivered" },
  { value: "RECEIVED", label: "Received" },
  { value: "CANCELLED", label: "Cancelled" },
];

export function FulfilmentSection() {
  const { can } = useCapabilities();
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });

  if (runtime.isPending) return <p className="text-sm text-on-surface-variant">Loading...</p>;
  if (runtime.error !== null) {
    return <p role="alert" className="text-sm text-error">{runtime.error.message}</p>;
  }

  const active = runtimeSliceOf(runtime.data);
  return (
    <FulfilmentEditor
      key={active.releaseId}
      active={active}
      canWrite={can("config.release.write")}
      canPublish={can("config.release.promote")}
    />
  );
}

function FulfilmentEditor({
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
      kicker="Fulfilment"
      title="Fulfilment"
      description="The shipment status ladder and its transitions, bay reservation and capacity rules, and the order-management connection."
      active={active}
      loaded={loaded}
      canWrite={canWrite}
      canPublish={canPublish}
      jsonLabel="Fulfilment section JSON"
      notObjectMessage="Each of shipment_tracking, bay and omc must be an object."
      renderTyped={({ get, set, errorMap }) => {
        const shipmentTracking = asObject(get(["shipment_tracking"]));
        const bay = asObject(get(["bay"]));
        const omc = asObject(get(["omc"]));
        const statuses = asArray(shipmentTracking.statuses);
        const knownCodes = statuses.map((status) => asString(asObject(status).code)).filter((code) => code !== "");

        return (
          <div className="flex flex-col gap-4">
            <StatusLadder
              statuses={statuses}
              knownCodes={knownCodes}
              onChange={(next) => { set(["shipment_tracking", "statuses"], next); }}
              errorMap={errorMap}
            />

            <FieldGroup kicker="Shipment tracking" title="Status catalogue defaults" description="Where a new shipment starts, by ladder.">
              <TextField
                label="Initial status (parcel)"
                value={asString(shipmentTracking.initial_status_parcel)}
                onChange={(next) => { set(["shipment_tracking", "initial_status_parcel"], next); }}
                hint="Must name a code on the parcel ladder."
                error={errorMap.get("shipment_tracking.initial_status_parcel")}
              />
              <TextField
                label="Initial status (freight)"
                value={asString(shipmentTracking.initial_status_freight)}
                onChange={(next) => { set(["shipment_tracking", "initial_status_freight"], next); }}
                hint="Must name a code on the freight ladder."
                error={errorMap.get("shipment_tracking.initial_status_freight")}
              />
              <TagListInput
                label="Freight methods"
                hint="Return methods routed onto the freight ladder rather than parcel."
                values={asStringArray(shipmentTracking.freight_methods)}
                onChange={(next) => { set(["shipment_tracking", "freight_methods"], next); }}
              />
            </FieldGroup>

            <FieldGroup kicker="Shipment tracking" title="Source mirror and constants" description="How the mirrored shipment document's physical fields are filled: source_mirror copies a logical field's value in; source_constants writes a literal.">
              <KeyValueTable
                label="Source mirror"
                hint="Physical path -> logical field name (shipment_tracking.fields)."
                error={errorMap.get("shipment_tracking.source_mirror")}
                entries={Object.entries(asObject(shipmentTracking.source_mirror)).map(
                  ([key, value]): KeyValueEntry => ({ key, value }),
                )}
                onChange={(next) => {
                  const record: JsonObject = {};
                  for (const entry of next) record[entry.key] = entry.value;
                  set(["shipment_tracking", "source_mirror"], record);
                }}
                valueKind="string"
              />
              <KeyValueTable
                label="Source constants"
                hint="Physical path -> literal value, written as-is."
                error={errorMap.get("shipment_tracking.source_constants")}
                entries={Object.entries(asObject(shipmentTracking.source_constants)).map(
                  ([key, value]): KeyValueEntry => ({ key, value }),
                )}
                onChange={(next) => {
                  const record: JsonObject = {};
                  for (const entry of next) record[entry.key] = entry.value;
                  set(["shipment_tracking", "source_constants"], record);
                }}
                valueKind="string"
              />
            </FieldGroup>

            <FieldGroup kicker="Bay" title="Bay placement" description="Reservation and receipt rules for warehouse bay assignment.">
              <TextField
                label="Authority mode"
                value={asString(bay.authority_mode)}
                onChange={(next) => { set(["bay", "authority_mode"], next); }}
                error={errorMap.get("bay.authority_mode")}
              />
              <Toggle
                label="Require physical receipt"
                hint="A bay assignment is not honoured until the item is physically received."
                value={asBoolean(bay.require_physical_receipt)}
                onChange={(next) => { set(["bay", "require_physical_receipt"], next); }}
              />
              <Toggle
                label="Allow pre-arrival reservation"
                hint="A bay may be reserved before the item has arrived."
                value={asBoolean(bay.allow_prearrival_reservation)}
                onChange={(next) => { set(["bay", "allow_prearrival_reservation"], next); }}
              />
              <TagListInput
                label="Eligible statuses"
                hint="Case physical-status values a bay assignment is offered for. No enum exists on the model (free NonBlank strings) -- suggestions are the release's own current values plus the codes named in code comments, not an exhaustive list."
                values={asStringArray(bay.eligible_statuses)}
                onChange={(next) => { set(["bay", "eligible_statuses"], next); }}
                // RV F7: this used to be only the six codes documented in
                // `case_placement.py`'s comments -- two of the live release's
                // own three values (`WAREHOUSE_RECEIVED`, `INSPECTION_COMPLETE`)
                // were missing from it, so the one field an operator could not
                // extend without the suggestion covering what was actually
                // deployed. Seeded from the loaded value first (always
                // accurate to what is running) so the deployment's own
                // codes never go missing regardless of whether the
                // hardcoded list is complete.
                suggestions={[
                  ...new Set([
                    ...asStringArray(bay.eligible_statuses),
                    "AWAITING_RECEIPT",
                    "WAREHOUSE_STAGED",
                    "PLANNED",
                    "STAGED_AT_BRANCH",
                    "LICENSE_PLATE_ASSIGNED",
                    "UNKNOWN",
                  ]),
                ]}
                error={errorMap.get("bay.eligible_statuses")}
              />
            </FieldGroup>

            <FieldGroup kicker="OMC" title="Order management connection" description="Table names, status mappings and cancellation/display rules for the order-management system.">
              <TextField
                label="V2 customer return table"
                value={asString(omc.v2_customer_return_table)}
                onChange={(next) => { set(["omc", "v2_customer_return_table"], next); }}
              />
              <TextField
                label="V1 customer return table"
                value={asString(omc.v1_customer_return_table)}
                onChange={(next) => { set(["omc", "v1_customer_return_table"], next); }}
              />
              <KeyValueTable
                label="Customer return display"
                hint="Status code -> display label."
                error={errorMap.get("omc.customer_return_display")}
                entries={Object.entries(asObject(omc.customer_return_display)).map(
                  ([key, value]): KeyValueEntry => ({ key, value }),
                )}
                onChange={(next) => {
                  const record: JsonObject = {};
                  for (const entry of next) record[entry.key] = entry.value;
                  set(["omc", "customer_return_display"], record);
                }}
                valueKind="string"
              />
              <KeyValueTable
                label="Normalized statuses"
                hint="OMC status code -> the platform's own normalized status."
                error={errorMap.get("omc.normalized_statuses")}
                entries={Object.entries(asObject(omc.normalized_statuses)).map(
                  ([key, value]): KeyValueEntry => ({ key, value }),
                )}
                onChange={(next) => {
                  const record: JsonObject = {};
                  for (const entry of next) record[entry.key] = entry.value;
                  set(["omc", "normalized_statuses"], record);
                }}
                valueKind="string"
              />
              <Toggle
                label="Tendered is pickup"
                value={asBoolean(omc.tendered_is_pickup)}
                onChange={(next) => { set(["omc", "tendered_is_pickup"], next); }}
              />
              <Toggle
                label="License plate implies receipt"
                value={asBoolean(omc.license_plate_implies_receipt)}
                onChange={(next) => { set(["omc", "license_plate_implies_receipt"], next); }}
              />
              <Toggle
                label="RGA is customer return"
                value={asBoolean(omc.rga_is_customer_return)}
                onChange={(next) => { set(["omc", "rga_is_customer_return"], next); }}
              />
            </FieldGroup>
          </div>
        );
      }}
    />
  );
}

function newStatus(): JsonObject {
  return {
    code: "",
    label: "",
    ladder: "parcel",
    ordinal: 0,
    terminal: false,
    exception_state: false,
    color_token: "progress",
    allowed_next: [],
    projection_status: null,
  };
}

function StatusLadder({
  statuses,
  knownCodes,
  onChange,
  errorMap,
}: {
  statuses: readonly Json[];
  knownCodes: readonly string[];
  onChange: (next: Json[]) => void;
  errorMap: ReadonlyMap<string, string>;
}) {
  return (
    <FieldGroup
      kicker="Shipment tracking"
      title="Status ladder"
      description="Every status a shipment can be in, and which ones it may move to next. Reordering this list is for readability only -- ordinal is what the platform actually orders by."
    >
      <OrderedList
        label="Statuses"
        error={errorMap.get("shipment_tracking.statuses")}
        items={statuses.map((status, index) => ({ status: asObject(status), index }))}
        keyOf={({ status, index }) => asString(status.code) || `status-${String(index)}`}
        onChange={(next) => { onChange(next.map(({ status }) => status)); }}
        renderItem={({ status, index }) => (
          <StatusCard
            status={status}
            knownCodes={knownCodes}
            errorMap={errorMap}
            index={index}
            onChange={(next) => { onChange(statuses.map((entry, at) => (at === index ? next : entry))); }}
            onRemove={() => { onChange(statuses.filter((_, at) => at !== index)); }}
          />
        )}
      />
      <button
        type="button"
        onClick={() => { onChange([...statuses, newStatus()]); }}
        className="flex w-fit items-center gap-1 rounded-lg border border-dashed border-outline-control bg-surface-container-low/60 px-2.5 py-1.5 text-[11px] font-medium text-on-surface-variant transition hover:border-primary hover:text-primary"
      >
        <Plus size={12} aria-hidden="true" />
        Add status
      </button>
    </FieldGroup>
  );
}

function StatusCard({
  status,
  knownCodes,
  errorMap,
  index,
  onChange,
  onRemove,
}: {
  status: JsonObject;
  knownCodes: readonly string[];
  errorMap: ReadonlyMap<string, string>;
  index: number;
  onChange: (next: JsonObject) => void;
  onRemove: () => void;
}) {
  function update(key: string, value: Json) {
    onChange({ ...status, [key]: value });
  }
  const path = `shipment_tracking.statuses.${String(index)}`;

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-outline-variant bg-surface-container-lowest p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-2">
          <TextField label="Code" value={asString(status.code)} onChange={(next) => { update("code", next); }} required error={errorMap.get(`${path}.code`)} />
          <TextField label="Label" value={asString(status.label)} onChange={(next) => { update("label", next); }} required />
          <TextField label="Ladder" hint="parcel or freight" value={asString(status.ladder)} onChange={(next) => { update("ladder", next); }} />
          <NumberField label="Ordinal" value={asNumber(status.ordinal)} onChange={(next) => { update("ordinal", next); }} min={0} />
        </div>
        <button
          type="button"
          aria-label={`Remove status ${asString(status.code) || asString(status.label)}`}
          onClick={onRemove}
          className="flex size-8 shrink-0 items-center justify-center rounded-md text-outline transition hover:bg-error-container hover:text-error"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>
      <div className="flex flex-wrap gap-4">
        <Toggle label="Terminal" hint="No further transition is possible." value={asBoolean(status.terminal)} onChange={(next) => { update("terminal", next); }} />
        <Toggle label="Exception state" value={asBoolean(status.exception_state)} onChange={(next) => { update("exception_state", next); }} />
      </div>
      <EnumSelect
        label="Projection status"
        hint="What this status projects onto the case timeline, if anything."
        value={asString(status.projection_status)}
        options={PROJECTION_STATUSES}
        onChange={(next) => { update("projection_status", next === "" ? null : next); }}
      />
      <TagListInput
        label="Allowed next"
        hint="Statuses this one may transition to -- each should name a code on the same ladder."
        values={asStringArray(status.allowed_next)}
        onChange={(next) => { update("allowed_next", next); }}
        suggestions={knownCodes}
      />
    </div>
  );
}
