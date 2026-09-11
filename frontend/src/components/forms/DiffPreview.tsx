import { mergePatchOf, type JsonRecord, type JsonValue } from "../../api/mergePatch";

/**
 * What Publish will actually send -- the merge patch `mergePatchOf` computes
 * from `before` to `after`, flattened into one row per changed path. This is
 * deliberately the same function every publish path already calls, not a
 * second diff algorithm: what the operator sees here is what the PATCH body
 * carries, not an approximation of it.
 *
 * A key whose value is an object on both sides is walked rather than shown
 * as one opaque "object changed" row, so a single field changing three
 * levels deep still reads as one row naming that field, not the whole
 * subtree it sits in.
 */

type DiffRow = { path: string; before: JsonValue; after: JsonValue; deleted: boolean };

function isRecord(value: JsonValue): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function rowsOf(before: JsonValue, patch: JsonRecord, prefix: string): DiffRow[] {
  const rows: DiffRow[] = [];
  for (const [key, value] of Object.entries(patch)) {
    const path = prefix === "" ? key : `${prefix}.${key}`;
    const previous: JsonValue = isRecord(before) && key in before ? before[key] : null;
    if (value === null) {
      rows.push({ path, before: previous, after: null, deleted: true });
      continue;
    }
    if (isRecord(value) && isRecord(previous)) {
      rows.push(...rowsOf(previous, value, path));
      continue;
    }
    rows.push({ path, before: previous, after: value, deleted: false });
  }
  return rows;
}

function format(value: JsonValue): string {
  if (value === null) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function DiffPreview({
  before,
  after,
  title = "Changes",
}: {
  before: JsonRecord;
  after: JsonRecord;
  title?: string;
}) {
  const patch = mergePatchOf(before, after);
  const rows = isRecord(patch) ? rowsOf(before, patch, "") : [];

  if (rows.length === 0) {
    return (
      <p className="rounded-lg border border-outline-variant bg-surface-container-low px-3 py-2 text-sm text-on-surface-variant">
        Nothing changed
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-outline-variant">
      <table className="w-full min-w-max border-collapse text-xs">
        <caption className="sr-only">{title}</caption>
        <thead className="bg-surface-container-low">
          <tr>
            <th scope="col" className="px-3 py-2 text-left font-semibold text-on-surface-variant">Path</th>
            <th scope="col" className="px-3 py-2 text-left font-semibold text-on-surface-variant">Before</th>
            <th scope="col" className="px-3 py-2 text-left font-semibold text-on-surface-variant">After</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.path} className="border-t border-outline-variant/60">
              <td className="px-3 py-2 font-mono text-on-surface">{row.path}</td>
              <td className="px-3 py-2 font-mono text-on-surface-variant">{format(row.before)}</td>
              <td className={`px-3 py-2 font-mono ${row.deleted ? "text-error" : "text-primary"}`}>
                {row.deleted ? "Removed" : format(row.after)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
