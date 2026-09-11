import type { PublishStep } from "../../api/releasePublish";
import { PublishProgress } from "../PublishProgress";

/**
 * The sticky footer every typed config screen ends with: how many changes
 * are staged, an optional validate step, and Publish -- disabled with a
 * visible reason rather than a silent no-op when there is nothing to
 * publish or the operator lacks the capability.
 *
 * Rides `PublishProgress`, the same step indicator the JSON editors already
 * show, so a screen built from primitives looks like the platform's one
 * release lifecycle rather than inventing a second one.
 */
export function PublishBar({
  dirtyCount,
  onValidate,
  onPublish,
  steps = [],
  disabledReason,
  validating = false,
  publishing = false,
  error = null,
}: {
  dirtyCount: number;
  onValidate?: () => void;
  onPublish: () => void;
  steps?: readonly PublishStep[];
  disabledReason?: string;
  validating?: boolean;
  publishing?: boolean;
  error?: string | null;
}) {
  const publishDisabled = disabledReason !== undefined || dirtyCount === 0 || publishing;

  return (
    <div className="sticky bottom-0 z-10 flex flex-col gap-2 border-t border-outline-variant bg-surface-container-lowest/95 px-4 py-3 shadow-[0_-8px_24px_-16px_rgb(11_31_28_/_0.3)] backdrop-blur">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs text-on-surface-variant">
          {dirtyCount === 0
            ? "Nothing staged yet."
            : `${String(dirtyCount)} change${dirtyCount === 1 ? "" : "s"} staged.`}
        </span>
        {onValidate !== undefined ? (
          <button
            type="button"
            onClick={onValidate}
            disabled={dirtyCount === 0 || validating}
            className="rounded-lg border border-outline-control bg-surface-container-lowest px-3 py-2 text-xs font-medium text-on-surface-variant transition hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
          >
            {validating ? "Validating..." : "Validate"}
          </button>
        ) : null}
        <button
          type="button"
          onClick={onPublish}
          disabled={publishDisabled}
          title={disabledReason}
          className="ml-auto rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-on-primary shadow-sm transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {publishing ? "Publishing..." : "Publish"}
        </button>
        {disabledReason !== undefined ? (
          <span className="basis-full text-xs text-outline">{disabledReason}</span>
        ) : null}
      </div>
      <PublishProgress steps={steps} error={error} />
    </div>
  );
}
