import { useId } from "react";

import { Field } from "../../components/forms/Field";

/**
 * A plain string field, built on `Field` the same way `NumberField`/
 * `EnumSelect`/`PathPicker` are -- for the CFG-4 screens' many `NonBlank`
 * model fields that are neither an enum (`EnumSelect`) nor a known source
 * path (`PathPicker`): `default_method`, `field_id`, `ladder`, `strategy`,
 * and every other free string the backend validates as non-empty rather than
 * against a closed vocabulary. Not in `components/forms/` -- that directory
 * is this lease's carried-advisories-only area; this is local to the config
 * domain's own typed screens, the same way `TextField` would have been named
 * had CFG-3b built one.
 */
export function TextField({
  label,
  hint,
  error,
  required,
  id: providedId,
  value,
  onChange,
  mono = false,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  id?: string;
  value: string;
  onChange: (next: string) => void;
  mono?: boolean;
}) {
  const autoId = useId();
  const id = providedId ?? autoId;
  return (
    <Field label={label} hint={hint} error={error} required={required} htmlFor={id}>
      {(control) => (
        <input
          {...control}
          type="text"
          required={required}
          value={value}
          onChange={(event) => { onChange(event.target.value); }}
          className={`premium-field text-sm ${mono ? "font-mono text-xs" : ""}`}
        />
      )}
    </Field>
  );
}
