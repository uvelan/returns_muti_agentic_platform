/**
 * A bounded, read-only JSON viewer.
 *
 * Phase 19 requires data preview to be bounded and read-only. A configuration
 * snapshot is arbitrarily deep and can be large, so this truncates rather than
 * risking a multi-megabyte string pinned in the DOM, and says when it has.
 *
 * It renders whatever the server sent and masks nothing: `redact_secret_values`
 * already scrubbed secrets server-side, and non-secret references are
 * intentionally left legible so an operator can see which secret a binding
 * points at. Re-masking here would hide that on purpose-built data and imply a
 * boundary the browser does not provide.
 */

const MAX_CHARACTERS = 200_000;

export function JsonView({ value }: { value: unknown }) {
  if (value === undefined) {
    // `JSON.stringify(undefined)` returns undefined at runtime even though its
    // type says string, so this is handled before the call rather than with a
    // `??` the type checker considers unreachable.
    return <p className="text-sm text-slate-600">No value.</p>;
  }

  let text: string;
  try {
    text = JSON.stringify(value, null, 2);
  } catch {
    // Circular structures cannot round-trip; saying so beats an empty block.
    return <p className="text-sm text-red-700">This value could not be serialized.</p>;
  }

  const truncated = text.length > MAX_CHARACTERS;
  const shown = truncated ? `${text.slice(0, MAX_CHARACTERS)}\n...` : text;

  return (
    <div>
      <pre className="max-h-[32rem] overflow-auto rounded-md bg-slate-50 p-3 text-xs text-slate-800">
        {shown}
      </pre>
      {truncated ? (
        <p className="mt-1 text-xs text-slate-500">
          Truncated at {MAX_CHARACTERS.toLocaleString()} characters.
        </p>
      ) : null}
    </div>
  );
}
