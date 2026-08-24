import { useMemo, useState } from "react";
import {
  AlertTriangle,
  FileJson,
  ListTree,
  RotateCcw,
  Save,
  Search,
  ShieldCheck,
} from "lucide-react";
import type { SchemaDocument } from "../../../api/schemaReleases";

/**
 * The running schema, edited in place.
 *
 * Everything else in this workspace edits the *proposed* graph -- a draft that
 * becomes real only when it is finalized and synchronized. This edits the
 * schema the platform is answering with right now, which until this existed
 * meant editing a YAML file on the server and restarting it.
 *
 * Two views over one document, because the document is thousands of lines and
 * the two jobs it gets used for are nothing alike:
 *
 *   * **Fields** flattens it to `path -> value` with a filter. This is how you
 *     change one capability on one field without scrolling through a schema
 *     that describes fifteen entities.
 *   * **Document** is the whole thing as JSON. Structural edits -- adding a
 *     field, a projection, an entity -- are not `path -> value` changes, and a
 *     view that only offered leaves would quietly make them impossible.
 *
 * Neither view computes the checksum. The server recomputes it from the
 * document it stores, because a checksum supplied by the thing being checked
 * proves nothing -- and a stale one is how a hand edit produced a platform that
 * would not start.
 */

type View = "FIELDS" | "DOCUMENT";

type Leaf = {
  readonly path: string;
  readonly value: string | number | boolean | null;
};

/** Every scalar in the document, as a dotted path. Arrays keep their index. */
function leaves(value: unknown, path = ""): readonly Leaf[] {
  if (value === null || typeof value !== "object") {
    return [{ path, value: value as Leaf["value"] }];
  }
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => leaves(item, `${path}[${String(index)}]`));
  }
  return Object.entries(value as Record<string, unknown>).flatMap(([key, item]) =>
    leaves(item, path === "" ? key : `${path}.${key}`),
  );
}

/** Split `a.b[2].c` into the keys and indices needed to walk it. */
function segments(path: string): readonly (string | number)[] {
  const parts: (string | number)[] = [];
  for (const chunk of path.split(".")) {
    const [name, ...indices] = chunk.split("[");
    if (name !== "") parts.push(name);
    for (const index of indices) parts.push(Number.parseInt(index, 10));
  }
  return parts;
}

/**
 * A copy of `document` with one path replaced.
 *
 * Structurally shared rather than deep-cloned: only the containers along the
 * edited path are rebuilt, so editing one field in a schema this size does not
 * copy the whole thing on every keystroke.
 */
function withValue(
  document: Record<string, unknown>,
  path: string,
  value: unknown,
): Record<string, unknown> {
  const parts = segments(path);
  if (parts.length === 0) return document;

  const replace = (node: unknown, depth: number): unknown => {
    const key = parts[depth];
    const last = depth === parts.length - 1;
    if (typeof key === "number") {
      const copy = [...(node as unknown[])];
      copy[key] = last ? value : replace(copy[key], depth + 1);
      return copy;
    }
    const copy = { ...(node as Record<string, unknown>) };
    copy[key] = last ? value : replace(copy[key], depth + 1);
    return copy;
  };
  return replace(document, 0) as Record<string, unknown>;
}

/** Read back the same type the field already held, so a number stays a number. */
function coerce(previous: Leaf["value"], next: string): Leaf["value"] {
  if (typeof previous === "boolean") return next === "true";
  if (typeof previous === "number") {
    const parsed = Number(next);
    return Number.isNaN(parsed) ? previous : parsed;
  }
  if (previous === null && next.trim() === "") return null;
  return next;
}

