import { useEffect, useState } from "react";
import { Clock, Radio } from "lucide-react";

import type { CasePanelView } from "../../../../api/casePanel";
import { COPILOT_TOKENS } from "../../copilotTokens";
import { humanizeRemaining } from "./humanizeRemaining";

/**
 * Where the case is, and how long the review has.
 *
 * **The countdown is the browser's** (contracts.md sect. 9, DR-10). The panel
 * carries `deadline_iso` -- an absolute instant -- and nothing else, because a
 * server-computed `seconds_remaining` is stale the moment it is serialized and,
 * worse, changes the body every second so no ETag ever matches. One field
 * would have cost every cached panel on the estate.
 *
 * So the tick happens here, once a second, against a fixed instant. If the tab
 * sleeps the clock is simply read again on wake; there is no drift to
 * accumulate because nothing is being counted, only subtracted.
 */

type Props = {
  readonly panel: CasePanelView;
};

/** Recomputed each tick from the instant. Not a decremented counter. */
function useRemaining(deadlineIso: string | null): number | null {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (deadlineIso === null) return;
    // A minute would be cheaper, but the last minute is the one that matters,
    // and a second's work here is one subtraction and one string.
    const handle = setInterval(() => {
      setNow(Date.now());
    }, 1_000);
    return () => {
      clearInterval(handle);
    };
  }, [deadlineIso]);

  if (deadlineIso === null) return null;
  const deadline = Date.parse(deadlineIso);
  return Number.isNaN(deadline) ? null : deadline - now;
}

export function StatusTimersSection({ panel }: Props) {
  const remaining = useRemaining(panel.timers.template_review_deadline_iso);
  const execution = panel.execution;

  return (
    <section aria-labelledby="case-panel-status" className="space-y-2">
      <h3 id="case-panel-status" className={COPILOT_TOKENS.typography.subheading}>
        Status
      </h3>

      {execution.status === "degraded" ? (
        <p className={COPILOT_TOKENS.typography.caption}>
          {/*
            Named rather than hidden behind a spinner. "We could not read the
            workflow" and "the workflow says nothing is happening" look
            identical on a screen that shows neither, and only one of them is a
            reason to call someone.
          */}
          The workflow could not be reached just now, so the status and the
          countdown are not shown. Everything else on this panel is current.
        </p>
      ) : (
        <dl className="space-y-1">
          <div className="flex items-center justify-between gap-3">
            <dt className={COPILOT_TOKENS.typography.caption}>Case</dt>
            <dd className={COPILOT_TOKENS.typography.body}>
              {execution.case_status ?? "Pending"}
            </dd>
          </div>
          {execution.parked_reason !== null ? (
            <div className="flex items-center justify-between gap-3">
              <dt className={COPILOT_TOKENS.typography.caption}>Parked</dt>
              <dd className={COPILOT_TOKENS.typography.body}>{execution.parked_reason}</dd>
            </div>
          ) : null}
        </dl>
      )}

      {remaining !== null ? (
        <p className="flex items-center gap-1.5 text-sm text-on-surface">
          <Clock aria-hidden="true" className="size-3.5 text-outline" />
          {/*
            `aria-live` is deliberately absent. A countdown that announced
            itself every second would make this pane unusable with a screen
            reader; the value is on the page and re-read on demand, which is
            what a person actually wants from a deadline.
          */}
          <span>{humanizeRemaining(remaining)}</span>
          <span className={COPILOT_TOKENS.typography.caption}>
            (until {new Date(panel.timers.template_review_deadline_iso ?? "").toLocaleString()})
          </span>
        </p>
      ) : null}

      {panel.timers.template_review_max_reminders > 0 ? (
        <p className="flex items-center gap-1.5">
          <Radio aria-hidden="true" className="size-3.5 text-outline" />
          <span className={COPILOT_TOKENS.typography.caption}>
            {panel.timers.template_review_reminders_sent === 0
              ? "No reminders sent yet"
              : `${String(panel.timers.template_review_reminders_sent)} of ${String(
                  panel.timers.template_review_max_reminders,
                )} reminders sent`}
          </span>
        </p>
      ) : null}

      {panel.return_records.length > 0 ? (
        <div>
          <p className={COPILOT_TOKENS.section.kicker}>RMAs on this case</p>
          <ul className="mt-1 space-y-0.5">
            {panel.return_records.map((record) => {
              // The projection is `dict[str, Any]` on the wire, deliberately:
              // it is the record shape, not the panel's, and typing it here
              // would put the record contract in a component.
              const held = record as Record<string, string | null | undefined>;
              return (
                <li key={String(held.return_record_id)} className={COPILOT_TOKENS.typography.body}>
                  {/* `Pending` is this domain's one word for "the platform
                      has not said". A screen inventing its own -- "not issued
                      yet", "unknown", "n/a" -- is how an empty case comes to
                      look like six different kinds of full one. */}
                  {held.return_reference ?? "Pending"}
                  {held.status != null ? ` · ${held.status}` : ""}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {panel.accepted_commands.length > 0 ? (
        <p className={COPILOT_TOKENS.typography.caption}>
          {/*
            This is what answers "I pressed Send and nothing happened": the
            command is durable, the workflow has not applied it yet, and the
            panel says so instead of showing an unchanged review and letting the
            associate press it again.
          */}
          {panel.accepted_commands.filter((command) => !command.applied).length > 0
            ? "An action you took is still being applied."
            : "Every action you took has been applied."}
        </p>
      ) : null}
    </section>
  );
}
