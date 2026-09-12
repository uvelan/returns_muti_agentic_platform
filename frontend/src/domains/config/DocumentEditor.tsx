import { createContext, useCallback, useContext, useEffect, useId, useState, type ReactNode } from "react";
import { useMutation } from "@tanstack/react-query";
import { Braces, Columns3, ListTree, Minus, Plus, RotateCcw } from "lucide-react";

import { KeyValueTable, type KeyValueEntry, type KeyValueKind } from "../../components/forms/KeyValueTable";
import { ValidationErrors, type ValidationError } from "../../components/forms/ValidationErrors";
import { normalizeErrorPath } from "./jsonPath";

/**
 * One JSON document, editable as nested key/value, split view, or raw JSON.
 *
 * Lifted out of `AgentsSection` unchanged in behaviour when the support
 * template needed the same editor. It is deliberately the *same* component
 * rather than a second one that looks like it: two editors would be two answers
 * to "what happens to my edit when the JSON stops parsing", and the operator
 * would have to learn which screen they were on.
 *
 * **Neither editor knows the schema, and that is deliberate.** A configuration
 * document's shape differs by subject, and the backend validates a submission
 * through the loader the platform itself boots from. A form built from a
 * hardcoded field list here would be a second, weaker definition of valid -- it
 * would forbid fields the platform accepts and accept ones it rejects, and it
 * would go stale the first time the document gained a setting. So the form is
 * generated from the document, and the answer to "is this allowed" comes from
 * the backend, in the backend's own words.
 *
 * What the caller supplies is the *destination*: `onSubmit` is the write path,
 * and the two callers use genuinely different ones -- an agent module becomes a
 * governance proposal, a behaviour-domain field becomes a draft release. The
 * editor does not choose between them and does not need to know which it got.
 */

/**
 * Any JSON value. Recursive, because the documents are.
 *
 * The object arm is an interface; see the note on its declaration.
 */
export type Json = string | number | boolean | null | Json[] | JsonObject;

// `interface`, against this repo's `type` preference, for a mechanical reason:
// a self-referencing *alias* resolves to `error` under the lint program, which
// then reports every use of the type as an unsafe assignment while `tsc`
// compiles it happily. An interface is resolved lazily and the recursion is
// fine. Narrowed to this declaration rather than relaxed repository-wide.
// eslint-disable-next-line @typescript-eslint/consistent-type-definitions
export interface JsonObject {
  [key: string]: Json;
}

function isObject(value: Json): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}


/** Whether `dotted` (split on `.`) resolves to something inside `document` -- array indices count as segments too. */
function hasPath(document: Json, segments: readonly string[]): boolean {
  if (segments.length === 0) return true;
  const [head, ...rest] = segments;
  if (Array.isArray(document)) {
    const index = Number(head);
    if (!Number.isInteger(index) || index < 0 || index >= document.length) return false;
    return hasPath(document[index], rest);
  }
  if (isObject(document)) {
    if (!(head in document)) return false;
    return hasPath(document[head], rest);
  }
  return false;
}

/**
 * What `Node` needs at every depth but does not vary per node: the errors
 * matched to a path, which object paths render as `KeyValueTable`, and a way
 * for a data-keyed node to tell the editor "publishing is blocked while I
 * hold an unresolved duplicate key". Threaded via context rather than props
 * so adding it did not mean changing every recursive call's signature --
 * `path` still is a prop, because it genuinely changes at every level.
 */
const FormMetaContext = createContext<{
  errorsByPath: ReadonlyMap<string, string>;
  dataKeyedPaths: ReadonlySet<string>;
  reportBlocked: (path: string, reason: string | null) => void;
}>({ errorsByPath: new Map(), dataKeyedPaths: new Set(), reportBlocked: () => { /* no-op default */ } });

function childPath(parent: string, key: string): string {
  return parent === "" ? key : `${parent}.${key}`;
}

