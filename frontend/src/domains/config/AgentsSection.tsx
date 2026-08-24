import { useEffect, useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Braces, CheckCircle2, Columns3, ListTree, Minus, Plus, RotateCcw } from "lucide-react";
import { Link } from "wouter";

import { agentConfigApi, type AgentSummary } from "../../api/agentConfig";
import { useCapabilities } from "../../hooks/capabilityContext";

/**
 * Per-agent configuration, editable as nested key/value, split view, or JSON.
 *
 * Each agent already had its own module file; nothing served it. This is that
 * surface: pick an agent, then edit the same document as raw JSON or through a
 * key/value editor that follows the document's own nesting.
 *
 * **Neither editor knows the schema, and that is deliberate.** A module's
 * payload differs by agent, and the backend validates a proposal through the
 * loader the platform itself boots from. A form built from a hardcoded field
 * list here would be a second, weaker definition of valid -- it would forbid
 * fields the platform accepts and accept ones it rejects, and it would go
 * stale the first time an agent gained a setting. So the form is generated
 * from the document, and the answer to "is this allowed" comes from the
 * backend, in the backend's own words.
 */

/**
 * Any JSON value. Recursive, because the documents are.
 *
 * The object arm is an interface; see the note on its declaration.
 */
type Json = string | number | boolean | null | Json[] | JsonObject;

// `interface`, against this repo's `type` preference, for a mechanical reason:
// a self-referencing *alias* resolves to `error` under the lint program, which
// then reports every use of the type as an unsafe assignment while `tsc`
// compiles it happily. An interface is resolved lazily and the recursion is
// fine. Narrowed to this declaration rather than relaxed repository-wide.
// eslint-disable-next-line @typescript-eslint/consistent-type-definitions
interface JsonObject {
  [key: string]: Json;
}

function isObject(value: Json): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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
function configurationSourceLabel(source: string | undefined): string {
  if (source === undefined) return "Source unknown";
  if (source === "RELEASE") return "Active release";
  if (source === "PACKAGED_BASELINE") return "Packaged baseline";
  return source.replaceAll("_", " ").toLowerCase();
}


