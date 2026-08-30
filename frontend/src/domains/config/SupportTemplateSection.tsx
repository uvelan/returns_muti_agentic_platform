import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye } from "lucide-react";

import { configApi } from "../../api/configuration";
import {
  defaultReleaseId,
  runPublishPipeline,
  type PublishStep,
} from "../../api/releasePublish";
import {
  supportTemplateApi,
  type PreviewedField,
  type SupportTemplatePreviewResponse,
} from "../../api/supportTemplate";
import { PublishProgress } from "../../components/PublishProgress";
import { useCapabilities } from "../../hooks/capabilityContext";
import { DocumentEditor, type JsonObject } from "./DocumentEditor";

/**
 * The support handoff template: what the platform says to Channel B, as
 * configuration rather than as code.
 *
 * Two things are happening on this screen and they are deliberately separate.
 *
 * **Editing** rides the same `DocumentEditor` the Agents tab uses -- key/value,
 * split, or raw JSON, generated from the document rather than from a hardcoded
 * field list, because the backend's own model is the definition of a valid
 * template and a form built here would be a second, weaker one.
 *
 * **Publishing** rides the platform's one release lifecycle: open a DRAFT,
 * patch the `RETURN_PLATFORM` behaviour domain, promote to VALIDATED, promote
 * to RELEASED. That is the write path this document has -- `support_template`
 * is a field on `ReturnPlatformConfiguration`, so it moves the way every other
 * behaviour field moves, and the case's pinned `configurationReleaseId` is what
 * makes a sent handoff traceable to the template that produced it. (The Agents
 * tab's `PUT`-becomes-a-proposal path is agent-module-shaped and cannot carry
 * this document; the shared thing between the two screens is the editor.)
 *
 * **Preview is the reason this tab is worth having.** A template is only
 * correct with respect to a case, and until now the only way to find out what a
 * variant rendered was to publish it and wait for a handoff. The preview renders
 * the draft in the editor -- not the saved one -- against a fabricated sample
 * case, and shows which variant the selector chose, the text it produced, where
 * every field came from, and which required fields could not be filled.
 */

const RETURN_PLATFORM_DOMAIN_KEY = "RETURN_PLATFORM";

/** The key the template hangs from on the behaviour domain payload. */
const SUPPORT_TEMPLATE_KEY = "support_template";

/**
 * What a release with no template block yet gets in the editor.
 *
 * An empty `variants` list is the honest pre-template state, and the platform
 * treats it as one: the renderer refuses it loudly and the workflow keeps
 * composing the handoff the old way. Seeding a plausible-looking variant here
 * would be this screen inventing configuration nobody published.
 */
const EMPTY_TEMPLATE: JsonObject = {
  template_id: "support-handoff",
  default_variant_id: "default",
  variants: [],
};

type Snapshot = {
  releaseId: string;
  headRevision: number | null;
  template: JsonObject | null;
};

function snapshotOf(snapshot: Readonly<Record<string, unknown>>): Snapshot {
  const configuration = snapshot.configuration;
  const template =
    typeof configuration === "object" && configuration !== null
      ? (configuration as Record<string, unknown>)[SUPPORT_TEMPLATE_KEY]
      : undefined;
  const releaseId = snapshot.release_id;
  const head = snapshot.head_revision;
  return {
    releaseId: typeof releaseId === "string" ? releaseId : "unknown",
    headRevision: typeof head === "number" ? head : null,
    template:
      typeof template === "object" && template !== null && !Array.isArray(template)
        ? (template as JsonObject)
        : null,
  };
}

