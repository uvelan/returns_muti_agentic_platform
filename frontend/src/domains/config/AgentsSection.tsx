import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Braces, ListTree, Minus, Plus } from "lucide-react";

import { agentConfigApi, type AgentSummary } from "../../api/agentConfig";

/**
 * Per-agent configuration, editable two ways.
 *
 * Each agent already had its own module file; nothing served it. This is that
 * surface: pick an agent, then edit its document either as JSON or through a
 * form that follows the document's own nesting.
 *
 * **Neither editor knows the schema, and that is deliberate.** A module's
 * payload differs by agent, and the backend validates a save through the
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

/** A field's label, from its key: `configuration_version` -> `Configuration version`. */
function label(key: string): string {
  const spaced = key.replace(/[_-]+/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function AgentsSection() {
  const [selected, setSelected] = useState<string | null>(null);
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

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2.4fr)]">
      <ul className="flex flex-col gap-1">
        {list.map((agent) => (
          <li key={agent.manifestId}>
            <button
              type="button"
              onClick={() => { setSelected(agent.manifestId); }}
              className={`flex w-full flex-col gap-0.5 rounded-lg border px-3 py-2 text-left transition ${
                agent.manifestId === active
                  ? "border-primary bg-secondary-container"
                  : "border-outline-variant hover:border-primary"
              }`}
            >
              <span className="text-sm font-medium text-on-surface">{agent.name}</span>
              <span className="text-[11px] text-outline">
                {agent.status} -- v{agent.configurationVersion}
                {agent.enabled ? "" : " -- disabled"}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {active === null ? (
        <p className="text-sm text-on-surface-variant">No agents are configured.</p>
      ) : (
        <AgentEditor key={active} manifestId={active} />
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
function AgentEditor({ manifestId }: { manifestId: string }) {
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
    />
  );
}

function DocumentEditor({
  manifestId,
  path,
  loaded,
}: {
  manifestId: string;
  path: string;
  loaded: Json;
}) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"form" | "json">("form");
  const [draft, setDraft] = useState<Json>(loaded);
  const [text, setText] = useState(() => JSON.stringify(loaded, null, 2));
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const save = useMutation({
    mutationFn: (document: Record<string, unknown>) => agentConfigApi.save(manifestId, document),
    onSuccess: async (result) => {
      setSaved(true);
      setDraft(result.document as Json);
      setText(JSON.stringify(result.document, null, 2));
      await queryClient.invalidateQueries({ queryKey: ["config", "agents"] });
    },
  });

  /** JSON mode is the source of truth while it is open, and it may be invalid. */
  function switchTo(next: "form" | "json") {
    if (next === "form" && mode === "json") {
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
    if (next === "json") setText(JSON.stringify(draft, null, 2));
    setMode(next);
  }

  function onSave() {
    setSaved(false);
    let document: Json = draft;
    if (mode === "json") {
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

  return (
    <section className="flex min-w-0 flex-col gap-3">
      <div className="flex items-center gap-2">
        <div className="flex overflow-hidden rounded-lg border border-outline-variant">
          <button
            type="button"
            onClick={() => { switchTo("form"); }}
            aria-pressed={mode === "form"}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs transition ${
              mode === "form" ? "bg-primary text-on-primary" : "text-on-surface-variant"
            }`}
          >
            <ListTree size={13} />
            Form
          </button>
          <button
            type="button"
            onClick={() => { switchTo("json"); }}
            aria-pressed={mode === "json"}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs transition ${
              mode === "json" ? "bg-primary text-on-primary" : "text-on-surface-variant"
            }`}
          >
            <Braces size={13} />
            JSON
          </button>
        </div>
        <span className="flex-1 truncate text-xs text-outline">{path}</span>
        <button
          type="button"
          onClick={onSave}
          disabled={save.isPending}
          className="rounded-lg bg-primary px-4 py-1.5 text-xs font-medium text-on-primary transition disabled:opacity-40"
        >
          {save.isPending ? "Saving..." : "Save"}
        </button>
      </div>

      {jsonError !== null ? (
        <p role="alert" className="text-sm text-error">
          {jsonError}
        </p>
      ) : null}
      {/*
        The backend's own words. It validates a save by writing the file and
        reloading it through the loader the platform boots from, so its message
        names the field that is wrong -- "invalid configuration" would give an
        operator nothing to correct.
      */}
      {save.error !== null ? (
        <p role="alert" className="text-sm text-error">
          {save.error.message}
        </p>
      ) : null}
      {saved && save.error === null ? (
        <p className="text-sm text-primary">Saved.</p>
      ) : null}

      {mode === "json" ? (
        <textarea
          aria-label="Agent configuration JSON"
          value={text}
          onChange={(event) => { setText(event.target.value); setSaved(false); }}
          spellCheck={false}
          className="h-[28rem] w-full rounded-lg border border-outline-variant bg-surface p-3 font-mono text-xs text-on-surface outline-none focus:border-primary"
        />
      ) : (
        <div className="max-h-[28rem] overflow-y-auto rounded-lg border border-outline-variant p-3">
          <Node
            value={draft}
            onChange={(next) => { setDraft(next); setSaved(false); }}
          />
        </div>
      )}
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
function Node({ value, onChange }: { value: Json; onChange: (next: Json) => void }) {
  if (Array.isArray(value)) {
    return (
      <div className="flex flex-col gap-2">
        {value.map((item, index) => (
          <div key={index} className="flex items-start gap-2">
            <span className="mt-2 w-5 shrink-0 text-right text-[11px] text-outline">{index}</span>
            <div className="min-w-0 flex-1">
              <Node
                value={item}
                onChange={(next) => {
                  onChange(value.map((existing, at) => (at === index ? next : existing)));
                }}
              />
            </div>
            <button
              type="button"
              aria-label={`Remove item ${String(index)}`}
              onClick={() => { onChange(value.filter((_, at) => at !== index)); }}
              className="mt-1 flex size-6 shrink-0 items-center justify-center rounded border border-outline-variant text-on-surface-variant transition hover:border-error hover:text-error"
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
          className="flex w-fit items-center gap-1 rounded border border-outline-variant px-2 py-1 text-[11px] text-on-surface-variant transition hover:border-primary hover:text-primary"
        >
          <Plus size={11} />
          Add
        </button>
      </div>
    );
  }

  if (isObject(value)) {
    return (
      <div className="flex flex-col gap-2.5 border-l border-outline-variant pl-3">
        {Object.entries(value).map(([key, child]) => (
          <div key={key} className="flex flex-col gap-1">
            <span className="text-[11px] font-medium text-on-surface-variant">{label(key)}</span>
            <Node
              value={child}
              onChange={(next) => { onChange({ ...value, [key]: next }); }}
            />
          </div>
        ))}
      </div>
    );
  }

  if (typeof value === "boolean") {
    return (
      <label className="flex w-fit cursor-pointer items-center gap-2 text-sm text-on-surface">
        <input
          type="checkbox"
          checked={value}
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
        onChange={(event) => {
          // An empty or half-typed number must not become NaN in the document:
          // it would serialize as null and silently blank the setting.
          const parsed = Number(event.target.value);
          onChange(event.target.value === "" || Number.isNaN(parsed) ? 0 : parsed);
        }}
        className="w-40 rounded border border-outline-variant bg-surface px-2 py-1 text-sm text-on-surface outline-none focus:border-primary"
      />
    );
  }

  return (
    <input
      type="text"
      value={value ?? ""}
      onChange={(event) => { onChange(event.target.value); }}
      className="w-full rounded border border-outline-variant bg-surface px-2 py-1 text-sm text-on-surface outline-none focus:border-primary"
    />
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
