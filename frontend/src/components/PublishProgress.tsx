import type { PublishStep } from "../api/releasePublish";

/**
 * How a release publish run is going, drawn the same way wherever it runs.
 *
 * The four steps are the platform's one release lifecycle, so an operator who
 * has watched a provider change publish already knows how to read a template
 * change publishing.
 */
export function PublishProgress({
  steps,
  // Optional, because a caller whose surrounding editor already reports the
  // refusal and the success would otherwise have to pass `null`, `false` and
  // `""` to say "not mine to report".
  error = null,
  published = false,
  publishedNote = "",
}: {
  steps: readonly PublishStep[];
  error?: string | null;
  published?: boolean;
  publishedNote?: string;
}) {
  return (
    <>
      {steps.length > 0 ? (
        <ol className="flex flex-col gap-1 text-xs">
          {steps.map((step) => (
            <li key={step.name} className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className={`size-1.5 rounded-full ${
                  step.state === "DONE"
                    ? "bg-primary"
                    : step.state === "FAILED"
                      ? "bg-error"
                      : step.state === "RUNNING"
                        ? "bg-amber-500"
                        : "bg-outline-variant"
                }`}
              />
              <span className={step.state === "FAILED" ? "text-error" : "text-on-surface-variant"}>
                {step.name}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
      {error !== null ? (
        <p role="alert" className="text-sm text-error">{error}</p>
      ) : null}
      {published ? (
        <p role="status" className="text-sm text-primary">{publishedNote}</p>
      ) : null}
    </>
  );
}
