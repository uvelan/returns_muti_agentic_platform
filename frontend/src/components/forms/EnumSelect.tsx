import { useId } from "react";

import { Field } from "./Field";

export type EnumOption = { value: string; label: string };

/**
 * A dropdown over the model's enum -- with one guarantee an ordinary
 * `<select>` does not give: the current value is always an option, even when
 * it is not one the model still lists. A release written before an enum
 * value was retired must still show what it holds rather than silently
 * snapping to the first option, which would change the document the moment
 * the operator opened it.
 *
 * `allowUnknown: false` marks that state as an error instead of accepting it
 * silently -- the value renders, but the screen says it is not one of the
 * allowed options.
 */
export function EnumSelect({
  label,
  hint,
  error,
  required,
  id: providedId,
  value,
  options,
  onChange,
  allowUnknown = true,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  id?: string;
  value: string;
  options: readonly EnumOption[];
  onChange: (next: string) => void;
  allowUnknown?: boolean;
}) {
  const autoId = useId();
  const id = providedId ?? autoId;
  const known = options.some((option) => option.value === value);
  const items: readonly EnumOption[] =
    known || value === "" ? options : [{ value, label: `${value} (not in the current schema)` }, ...options];
  const unknownError = !known && !allowUnknown ? `"${value}" is not one of the allowed options.` : undefined;

  return (
    <Field label={label} hint={hint} error={error ?? unknownError} required={required} htmlFor={id}>
      {(control) => (
        <select
          {...control}
          required={required}
          value={value}
          onChange={(event) => { onChange(event.target.value); }}
          className="premium-field text-sm"
        >
          {items.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      )}
    </Field>
  );
}
