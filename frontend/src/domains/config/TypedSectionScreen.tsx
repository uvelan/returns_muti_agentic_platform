import { useState, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Braces, ListTree } from "lucide-react";

import { configApi, type ValidationErrorItem } from "../../api/configuration";
import { mergePatchOf } from "../../api/mergePatch";
import { DiffPreview } from "../../components/forms/DiffPreview";
import { PublishBar } from "../../components/forms/PublishBar";
import { ValidationErrors } from "../../components/forms/ValidationErrors";
import { DocumentEditor, type Json, type JsonObject } from "./DocumentEditor";
import { errorsByPath, type RuntimeSlice } from "./runtimeSlice";
import { getPath, setPath } from "./jsonPath";

/**
 * The chrome every CFG-4 typed screen shares: a typed-form/Advanced(JSON)
 * toggle over the *same* section slice, a `DiffPreview` of the merge patch,
 * page-level `ValidationErrors` for anything the typed form has no field
 * for, and a sticky `PublishBar` wired to `POST /api/config/validate/{domain}`
 * and `POST /api/config/publish` -- the brief's own design, factored out once
 * `/config/discovery` and `/config/return-policy` turned out to need
 * identically-shaped footers. `/config/overview` does not use this: it edits
 * no single section slice, and its actions are adopt-packaged, not publish.
 *
 * `renderTyped` gets `get`/`set` bound to dotted-or-indexed paths through the
 * draft slice (`jsonPath.ts`) rather than the whole draft object, so a field
 * binds to `set(["return_policy", "return_method_derivation", "default_method"], next)`
 * without the caller hand-writing an immutable update for every field.
 */
