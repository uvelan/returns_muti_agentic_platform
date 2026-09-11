import { useQuery } from "@tanstack/react-query";
import { Minus, Plus, X } from "lucide-react";

import { configApi } from "../../api/configuration";
import { schemaReleasesApi } from "../../api/schemaReleases";
import { FieldGroup } from "../../components/forms/FieldGroup";
import { NumberField } from "../../components/forms/NumberField";
import { OrderedList } from "../../components/forms/OrderedList";
import { TagListInput } from "../../components/forms/TagListInput";
import { Toggle } from "../../components/forms/Toggle";
import { useCapabilities } from "../../hooks/capabilityContext";
import type { Json, JsonObject } from "./DocumentEditor";
import { extractKnownPaths } from "./knownSourcePaths";
import { asArray, asBoolean, asNumber, asObject, asString, asStringArray, sliceOf } from "./jsonPath";
import { PathPickerList } from "./PathPickerList";
import { runtimeSliceOf, type RuntimeSlice } from "./runtimeSlice";
import { TextField } from "./TextField";
import { TypedSectionScreen } from "./TypedSectionScreen";

/**
 * `/config/discovery` -- how an order is identified and confirmed from what
 * an associate says: `discovery.identification_fields` and a handful of its
 * scalars, `source_resolution`'s path lists, `clarification_policy.fields`,
 * `selection_vocabulary`.
 *
 * All four are `RETURN_PLATFORM` top-level keys, published together as one
 * merge patch -- see `TypedSectionScreen`, which owns load/draft/validate/
 * publish/Advanced-toggle for every CFG-4 screen.
 *
 * **Coverage is deliberately partial, and the JSON escape hatch is where the
 * rest lives.** `source_resolution` alone carries nineteen `*_paths` fields;
 * this typed form covers the ones an operator most often has to change
 * (identification, contact, and product-lookup paths) rather than
 * nineteen near-identical `PathPickerList`s, and `discovery.conversation`/
 * `.progressive`/`.anchor_extractors` render on no control here at all. A
 * field not rendered here is not lost: the draft this screen edits is a full
 * copy of the loaded section, so publishing sends only what actually
 * changed, and Advanced mode edits the same slice as JSON with nothing held
 * back.
 */

const SECTION_KEYS = ["discovery", "source_resolution", "clarification_policy", "selection_vocabulary"] as const;

export function DiscoverySection() {
  const { can } = useCapabilities();
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });
  const schema = useQuery({
    queryKey: ["schema-releases", "active-document"],
    queryFn: () => schemaReleasesApi.activeDocument(),
  });

  if (runtime.isPending) return <p className="text-sm text-on-surface-variant">Loading...</p>;
  if (runtime.error !== null) {
    return <p role="alert" className="text-sm text-error">{runtime.error.message}</p>;
  }

  const active = runtimeSliceOf(runtime.data);
  const knownPaths = schema.data !== undefined ? extractKnownPaths(schema.data.document) : [];

  return (
    <DiscoveryEditor
      key={active.releaseId}
      active={active}
      knownPaths={knownPaths}
      canWrite={can("config.release.write")}
      canPublish={can("config.release.promote")}
    />
  );
}