export function AgentsSection() {
  const [selected, setSelected] = useState<string | null>(null);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const agents = useQuery({ queryKey: ["config", "agents"], queryFn: () => agentConfigApi.list() });

  if (agents.error !== null) {
    return (
      <p role="alert" className="text-sm text-error">
        {agents.error.message}
      </p>
    );
  }
  if (agents.isPending) return <p className="text-sm text-on-surface-variant">Loading...</p>;

  const list = agents.data;
  // First by default, so the pane is never an empty frame beside a full list.
  const active = selected ?? (list.length > 0 ? list[0].manifestId : null);
  function selectAgent(manifestId: string) {
    if (manifestId === active) return;
    if (
      hasUnsavedChanges
      && !window.confirm("Discard unsaved configuration changes and open another agent?")
    ) return;
    setHasUnsavedChanges(false);
    setSelected(manifestId);
  }


  return (
    // The registry track was a hard `20rem`, which needs 320px for itself at
    // a 320px viewport. The mock never populates agents, so the route sweep
    // passed this vacuously -- it stacks below `lg` now.
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[20rem_minmax(0,1fr)]">
      <aside className="premium-panel self-start overflow-hidden">
        <header className="border-b border-outline-variant/80 bg-surface-container-low px-4 py-3">
          <p className="premium-kicker">Agent registry</p>
          <div className="mt-1 flex items-end justify-between gap-3">
            <h2 className="text-base font-semibold text-on-surface">Configured agents</h2>
            <span className="rounded-full bg-secondary-container px-2 py-0.5 text-[11px] font-semibold text-on-secondary-container">
              {list.length}
            </span>
          </div>
        </header>
        <ul className="flex max-h-[42rem] flex-col gap-1.5 overflow-y-auto p-2">
          {list.map((agent) => (
            <li key={agent.manifestId}>
              <button
                type="button"
                onClick={() => { selectAgent(agent.manifestId); }}
                aria-current={agent.manifestId === active ? "true" : undefined}
                className={[
                  "flex w-full items-start gap-3 rounded-xl border px-3 py-3 text-left transition",
                  agent.manifestId === active
                    ? "border-primary/30 bg-secondary-container shadow-sm"
                    : "border-transparent hover:border-outline-control hover:bg-surface-container-low",
                ].join(" ")}
              >
                <span aria-hidden="true" className={[
                  "mt-1.5 size-2 shrink-0 rounded-full",
                  agent.enabled ? "bg-primary" : "bg-outline-variant",
                ].join(" ")} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-on-surface">
                    {agent.name}
                  </span>
                  <span className="mt-1 block truncate font-mono text-[10px] text-outline">
                    {agent.moduleId}
                  </span>
                  <span className="mt-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-on-surface-variant">
                    <span>{agent.status.replaceAll("_", " ")}</span>
                    <span aria-hidden="true">&middot;</span>
                    <span>v{agent.configurationVersion}</span>
                    <span aria-hidden="true">&middot;</span>
                    <span>{agent.enabled ? "Enabled" : "Disabled"}</span>
                  </span>
                  <span className="mt-1 block text-[10px] font-semibold text-primary">{configurationSourceLabel(agent.source)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      {active === null ? (
        <div className="premium-panel flex min-h-64 items-center justify-center text-sm text-on-surface-variant">
          No agents are configured.
        </div>
      ) : (
        <AgentEditor key={active} manifestId={active} onDirtyChange={setHasUnsavedChanges} />
      )}
    </div>
  );
}

/**
 * Fetches, then hands the loaded document to the editor.
 *
 * Split in two so the editor can seed its state from a prop in `useState`
 * rather than copying the query result across in an effect. An effect that
 * writes state renders once with the wrong value and again with the right one,
 * and here the wrong value is "no document" -- which would blank an operator's
 * in-progress edit every time the query refetched.
 */
function AgentEditor({
  manifestId,
  onDirtyChange,
}: {
  manifestId: string;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const configuration = useQuery({
    queryKey: ["config", "agents", manifestId],
    queryFn: () => agentConfigApi.read(manifestId),
  });

  if (configuration.error !== null) {
    return (
      <p role="alert" className="text-sm text-error">
        {configuration.error.message}
      </p>
    );
  }
  if (configuration.isPending) {
    return <p className="text-sm text-on-surface-variant">Loading...</p>;
  }
  return (
    <DocumentEditor
      manifestId={manifestId}
      path={configuration.data.path}
      loaded={configuration.data.document as Json}
      source={configuration.data.source}
      onDirtyChange={onDirtyChange}
    />
  );
}

function DocumentEditor({
  manifestId,
  path,
  loaded,
  source,
  onDirtyChange,
}: {
  manifestId: string;
  path: string;
  loaded: Json;
  source: string;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const { can } = useCapabilities();
  const [mode, setMode] = useState<"form" | "split" | "json">("form");
  const [draft, setDraft] = useState<Json>(loaded);
  const [baseline, setBaseline] = useState<Json>(loaded);
  const canWrite = can("governance.proposal.write");
  const [text, setText] = useState(() => JSON.stringify(loaded, null, 2));
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<{ proposalId: string; status: string } | null>(null);

  const [dirty, setDirty] = useState(false);

  const save = useMutation({
    mutationFn: (document: Record<string, unknown>) => agentConfigApi.save(manifestId, document),
    onSuccess: async (result, document) => {
      setProposal({ proposalId: result.proposalId, status: result.status });
      setBaseline(document as Json);
      setDirty(false);
      onDirtyChange(false);
      await queryClient.invalidateQueries({ queryKey: ["proposals"] });
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
    if (changed) setProposal(null);
    onDirtyChange(changed);
  }

  /** JSON mode is the source of truth while it is open, and it may be invalid. */
  function switchTo(next: "form" | "split" | "json") {
    if (next === mode) return;
    if (mode === "form") {
      if (next !== "form") setText(JSON.stringify(draft, null, 2));
      setMode(next);
      return;
    }
    if (next === "form") {
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
    } else if (next === "split") {
      try {
        setDraft(JSON.parse(text) as Json);
        setJsonError(null);
      } catch (error) {
        setJsonError(error instanceof Error ? error.message : "That is not valid JSON.");
        return;
      }
    }
    setMode(next);
  }

  function onSave() {
    setProposal(null);
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
      setJsonError("An agent configuration must be an object.");
      return;
    }
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

  const formEditor = (
    <fieldset disabled={!canWrite} className="max-h-[34rem] min-h-[28rem] overflow-y-auto rounded-xl border border-outline-variant bg-surface-container-lowest p-4 shadow-inner disabled:cursor-not-allowed disabled:opacity-75">
      <Node value={draft} onChange={updateDraft} />
    </fieldset>
  );

  const jsonEditor = (
    <textarea
      aria-label="Agent configuration JSON"
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
          <p className="premium-kicker">Agent configuration</p>
          <p className="mt-0.5 max-w-72 truncate text-xs text-on-surface-variant" title={path}>
            {path}
          </p>
          <div className="mt-1.5 flex items-center gap-2 text-[10px] font-semibold">
            <span className="rounded-full bg-secondary-container px-2 py-0.5 text-on-secondary-container">
              {configurationSourceLabel(source)}
            </span>
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
            setProposal(null);
            setDirty(false);
            onDirtyChange(false);
          }}
          disabled={!canWrite || !dirty}
          className="flex items-center gap-1.5 rounded-lg border border-outline-control bg-surface-container-lowest px-3 py-2 text-xs font-medium text-on-surface-variant transition hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
        >
          <RotateCcw size={13} aria-hidden="true" />
          Reset
        </button>
        <button
          type="button"
          onClick={onSave}
          disabled={save.isPending || !canWrite || !dirty}
          title={canWrite ? undefined : "Proposal write access is required"}
          className="rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-on-primary shadow-sm transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {save.isPending ? "Submitting..." : "Submit for review"}
        </button>
      </header>

      <div className="flex flex-col gap-3 p-4">
        {!canWrite ? (
          <p className="rounded-lg border border-outline-variant bg-surface-container-low px-3 py-2 text-sm text-on-surface-variant">
            Read-only access. Proposal write permission is required to change this configuration.
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
        {proposal !== null && save.error === null ? (
          <div role="status" className="flex items-center justify-between gap-4 rounded-xl border border-primary/20 bg-secondary-container px-4 py-3 text-sm text-on-secondary-container">
            <span className="flex items-center gap-2">
              <CheckCircle2 size={16} aria-hidden="true" />
              Proposal {proposal.proposalId} is {proposal.status.replaceAll("_", " ").toLowerCase()}.
              The active configuration has not changed.
            </span>
            <Link href="/approvals" className="shrink-0 font-semibold text-primary hover:underline">
              Open Approvals
            </Link>
          </div>
        ) : null}

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
 */
/**
 * One leaf of the agent document.
 *
 * `labelledBy` is the id of the `<span>` that shows this field's name, which
 * lives in the *parent* object's render rather than here -- the name comes from
 * the key, and the key is the parent's to know. Every scalar input therefore
 * looked labelled and was not: `bay_allocation` alone renders fourteen
 * non-boolean scalar leaves, which is the audit's "14 agent labels" exactly.
 * Passing the id down is what turns the visible name into a programmatic one.
 */
function Node({
  value,
  onChange,
  labelledBy,
}: {
  value: Json;
  onChange: (next: Json) => void;
  labelledBy?: string;
}) {
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
    return <ObjectNode value={value} onChange={onChange} />;
  }

  if (typeof value === "boolean") {
    return (
      <label className="flex w-fit cursor-pointer items-center gap-2 text-sm text-on-surface">
        <input
          type="checkbox"
          checked={value}
          // The field's name, not "Yes"/"No". A checkbox already announces its
          // own state, so the visible word is redundant to a screen reader and
          // was the only accessible name it had -- fourteen inputs on one
          // screen all called "Yes".
          aria-labelledby={labelledBy}
          onChange={(event) => { onChange(event.target.checked); }}
          className="size-4 accent-primary"
        />
        {value ? "Yes" : "No"}
      </label>
    );
  }

  if (typeof value === "number") {
    return (
      <input
        type="number"
        value={value}
        aria-labelledby={labelledBy}
        onChange={(event) => {
          // An empty or half-typed number must not become NaN in the document:
          // it would serialize as null and silently blank the setting.
          const parsed = Number(event.target.value);
          onChange(event.target.value === "" || Number.isNaN(parsed) ? 0 : parsed);
        }}
        className="w-40 rounded border border-outline-control bg-surface px-2 py-1 text-sm text-on-surface outline-none focus:border-primary"
      />
    );
  }

  return (
    <input
      type="text"
      value={value ?? ""}
      aria-labelledby={labelledBy}
      onChange={(event) => { onChange(event.target.value); }}
      className="w-full rounded border border-outline-control bg-surface px-2 py-1 text-sm text-on-surface outline-none focus:border-primary"
    />
  );
}

type JsonKind = "string" | "number" | "boolean" | "object" | "array";

/**
 * An object's fields, each one named by an element the input can point at.
 *
 * Split out of `Node` because it needs `useId`, and `Node` returns early for
 * arrays and scalars -- a hook above those branches would run for every leaf
 * and a hook below them would be conditional.
 */
function ObjectNode({ value, onChange }: { value: JsonObject; onChange: (next: Json) => void }) {
  const base = useId();
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
        className="flex items-center gap-1 rounded-lg border border-outline-control bg-surface-container-lowest px-2.5 py-2 text-[11px] font-medium text-on-surface-variant transition hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
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

export type { AgentSummary };
