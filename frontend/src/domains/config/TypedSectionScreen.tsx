import { useState, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Braces, ListTree } from "lucide-react";

import { APIError } from "../../api/client";
import { configApi, type ValidationErrorItem } from "../../api/configuration";
import { mergePatchOf } from "../../api/mergePatch";
import { DiffPreview } from "../../components/forms/DiffPreview";
import { PublishBar } from "../../components/forms/PublishBar";
import { ValidationErrors } from "../../components/forms/ValidationErrors";
import { DocumentEditor, type Json, type JsonObject } from "./DocumentEditor";
import { errorsByPath, runtimeSliceOf, type RuntimeSlice } from "./runtimeSlice";
import { countPatchLeaves, getPath, setPath } from "./jsonPath";

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
  // RV F5: a 409 (`CONFIGURATION_REVISION_CONFLICT`) means someone else
  // published while this draft was open. `active.headRevision` -- the prop
  // -- only updates when the parent's runtime query refetches, and every
  // caller keys its editor by `active.releaseId` (deliberately, so a
  // genuine external release change resets stale state elsewhere in this
  // codebase) -- so invalidating `["config"]` here to pick up the new head
  // would remount this component and silently discard the draft the
  // conflict was about. Held locally instead: refreshed straight from
  // `configApi.runtime()` (not through the shared query cache) so the next
  // Publish attempt uses the real head without touching `active` or
  // triggering a remount.
  const [headRevisionOverride, setHeadRevisionOverride] = useState<number | null>(null);
  const [conflictNotice, setConflictNotice] = useState<string | null>(null);
  const effectiveHeadRevision = headRevisionOverride ?? active.headRevision;

  // `mergePatchOf(JsonObject, JsonObject)` always returns an object patch --
  // the `null`/array arms of its `JsonValue` return type are for a `before`/
  // `after` pair that is not both objects, which `loaded`/`draft` always are
  // here. Narrowed once so every caller below is not left re-asserting it.
  const rawPatch = mergePatchOf(loaded, draft);
  const patch: Readonly<Record<string, unknown>> =
    typeof rawPatch === "object" && rawPatch !== null && !Array.isArray(rawPatch) ? rawPatch : {};
  // RV CFG-4 round 2, F6: leaves, not top-level sections -- see
  // `countPatchLeaves`'s own note in `jsonPath.ts`.
  const dirtyCount = countPatchLeaves(patch);

  const validate = useMutation({
    mutationFn: () => configApi.validateDomain(domainKey, { patch }),
    onSuccess: (result) => { setValidation(result); },
  });

  const publish = useMutation({
    mutationFn: () =>
      configApi.publish({
        domainKey,
        patch,
        expectedHeadRevision: effectiveHeadRevision ?? 0,
      }),
    onSuccess: async (result) => {
      setPublishedId(result.release_id);
      setValidation(null);
      setConflictNotice(null);
      setHeadRevisionOverride(null);
      await queryClient.invalidateQueries({ queryKey: ["config"] });
    },
    onError: (error: unknown) => {
      if (!(error instanceof APIError) || error.status !== 409) return;
      // Best-effort: read the head fresh, off the shared cache, so a second
      // Publish click has a real revision to lock against. If this itself
      // fails, the original 409's own message (already shown via
      // `publish.error`) is still the honest answer.
      void configApi
        .runtime()
        .then((snapshot) => {
          const fresh = runtimeSliceOf(snapshot).headRevision;
          setHeadRevisionOverride(fresh);
          setConflictNotice(
            fresh === null
              ? "The release moved while you were editing. Review the diff and publish again."
              : `The release moved to head ${String(fresh)} while you were editing. Review the diff and publish again.`,
          );
        })
        .catch(() => { /* the 409's own message still shows */ });
    },
  });

  function set(path: readonly (string | number)[], value: Json) {
    setDraft((prev) => setPath(prev, path, value) as JsonObject);
    setValidation(null);
    // RV CFG-4 round 2, G1: clearing `conflictNotice` alone let `publish.error`
    // show through underneath it -- `PublishBar`'s `error` prop falls back to
    // `publish.error.message` once `conflictNotice` is `null`, and that mutation
    // stays in its errored state (carrying the *old* 409's raw message,
    // "Configuration head revision changed from 41 to 44") until the next
    // `mutate()` call, not the next keystroke. The first keystroke after a
    // conflict read as a *second*, unrelated failure arriving while the
    // operator was still fixing the first. `publish.reset()` clears the
    // mutation's own error state in the same breath as the friendly notice, so
    // an edit truly leaves no stale error on screen either way.
    setConflictNotice(null);
    publish.reset();
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
              expectedHeadRevision: effectiveHeadRevision ?? 0,
            });
            setDraft(document);
            setPublishedId(result.release_id);
            setConflictNotice(null);
            setHeadRevisionOverride(null);
            await queryClient.invalidateQueries({ queryKey: ["config"] });
            return result;
          }}
        />
      ) : (
        <>
          {/*
            RV F2: `canWrite` used to be consumed only by the Advanced
            branch's `DocumentEditor` (which already gates itself at
            `DocumentEditor.tsx:406`) -- the typed branch rendered every
            field editable regardless, so a principal with
            `config.release.promote` but not `config.release.write` saw an
            enabled form the backend would 403 every write from. A native
            `<fieldset disabled>` cascades to every input/select/textarea/
            button `renderTyped` renders, the same mechanism `DocumentEditor`
            itself uses, with no change needed in any of the three screens.
          */}
          {!canWrite ? (
            <p className="rounded-lg border border-outline-variant bg-surface-container-low px-3 py-2 text-sm text-on-surface-variant">
              Read-only access. Editing this section requires config.release.write.
            </p>
          ) : null}
          <fieldset disabled={!canWrite} className="flex flex-col gap-4 disabled:opacity-75">
            {renderTyped({ draft, get, set, errorMap })}
          </fieldset>
          <ValidationErrors errors={errors} title="Validation results" />
          <DiffPreview before={loaded} after={draft} title={`${title} changes`} />
          <PublishBar
            dirtyCount={dirtyCount}
            onValidate={() => { validate.mutate(); }}
            onPublish={() => { publish.mutate(); }}
            disabledReason={disabledReason}
            validating={validate.isPending}
            publishing={publish.isPending}
            error={conflictNotice ?? (publish.error instanceof Error ? publish.error.message : null)}
          />
        </>
      )}
    </div>
  );
}