export function RuntimeSchemaEditor({
  active,
  saving,
  error,
  onSave,
  onReload,
}: {
  readonly active: SchemaDocument;
  readonly saving: boolean;
  readonly error: string | null;
  readonly onSave: (document: Record<string, unknown>, activate: boolean) => void;
  readonly onReload: () => void;
}) {
  const [view, setView] = useState<View>("FIELDS");
  const [draft, setDraft] = useState<Record<string, unknown>>(active.document);
  const [filter, setFilter] = useState("");
  const [activate, setActivate] = useState(true);
  // The JSON view edits text, not a document: a half-typed object is not
  // parseable, and reformatting the box under the cursor on every keystroke
  // would make it unusable.
  const [text, setText] = useState(() => JSON.stringify(active.document, null, 2));
  const [textError, setTextError] = useState<string | null>(null);

  const allLeaves = useMemo(() => leaves(draft), [draft]);
  const changed = useMemo(() => {
    const original = new Map(leaves(active.document).map((leaf) => [leaf.path, leaf.value]));
    return new Set(
      allLeaves.filter((leaf) => original.get(leaf.path) !== leaf.value).map((leaf) => leaf.path),
    );
  }, [allLeaves, active.document]);

  const needle = filter.trim().toLowerCase();
  const shown = useMemo(
    () =>
      needle === ""
        ? allLeaves.slice(0, 200)
        : allLeaves
            .filter(
              (leaf) =>
                leaf.path.toLowerCase().includes(needle) ||
                String(leaf.value).toLowerCase().includes(needle),
            )
            .slice(0, 200),
    [allLeaves, needle],
  );

  const dirty = changed.size > 0 || (view === "DOCUMENT" && text !== JSON.stringify(draft, null, 2));

  const adoptText = () => {
    try {
      const parsed: unknown = JSON.parse(text);
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        setTextError("The schema must be a JSON object.");
        return null;
      }
      setTextError(null);
      const next = parsed as Record<string, unknown>;
      setDraft(next);
      return next;
    } catch (parseError) {
      setTextError(parseError instanceof Error ? parseError.message : "That is not valid JSON.");
      return null;
    }
  };

  const save = () => {
    const document = view === "DOCUMENT" ? adoptText() : draft;
    if (document !== null) onSave(document, activate);
  };

  const reset = () => {
    setDraft(active.document);
    setText(JSON.stringify(active.document, null, 2));
    setTextError(null);
  };

  return (
    <section className="rounded-xl border border-analyzer-outline-variant bg-analyzer-surface-container p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h3 className="font-semibold text-analyzer-on-surface-emphasis">
            Running schema configuration
          </h3>
          <p className="mt-1 max-w-prose text-sm text-analyzer-on-surface-variant">
            This is the schema the copilot is answering with. Saving publishes a new release and,
            unless you say otherwise, points the runtime at it.
          </p>
          <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs">
            <div className="flex gap-2">
              <dt className="text-analyzer-on-surface-variant">Release</dt>
              <dd className="font-mono text-analyzer-on-surface">{active.configurationReleaseId}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-analyzer-on-surface-variant">Checksum</dt>
              <dd className="font-mono text-analyzer-on-surface">
                {active.configurationChecksum.slice(0, 12)}…
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-analyzer-on-surface-variant">Version</dt>
              <dd className="font-mono text-analyzer-on-surface">{active.schemaVersion}</dd>
            </div>
          </dl>
        </div>
        <div className="flex gap-1 rounded-lg border border-analyzer-outline-control p-1">
          {(
            [
              ["FIELDS", "Fields", ListTree],
              ["DOCUMENT", "Document", FileJson],
            ] as const
          ).map(([value, label, Icon]) => (
            <button
              key={value}
              type="button"
              aria-pressed={view === value}
              onClick={() => {
                if (value === "DOCUMENT") setText(JSON.stringify(draft, null, 2));
                setView(value);
              }}
              className={`inline-flex items-center gap-2 rounded px-3 py-1.5 text-xs font-medium ${
                view === value
                  ? "bg-analyzer-primary-container text-analyzer-accent"
                  : "text-analyzer-on-surface-variant hover:text-analyzer-on-surface"
              }`}
            >
              <Icon size={13} aria-hidden="true" />
              {label}
            </button>
          ))}
        </div>
      </div>

      {active.fromFile ? (
        <p className="mt-4 flex gap-2 rounded-lg border border-analyzer-outline bg-analyzer-surface-sunken p-3 text-xs leading-5 text-analyzer-on-surface-variant">
          <AlertTriangle size={15} className="mt-px shrink-0 text-analyzer-warning" aria-hidden="true" />
          Nothing has been published yet, so this is the schema file answering by fallback. Saving
          publishes it as the first release.
        </p>
      ) : null}

      {view === "FIELDS" ? (
        <div className="mt-4">
          <label className="block">
            <span className="text-xs text-analyzer-on-surface-variant">Filter fields</span>
            <span className="relative mt-1 block">
              <Search
                size={14}
                aria-hidden="true"
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-analyzer-on-surface-variant"
              />
              <input
                value={filter}
                onChange={(event) => {
                  setFilter(event.target.value);
                }}
                placeholder="contact_first_name, searchable, entities.customer…"
                className="w-full rounded-lg border border-analyzer-outline-control bg-analyzer-surface-sunken py-2 pl-9 pr-3 text-sm text-analyzer-on-surface"
              />
            </span>
          </label>

          <p className="mt-2 text-xs text-analyzer-on-surface-variant" role="status">
            {shown.length} of {allLeaves.length} values
            {allLeaves.length > shown.length && needle === "" ? " (filter to see the rest)" : ""}
            {changed.size > 0 ? ` · ${String(changed.size)} edited` : ""}
          </p>

          <div className="mt-3 max-h-[28rem] divide-y divide-analyzer-outline-variant overflow-y-auto rounded-lg border border-analyzer-outline-variant">
            {shown.map((leaf) => (
              <label
                key={leaf.path}
                className="grid grid-cols-1 items-center gap-2 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,18rem)]"
              >
                <span
                  className={`truncate font-mono text-xs ${
                    changed.has(leaf.path)
                      ? "text-analyzer-accent"
                      : "text-analyzer-on-surface-variant"
                  }`}
                  title={leaf.path}
                >
                  {leaf.path}
                </span>
                {typeof leaf.value === "boolean" ? (
                  <select
                    value={String(leaf.value)}
                    onChange={(event) => {
                      setDraft(withValue(draft, leaf.path, event.target.value === "true"));
                    }}
                    className="rounded border border-analyzer-outline-control bg-analyzer-surface-sunken px-2 py-1.5 text-sm text-analyzer-on-surface"
                  >
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : (
                  <input
                    value={leaf.value === null ? "" : String(leaf.value)}
                    onChange={(event) => {
                      setDraft(withValue(draft, leaf.path, coerce(leaf.value, event.target.value)));
                    }}
                    className="rounded border border-analyzer-outline-control bg-analyzer-surface-sunken px-2 py-1.5 font-mono text-sm text-analyzer-on-surface"
                  />
                )}
              </label>
            ))}
            {shown.length === 0 ? (
              <p className="p-6 text-center text-sm text-analyzer-on-surface-variant">
                Nothing matches “{filter}”.
              </p>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="mt-4">
          <label className="block">
            <span className="text-xs text-analyzer-on-surface-variant">
              Schema document (JSON). Structural changes belong here.
            </span>
            <textarea
              value={text}
              spellCheck={false}
              onChange={(event) => {
                setText(event.target.value);
                setTextError(null);
              }}
              onBlur={adoptText}
              rows={22}
              className="mt-1 w-full rounded-lg border border-analyzer-outline-control bg-analyzer-surface-sunken p-3 font-mono text-xs leading-5 text-analyzer-on-surface"
            />
          </label>
          {textError !== null ? (
            <p role="alert" className="mt-2 text-sm text-analyzer-error">
              {textError}
            </p>
          ) : null}
        </div>
      )}

      {error !== null ? (
        <div role="alert" className="mt-4 rounded-lg border border-analyzer-outline bg-analyzer-surface-sunken p-3 text-sm text-analyzer-error">
          {error}
          {error.toLowerCase().includes("changed") ? (
            <button
              type="button"
              onClick={onReload}
              className="ml-2 underline underline-offset-2 hover:no-underline"
            >
              Reload the current schema
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-analyzer-outline-variant pt-4">
        <label className="flex items-center gap-2 text-xs text-analyzer-on-surface-variant">
          <input
            type="checkbox"
            checked={activate}
            onChange={(event) => {
              setActivate(event.target.checked);
            }}
          />
          Point the runtime at this release once it is published
        </label>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={!dirty || saving}
            onClick={reset}
            className="inline-flex items-center gap-2 rounded-lg border border-analyzer-outline-control px-3 py-2 text-sm text-analyzer-on-surface-variant disabled:opacity-40"
          >
            <RotateCcw size={14} aria-hidden="true" />
            Discard changes
          </button>
          <button
            type="button"
            disabled={!dirty || saving}
            onClick={save}
            className="inline-flex items-center gap-2 rounded-lg bg-analyzer-primary px-4 py-2 text-sm font-semibold text-analyzer-on-primary disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Save size={14} aria-hidden="true" />
            {saving ? "Publishing…" : activate ? "Publish and activate" : "Publish only"}
          </button>
        </div>
      </div>

      <p className="mt-3 text-[11px] leading-5 text-analyzer-accent">
        <ShieldCheck className="mr-1 inline" size={13} aria-hidden="true" />
        The checksum is recomputed from what is stored. A release that fails validation is refused
        before it is published, and the previous one keeps serving.
      </p>
    </section>
  );
}