export function SupportTemplateSection() {
  const { can } = useCapabilities();
  const queryClient = useQueryClient();
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });
  const [steps, setSteps] = useState<readonly PublishStep[]>([]);
  // Held here rather than inside the editor, and that is the whole point.
  // Publishing invalidates the runtime query; the refetch returns the *new*
  // release id, `key={active.releaseId}` changes, and the editor remounts --
  // taking any confirmation rendered inside it with it. The operator would have
  // watched four steps go green and then been told nothing.
  const [published, setPublished] = useState<string | null>(null);
  const [, setDirty] = useState(false);

  if (runtime.isPending) return <p className="text-sm text-on-surface-variant">Loading...</p>;
  if (runtime.error !== null) {
    return (
      <p role="alert" className="text-sm text-error">
        {runtime.error.message}
      </p>
    );
  }

  const active = snapshotOf(runtime.data);
  const canPublish = can("config.release.promote");

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h2 className="text-base font-semibold text-on-surface">Support handoff template</h2>
        <p className="mt-1 max-w-3xl text-sm text-on-surface-variant">
          What the platform sends to support when a return needs an RMA. Variants are chosen by
          declarative selectors -- shipping mode, reason class, order source, item count -- and the
          default variant is used when none of them match.
        </p>
      </header>

      {published !== null ? (
        <p role="status" className="rounded-xl border border-primary/20 bg-secondary-container px-4 py-3 text-sm text-on-secondary-container">
          Release {published} is published. Cases opened from now on pin it; cases already running
          keep the template they started with.
        </p>
      ) : null}

      {active.template === null ? (
        <p className="rounded-lg border border-outline-variant bg-surface-container-low px-3 py-2 text-sm text-on-surface-variant">
          Release {active.releaseId} carries no template yet, so handoffs are still composed the
          built-in way. Adding a variant and publishing is what switches that over.
        </p>
      ) : null}

      <DocumentEditor
        key={active.releaseId}
        kicker="Support template"
        subtitle={`${RETURN_PLATFORM_DOMAIN_KEY} · ${SUPPORT_TEMPLATE_KEY}`}
        badges={
          <span className="rounded-full bg-secondary-container px-2 py-0.5 text-on-secondary-container">
            Release {active.releaseId}
          </span>
        }
        loaded={active.template ?? EMPTY_TEMPLATE}
        canWrite={canPublish}
        jsonLabel="Support template JSON"
        submitLabel="Publish release"
        submittingLabel="Publishing..."
        submitTitle="Publishing a configuration release requires config.release.promote"
        readOnlyNotice="Read-only access. Publishing a configuration release requires config.release.promote. Preview works without it."
        notObjectMessage="A support template must be an object with template_id, default_variant_id and variants."
        // Publishing changes what the platform runs. The Agents tab needs no
        // confirmation because its button only proposes; this one does not.
        confirmSubmit="Publish this template as a new configuration release? Cases opened afterwards pin it."
        notice={<PublishProgress steps={steps} />}
        onDirtyChange={setDirty}
        onSubmit={async (document: JsonObject) => {
          const releaseId = defaultReleaseId("support-template");
          setPublished(null);
          await runPublishPipeline({
            releaseId,
            domainKey: RETURN_PLATFORM_DOMAIN_KEY,
            patch: { [SUPPORT_TEMPLATE_KEY]: document },
            headRevision: active.headRevision,
            onSteps: setSteps,
          });
          setPublished(releaseId);
          await queryClient.invalidateQueries({ queryKey: ["config"] });
          return releaseId;
        }}
        footer={(document) => <VariantPreview template={document} />}
      />
    </div>
  );
}

type PreviewContextForm = {
  shippingModes: string;
  returnReasonClasses: string;
  orderSources: string;
  itemCount: number;
};

/** `BRANCH_LTL, PREPAID_PARCEL` -> `["BRANCH_LTL", "PREPAID_PARCEL"]`. */
function clauseValues(raw: string): string[] {
  return raw
    .split(",")
    .map((value) => value.trim())
    .filter((value) => value !== "");
}

/**
 * Render the draft against the sample case, and say where every value came from.
 *
 * `template` is `null` while the raw JSON in the editor does not parse. That is
 * a normal state mid-edit rather than an error, so the control says why it is
 * unavailable instead of disappearing or refusing after the click.
 */
