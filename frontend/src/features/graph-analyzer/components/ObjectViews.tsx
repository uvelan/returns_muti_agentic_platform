import { useEffect, useState } from "react";
import { X } from "lucide-react";

import type { previewSourceObject } from "../../../api/graphAnalyzer";
import type { SourceObject } from "../../../contracts/graphAnalyzer";

/**
 * How a source object is read: its declared shape, and a bounded sample of it.
 *
 * Extracted so the Data Sources drill-down and anything else that inspects an
 * object render the same thing. They were one screen's private helpers, and a
 * second reader would otherwise have meant a second copy that drifts.
 *
 * Both are read-only by construction: there is no control here that could edit
 * a value, and no request these components can make other than the bounded
 * preview they are handed.
 */

/** The fields a source declares, with the evidence that shaped the proposal. */
export function StructureView({ object }: { readonly object: SourceObject }) {
  const fields = object.fields ?? [];
  if (fields.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        No field metadata is available. Refresh the source or revalidate the connection.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wider text-slate-400">
          <tr>
            <th className="pb-3">Field</th>
            <th className="pb-3">Type</th>
            <th className="pb-3">Nullable</th>
            <th className="pb-3">Evidence</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => (
            <tr key={field.name} className="border-t border-emerald-950">
              <td className="py-3 font-mono text-emerald-200">{field.name}</td>
              <td className="py-3 text-slate-400">{field.dataType}</td>
              <td className="py-3 text-slate-400">{field.nullable ? "Yes" : "Required"}</td>
              <td className="py-3">
                <div className="flex gap-2">
                  {field.identifier ? (
                    <span className="rounded bg-violet-950 px-2 py-0.5 text-xs text-violet-300">
                      Identifier
                    </span>
                  ) : null}
                  {field.indexed ? (
                    <span className="rounded bg-sky-950 px-2 py-0.5 text-xs text-sky-300">
                      Indexed
                    </span>
                  ) : null}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type PreviewPayload = Awaited<ReturnType<typeof previewSourceObject>>;

/** A bounded page of rows, as a table or as JSON. */
export function DataView({
  asJson,
  onFormat,
  loading,
  error,
  data,
  page,
  onPage,
}: {
  readonly asJson: boolean;
  readonly onFormat: (asJson: boolean) => void;
  readonly loading: boolean;
  readonly error: string | null;
  readonly data: PreviewPayload | undefined;
  readonly page: number;
  readonly onPage: (page: number) => void;
}) {
  // A table cell is truncated to keep the grid readable, which means the long
  // values -- the nested documents and arrays this platform mostly reads --
  // are exactly the ones a row does not show. Opening the whole record is how
  // you see them without leaving the page for a JSON dump of all 25 rows.
  const [detail, setDetail] = useState<Readonly<Record<string, unknown>> | null>(null);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-1 rounded-lg border border-emerald-950 p-1">
          {([false, true] as const).map((json) => (
            <button
              key={String(json)}
              type="button"
              onClick={() => {
                onFormat(json);
              }}
              aria-pressed={asJson === json}
              className={`rounded-md px-3 py-1.5 text-xs font-medium ${
                asJson === json ? "bg-emerald-400 text-emerald-950" : "text-slate-400 hover:text-white"
              }`}
            >
              {json ? "JSON view" : "Table view"}
            </button>
          ))}
        </div>
        <span className="text-[11px] text-slate-400">
          {asJson ? "" : "Select a row to read the whole record. "}Bounded read. Source records are
          never editable here.
        </span>
      </div>

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((id) => (
            <div key={id} className="h-9 animate-pulse rounded bg-white/[.035]" />
          ))}
        </div>
      ) : error !== null ? (
        <p role="alert" className="text-sm text-red-300">
          Data preview failed: {error}
        </p>
      ) : data === undefined || data.rows.length === 0 ? (
        <p className="text-sm text-slate-400">No accessible records were returned for this page.</p>
      ) : asJson ? (
        <pre
          tabIndex={0}
          aria-label="Raw object"
          className="max-h-96 overflow-auto rounded-lg bg-[#050c0a] p-4 text-xs leading-5 text-emerald-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
        >
          {JSON.stringify(data.rows, null, 2)}
        </pre>
      ) : (
        <div className="overflow-auto">
          <table className="min-w-full text-left text-xs">
            <thead className="text-slate-400">
              <tr>
                {data.columns.map((column) => (
                  <th
                    key={column}
                    className="whitespace-nowrap border-b border-emerald-950 px-3 py-2"
                  >
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, rowIndex) => (
                <tr
                  key={rowIndex}
                  tabIndex={0}
                  aria-label={`Open record ${String(rowIndex + 1)}`}
                  onClick={() => {
                    setDetail(row);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setDetail(row);
                    }
                  }}
                  className="cursor-pointer outline-none hover:bg-white/[.04] focus-visible:bg-white/[.06]"
                >
                  {data.columns.map((column) => (
                    <td
                      key={column}
                      className="max-w-56 truncate border-b border-emerald-950/70 px-3 py-2 font-mono text-slate-300"
                    >
                      {formatCell(row[column])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          disabled={page === 1 || loading}
          onClick={() => {
            onPage(page - 1);
          }}
          className="rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-30"
        >
          Previous
        </button>
        <span className="px-2 py-1 text-xs text-slate-400">Page {page}</span>
        <button
          type="button"
          disabled={loading || data === undefined || data.rows.length < data.pageSize}
          onClick={() => {
            onPage(page + 1);
          }}
          className="rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-30"
        >
          Next
        </button>
      </div>

      <RecordDialog
        row={detail}
        columns={data?.columns ?? []}
        onClose={() => {
          setDetail(null);
        }}
      />
    </div>
  );
}

/**
 * One record, whole.
 *
 * Field by field first, because that is how someone checks a value they came
 * looking for, with the raw document underneath for the shape of it. Both are
 * text: there is no control in here that writes, and the record arrived from
 * the same bounded preview the table read.
 */
function RecordDialog({
  row,
  columns,
  onClose,
}: {
  readonly row: Readonly<Record<string, unknown>> | null;
  readonly columns: readonly string[];
  readonly onClose: () => void;
}) {
  useEffect(() => {
    if (row === null) return undefined;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", close);
    return () => {
      window.removeEventListener("keydown", close);
    };
  }, [row, onClose]);

  if (row === null) return null;

  // Whatever the record carries, in the column order the table used, with any
  // field the columns did not name appended rather than dropped.
  const names = [...columns, ...Object.keys(row).filter((key) => !columns.includes(key))];

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="analyzer-record-title"
        onClick={(event) => {
          event.stopPropagation();
        }}
        className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-emerald-900 bg-[#0a1714] shadow-2xl"
      >
        <header className="flex items-start justify-between gap-4 border-b border-emerald-950 px-5 py-4">
          <div>
            <h2 id="analyzer-record-title" className="font-semibold text-white">
              Record
            </h2>
            <p className="mt-0.5 text-xs text-slate-400">
              {names.length} field(s) · read-only
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close record"
            className="rounded-md p-1.5 text-slate-400 hover:bg-white/5 hover:text-white"
          >
            <X size={16} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <dl className="space-y-3">
            {names.map((name) => (
              <div key={name} className="grid gap-1 sm:grid-cols-[minmax(0,180px)_minmax(0,1fr)]">
                <dt className="font-mono text-xs text-emerald-300">{name}</dt>
                <dd className="whitespace-pre-wrap break-words font-mono text-xs text-slate-300">
                  {formatValue(row[name])}
                </dd>
              </div>
            ))}
          </dl>

          <details className="mt-5">
            <summary className="cursor-pointer text-xs text-slate-400">Raw document</summary>
            <pre
              tabIndex={0}
              aria-label="Raw payload"
              className="mt-2 overflow-auto rounded-lg bg-[#050c0a] p-4 text-xs leading-5 text-emerald-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
            >
              {JSON.stringify(row, null, 2)}
            </pre>
          </details>
        </div>
      </section>
    </div>
  );
}

/** A value at full length, indented when it has structure. */
function formatValue(value: unknown): string {
  if (value === null) return "null";
  if (value === undefined) return "—";
  if (typeof value === "string") return value;
  const serialized: string | undefined = JSON.stringify(value, null, 2);
  return typeof serialized === "string" ? serialized : typeof value;
}

/**
 * A cell as text.
 *
 * A field the document does not carry and a field explicitly set to null are
 * different facts about a source, so they read differently -- but the absent
 * one reads as a dash rather than as the word "undefined", which is a
 * JavaScript detail and not something the source said.
 */
function formatCell(value: unknown): string {
  if (value === null) return "null";
  if (value === undefined) return "—";
  const serialized: string | undefined = JSON.stringify(value);
  return typeof serialized === "string" ? serialized : typeof value;
}
