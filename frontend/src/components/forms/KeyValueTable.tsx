import { useId, useState } from "react";
import { ChevronDown, ChevronUp, Minus, Plus } from "lucide-react";

import type { JsonValue } from "../../api/mergePatch";

/**
 * The mapping editor `DocumentEditor` reaches for once an object's keys are
 * *data* rather than schema -- ship-via codes, dependency names, task ids --
 * where the generic per-key form (one box per key, named from the key) makes
 * no sense because the keys themselves are what is being edited. Reusing
 * `mergePatch.ts`'s `JsonValue` rather than declaring a third copy of the
 * same recursive JSON type is deliberate: `DocumentEditor.Json` already
 * crosses this exact boundary through `mergePatchOf`, so the shapes are
 * proven compatible.
 *
 * Order is insertion order -- the order `entries` arrives in -- unless
 * `sortable` is set, in which case reorder controls appear. Renaming a key
 * edits it in place rather than delete-then-add, so a mid-edit duplicate is
 * flagged without losing the row.
 */

export type KeyValueEntry = { key: string; value: JsonValue };
export type KeyValueKind = "string" | "number" | "boolean" | "json";

function blankValue(kind: KeyValueKind): JsonValue {
  if (kind === "number") return 0;
  if (kind === "boolean") return false;
  if (kind === "json") return {};
  return "";
}