function DiscoveryEditor({
  active,
  knownPaths,
  canWrite,
  canPublish,
}: {
  active: RuntimeSlice;
  knownPaths: readonly string[];
  canWrite: boolean;
  canPublish: boolean;
}) {
  const loaded = sliceOf(active.configuration, SECTION_KEYS);

  return (
    <TypedSectionScreen
      kicker="Order discovery"
      title="Discovery"
      description="How an order is found and confirmed from what an associate says: identification fields, where each fact is read from, and what the copilot asks for."
      active={active}
      loaded={loaded}
      canWrite={canWrite}
      canPublish={canPublish}
      jsonLabel="Discovery section JSON"
      notObjectMessage="Each of discovery, source_resolution, clarification_policy and selection_vocabulary must be an object."
      renderTyped={({ get, set, errorMap }) => {
        const discovery = asObject(get(["discovery"]));
        const sourceResolution = asObject(get(["source_resolution"]));
        const clarificationPolicy = asObject(get(["clarification_policy"]));
        const selectionVocabulary = asObject(get(["selection_vocabulary"]));

        return (
          <div className="flex flex-col gap-4">
            <FieldGroup kicker="Discovery" title="Identification" description="Fields an associate's description can be matched against.">
              <NumberField
                label="Ambiguity gap"
                hint="Millionths -- how much two candidates' scores must differ before one is preferred without asking."
                value={asNumber(discovery.ambiguity_gap_millionths)}
                onChange={(next) => { set(["discovery", "ambiguity_gap_millionths"], next); }}
                min={0}
                max={1_000_000}
                error={errorMap.get("discovery.ambiguity_gap_millionths")}
              />
              <Toggle
                label="Allow auto-confirmation"
                hint="Must stay off in production -- the platform refuses to boot with this on outside a dev/test build."
                value={asBoolean(discovery.auto_confirmation_allowed)}
                onChange={(next) => { set(["discovery", "auto_confirmation_allowed"], next); }}
              />
              <TextField
                label="Free-text fallback anchor"
                hint="Which identification field an unstructured description is matched against when nothing else anchors it."
                value={asString(discovery.free_text_fallback_anchor)}
                onChange={(next) => { set(["discovery", "free_text_fallback_anchor"], next); }}
                error={errorMap.get("discovery.free_text_fallback_anchor")}
              />
              <TagListInput
                label="Strong anchors"
                hint="Identification fields that, alone, resolve an order with no further confirmation."
                values={asStringArray(discovery.strong_anchors)}
                onChange={(next) => { set(["discovery", "strong_anchors"], next); }}
                suggestions={asArray(discovery.identification_fields).map((field) =>
                  asString(asObject(field).intent_key),
                )}
              />
            </FieldGroup>

            <IdentificationFields
              fields={asArray(discovery.identification_fields)}
              onChange={(next) => { set(["discovery", "identification_fields"], next); }}
              error={errorMap.get("discovery.identification_fields")}
            />

            <FieldGroup kicker="Source resolution" title="Where each fact is read from" description="A source path is autocompleted from the active graph schema, but free text is always accepted -- a release can carry a binding the schema has not been re-synced to yet.">
              <PathPickerList
                label="Order number paths"
                values={asStringArray(sourceResolution.order_number_paths)}
                onChange={(next) => { set(["source_resolution", "order_number_paths"], next); }}
                paths={knownPaths}
              />
              <PathPickerList
                label="Ship-via paths"
                values={asStringArray(sourceResolution.ship_via_paths)}
                onChange={(next) => { set(["source_resolution", "ship_via_paths"], next); }}
                paths={knownPaths}
              />
              <PathPickerList
                label="Customer phone paths"
                values={asStringArray(sourceResolution.customer_phone_paths)}
                onChange={(next) => { set(["source_resolution", "customer_phone_paths"], next); }}
                paths={knownPaths}
              />
              <PathPickerList
                label="Customer email paths"
                values={asStringArray(sourceResolution.customer_email_paths)}
                onChange={(next) => { set(["source_resolution", "customer_email_paths"], next); }}
                paths={knownPaths}
              />
              <PathPickerList
                label="SKU paths"
                values={asStringArray(sourceResolution.sku_paths)}
                onChange={(next) => { set(["source_resolution", "sku_paths"], next); }}
                paths={knownPaths}
              />
              <PathPickerList
                label="Product description paths"
                values={asStringArray(sourceResolution.product_description_paths)}
                onChange={(next) => { set(["source_resolution", "product_description_paths"], next); }}
                paths={knownPaths}
              />
              <p className="text-xs text-on-surface-variant">
                Thirteen more path lists on this section (order dates, customer identity, line items and
                more) are not shown here individually -- switch to Advanced to edit any of them.
              </p>
            </FieldGroup>

            <ClarificationFields
              fields={asArray(clarificationPolicy.fields)}
              onChange={(next) => { set(["clarification_policy", "fields"], next); }}
              error={errorMap.get("clarification_policy.fields")}
            />

            <FieldGroup kicker="Selection vocabulary" title="Reasons and conditions" description="The words a selection is described with -- reasons must be one of the platform's known return reasons; conditions are free-form.">
              <TagListInput
                label="Return reasons"
                values={asStringArray(selectionVocabulary.reasons)}
                onChange={(next) => { set(["selection_vocabulary", "reasons"], next); }}
                suggestions={[
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
                ]}
                hint="Free text is accepted, but only a value from this vocabulary validates -- see the model's ReturnReason enum."
                error={errorMap.get("selection_vocabulary.reasons")}
              />
              <TagListInput
                label="Item conditions"
                hint="No fixed vocabulary exists for conditions -- any non-empty, non-duplicate value is accepted."
                values={asStringArray(selectionVocabulary.conditions)}
                onChange={(next) => { set(["selection_vocabulary", "conditions"], next); }}
              />
            </FieldGroup>
          </div>
        );
      }}
    />
  );
}

