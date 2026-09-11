import { useId, useState } from "react";

import { Field } from "./Field";

const UNITS = [
  { key: "s", label: "sec", plural: "seconds", factor: 1 },
  { key: "min", label: "min", plural: "minutes", factor: 60 },
  { key: "h", label: "hr", plural: "hours", factor: 3600 },
  { key: "d", label: "day", plural: "days", factor: 86400 },
] as const;

type UnitKey = (typeof UNITS)[number]["key"];

function unitOf(key: UnitKey) {
  return UNITS.find((unit) => unit.key === key) ?? UNITS[0];
}

/** The largest whole unit that divides `seconds` exactly, so `7200` reads as `2 hr` rather than `120 min`. */
function bestUnit(seconds: number): UnitKey {
  for (const unit of [...UNITS].reverse()) {
    if (seconds !== 0 && seconds % unit.factor === 0) return unit.key;
  }
  return "s";
}

/** `"2 hours"`, for the hint -- the stored value is always seconds. */
function humanize(seconds: number): string {
  const unit = unitOf(bestUnit(seconds));
  const amount = seconds / unit.factor;
  return `${String(amount)} ${amount === 1 ? unit.plural.slice(0, -1) : unit.plural}`;
}

/**
 * A duration, edited as an amount and a unit but stored as seconds -- the
 * shape every wait and timeout in the platform's model already carries. The
 * unit is a display choice, not part of the value: switching it recomputes
 * the same number of seconds in the new unit rather than changing the value.
 */
export function DurationField({
  label,
  hint,
  error,
  required,
  id: providedId,
  seconds,
  onChange,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  id?: string;
  seconds: number;
  onChange: (next: number) => void;
}) {
  const autoId = useId();
  const id = providedId ?? autoId;
  const unitSelectId = `${id}-unit`;
  const [unitKey, setUnitKey] = useState<UnitKey>(() => bestUnit(seconds));
  const unit = unitOf(unitKey);
  const amount = seconds / unit.factor;
  const combinedHint = [hint, humanize(seconds)].filter((part): part is string => part !== undefined).join(" · ");

  return (
    <Field label={label} hint={combinedHint} error={error} required={required} htmlFor={id}>
      {(control) => (
        <div className="flex items-center gap-2">
          <input
            {...control}
            type="number"
            min={0}
            required={required}
            value={amount}
            onChange={(event) => {
              const parsed = Number(event.target.value);
              const next = event.target.value === "" || Number.isNaN(parsed) ? 0 : Math.max(0, parsed);
              onChange(next * unit.factor);
            }}
            className="premium-field w-24 tabular-nums"
          />
          <label htmlFor={unitSelectId} className="sr-only">{`${label} unit`}</label>
          <select
            id={unitSelectId}
            value={unitKey}
            onChange={(event) => { setUnitKey(event.target.value as UnitKey); }}
            className="premium-field w-24 text-xs"
          >
            {UNITS.map((option) => (
              <option key={option.key} value={option.key}>{option.label}</option>
            ))}
          </select>
        </div>
      )}
    </Field>
  );
}