export function KeyValueTable({
  label,
  hint,
  error,
  entries,
  onChange,
  valueKind,
  keyPattern,
  keyLabel = "Key",
  valueLabel = "Value",
  sortable = false,
}: {
  label: string;
  hint?: string;
  /** A table-level error -- "at least one entry is required", say -- not tied to any one row. */
  error?: string;
  entries: readonly KeyValueEntry[];
  onChange: (next: KeyValueEntry[]) => void;
  valueKind: KeyValueKind;
  keyPattern?: RegExp;
  keyLabel?: string;
  valueLabel?: string;
  sortable?: boolean;
}) {
  const base = useId();
  const [newKey, setNewKey] = useState("");
  const [jsonDrafts, setJsonDrafts] = useState<Record<number, string>>({});

  const duplicateKeys = new Set(
    entries.map((entry) => entry.key).filter((key, index, all) => all.indexOf(key) !== index),
  );

  function renameAt(index: number, key: string) {
    onChange(entries.map((entry, at) => (at === index ? { ...entry, key } : entry)));
  }
  function valueAt(index: number, value: JsonValue) {
    onChange(entries.map((entry, at) => (at === index ? { ...entry, value } : entry)));
  }
  function removeAt(index: number) {
    onChange(entries.filter((_, at) => at !== index));
    // Drafts are keyed by row index, so removing a row must also shift every
    // later draft down a slot -- otherwise the JSON textarea two rows below
    // the deleted one would silently start editing the wrong entry.
    setJsonDrafts((prev) => {
      const next: Record<number, string> = {};
      for (const [key, text] of Object.entries(prev)) {
        const at = Number(key);
        if (at < index) next[at] = text;
        else if (at > index) next[at - 1] = text;
      }
      return next;
    });
  }
  function moveAt(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= entries.length) return;
    const next = entries.slice();
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
    setJsonDrafts((prev) => {
      if (!(index in prev) && !(target in prev)) return prev;
      const swapped: Record<number, string> = {};
      for (const [key, text] of Object.entries(prev)) {
        const at = Number(key);
        if (at === index) swapped[target] = text;
        else if (at === target) swapped[index] = text;
        else swapped[at] = text;
      }
      return swapped;
    });
  }

  const newKeyTrimmed = newKey.trim();
  const newKeyTaken = entries.some((entry) => entry.key === newKeyTrimmed);
  const newKeyInvalid = newKeyTrimmed !== "" && keyPattern !== undefined && !keyPattern.test(newKeyTrimmed);

  function addRow() {
    if (newKeyTrimmed === "" || newKeyTaken || newKeyInvalid) return;
    onChange([...entries, { key: newKeyTrimmed, value: blankValue(valueKind) }]);
    setNewKey("");
  }

  return (
    <div className="flex flex-col gap-2">
      <div>
        <p className="premium-kicker">{label}</p>
        {hint !== undefined ? <p className="mt-0.5 text-[10px] text-outline">{hint}</p> : null}
        {error !== undefined ? (
          <p role="alert" className="mt-0.5 text-xs text-error">{error}</p>
        ) : null}
      </div>

      <table className="w-full border-separate border-spacing-y-1.5 text-sm">
        <thead>
          <tr>
            <th scope="col" className="w-1/3 px-2 text-left text-[10px] font-semibold uppercase tracking-wide text-outline">
              {keyLabel}
            </th>
            <th scope="col" className="px-2 text-left text-[10px] font-semibold uppercase tracking-wide text-outline">
              {valueLabel}
            </th>
            <th scope="col" className="sr-only">Row actions</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, index) => {
            const rowId = `${base}-${String(index)}`;
            const rowLabel = entry.key === "" ? `row ${String(index + 1)}` : entry.key;
            const keyInvalid =
              duplicateKeys.has(entry.key) || (keyPattern !== undefined && !keyPattern.test(entry.key));
            const keyErrorId = keyInvalid ? `${rowId}-key-error` : undefined;
            return (
              <tr key={rowId}>
                <td className="px-2 align-top">
                  <label htmlFor={`${rowId}-key`} className="sr-only">{`${keyLabel} ${String(index + 1)}`}</label>
                  <input
                    id={`${rowId}-key`}
                    value={entry.key}
                    onChange={(event) => { renameAt(index, event.target.value); }}
                    aria-invalid={keyInvalid ? true : undefined}
                    aria-describedby={keyErrorId}
                    className="premium-field w-full py-1.5 font-mono text-xs"
                  />
                  {keyInvalid ? (
                    <p id={keyErrorId} role="alert" className="mt-1 text-[10px] text-error">
                      {duplicateKeys.has(entry.key)
                        ? "This key is used more than once."
                        : "Does not match the required pattern."}
                    </p>
                  ) : null}
                </td>
                <td className="px-2 align-top">
                  <ValueControl
                    id={`${rowId}-value`}
                    label={`${valueLabel} for ${rowLabel}`}
                    kind={valueKind}
                    value={entry.value}
                    draft={jsonDrafts[index]}
                    onDraft={(text) => { setJsonDrafts((prev) => ({ ...prev, [index]: text })); }}
                    onChange={(value) => { valueAt(index, value); }}
                  />
                </td>
                <td className="align-top">
                  <div className="flex items-center gap-1 pt-1">
                    {sortable ? (
                      <>
                        <button
                          type="button"
                          aria-label={`Move ${rowLabel} up`}
                          disabled={index === 0}
                          onClick={() => { moveAt(index, -1); }}
                          className="flex size-6 items-center justify-center rounded text-outline transition hover:bg-surface-container-low hover:text-primary disabled:cursor-not-allowed disabled:opacity-30"
                        >
                          <ChevronUp size={12} aria-hidden="true" />
                        </button>
                        <button
                          type="button"
                          aria-label={`Move ${rowLabel} down`}
                          disabled={index === entries.length - 1}
                          onClick={() => { moveAt(index, 1); }}
                          className="flex size-6 items-center justify-center rounded text-outline transition hover:bg-surface-container-low hover:text-primary disabled:cursor-not-allowed disabled:opacity-30"
                        >
                          <ChevronDown size={12} aria-hidden="true" />
                        </button>
                      </>
                    ) : null}
                    <button
                      type="button"
                      aria-label={`Remove ${rowLabel}`}
                      onClick={() => { removeAt(index); }}
                      className="flex size-6 items-center justify-center rounded-md text-outline transition hover:bg-error-container hover:text-error"
                    >
                      <Minus size={12} aria-hidden="true" />
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2 rounded-lg border border-dashed border-outline-variant bg-surface-container-low/60 p-2">
          <label htmlFor={`${base}-new-key`} className="sr-only">{`New ${keyLabel.toLowerCase()}`}</label>
          <input
            id={`${base}-new-key`}
            value={newKey}
            onChange={(event) => { setNewKey(event.target.value); }}
            onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addRow(); } }}
            placeholder={`new_${keyLabel.toLowerCase()}`}
            aria-invalid={newKeyTaken || newKeyInvalid ? true : undefined}
            aria-describedby={newKeyTaken || newKeyInvalid ? `${base}-new-key-error` : undefined}
            className="premium-field min-w-0 flex-1 py-1.5 font-mono text-xs"
          />
          <button
            type="button"
            onClick={addRow}
            disabled={newKeyTrimmed === "" || newKeyTaken || newKeyInvalid}
            title={newKeyTaken ? "That key already exists" : newKeyInvalid ? "Does not match the required pattern" : undefined}
            className="flex items-center gap-1 rounded-lg border border-outline-control bg-surface-container-lowest px-2.5 py-2 text-[11px] font-medium text-on-surface-variant transition hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:border-outline-variant disabled:bg-surface-container-low disabled:hover:border-outline-variant disabled:hover:text-on-surface-variant"
          >
            <Plus size={12} aria-hidden="true" />
            {`Add ${keyLabel.toLowerCase()}`}
          </button>
        </div>
        {/*
          A5 (CFG-3b RV, carried to CFG-4): the new-key input was invalid --
          `aria-invalid`, a disabled Add button -- with no element the failure
          could point to. `title` on a disabled button is not reliably
          announced (some screen readers skip a disabled control's
          accessible-description sources entirely), so the reason now also
          exists as a real, `aria-describedby`-wired error paragraph, the same
          convention every row's own key error already uses.
        */}
        {newKeyTaken || newKeyInvalid ? (
          <p id={`${base}-new-key-error`} role="alert" className="text-[10px] text-error">
            {newKeyTaken ? "That key already exists." : "Does not match the required pattern."}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function ValueControl({
  id,
  label,
  kind,
  value,
  draft,
  onDraft,
  onChange,
}: {
  id: string;
  label: string;
  kind: KeyValueKind;
  value: JsonValue;
  draft: string | undefined;
  onDraft: (text: string) => void;
  onChange: (value: JsonValue) => void;
}) {
  if (kind === "boolean") {
    return (
      <input
        id={id}
        type="checkbox"
        aria-label={label}
        checked={value === true}
        onChange={(event) => { onChange(event.target.checked); }}
        className="size-4 accent-primary"
      />
    );
  }
  if (kind === "number") {
    return (
      <input
        id={id}
        type="number"
        aria-label={label}
        value={typeof value === "number" ? value : 0}
        onChange={(event) => {
          const parsed = Number(event.target.value);
          onChange(event.target.value === "" || Number.isNaN(parsed) ? 0 : parsed);
        }}
        className="premium-field w-32 py-1.5 tabular-nums text-xs"
      />
    );
  }
  if (kind === "json") {
    const text = draft ?? JSON.stringify(value, null, 2);
    let parseError: string | null = null;
    try {
      JSON.parse(text);
    } catch (caught) {
      parseError = caught instanceof Error ? caught.message : "That is not valid JSON.";
    }
    const errorId = parseError !== null ? `${id}-error` : undefined;
    return (
      <div className="flex flex-col gap-1">
        <textarea
          id={id}
          aria-label={label}
          aria-invalid={parseError !== null ? true : undefined}
          aria-describedby={errorId}
          value={text}
          spellCheck={false}
          onChange={(event) => {
            const next = event.target.value;
            onDraft(next);
            try {
              onChange(JSON.parse(next) as JsonValue);
            } catch {
              // Keep letting the operator finish typing valid JSON.
            }
          }}
          className="premium-field min-h-16 py-1.5 font-mono text-xs"
        />
        {parseError !== null ? (
          <p id={errorId} role="alert" className="text-[10px] text-error">{parseError}</p>
        ) : null}
      </div>
    );
  }
  return (
    <input
      id={id}
      type="text"
      aria-label={label}
      value={typeof value === "string" ? value : ""}
      onChange={(event) => { onChange(event.target.value); }}
      className="premium-field w-full py-1.5 text-xs"
    />
  );
}
