/**
 * The page-level list of validation errors a form did not manage to place
 * next to a field -- `DocumentEditor` uses this for a path the backend
 * reported that does not correspond to anything currently rendered, so
 * nothing is silently dropped. `onJump` is how a caller wires "click a row,
 * land on the field": this component only calls it with the path, since it
 * is the caller's document, not this one's, that knows where that field is.
 */

export type ValidationError = { path: string; message: string };

export function ValidationErrors({
  errors,
  onJump,
  title = "Validation errors",
}: {
  errors: readonly ValidationError[];
  onJump?: (path: string) => void;
  title?: string;
}) {
  if (errors.length === 0) return null;

  return (
    <section role="alert" aria-live="assertive" className="rounded-xl border border-error/20 bg-error-container p-3">
      <p className="text-sm font-semibold text-on-error-container">
        {title} ({errors.length})
      </p>
      <ul className="mt-2 flex flex-col gap-1">
        {errors.map((error) =>
          onJump !== undefined ? (
            <li key={error.path}>
              <button
                type="button"
                onClick={() => { onJump(error.path); }}
                className="flex w-full items-start gap-2 rounded-lg px-2 py-1 text-left text-xs text-on-error-container underline-offset-2 hover:underline"
              >
                <code className="shrink-0 font-mono">{error.path}</code>
                <span>{error.message}</span>
              </button>
            </li>
          ) : (
            <li key={error.path} className="flex items-start gap-2 px-2 py-1 text-xs text-on-error-container">
              <code className="shrink-0 font-mono">{error.path}</code>
              <span>{error.message}</span>
            </li>
          ),
        )}
      </ul>
    </section>
  );
}