function newIdentificationField(): JsonObject {
  return {
    field_id: "",
    intent_key: "",
    enabled: true,
    value_type: "STRING",
    multiple: false,
    label: "",
    description: "",
    aliases: [],
    normalization: "NONE",
    sensitivity: "NONE",
    clarification_priority: 0,
    searches: [],
    searches_only_with: null,
    known_values: [],
  };
}

function IdentificationFields({
  fields,
  onChange,
  error,
}: {
  fields: readonly Json[];
  onChange: (next: Json[]) => void;
  error?: string;
}) {
  return (
    <FieldGroup
      kicker="Discovery"
      title="Identification fields"
      description="What an associate's description is matched against, in the order the copilot tries them."
    >
      <OrderedList
        label="Identification fields"
        error={error}
        items={fields.map((field, index) => ({ field: asObject(field), index }))}
        keyOf={({ field, index }) => asString(field.field_id) || `field-${String(index)}`}
        onChange={(next) => { onChange(next.map(({ field }) => field)); }}
        renderItem={({ field, index }) => (
          <IdentificationFieldCard
            field={field}
            onChange={(next) => {
              onChange(fields.map((entry, at) => (at === index ? next : entry)));
            }}
            onRemove={() => { onChange(fields.filter((_, at) => at !== index)); }}
          />
        )}
      />
      <button
        type="button"
        onClick={() => { onChange([...fields, newIdentificationField()]); }}
        className="flex w-fit items-center gap-1 rounded-lg border border-dashed border-outline-control bg-surface-container-low/60 px-2.5 py-1.5 text-[11px] font-medium text-on-surface-variant transition hover:border-primary hover:text-primary"
      >
        <Plus size={12} aria-hidden="true" />
        Add identification field
      </button>
    </FieldGroup>
  );
}

function IdentificationFieldCard({
  field,
  onChange,
  onRemove,
}: {
  field: JsonObject;
  onChange: (next: JsonObject) => void;
  onRemove: () => void;
}) {
  function update(key: string, value: Json) {
    onChange({ ...field, [key]: value });
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-outline-variant bg-surface-container-lowest p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-2">
          <TextField label="Field id" value={asString(field.field_id)} onChange={(next) => { update("field_id", next); }} required />
          <TextField label="Intent key" value={asString(field.intent_key)} onChange={(next) => { update("intent_key", next); }} required />
          <TextField label="Label" value={asString(field.label)} onChange={(next) => { update("label", next); }} required />
          <NumberField
            label="Clarification priority"
            hint="0 to 10,000"
            value={asNumber(field.clarification_priority)}
            onChange={(next) => { update("clarification_priority", next); }}
            min={0}
            max={10_000}
          />
        </div>
        <button
          type="button"
          aria-label={`Remove identification field ${asString(field.field_id) || asString(field.label)}`}
          onClick={onRemove}
          className="flex size-8 shrink-0 items-center justify-center rounded-md text-outline transition hover:bg-error-container hover:text-error"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>
      <TagListInput
        label="Aliases"
        hint="Other words an associate might use for this field."
        values={asStringArray(field.aliases)}
        onChange={(next) => { update("aliases", next); }}
      />
      <TagListInput
        label="Known values"
        hint="Values this field is already known to accept, if any."
        values={asStringArray(field.known_values)}
        onChange={(next) => { update("known_values", next); }}
      />
      <SearchesEditor
        searches={asArray(field.searches)}
        onChange={(next) => { update("searches", next); }}
      />
    </div>
  );
}

function newSearch(): JsonObject {
  return {
    entity: "",
    field: "",
    strategy: "EXACT",
    limit: 5,
    result_fields: [],
    value_form: "AS_TYPED",
    narrow_with: [],
    searches_only_with: null,
    known_values: [],
  };
}

/**
 * `identification_fields[].searches` -- a `KeyValueTable`-style sub-list,
 * per the brief's own wording: entity/field are schema-shaped (free text,
 * since neither is an enum this client has a vocabulary for), `strategy` and
 * `value_form` are documented conventions (`EXACT`/`CONTAINS`/`FULLTEXT`,
 * `AS_TYPED`/`DIGITS`/`LOWERCASE`) rather than a backend-enforced enum --
 * both stay free text with a hint rather than an invented `EnumSelect` list.
 * `narrow_with` (a nested path-step structure) is not edited here; it is
 * reachable in Advanced mode.
 */