function VariantPreview({ template }: { template: JsonObject | null }) {
  const fieldId = useId();
  const [form, setForm] = useState<PreviewContextForm>({
    shippingModes: "",
    returnReasonClasses: "",
    orderSources: "",
    itemCount: 1,
  });

  const preview = useMutation({
    mutationFn: (draft: JsonObject) =>
      supportTemplateApi.preview(draft, {
        shipping_modes: clauseValues(form.shippingModes),
        return_reason_classes: clauseValues(form.returnReasonClasses),
        order_sources: clauseValues(form.orderSources),
        item_count: form.itemCount,
      }),
  });

  return (
    <section className="rounded-xl border border-outline-variant bg-surface-container-lowest">
      <header className="flex flex-wrap items-end gap-3 border-b border-outline-variant/80 px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="premium-kicker">Preview</p>
          <h3 className="mt-0.5 text-sm font-semibold text-on-surface">
            Render this draft against a sample case
          </h3>
          <p className="mt-1 max-w-2xl text-xs text-on-surface-variant">
            The sample case is fabricated, never a real one, and no graph read is spent on a
            preview -- a <code>graph:</code> binding shows its fallback or a gap. Describe the case
            shape you want to test and the selectors are judged against it.
          </p>
        </div>
        <button
          type="button"
          onClick={() => { if (template !== null) preview.mutate(template); }}
          disabled={template === null || preview.isPending}
          title={template === null ? "The JSON in the editor does not parse yet" : undefined}
          className="flex items-center gap-1.5 rounded-lg border border-outline-control bg-surface-container-lowest px-3 py-2 text-xs font-semibold text-on-surface-variant transition hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Eye size={13} aria-hidden="true" />
          {preview.isPending ? "Rendering..." : "Render preview"}
        </button>
      </header>

      <div className="grid grid-cols-1 gap-3 px-4 py-3 sm:grid-cols-2 xl:grid-cols-4">
        <ClauseField
          id={`${fieldId}-modes`}
          label="Shipping modes"
          hint="Comma separated, e.g. BRANCH_LTL"
          value={form.shippingModes}
          onChange={(shippingModes) => { setForm({ ...form, shippingModes }); }}
        />
        <ClauseField
          id={`${fieldId}-reasons`}
          label="Return reason classes"
          hint="Comma separated"
          value={form.returnReasonClasses}
          onChange={(returnReasonClasses) => { setForm({ ...form, returnReasonClasses }); }}
        />
        <ClauseField
          id={`${fieldId}-sources`}
          label="Order sources"
          hint="Comma separated"
          value={form.orderSources}
          onChange={(orderSources) => { setForm({ ...form, orderSources }); }}
        />
        <div className="flex flex-col gap-1">
          <label htmlFor={`${fieldId}-items`} className="text-[11px] font-semibold text-on-surface-variant">
            Item count
          </label>
          <input
            id={`${fieldId}-items`}
            type="number"
            min={0}
            value={form.itemCount}
            onChange={(event) => {
              const parsed = Number(event.target.value);
              setForm({
                ...form,
                itemCount: event.target.value === "" || Number.isNaN(parsed) ? 0 : parsed,
              });
            }}
            className="premium-field py-1.5 text-xs"
          />
          <p className="text-[10px] text-outline">Matched against min/max item count clauses</p>
        </div>
      </div>

      <div className="flex flex-col gap-3 border-t border-outline-variant/80 px-4 py-3">
        {/*
          One live region, present from the first render rather than created
          when the result arrives -- a region added to the document at the same
          moment as its content is not reliably announced. It carries the
          one-line summary only: putting the whole rendered message in here
          would read the entire handoff aloud every time.
        */}
        <p role="status" className="text-sm text-on-surface">
          {template === null
            ? "The JSON in the editor does not parse yet, so there is nothing to render. Fix it and the preview comes back."
            : preview.data === undefined
              ? "Nothing rendered yet. Set the case shape above, then render."
              : summarize(preview.data)}
        </p>
        {preview.error !== null ? (
          <p role="alert" className="rounded-lg border border-error/20 bg-error-container px-3 py-2 text-sm text-on-error-container">
            {preview.error.message}
          </p>
        ) : null}
        {template !== null && preview.data !== undefined ? (
          <RenderedPreview rendered={preview.data} />
        ) : null}
      </div>
    </section>
  );
}

