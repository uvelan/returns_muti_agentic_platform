import { useId } from "react";

import { Field } from "./Field";

/**
 * A source path, autocompleted from the active schema's known paths but not
 * limited to them -- `graph:`, `case_fact:` and `static:` bindings can name
 * a path the schema has not been re-synced to yet, and refusing that text
 * would make the field unable to express what the release already carries.
 * `<datalist>` gives the suggestions without narrowing what can be typed.
 */
export function PathPicker({
  label,
  hint,
  error,
  required,
  id: providedId,
  value,
  onChange,
  paths,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  id?: string;
  value: string;
  onChange: (next: string) => void;
  paths: readonly string[];
}) {
  const autoId = useId();
  const id = providedId ?? autoId;
  const listId = `${id}-paths`;

  return (
    <Field label={label} hint={hint} error={error} required={required} htmlFor={id}>
      {(control) => (
        <>
          <input
            {...control}
            type="text"
            list={listId}
            required={required}
            value={value}
            onChange={(event) => { onChange(event.target.value); }}
            className="premium-field w-full font-mono text-xs"
          />
          <datalist id={listId}>
            {paths.map((path) => <option key={path} value={path} />)}
          </datalist>
        </>
      )}
    </Field>
  );
}