/** A stable, path-derived id for the error paragraph under a leaf -- unique even when several array items share one `labelledBy`. */
function errorIdFor(path: string): string {
  return `field-error-${path.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function inferValueKind(value: JsonObject): KeyValueKind {
  const sample = Object.values(value)[0];
  if (typeof sample === "number") return "number";
  if (typeof sample === "boolean") return "boolean";
  if (typeof sample === "string") return "string";
  return "json";
}

function sameJson(left: Json, right: Json): boolean {
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left)
      && Array.isArray(right)
      && left.length === right.length
      && left.every((value, index) => sameJson(value, right[index]))
    );
  }
  if (isObject(left) || isObject(right)) {
    if (!isObject(left) || !isObject(right)) return false;
    const keys = Object.keys(left);
    return (
      keys.length === Object.keys(right).length
      && keys.every((key) => key in right && sameJson(left[key], right[key]))
    );
  }
  return Object.is(left, right);
}

/** A field's label, from its key: `configuration_version` -> `Configuration version`. */
function label(key: string): string {
  const spaced = key.replace(/[_-]+/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

type Mode = "form" | "split" | "json";

export function DocumentEditor<TResult>({
  kicker,
  subtitle,
  badges,
  loaded,
  canWrite,
  jsonLabel,
  submitLabel,
  submittingLabel,
  submitTitle,
  readOnlyNotice,
  notObjectMessage,
  confirmSubmit,
  onSubmit,
  renderResult,
  notice,
  onDirtyChange,
  footer,
  errors,
  dataKeyedPaths,
}: {
  /** The small uppercase line above the subject. */
  kicker: string;
  /** Where this document lives -- a config path, a release id. */
  subtitle: string;
  badges?: ReactNode;
  loaded: Json;
  canWrite: boolean;
  /** The textarea's accessible name; a screen with two editors needs two. */
  jsonLabel: string;
  submitLabel: string;
  submittingLabel: string;
  /** Why the submit button is disabled, when it is. */
  submitTitle?: string;
  readOnlyNotice: string;
  /** Refusal shown when the document is not an object. Subject-specific wording. */
  notObjectMessage: string;
  /**
   * Asked before submitting, when submitting is the irreversible half.
   *
   * Absent for a write that only *proposes* a change -- an agent edit waits in
   * the approvals queue and a confirmation there would be ceremony over
   * nothing. Present where the button publishes.
   */
  confirmSubmit?: string;
  onSubmit: (document: JsonObject) => Promise<TResult>;
  /** What to show once the write succeeded -- a proposal id, a release. */
  renderResult?: (result: TResult) => ReactNode;
  /** Shown with the editor's own messages, under the header: progress, warnings. */
  notice?: ReactNode;
  onDirtyChange: (dirty: boolean) => void;
  /**
   * Rendered under the editor with the document as it currently stands, or
   * `null` while raw JSON does not parse. The template preview rides this: it
   * needs the draft the operator is looking at, not the last saved one.
   */
  footer?: (document: JsonObject | null) => ReactNode;
  /**
   * Backend-reported errors, path-mapped onto the generated form: a path
   * that resolves inside the current document renders as an inline error
   * under that field (form mode only -- JSON/split are raw text with no
   * per-field slot to put it in); a path that does not resolve -- the
   * document changed shape since the error was computed, or the backend
   * named a path this editor cannot reach -- goes to a page-level list
   * instead of being silently dropped.
   */
  errors?: readonly ValidationError[];
  /**
   * Dotted paths (matching the same key convention as `errors`) of objects
   * whose keys are data rather than schema -- `ship_via_methods`,
   * `dependencies`, `tasks` -- so they render as `KeyValueTable` instead of
   * one box per key named from the key.
   */
  dataKeyedPaths?: readonly string[];
}) {
  const [mode, setMode] = useState<Mode>("form");
  const [draft, setDraft] = useState<Json>(loaded);
  const [baseline, setBaseline] = useState<Json>(loaded);
  const [text, setText] = useState(() => JSON.stringify(loaded, null, 2));
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [result, setResult] = useState<TResult | null>(null);
  const [dirty, setDirty] = useState(false);
  // Paths (from a `dataKeyedPaths` node) currently holding an unresolved
  // duplicate/empty key -- publishing is refused while this is non-empty, and
  // becoming blocked counts as a change even though nothing has actually
  // reached `draft` yet (see `reportBlocked`).
  const [blocked, setBlocked] = useState<Record<string, string>>({});

  const reportBlocked = useCallback(
    (path: string, reason: string | null) => {
      setBlocked((prev) => {
        if (reason === null) {
          if (!(path in prev)) return prev;
          const next: Record<string, string> = {};
          for (const [key, value] of Object.entries(prev)) {
            if (key !== path) next[key] = value;
          }
          return next;
        }
        if (prev[path] === reason) return prev;
        return { ...prev, [path]: reason };
      });
      if (reason !== null) {
        setDirty(true);
        onDirtyChange(true);
      }
    },
    [onDirtyChange],
  );

  const save = useMutation({
    mutationFn: (document: JsonObject) => onSubmit(document),
    onSuccess: (saved, document) => {
      setResult(saved);
      setBaseline(document);
      setDirty(false);
      onDirtyChange(false);
    },
  });

  useEffect(() => {
    if (!dirty) return;
    const protectedUrl = window.location.href;
    let restoringHistory = false;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    const guardLinkNavigation = (event: MouseEvent) => {
      if (!(event.target instanceof Element) || event.target.closest("a[href]") === null) return;
      if (window.confirm("Discard unsaved configuration changes and leave this page?")) return;
      event.preventDefault();
      event.stopPropagation();
    };
    const guardHistoryNavigation = () => {
      if (restoringHistory) {
        restoringHistory = false;
        return;
      }
      if (window.confirm("Discard unsaved configuration changes and leave this page?")) {
        setDirty(false);
        onDirtyChange(false);
        return;
      }
      restoringHistory = true;
      window.history.pushState(null, "", protectedUrl);
      window.dispatchEvent(new PopStateEvent("popstate"));
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    window.addEventListener("popstate", guardHistoryNavigation);
    document.addEventListener("click", guardLinkNavigation, true);
    return () => {
      window.removeEventListener("beforeunload", warnBeforeUnload);
      window.removeEventListener("popstate", guardHistoryNavigation);
      document.removeEventListener("click", guardLinkNavigation, true);
    };
  }, [dirty, onDirtyChange]);

  function setDirtyState(changed: boolean) {
    setDirty(changed);
    if (changed) setResult(null);
    onDirtyChange(changed);
  }

  /** JSON mode is the source of truth while it is open, and it may be invalid. */
  function switchTo(next: Mode) {
    if (next === mode) return;
    if (mode === "form") {
      if (next !== "form") setText(JSON.stringify(draft, null, 2));
      setMode(next);
      return;
    }
    if (next === "form" || next === "split") {
      try {
        setDraft(JSON.parse(text) as Json);
        setJsonError(null);
      } catch (error) {
        // Refuse the switch rather than silently dropping the edit: the form
        // cannot render text that is not a document, and discarding it without
        // saying so loses work the operator can see on screen.
        setJsonError(error instanceof Error ? error.message : "That is not valid JSON.");
        return;
      }
    }
    setMode(next);
  }

  /** The document as it stands right now, or `null` while the raw JSON is broken. */
  function currentDocument(): JsonObject | null {
    let document: Json = draft;
    if (mode !== "form") {
      try {
        document = JSON.parse(text) as Json;
      } catch {
        return null;
      }
    }
    return isObject(document) ? document : null;
  }

  function onSave() {
    // Belt-and-braces alongside the disabled Save button: a data-keyed table
    // holding an unresolved duplicate never reached `draft`, so submitting
    // anyway would silently publish whatever the document looked like before
    // the collision -- the same loss B1 was about, one layer up.
    if (Object.keys(blocked).length > 0) return;
    setResult(null);
    let document: Json = draft;
    if (mode !== "form") {
      try {
        document = JSON.parse(text) as Json;
        setJsonError(null);
      } catch (error) {
        setJsonError(error instanceof Error ? error.message : "That is not valid JSON.");
        return;
      }
    }
    if (!isObject(document)) {
      setJsonError(notObjectMessage);
      return;
    }
    if (confirmSubmit !== undefined && !window.confirm(confirmSubmit)) return;
    save.mutate(document);
  }

  function updateDraft(next: Json) {
    setDraft(next);
    setDirtyState(!sameJson(next, loaded) && !sameJson(next, baseline));
    if (mode === "split") setText(JSON.stringify(next, null, 2));
  }

  function updateText(next: string) {
    setText(next);
    try {
      const parsed = JSON.parse(next) as Json;
      setDirtyState(!sameJson(parsed, loaded) && !sameJson(parsed, baseline));
      if (mode === "split") {
        setDraft(parsed);
        setJsonError(null);
      }
    } catch (error) {
      setDirtyState(true);
      if (mode === "split") {
        setJsonError(error instanceof Error ? error.message : "That is not valid JSON.");
      }
    }
  }

  // A path "matches" when it resolves inside the *current* draft -- computed
  // fresh each render rather than once at mount, so an error computed against
  // last publish's shape that the operator has since edited away is treated
  // as unknown rather than pinned to a field that no longer means the same
  // thing.
  const errorList = errors ?? [];
  // `foo[3].bar` and `foo.3.bar` both mean the same field internally --
  // normalise before matching so either spelling a backend validator uses
  // reaches its field (documented in components/forms/README.md).
  const normalizedErrors = errorList.map((error) => ({ ...error, path: normalizeErrorPath(error.path) }));
  const matchedErrors = normalizedErrors.filter((error) => hasPath(draft, error.path === "" ? [] : error.path.split(".")));
  const unknownErrors = normalizedErrors.filter((error) => !hasPath(draft, error.path === "" ? [] : error.path.split(".")));
  const errorsByPath = new Map<string, string>();
  for (const error of matchedErrors) {
    const existing = errorsByPath.get(error.path);
    errorsByPath.set(error.path, existing === undefined ? error.message : `${existing}; ${error.message}`);
  }
  const dataKeyedPathSet = new Set(dataKeyedPaths ?? []);
  const blockedReasons = Object.values(blocked);

  const formEditor = (
    <fieldset disabled={!canWrite} className="max-h-[34rem] min-h-[28rem] overflow-y-auto rounded-xl border border-outline-variant bg-surface-container-lowest p-4 shadow-inner disabled:cursor-not-allowed disabled:opacity-75">
      <FormMetaContext.Provider value={{ errorsByPath, dataKeyedPaths: dataKeyedPathSet, reportBlocked }}>
        <Node value={draft} onChange={updateDraft} path="" />
      </FormMetaContext.Provider>
    </fieldset>
  );

  const jsonEditor = (
    <textarea
      aria-label={jsonLabel}
      value={text}
      onChange={(event) => { updateText(event.target.value); }}
      readOnly={!canWrite}
      spellCheck={false}
      className="h-[34rem] min-h-[28rem] w-full resize-none rounded-xl border border-outline-control bg-rail-surface p-4 font-mono text-xs leading-5 text-rail-on-surface outline-none transition focus:border-inverse-primary focus:ring-2 focus:ring-inverse-primary/20"
    />
  );

  return (
    <section className="premium-panel flex min-w-0 flex-col overflow-hidden">
      <header className="flex items-center gap-3 border-b border-outline-variant/80 bg-surface-container-low px-4 py-3">
        <div className="min-w-0">
          <p className="premium-kicker">{kicker}</p>
          <p className="mt-0.5 max-w-72 truncate text-xs text-on-surface-variant" title={subtitle}>
            {subtitle}
          </p>
          <div className="mt-1.5 flex items-center gap-2 text-[10px] font-semibold">
            {badges}
            {dirty ? (
              <span className="rounded-full bg-tertiary-container px-2 py-0.5 text-on-tertiary-container">
                Unsaved changes
              </span>
            ) : null}
          </div>

        </div>
        <div className="ml-auto flex overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest shadow-sm">
          <button
            type="button"
            onClick={() => { switchTo("form"); }}
            aria-pressed={mode === "form"}
            className={[
              "flex items-center gap-1.5 px-3 py-2 text-xs font-medium transition",
              mode === "form" ? "bg-primary text-on-primary" : "text-on-surface-variant hover:bg-surface-container",
            ].join(" ")}
          >
            <ListTree size={13} aria-hidden="true" />
            Key-value
          </button>
          <button
            type="button"
            onClick={() => { switchTo("split"); }}
            aria-pressed={mode === "split"}
            className={[
              "flex items-center gap-1.5 border-x border-outline-variant px-3 py-2 text-xs font-medium transition",
              mode === "split" ? "bg-primary text-on-primary" : "text-on-surface-variant hover:bg-surface-container",
            ].join(" ")}
          >
            <Columns3 size={13} aria-hidden="true" />
            Split
          </button>
          <button
            type="button"
            onClick={() => { switchTo("json"); }}
            aria-pressed={mode === "json"}
            className={[
              "flex items-center gap-1.5 px-3 py-2 text-xs font-medium transition",
              mode === "json" ? "bg-primary text-on-primary" : "text-on-surface-variant hover:bg-surface-container",
            ].join(" ")}
          >
            <Braces size={13} aria-hidden="true" />
            JSON
          </button>
        </div>
        <button
          type="button"
          onClick={() => {
            if (!dirty) return;
            if (!window.confirm("Discard all unsaved configuration changes?")) return;
            setDraft(baseline);
            setText(JSON.stringify(baseline, null, 2));
            setJsonError(null);
            setResult(null);
            setDirty(false);
            setBlocked({});
            onDirtyChange(false);
          }}
          disabled={!canWrite || !dirty}
          className="flex items-center gap-1.5 rounded-lg border border-outline-control bg-surface-container-lowest px-3 py-2 text-xs font-medium text-on-surface-variant transition hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:border-outline-variant disabled:bg-surface-container-low disabled:hover:border-outline-variant disabled:hover:text-on-surface-variant"
        >
          <RotateCcw size={13} aria-hidden="true" />
          Reset
        </button>
        <button
          type="button"
          onClick={onSave}
          disabled={save.isPending || !canWrite || !dirty || blockedReasons.length > 0}
          title={!canWrite ? submitTitle : blockedReasons.length > 0 ? blockedReasons[0] : undefined}
          className="rounded-lg border border-transparent bg-primary px-4 py-2 text-xs font-semibold text-on-primary shadow-sm transition hover:brightness-105 disabled:cursor-not-allowed disabled:border-outline-variant disabled:bg-surface-container-low disabled:text-on-surface-variant disabled:shadow-none disabled:hover:brightness-100"
        >
          {save.isPending ? submittingLabel : submitLabel}
        </button>
      </header>

      <div className="flex flex-col gap-3 p-4">
        {!canWrite ? (
          <p className="rounded-lg border border-outline-variant bg-surface-container-low px-3 py-2 text-sm text-on-surface-variant">
            {readOnlyNotice}
          </p>
        ) : null}
        {jsonError !== null ? (
          <p role="alert" className="rounded-lg border border-error/20 bg-error-container px-3 py-2 text-sm text-on-error-container">
            {jsonError}
          </p>
        ) : null}
        {save.error !== null ? (
          <p role="alert" className="rounded-lg border border-error/20 bg-error-container px-3 py-2 text-sm text-on-error-container">
            {save.error.message}
          </p>
        ) : null}
        <ValidationErrors errors={unknownErrors} title="Errors on paths this editor could not place" />
        {result !== null && save.error === null && renderResult !== undefined
          ? renderResult(result)
          : null}
        {notice}

        {mode === "json" ? jsonEditor : mode === "form" ? formEditor : (
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <div>
              <p className="premium-kicker mb-2">Nested key-value</p>
              {formEditor}
            </div>
            <div>
              <p className="premium-kicker mb-2">JSON source</p>
              {jsonEditor}
            </div>
          </div>
        )}

        {footer?.(currentDocument())}
      </div>
    </section>
  );
}

/**
 * One node of the document, rendered by what it is.
 *
 * Recursive, so nesting is followed however deep it goes rather than to a
 * fixed number of levels -- these payloads nest objects inside arrays inside
 * objects, and an editor that stopped at the top level would leave most of the
 * configuration unreachable.
 *
 * `labelledBy` is the id of the `<span>` that shows this field's name, which
 * lives in the *parent* object's render rather than here -- the name comes from
 * the key, and the key is the parent's to know. Every scalar input therefore
 * looked labelled and was not: `bay_allocation` alone renders fourteen
 * non-boolean scalar leaves, which is the audit's "14 agent labels" exactly.
 * Passing the id down is what turns the visible name into a programmatic one.
 *
 * `path` is this node's dotted location in the *document* (`""` at the root,
 * `limits.max_queries` three levels down, `variants.0.subject_template`
 * through an array) -- independent of `labelledBy`, and what the `errors`
 * prop and `dataKeyedPaths` are matched against via `FormMetaContext`.
 */
function Node({
  value,
  onChange,
  labelledBy,
  path,
}: {
  value: Json;
  onChange: (next: Json) => void;
  labelledBy?: string;
  path: string;
}) {
  const { errorsByPath } = useContext(FormMetaContext);

  if (Array.isArray(value)) {
    return (
      <div className="flex flex-col gap-2">
        {value.map((item, index) => (
          <div key={index} className="flex items-start gap-2">
            <span className="mt-2 w-5 shrink-0 text-right text-[11px] text-outline">{index}</span>
            <div className="min-w-0 flex-1">
              <Node
                value={item}
                labelledBy={labelledBy}
                path={childPath(path, String(index))}
                onChange={(next) => {
                  onChange(value.map((existing, at) => (at === index ? next : existing)));
                }}
              />
            </div>
            <button
              type="button"
              aria-label={`Remove item ${String(index)}`}
              onClick={() => { onChange(value.filter((_, at) => at !== index)); }}
              className="mt-1 flex size-6 shrink-0 items-center justify-center rounded border border-outline-control text-on-surface-variant transition hover:border-error hover:text-error"
            >
              <Minus size={12} />
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => {
            // A new entry copies the shape of the last one, so adding to a list
            // of objects gives you the object's fields rather than a bare
            // string you then have to reshape by hand in JSON mode.
            const template = value.length > 0 ? blank(value[value.length - 1]) : "";
            onChange([...value, template]);
          }}
          className="flex w-fit items-center gap-1 rounded border border-outline-control px-2 py-1 text-[11px] text-on-surface-variant transition hover:border-primary hover:text-primary"
        >
          <Plus size={11} />
          Add
        </button>
      </div>
    );
  }

  if (isObject(value)) {
    return <ObjectNode value={value} onChange={onChange} path={path} />;
  }

  const message = errorsByPath.get(path);
  const errorId = message !== undefined ? errorIdFor(path) : undefined;

  if (typeof value === "boolean") {
    return (
      <div className="flex flex-col gap-1">
        <label className="flex w-fit cursor-pointer items-center gap-2 text-sm text-on-surface">
          <input
            type="checkbox"
            checked={value}
            // The field's name, not "Yes"/"No". A checkbox already announces its
            // own state, so the visible word is redundant to a screen reader and
            // was the only accessible name it had -- fourteen inputs on one
            // screen all called "Yes".
            aria-labelledby={labelledBy}
            aria-invalid={message !== undefined ? true : undefined}
            aria-describedby={errorId}
            onChange={(event) => { onChange(event.target.checked); }}
            className="size-4 accent-primary"
          />
          {value ? "Yes" : "No"}
        </label>
        {message !== undefined ? (
          <p id={errorId} role="alert" className="text-[11px] text-error">{message}</p>
        ) : null}
      </div>
    );
  }

  if (typeof value === "number") {
    return (
      <div className="flex flex-col gap-1">
        <input
          type="number"
          value={value}
          aria-labelledby={labelledBy}
          aria-invalid={message !== undefined ? true : undefined}
          aria-describedby={errorId}
          onChange={(event) => {
            // An empty or half-typed number must not become NaN in the document:
            // it would serialize as null and silently blank the setting.
            const parsed = Number(event.target.value);
            onChange(event.target.value === "" || Number.isNaN(parsed) ? 0 : parsed);
          }}
          className="w-40 rounded border border-outline-control bg-surface px-2 py-1 text-sm text-on-surface outline-none focus:border-primary"
        />
        {message !== undefined ? (
          <p id={errorId} role="alert" className="text-[11px] text-error">{message}</p>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <input
        type="text"
        value={value ?? ""}
        aria-labelledby={labelledBy}
        aria-invalid={message !== undefined ? true : undefined}
        aria-describedby={errorId}
        onChange={(event) => { onChange(event.target.value); }}
        className="w-full rounded border border-outline-control bg-surface px-2 py-1 text-sm text-on-surface outline-none focus:border-primary"
      />
      {message !== undefined ? (
        <p id={errorId} role="alert" className="text-[11px] text-error">{message}</p>
      ) : null}
    </div>
  );
}

type JsonKind = "string" | "number" | "boolean" | "object" | "array";

/**
 * An object's fields, each one named by an element the input can point at.
 *
 * Split out of `Node` because it needs `useId`, and `Node` returns early for
 * arrays and scalars -- a hook above those branches would run for every leaf
 * and a hook below them would be conditional.
 *
 * When `path` is one of the caller's `dataKeyedPaths`, the object's keys are
 * data rather than schema -- `ship_via_methods`, `dependencies`, `tasks` --
 * and this renders as `KeyValueTable` instead: one box per key named from
 * the key reads as a form with an unbounded number of unrelated fields,
 * where what the operator actually has is a mapping to edit as one.
 */
function ObjectNode({ value, onChange, path }: { value: JsonObject; onChange: (next: Json) => void; path: string }) {
  const base = useId();
  const { dataKeyedPaths } = useContext(FormMetaContext);

  if (dataKeyedPaths.has(path)) {
    return <DataKeyedObjectNode value={value} onChange={onChange} path={path} />;
  }

  return (
    <div className="flex flex-col gap-2.5 border-l border-outline-variant pl-3">
      {Object.entries(value).map(([key, child]) => {
        const labelId = `${base}-${key}`;
        return (
          <div key={key} className="rounded-lg border border-outline-variant/70 bg-surface-container-low p-3">
            <div className="mb-2 flex items-center gap-2">
              <span id={labelId} className="text-[11px] font-semibold text-on-surface-variant">
                {label(key)}
              </span>
              <code className="truncate text-[10px] text-outline">{key}</code>
              <button
                type="button"
                aria-label={`Remove property ${key}`}
                onClick={() => {
                  onChange(Object.fromEntries(Object.entries(value).filter(([entry]) => entry !== key)));
                }}
                className="ml-auto flex size-6 items-center justify-center rounded-md text-outline transition hover:bg-error-container hover:text-error"
              >
                <Minus size={12} aria-hidden="true" />
              </button>
            </div>
            <Node
              value={child}
              labelledBy={labelId}
              path={childPath(path, key)}
              onChange={(next) => { onChange({ ...value, [key]: next }); }}
            />
          </div>
        );
      })}
      <NewProperty
        existing={Object.keys(value)}
        onAdd={(key, child) => { onChange({ ...value, [key]: child }); }}
      />
    </div>
  );
}

/** Why `entries` cannot become the document yet -- `null` once every key is unique and filled in. */
function keyValueBlockReason(entries: readonly KeyValueEntry[]): string | null {
  const keys = entries.map((entry) => entry.key);
  if (keys.some((key) => key.trim() === "")) {
    return "Every key must be filled in before publishing.";
  }
  const duplicate = keys.find((key, index) => keys.indexOf(key) !== index);
  if (duplicate !== undefined) {
    return `"${duplicate}" is used by more than one row here -- rename one before publishing.`;
  }
  return null;
}

/**
 * The `KeyValueTable` rendering of a data-keyed object -- split out of
 * `ObjectNode` because it needs its own state.
 *
 * `entries` is a *list*, and a list can hold two rows with the same key for
 * as long as the operator is mid-rename; `value`, the actual document, is a
 * plain JS object, which cannot. Flattening on every keystroke (the B1
 * defect) forced every intermediate state through that object regardless,
 * so a rename that passed through another row's key -- deliberately, or as a
 * prefix while typing -- silently collapsed the two rows into one before
 * `KeyValueTable` ever got to show the collision. Holding `entries` as this
 * component's own state instead means a duplicate stays visible, in both
 * rows, for as long as it exists, and `onChange` only ever receives a
 * document that could not have lost anything.
 *
 * What makes that safe rather than sticky is the render-phase reset below --
 * React's documented pattern for "adjust state when a prop changes"
 * (https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes),
 * a conditional `setState` call during render rather than inside an effect.
 * Any *external* change to the document -- Reset, a sibling edit made
 * through JSON/split mode, simply mounting -- abandons whatever local,
 * unresolved edit was in progress and resyncs from the document that is now
 * current, the same way discarding a JSON-mode edit on a parse error does
 * elsewhere in this file. It does not fire on the render that follows this
 * component's *own* successful resolution, because `pending` is already
 * cleared, synchronously, before that `onChange` is called -- `value` and
 * `trackedValue` arrive at the new document together.
 *
 * Telling the *ancestor* that publishing is unblocked again, in contrast,
 * genuinely belongs in an effect: it is a different component's state
 * (`DocumentEditor`'s `blocked`), and React does not support setting one
 * component's state from inside another's render.
 */
function DataKeyedObjectNode({
  value,
  onChange,
  path,
}: {
  value: JsonObject;
  onChange: (next: Json) => void;
  path: string;
}) {
  const { errorsByPath, reportBlocked } = useContext(FormMetaContext);
  const [pending, setPending] = useState<KeyValueEntry[] | null>(null);
  const [trackedValue, setTrackedValue] = useState(value);

  if (value !== trackedValue) {
    setTrackedValue(value);
    setPending(null);
  }

  useEffect(() => {
    if (pending === null) reportBlocked(path, null);
  }, [pending, path, reportBlocked]);

  // C1 (CFG-3b RV, carried to CFG-4): clear this node's block when it
  // unmounts, not only while it is mounted and resolved. Switching to JSON
  // mode unmounts the whole form editor, `DataKeyedObjectNode` included --
  // with no cleanup, a duplicate key held at that moment left `blocked`
  // naming a row no longer on screen, and Save stayed disabled with a
  // `title` an operator who had already switched away could not act on.
  //
  // A second effect, deliberately not folded into the one above: that one
  // has `pending` in its dependency array, so its cleanup re-runs on every
  // keystroke that changes `pending`, not only on unmount. Putting the
  // unconditional `reportBlocked(path, null)` there cleared a genuine,
  // still-open block the instant the operator typed the very next character
  // of the rename. This effect's only dependencies are `path` and
  // `reportBlocked`, both stable for the node's life, so its cleanup fires
  // on unmount (or a real path change) and nowhere else.
  useEffect(() => () => { reportBlocked(path, null); }, [path, reportBlocked]);

  const entries: KeyValueEntry[] =
    pending ?? Object.entries(value).map(([key, child]) => ({ key, value: child }));
  const nestedErrors = Array.from(errorsByPath.entries()).filter(
    ([errorPath]) => errorPath === path || errorPath.startsWith(`${path}.`),
  );

  return (
    <div className="flex flex-col gap-2">
      <KeyValueTable
        label={label(path.split(".").pop() ?? path)}
        entries={entries}
        valueKind={inferValueKind(value)}
        onChange={(next) => {
          const blockReason = keyValueBlockReason(next);
          if (blockReason !== null) {
            setPending(next);
            reportBlocked(path, blockReason);
            return;
          }
          setPending(null);
          reportBlocked(path, null);
          const nextValue: JsonObject = {};
          for (const entry of next) nextValue[entry.key] = entry.value;
          onChange(nextValue);
        }}
      />
      {nestedErrors.length > 0 ? (
        <ul className="flex flex-col gap-1">
          {nestedErrors.map(([errorPath, message]) => (
            <li
              key={errorPath}
              role="alert"
              className="rounded-lg border border-error/20 bg-error-container px-2 py-1 text-xs text-on-error-container"
            >
              <code className="font-mono">{errorPath}</code> — {message}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function NewProperty({
  existing,
  onAdd,
}: {
  existing: string[];
  onAdd: (key: string, value: Json) => void;
}) {
  const [key, setKey] = useState("");
  const [kind, setKind] = useState<JsonKind>("string");
  const normalized = key.trim();
  const duplicate = existing.includes(normalized);

  function addProperty() {
    if (normalized === "" || duplicate) return;
    const initial: Record<JsonKind, Json> = {
      string: "",
      number: 0,
      boolean: false,
      object: {},
      array: [],
    };
    onAdd(normalized, initial[kind]);
    setKey("");
  }

  return (
    <div className="flex items-center gap-2 rounded-lg border border-dashed border-outline-variant bg-surface-container-low/60 p-2">
      <input
        aria-label="New property key"
        value={key}
        onChange={(event) => { setKey(event.target.value); }}
        onKeyDown={(event) => { if (event.key === "Enter") addProperty(); }}
        placeholder="new_property"
        className="premium-field min-w-0 flex-1 py-1.5 font-mono text-xs"
      />
      <select
        aria-label="New property type"
        value={kind}
        onChange={(event) => { setKind(event.target.value as JsonKind); }}
        className="premium-field w-28 py-1.5 text-xs"
      >
        <option value="string">Text</option>
        <option value="number">Number</option>
        <option value="boolean">Boolean</option>
        <option value="object">Object</option>
        <option value="array">List</option>
      </select>
      <button
        type="button"
        onClick={addProperty}
        disabled={normalized === "" || duplicate}
        title={duplicate ? "That key already exists" : undefined}
        className="flex items-center gap-1 rounded-lg border border-outline-control bg-surface-container-lowest px-2.5 py-2 text-[11px] font-medium text-on-surface-variant transition hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:border-outline-variant disabled:bg-surface-container-low disabled:hover:border-outline-variant disabled:hover:text-on-surface-variant"
      >
        <Plus size={12} aria-hidden="true" />
        Add property
      </button>
    </div>
  );
}

/** An empty value of the same shape, for a newly added list entry. */
function blank(example: Json): Json {
  if (Array.isArray(example)) return [];
  if (isObject(example)) {
    return Object.fromEntries(Object.entries(example).map(([key, value]) => [key, blank(value)]));
  }
  if (typeof example === "boolean") return false;
  if (typeof example === "number") return 0;
  return "";
}
