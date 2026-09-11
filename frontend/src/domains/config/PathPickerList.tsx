import { Minus, Plus } from "lucide-react";

import { PathPicker } from "../../components/forms/PathPicker";

/**
 * A `tuple[str, ...]` of source paths (`source_resolution.order_number_paths`
 * and its siblings) as a reorderless list of `PathPicker`s, each
 * autocompleted from the same known-paths list -- `PathPicker` itself edits
 * one path; the model field is a list of them, one source binding per
 * fallback in priority order (first match wins), so add/remove is the
 * operation this needs and reordering is not (unlike `OrderedList`'s
 * subjects, dropping one out of order does not change which one wins).
 */
export function PathPickerList({
  label,
  hint,
  values,
  onChange,
  paths,
}: {
  label: string;
  hint?: string;
  values: readonly string[];
  onChange: (next: string[]) => void;
  paths: readonly string[];
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <p className="premium-kicker">{label}</p>
      {hint !== undefined ? <p className="text-[10px] text-outline">{hint}</p> : null}
      <ul className="flex flex-col gap-1.5">
        {values.map((value, index) => (
          // Index as key: paths may repeat while being edited (two blank
          // rows, or a duplicate typed on the way to being fixed), so the
          // value itself is not a stable identity; the row's position is.
          <li key={index} className="flex items-end gap-2">
            <div className="min-w-0 flex-1">
              <PathPicker
                label={`${label} ${String(index + 1)}`}
                value={value}
                onChange={(next) => {
                  onChange(values.map((entry, at) => (at === index ? next : entry)));
                }}
                paths={paths}
              />
            </div>
            <button
              type="button"
              aria-label={`Remove ${label} ${String(index + 1)}`}
              onClick={() => { onChange(values.filter((_, at) => at !== index)); }}
              className="flex size-8 shrink-0 items-center justify-center rounded-md text-outline transition hover:bg-error-container hover:text-error"
            >
              <Minus size={13} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={() => { onChange([...values, ""]); }}
        className="flex w-fit items-center gap-1 rounded-lg border border-dashed border-outline-control bg-surface-container-low/60 px-2.5 py-1.5 text-[11px] font-medium text-on-surface-variant transition hover:border-primary hover:text-primary"
      >
        <Plus size={12} aria-hidden="true" />
        {`Add ${label.toLowerCase()}`}
      </button>
    </div>
  );
}
