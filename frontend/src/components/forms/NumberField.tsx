import { useId } from "react";

import { Field } from "./Field";

/** `[0, 100]`, `≥ 0`, `≤ 100`, or `""` when the model carries no range. */
function rangeText(min: number | undefined, max: number | undefined): string | undefined {
  if (min !== undefined && max !== undefined) return `${String(min)} to ${String(max)}`;
  if (min !== undefined) return `${String(min)} or more`;
  if (max !== undefined) return `${String(max)} or less`;
  return undefined;
}

function clamp(value: number, min: number | undefined, max: number | undefined): number {
  let next = value;
  if (min !== undefined) next = Math.max(min, next);
  if (max !== undefined) next = Math.min(max, next);
  return next;
}

/**
 * A number, with the model's own range shown as a hint and enforced on blur
 * -- not on every keystroke, which would fight a value being typed one digit
 * at a time.
 */
export function NumberField({
  label,
  hint,
  error,
  required,
  id: providedId,
  value,
  onChange,
  min,
  max,
  step,
  unit,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  id?: string;
  value: number;
  onChange: (next: number) => void;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
}) {
  const autoId = useId();
  const id = providedId ?? autoId;
  const range = rangeText(min, max);
  const combinedHint = [hint, range !== undefined ? `Range: ${range}` : undefined]
    .filter((part): part is string => part !== undefined)
    .join(" · ");

  return (
    <Field
      label={label}
      hint={combinedHint === "" ? undefined : combinedHint}
      error={error}
      required={required}
      htmlFor={id}
    >
      {(control) => (
        <div className="flex items-center gap-2">
          <input
            {...control}
            type="number"
            required={required}
            min={min}
            max={max}
            step={step}
            value={value}
            onChange={(event) => {
              const parsed = Number(event.target.value);
              onChange(event.target.value === "" || Number.isNaN(parsed) ? 0 : parsed);
            }}
            onBlur={(event) => {
              const parsed = Number(event.target.value);
              if (event.target.value === "" || Number.isNaN(parsed)) return;
              const clamped = clamp(parsed, min, max);
              if (clamped !== parsed) onChange(clamped);
            }}
            className="premium-field w-32 tabular-nums"
          />
          {unit !== undefined ? (
            <span className="text-xs text-on-surface-variant">{unit}</span>
          ) : null}
        </div>
      )}
    </Field>
  );
}
