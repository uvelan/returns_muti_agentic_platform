import { useId, useState } from "react";
import { X } from "lucide-react";

import { Field } from "./Field";

/**
 * A string array edited as chips rather than a raw `["a", "b"]` the operator
 * has to punctuate correctly by hand.
 *
 * Each chip is one focusable control, not three (a text span plus two
 * reorder buttons plus a remove button would be four stops per tag to tab
 * through). Its accessible name says its position and every key it
 * responds to; Enter or a click removes it, Alt+ArrowLeft/Right reorders it
 * in place. New tags are added from the text field on Enter or a comma.
 */
export function TagListInput({
  label,
  hint,
  error,
  required,
  id: providedId,
  values,
  onChange,
  suggestions,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  id?: string;
  values: readonly string[];
  onChange: (next: string[]) => void;
  suggestions?: readonly string[];
}) {
  const autoId = useId();
  const id = providedId ?? autoId;
  const listId = suggestions !== undefined ? `${id}-suggestions` : undefined;
  const [draft, setDraft] = useState("");

  function addTag(raw: string) {
    const tag = raw.trim();
    if (tag === "" || values.includes(tag)) return;
    onChange([...values, tag]);
  }

  function removeAt(index: number) {
    onChange(values.filter((_, at) => at !== index));
  }

  function moveAt(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= values.length) return;
    const next = values.slice();
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }

  return (
    <Field label={label} hint={hint} error={error} required={required} htmlFor={id}>
      {(control) => (
        <div className="flex flex-col gap-2">
          {values.length > 0 ? (
            <ul aria-label={`${label} values`} className="flex flex-wrap gap-1.5">
              {values.map((tag, index) => (
                <li key={tag}>
                  <button
                    type="button"
                    onClick={() => { removeAt(index); }}
                    onKeyDown={(event) => {
                      if (event.altKey && event.key === "ArrowLeft") {
                        event.preventDefault();
                        moveAt(index, -1);
                      } else if (event.altKey && event.key === "ArrowRight") {
                        event.preventDefault();
                        moveAt(index, 1);
                      }
                    }}
                    aria-label={`${tag}. Position ${String(index + 1)} of ${String(values.length)}. Press Enter to remove, Alt+Left or Alt+Right to reorder.`}
                    className="flex items-center gap-1 rounded-full border border-outline-control bg-secondary-container px-2 py-1 text-xs text-on-secondary-container transition hover:border-error hover:text-error"
                  >
                    {tag}
                    <X size={11} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          <input
            {...control}
            type="text"
            list={listId}
            value={draft}
            onChange={(event) => { setDraft(event.target.value); }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addTag(draft);
                setDraft("");
              } else if (event.key === "," ) {
                event.preventDefault();
                addTag(draft);
                setDraft("");
              } else if (event.key === "Backspace" && draft === "" && values.length > 0) {
                removeAt(values.length - 1);
              }
            }}
            placeholder="Add a value and press Enter"
            className="premium-field text-sm"
          />
          {listId !== undefined ? (
            <datalist id={listId}>
              {suggestions?.map((suggestion) => <option key={suggestion} value={suggestion} />)}
            </datalist>
          ) : null}
        </div>
      )}
    </Field>
  );
}