function ClauseField({
  id,
  label,
  hint,
  value,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-[11px] font-semibold text-on-surface-variant">
        {label}
      </label>
      <input
        id={id}
        type="text"
        value={value}
        onChange={(event) => { onChange(event.target.value); }}
        className="premium-field py-1.5 font-mono text-xs"
      />
      <p className="text-[10px] text-outline">{hint}</p>
    </div>
  );
}

/** The one line the live region announces. Deliberately short. */
function summarize(rendered: SupportTemplatePreviewResponse): string {
  const count = rendered.gaps.length;
  return count === 0
    ? `Variant ${rendered.variant_id} rendered with every required field filled.`
    : `Variant ${rendered.variant_id} rendered with ${String(count)} required field${count === 1 ? "" : "s"} unfilled.`;
}

function RenderedPreview({ rendered }: { rendered: SupportTemplatePreviewResponse }) {
  return (
    <div className="flex flex-col gap-3">
      {rendered.gaps.length > 0 ? (
        <div className="rounded-lg border border-error/20 bg-error-container px-3 py-2 text-sm text-on-error-container">
          <p className="font-semibold">This draft would be held rather than sent.</p>
          <ul className="mt-1 flex flex-col gap-0.5">
            {rendered.gaps.map((gap) => (
              <li key={gap.field_id}>
                <code className="text-xs">{gap.field_id}</code> — {gap.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div>
        <p className="premium-kicker mb-1">Subject</p>
        <p className="rounded-lg border border-outline-variant bg-surface-container-low px-3 py-2 text-sm text-on-surface">
          {rendered.subject}
        </p>
      </div>

      <div>
        <p className="premium-kicker mb-1">Message</p>
        <pre className="max-h-96 overflow-auto rounded-lg border border-outline-control bg-rail-surface p-3 font-mono text-xs leading-5 text-rail-on-surface">
          {rendered.text}
        </pre>
      </div>

      <div>
        <p className="premium-kicker mb-1">Where each value came from</p>
        <ul className="flex flex-col gap-2">
          {rendered.sections.map((section) => (
            <li
              key={`${section.section_id}:${section.return_record_id ?? ""}`}
              className="rounded-lg border border-outline-variant/70 bg-surface-container-low p-3"
            >
              <p className="text-[11px] font-semibold text-on-surface-variant">
                {section.title ?? section.section_id}
                {section.return_record_id !== null ? (
                  <span className="ml-2 rounded-full bg-secondary-container px-2 py-0.5 text-[10px] text-on-secondary-container">
                    Record {section.return_record_id}
                  </span>
                ) : null}
              </p>
              {section.fields.length === 0 ? (
                <p className="mt-1 text-xs text-outline">No fields.</p>
              ) : (
                <dl className="mt-2 flex flex-col gap-2">
                  {section.fields.map((field) => (
                    <FieldProvenance key={field.field_id} field={field} />
                  ))}
                </dl>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function FieldProvenance({ field }: { field: PreviewedField }) {
  return (
    <div className="flex flex-col gap-1 border-l border-outline-variant pl-3">
      <dt className="text-xs font-semibold text-on-surface">
        {field.label ?? field.field_id}
      </dt>
      <dd className="flex flex-col gap-1">
        <span className="break-words text-xs text-on-surface-variant">{field.value}</span>
        <span className="flex flex-wrap items-center gap-1.5 text-[10px]">
          <span className="rounded-full bg-surface-container px-2 py-0.5 font-medium text-on-surface-variant">
            {field.source}: {field.source_path}
          </span>
          {field.fact_id !== null ? (
            <span className="rounded-full bg-surface-container px-2 py-0.5 font-mono text-outline">
              fact {field.fact_id}
            </span>
          ) : null}
          {field.applied_fallback ? (
            <span className="rounded-full bg-tertiary-container px-2 py-0.5 font-medium text-on-tertiary-container">
              Fallback used
            </span>
          ) : null}
        </span>
      </dd>
    </div>
  );
}