export function TypedSectionScreen({
  kicker,
  title,
  description,
  domainKey = "RETURN_PLATFORM",
  active,
  loaded,
  canWrite,
  canPublish,
  jsonLabel,
  notObjectMessage,
  renderTyped,
}: {
  kicker: string;
  title: string;
  description: string;
  domainKey?: string;
  active: RuntimeSlice;
  /** This screen's slice of the domain document -- one or more top-level keys. */
  loaded: JsonObject;
  canWrite: boolean;
  canPublish: boolean;
  jsonLabel: string;
  notObjectMessage: string;
  renderTyped: (context: {
    draft: JsonObject;
    get: (path: readonly (string | number)[]) => Json;
    set: (path: readonly (string | number)[], value: Json) => void;
    errorMap: ReadonlyMap<string, string>;
  }) => ReactNode;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<JsonObject>(loaded);
  const [advanced, setAdvanced] = useState(false);
  const [validation, setValidation] = useState<{
    valid: boolean;
    errors: readonly ValidationErrorItem[];
  } | null>(null);
  const [publishedId, setPublishedId] = useState<string | null>(null);

  // `mergePatchOf(JsonObject, JsonObject)` always returns an object patch --
  // the `null`/array arms of its `JsonValue` return type are for a `before`/
  // `after` pair that is not both objects, which `loaded`/`draft` always are
  // here. Narrowed once so every caller below is not left re-asserting it.
  const rawPatch = mergePatchOf(loaded, draft);
  const patch: Readonly<Record<string, unknown>> =
    typeof rawPatch === "object" && rawPatch !== null && !Array.isArray(rawPatch) ? rawPatch : {};
  const dirtyCount = Object.keys(patch).length;

  const validate = useMutation({
    mutationFn: () => configApi.validateDomain(domainKey, { patch }),
    onSuccess: (result) => { setValidation(result); },
  });

  const publish = useMutation({
    mutationFn: () =>
      configApi.publish({
        domainKey,
        patch,
        expectedHeadRevision: active.headRevision ?? 0,
      }),
    onSuccess: async (result) => {
      setPublishedId(result.release_id);
      setValidation(null);
      await queryClient.invalidateQueries({ queryKey: ["config"] });
    },
  });

  function set(path: readonly (string | number)[], value: Json) {
    setDraft((prev) => setPath(prev, path, value) as JsonObject);
    setValidation(null);
  }
  function get(path: readonly (string | number)[]): Json {
    return getPath(draft, path);
  }

  const errors = validation?.errors ?? [];
  const errorMap = errorsByPath(errors);
  // Shown in full at the page level regardless of which ones a field also
  // highlighted inline: unlike `DocumentEditor`, which is generated from the
  // document and can tell mechanically whether a path resolved onto a field,
  // a typed screen's own field list is hand-written per page, so there is no
  // reliable way to know from here which paths a given screen chose to
  // render a control for. Duplicating an error onto both the field and this
  // list is a smaller failure than a path a screen does not have a field for
  // going missing from both.

  const disabledReason = !canPublish
    ? "config.release.promote is required to publish"
    : validation !== null && !validation.valid
      ? "Resolve the validation errors above before publishing"
      : undefined;

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="premium-kicker">{kicker}</p>
          <h2 className="mt-0.5 text-base font-semibold text-on-surface">{title}</h2>
          <p className="mt-1 max-w-3xl text-sm text-on-surface-variant">{description}</p>
        </div>
        <button
          type="button"
          onClick={() => { setAdvanced((value) => !value); }}
          aria-pressed={advanced}
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-outline-control bg-surface-container-lowest px-3 py-2 text-xs font-medium text-on-surface-variant transition hover:border-primary hover:text-primary"
        >
          {advanced ? <ListTree size={13} aria-hidden="true" /> : <Braces size={13} aria-hidden="true" />}
          {advanced ? "Typed form" : "Advanced (JSON)"}
        </button>
      </header>

      {publishedId !== null ? (
        <p role="status" className="rounded-xl border border-primary/20 bg-secondary-container px-4 py-3 text-sm text-on-secondary-container">
          Release {publishedId} is published. Cases opened from now on pin it; cases already running
          keep the configuration they started with.
        </p>
      ) : null}

      {advanced ? (
        <DocumentEditor
          kicker={kicker}
          subtitle={`${domainKey} · release ${active.releaseId}`}
          loaded={draft}
          canWrite={canWrite}
          jsonLabel={jsonLabel}
          submitLabel="Publish release"
          submittingLabel="Publishing..."
          submitTitle={!canPublish ? "config.release.promote is required" : undefined}
          readOnlyNotice="Read-only access. Publishing a configuration release requires config.release.promote."
          notObjectMessage={notObjectMessage}
          confirmSubmit="Publish this section as a new configuration release? Cases opened afterwards pin it."
          onDirtyChange={() => { /* dirtiness is derived from the draft itself above */ }}
          onSubmit={async (document) => {
            const rawWholePatch = mergePatchOf(loaded, document);
            const wholePatch: Readonly<Record<string, unknown>> =
              typeof rawWholePatch === "object" && rawWholePatch !== null && !Array.isArray(rawWholePatch)
                ? rawWholePatch
                : {};
            const result = await configApi.publish({
              domainKey,
              patch: wholePatch,
              expectedHeadRevision: active.headRevision ?? 0,
            });
            setDraft(document);
            setPublishedId(result.release_id);
            await queryClient.invalidateQueries({ queryKey: ["config"] });
            return result;
          }}
        />
      ) : (
        <>
          {renderTyped({ draft, get, set, errorMap })}
          <ValidationErrors errors={errors} title="Validation results" />
          <DiffPreview before={loaded} after={draft} title={`${title} changes`} />
          <PublishBar
            dirtyCount={dirtyCount}
            onValidate={() => { validate.mutate(); }}
            onPublish={() => { publish.mutate(); }}
            disabledReason={disabledReason}
            validating={validate.isPending}
            publishing={publish.isPending}
            error={publish.error instanceof Error ? publish.error.message : null}
          />
        </>
      )}
    </div>
  );
}
