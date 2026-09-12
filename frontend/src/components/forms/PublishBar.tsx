import { useId } from "react";

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
  const autoId = useId();
  // A7 (CFG-3b RV, carried to CFG-4): `title` alone is not reliably announced
  // for a disabled control, and the visible reason text sat in a sibling
  // `<span>` with nothing pointing the button at it. Both now share one id.
  const disabledReasonId = disabledReason !== undefined ? `${autoId}-disabled-reason` : undefined;

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
            // RV round 1 (CFG-7), F1 (BLOCKING, carrying CFG-8 A2/CFG-5 H1):
            // `disabled:opacity-40` on `text-on-surface-variant` composited
            // to ~2:1 -- this button is disabled by default (`dirtyCount ===
            // 0`) on every typed screen's first paint, so this was the most
            // visible instance of the defect in the whole domain. Dimmed
            // through background/border instead; `disabled:hover:*`
            // re-asserts the resting colours (H1: differ by fill/border, not
            // only cursor).
            className="rounded-lg border border-outline-control bg-surface-container-lowest px-3 py-2 text-xs font-medium text-on-surface-variant transition hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:border-outline-variant disabled:bg-surface-container-low disabled:hover:border-outline-variant disabled:hover:text-on-surface-variant"
          >
            {validating ? "Validating..." : "Validate"}
          </button>
        ) : null}
        <button
          type="button"
          onClick={onPublish}
          disabled={publishDisabled}
          title={disabledReason}
          aria-describedby={disabledReasonId}
          // Same finding, the primary-filled shape: `disabled:opacity-40` on
          // `bg-primary`/`text-on-primary` faded the same way `Save`
          // (`AgentsSection.tsx`) used to. Matches that button's own fix --
          // non-text channels only, `border border-transparent` added so the
          // disabled border colour has a width to render at.
          className="ml-auto rounded-lg border border-transparent bg-primary px-4 py-2 text-xs font-semibold text-on-primary shadow-sm transition hover:brightness-105 disabled:cursor-not-allowed disabled:border-outline-variant disabled:bg-surface-container-low disabled:text-on-surface-variant disabled:shadow-none disabled:hover:brightness-100"
        >
          {publishing ? "Publishing..." : "Publish"}
        </button>
        {disabledReason !== undefined ? (
          <span id={disabledReasonId} className="basis-full text-xs text-outline">{disabledReason}</span>
        ) : null}
      </div>
      <PublishProgress steps={steps} error={error} />
    </div>
  );
}