function SearchesEditor({
  searches,
  onChange,
}: {
  searches: readonly Json[];
  onChange: (next: Json[]) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <p className="premium-kicker">Searches</p>
      <ul className="flex flex-col gap-2">
        {searches.map((raw, index) => {
          const search = asObject(raw);
          function update(key: string, value: Json) {
            onChange(searches.map((entry, at) => (at === index ? { ...asObject(entry), [key]: value } : entry)));
          }
          return (
            // Index as key: a search row carries no stable id of its own, and
            // this list is never reordered (only appended to or removed from).
            <li key={index} className="flex flex-col gap-2 rounded-lg border border-outline-variant/70 bg-surface-container-low p-2.5">
              <div className="flex items-start justify-between gap-2">
                <div className="grid flex-1 grid-cols-2 gap-2 sm:grid-cols-4">
                  <TextField label="Entity" value={asString(search.entity)} onChange={(next) => { update("entity", next); }} />
                  <TextField label="Field" value={asString(search.field)} onChange={(next) => { update("field", next); }} />
                  <TextField label="Strategy" hint="e.g. EXACT, CONTAINS, FULLTEXT" value={asString(search.strategy)} onChange={(next) => { update("strategy", next); }} />
                  <NumberField label="Limit" value={asNumber(search.limit, 5)} onChange={(next) => { update("limit", next); }} min={1} max={100} />
                </div>
                <button
                  type="button"
                  aria-label={`Remove search ${String(index + 1)}`}
                  onClick={() => { onChange(searches.filter((_, at) => at !== index)); }}
                  className="flex size-8 shrink-0 items-center justify-center rounded-md text-outline transition hover:bg-error-container hover:text-error"
                >
                  <Minus size={13} aria-hidden="true" />
                </button>
              </div>
              <TagListInput
                label="Known values"
                values={asStringArray(search.known_values)}
                onChange={(next) => { update("known_values", next); }}
              />
            </li>
          );
        })}
      </ul>
      <button
        type="button"
        onClick={() => { onChange([...searches, newSearch()]); }}
        className="flex w-fit items-center gap-1 rounded-lg border border-dashed border-outline-control bg-surface-container-low/60 px-2.5 py-1.5 text-[11px] font-medium text-on-surface-variant transition hover:border-primary hover:text-primary"
      >
        <Plus size={12} aria-hidden="true" />
        Add search
      </button>
    </div>
  );
}

function ClarificationFields({
  fields,
  onChange,
  error,
}: {
  fields: readonly Json[];
  onChange: (next: Json[]) => void;
  error?: string;
}) {
  return (
    <FieldGroup kicker="Clarification policy" title="What the copilot asks for" description="Priority decides which field is asked about first when several are missing.">
      <OrderedList
        label="Clarification fields"
        error={error}
        items={fields.map((field, index) => ({ field: asObject(field), index }))}
        keyOf={({ field, index }) => asString(field.field) || `clarify-${String(index)}`}
        onChange={(next) => { onChange(next.map(({ field }) => field)); }}
        renderItem={({ field, index }) => (
          <div className="flex flex-col gap-2 py-1">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <TextField
                label="Field"
                value={asString(field.field)}
                onChange={(next) => { onChange(fields.map((entry, at) => (at === index ? { ...asObject(entry), field: next } : entry))); }}
              />
              <TextField
                label="Label"
                value={asString(field.label)}
                onChange={(next) => { onChange(fields.map((entry, at) => (at === index ? { ...asObject(entry), label: next } : entry))); }}
              />
              <NumberField
                label="Priority"
                hint="0 to 10,000"
                value={asNumber(field.priority)}
                onChange={(next) => { onChange(fields.map((entry, at) => (at === index ? { ...asObject(entry), priority: next } : entry))); }}
                min={0}
                max={10_000}
              />
            </div>
            <div className="flex flex-wrap gap-4">
              <Toggle
                label="Customer answerable"
                value={asBoolean(field.customer_answerable)}
                onChange={(next) => { onChange(fields.map((entry, at) => (at === index ? { ...asObject(entry), customer_answerable: next } : entry))); }}
              />
              <Toggle
                label="Confirmation required"
                value={asBoolean(field.confirmation_required)}
                onChange={(next) => { onChange(fields.map((entry, at) => (at === index ? { ...asObject(entry), confirmation_required: next } : entry))); }}
              />
            </div>
          </div>
        )}
      />
    </FieldGroup>
  );
}
