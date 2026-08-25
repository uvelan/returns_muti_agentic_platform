import { useEffect, useId, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";

import { APIError } from "../../api/client";
import {
  aiControlCenterApi,
  type AIUsageAttemptView,
  type InterceptionPoint,
  type InterceptionRequest,
  type InterceptionRow,
} from "../../api/aiControlCenter";
import { useCapabilities } from "../../hooks/capabilityContext";
import { type AI_SECTIONS, requireDomain } from "../registry";
import { useDomainSection } from "../useDomainSection";

/**
 * The AI Control Center (Phase 21).
 *
 * **Respond Manually now works.** D2 landed two operator routes -- unseal a held
 * request, and answer it -- so the manual response editor is real. Claim,
 * Generate Candidate and Release still have no route and are still named as
 * unavailable rather than rendered as buttons that would 404.
 *
 * **Both interception points share one queue.** A `REQUEST` hold is a call that
 * has not been made; a `RESPONSE` hold is a reply that has come back and is
 * waiting to be accepted, edited or rejected. Two queues would mean two screens
 * to remember to open, so the rows are tagged rather than split, and every
 * label -- the badge, the buttons, the reading of each status -- follows the
 * point. Backed by the same three routes for both, because the operator's three
 * actions are the same three actions.
 *
 * **Replay and Compare are wired (W4.12).** Both routes had shipped and neither
 * was reachable from here, so "was that a model problem or a prompt problem?"
 * had no answer short of a database query. Both mint new traces; the recorded
 * original is evidence and is never edited.
 *
 * **Payloads on demand, digests on the list.** An attempt row carries a
 * `requestDigest`/`responseDigest` rather than bodies, so the list renders
 * without hauling prompts. Opening a row fetches the full recorded trace --
 * the system prompt, the redacted input the model actually received, and the
 * response it delivered. `redactedInput` was redacted *before* storage and
 * `responseText` is the final answer, never hidden reasoning; the one place a
 * sealed, unredacted payload exists is the interception store, behind
 * `ai.interception.act`.
 */

const AI_DOMAIN = requireDomain("/ai");

/** Mirrors `AI_DOMAIN.sections`; the registry drives the sidebar, this drives the body. */
type Tab = (typeof AI_SECTIONS)[number];

/** Tabs with no backing route on `/api/ai`. Named, not silently dropped. */
const UNBACKED: Partial<Record<Tab, string>> = {
  Audit:
    "Every call's full record lives under Requests: open a row to read the system prompt, the redacted input the model received, and the response it delivered. A dedicated audit surface (export, retention, tamper evidence) has no endpoint on /api/ai.",
  Safety:
    "Per-request safety status appears under Requests. A dedicated safety surface (guard configuration, rejection history) has no endpoint on /api/ai.",
  Configuration:
    "AI configuration is served by /api/config. Wave D3 settled the release-lifecycle question -- the graph lifecycle is authoritative -- but no mutation surface is built on it yet, so this stays read-only for a different reason than it used to.",
};

export function AiControlCenterPage() {
  const { can } = useCapabilities();
  // The section comes from the URL and the sidebar sets it, so the screen
  // holds no navigation state of its own.
  const tab = useDomainSection(AI_DOMAIN) as Tab;

  if (!can("ai.request.read")) {
    return <p className="text-sm text-on-surface-variant">You do not have access to the AI Control Center.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h2 className="text-2xl font-semibold text-on-surface">AI Control Center</h2>
        <p className="mt-1 text-sm text-on-surface-variant">
          Requests, interceptions, metrics, routes, and safety.
        </p>
      </header>


      <TabBody tab={tab} canReadInterceptions={can("ai.interception.read")} />
    </div>
  );
}

function TabBody({ tab, canReadInterceptions }: { tab: Tab; canReadInterceptions: boolean }) {
  const unbacked = UNBACKED[tab];
  if (unbacked) return <p className="text-sm text-outline">{unbacked}</p>;

  switch (tab) {
    case "Overview":
    case "Metrics":
      return <MetricsTab />;
    case "Requests":
      return <RequestsTab />;
    case "Interceptions":
      return <InterceptionsTab canRead={canReadInterceptions} />;
    case "Providers & Models":
    case "Routes & Tasks":
      return <RoutesTab />;
    default:
      return null;
  }
}

function Stat({
  label,
  value,
  tone = "text-on-surface",
}: {
  label: string;
  value: number | string;
  /** Color for the value when the number itself is the signal -- a failure
      count that is red only when it is non-zero, a success rate that says how
      healthy it is before the percentage is read. */
  tone?: string;
}) {
  return (
    <div className="premium-panel p-4">
      <p className="premium-kicker">{label}</p>
      <p className={`mt-1.5 text-2xl font-semibold tabular-nums ${tone}`}>{value}</p>
    </div>
  );
}

function MetricsTab() {
  const summary = useQuery({
    queryKey: ["ai", "metrics", "summary"],
    queryFn: aiControlCenterApi.getSummary,
  });

  if (summary.isLoading) return <p className="text-sm text-outline">Loading...</p>;
  if (summary.error) return <p className="text-sm text-error">{summary.error.message}</p>;
  if (!summary.data) return null;

  const s = summary.data;
  const successRate = s.attempts > 0 ? Math.round((s.successes / s.attempts) * 100) : 0;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <Stat label="Attempts" value={s.attempts} />
        <Stat
          label="Success rate"
          value={`${String(successRate)}%`}
          tone={
            s.attempts === 0
              ? "text-on-surface"
              : successRate >= 90
                ? "text-primary"
                : successRate >= 50
                  ? "text-amber-700"
                  : "text-error"
          }
        />
        <Stat label="Failures" value={s.failures} tone={s.failures > 0 ? "text-error" : "text-on-surface"} />
        <Stat label="Fallbacks" value={s.fallbacks} tone={s.fallbacks > 0 ? "text-amber-700" : "text-on-surface"} />
        <Stat
          label="Blocked by safety"
          value={s.blockedBySafety}
          tone={s.blockedBySafety > 0 ? "text-error" : "text-on-surface"}
        />
        <Stat label="Total tokens" value={s.totalTokens.toLocaleString()} />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Breakdown title="By provider" data={s.byProvider} />
        <Breakdown title="By model" data={s.byModel} />
        <Breakdown title="By task" data={s.byTask} />
        <Breakdown title="By tier" data={s.byTier} />
      </div>

      <p className="text-xs text-outline">
        Estimated cost: {(s.estimatedCostMicros / 1_000_000).toFixed(4)}{" "}
        {s.pricingCurrency ?? "(currency unknown)"}.
        {s.unpricedAttempts > 0 && (
          // Named, not hidden. The total covers only the attempts the active
          // release holds a price for, and a figure that quietly excludes a
          // provider is worse than one that says how much it is missing.
          <>
            {" "}
            {s.unpricedAttempts} attempt
            {s.unpricedAttempts === 1 ? " is" : "s are"} not included: no price in the active
            configuration release.
          </>
        )}
      </p>
    </div>
  );
}

function Breakdown({ title, data }: { title: string; data: Readonly<Record<string, number>> }) {
  const rows = Object.entries(data).sort(([, a], [, b]) => b - a);
  const total = rows.reduce((sum, [, count]) => sum + count, 0);
  return (
    <section className="premium-panel p-4">
      <h2 className="text-sm font-semibold text-on-surface">{title}</h2>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-on-surface-variant">No data.</p>
      ) : (
        <ul className="mt-3 flex flex-col gap-2.5">
          {rows.map(([key, count]) => (
            <li key={key} className="flex flex-col gap-1 text-sm">
              <span className="flex items-baseline justify-between gap-3">
                <span className="truncate text-on-surface-variant" title={key}>{key}</span>
                <span className="font-medium tabular-nums text-on-surface">{count}</span>
              </span>
              {/* The share, drawn. Four lists of counts is a table; the bar is
                  what lets "MANUAL took three quarters of the traffic" be seen
                  before it is computed. Width from data, never below 2% so a
                  1-of-58 row still visibly exists. */}
              <span aria-hidden="true" className="h-1 overflow-hidden rounded-full bg-surface-container">
                <span
                  className="block h-full rounded-full bg-primary/60"
                  style={{ width: `${String(Math.max(2, total === 0 ? 0 : (count / total) * 100))}%` }}
                />
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RequestsTab() {
  const [selected, setSelected] = useState<AIUsageAttemptView | null>(null);
  const attempts = useQuery({
    queryKey: ["ai", "metrics", "attempts"],
    queryFn: aiControlCenterApi.listAttempts,
  });

  if (attempts.isLoading) return <p className="text-sm text-outline">Loading...</p>;
  if (attempts.error) return <p className="text-sm text-error">{attempts.error.message}</p>;

  const rows = attempts.data ?? [];

  return (
    <div className="flex flex-col gap-3">
      <div className="premium-panel overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-outline-variant/70">
              <th className="premium-kicker px-4 py-3 font-semibold">Task</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Provider / model</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Status</th>
              <th className="premium-kicker px-4 py-3 text-right font-semibold">Latency</th>
              <th className="premium-kicker px-4 py-3 text-right font-semibold">Tokens</th>
              <th className="premium-kicker px-4 py-3 text-right font-semibold">When</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-on-surface-variant">
                  No requests recorded yet. The first model call this process makes will
                  appear here.
                </td>
              </tr>
            ) : null}
            {rows.map((attempt) => (
              <tr
                key={attempt.id}
                onClick={() => { setSelected(attempt); }}
                className="group cursor-pointer border-t border-outline-variant/50 transition-colors hover:bg-surface-container-low"
              >
                <td className="px-4 py-2.5">
                  {/*
                    A real control, not a bare cell -- the same correction
                    `ConfigurationPage` already made for its release rows.
                    Opening a request is the only way to reach the payloads,
                    and a `<tr>` is not focusable and carries no role, so
                    without this button the table would be mouse-only. The row
                    handler stays for click-anywhere.
                  */}
                  <button
                    type="button"
                    onClick={() => { setSelected(attempt); }}
                    className="w-full text-left font-medium text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary group-hover:text-primary"
                  >
                    {attempt.taskId}
                  </button>
                </td>
                <td className="px-4 py-2.5">
                  <span className="text-on-surface">{attempt.provider ?? "-"}</span>
                  <span className="block text-xs text-on-surface-variant">
                    {attempt.model ?? "-"}
                  </span>
                </td>
                <td className="px-4 py-2.5">
                  <StatusPill status={attempt.status} fallbackUsed={attempt.fallbackUsed} />
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-on-surface-variant">
                  {attempt.latencyMs} ms
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-on-surface-variant">
                  {attempt.totalTokens}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-xs text-on-surface-variant">
                  {formatWhen(attempt.createdAt)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-outline">
        Open a request to read the full record: the system prompt, the redacted input the
        model received, and the response it delivered.
      </p>

      {selected !== null ? (
        <RequestDetailDialog
          attempt={selected}
          onClose={() => { setSelected(null); }}
        />
      ) : null}
    </div>
  );
}

/**
 * Status, readable at a glance. The raw enum stays visible -- an operator
 * greps logs with it -- but the color answers "is this fine" before the word
 * does. Fallback is a separate marker rather than folded into the status,
 * because a fallback that succeeded and a clean success are different facts.
 */
function StatusPill({ status, fallbackUsed }: { status: string; fallbackUsed: boolean }) {
  const upper = status.toUpperCase();
  const tone = upper.includes("FAIL") || upper.includes("BLOCKED")
    ? "bg-error-container/60 text-on-error-container"
    : upper.includes("SUCCESS") || upper.includes("VALIDATED") || upper.includes("PERSISTED")
      ? "bg-primary-container/15 text-primary"
      : "bg-surface-container text-on-surface-variant";
  const dot = upper.includes("FAIL") || upper.includes("BLOCKED")
    ? "bg-error"
    : upper.includes("SUCCESS") || upper.includes("VALIDATED") || upper.includes("PERSISTED")
      ? "bg-primary"
      : "bg-outline";
  return (
    <span className="inline-flex items-center gap-2">
      <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${tone}`}>
        <span aria-hidden="true" className={`size-1.5 rounded-full ${dot}`} />
        {status}
      </span>
      {fallbackUsed ? (
        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
          fallback
        </span>
      ) : null}
    </span>
  );
}

/** Compact clock time for today, date plus time for anything older. */
function formatWhen(iso: string): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return iso;
  const now = new Date();
  const sameDay =
    then.getFullYear() === now.getFullYear() &&
    then.getMonth() === now.getMonth() &&
    then.getDate() === now.getDate();
  return sameDay
    ? then.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : then.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/**
 * Everything the platform recorded about one request, in one place.
 *
 * The attempt row is the summary; this dialog fetches the full trace on open --
 * the metrics list deliberately carries digests, so the payloads travel only
 * when somebody asks for exactly one. A modal rather than the old side panel
 * because the payloads need width and protected reading focus: a system prompt
 * squeezed into 22rem beside a live table was the reason nobody could read one.
 */
function RequestDetailDialog({
  attempt,
  onClose,
}: {
  attempt: AIUsageAttemptView;
  onClose: () => void;
}) {
  const titleId = useId();
  const trace = useQuery({
    queryKey: ["ai", "trace", attempt.traceId],
    queryFn: () => aiControlCenterApi.getRequest(attempt.traceId),
  });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); };
  }, [onClose]);

  const detail = trace.data;

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(event) => { event.stopPropagation(); }}
        className="premium-panel flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden"
      >
        <header className="flex items-start justify-between gap-4 border-b border-outline-variant/70 px-6 py-4">
          <div>
            <h2 id={titleId} className="text-base font-semibold text-on-surface">
              {attempt.taskId}
            </h2>
            <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-on-surface-variant">
              <StatusPill status={attempt.status} fallbackUsed={attempt.fallbackUsed} />
              <span>{attempt.provider ?? "-"} / {attempt.model ?? "-"}</span>
              <span className="tabular-nums">{attempt.latencyMs} ms</span>
              <span className="tabular-nums">{formatWhen(attempt.createdAt)}</span>
            </p>
          </div>
          <button
            type="button"
            // The first focus lands here so Escape and Enter both close without
            // a tab stop hunt; the payload <pre>s below are in the tab order.
            autoFocus
            onClick={onClose}
            aria-label="Close request details"
            className="rounded-lg p-1.5 text-outline transition-colors hover:bg-surface-container-low hover:text-on-surface"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="flex flex-col gap-5 overflow-y-auto px-6 py-5">
          {attempt.errorCode || attempt.fallbackReason ? (
            <div className="rounded-xl border border-error-container bg-error-container/25 px-4 py-3 text-sm">
              {attempt.errorCode ? (
                <p className="font-medium text-on-error-container">{attempt.errorCode}</p>
              ) : null}
              {attempt.fallbackReason ? (
                <p className="mt-0.5 text-on-error-container/90">{attempt.fallbackReason}</p>
              ) : null}
            </div>
          ) : null}

          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm md:grid-cols-3">
            <Field label="Trace" value={attempt.traceId} mono />
            <Field label="Attempt" value={String(attempt.attemptNumber)} />
            <Field label="Selection reason" value={attempt.selectionReason} />
            <Field label="Route" value={attempt.routeId ?? "-"} mono />
            <Field
              label="Tier"
              value={
                attempt.selectedTier && attempt.selectedTier !== attempt.configuredTier
                  ? `${attempt.configuredTier} -> ${attempt.selectedTier}`
                  : attempt.configuredTier
              }
            />
            <Field label="Safety" value={attempt.safetyStatus} />
            <Field
              label="Tokens in / out"
              value={`${String(attempt.inputTokens)} / ${String(attempt.outputTokens)}`}
            />
            <Field label="Rate-limit wait" value={`${String(attempt.rateLimitWaitMs)} ms`} />
            <Field label="Cost" value={formatCost(attempt)} />
            {/* W4.12: the business dimension. Ids only -- no customer data
                reaches this surface, by design of the record itself. */}
            <Field label="Correlation" value={attempt.correlationId ?? "-"} mono />
            <Field label="Case" value={attempt.caseId ?? "-"} mono />
            <Field label="Prompt version" value={attempt.promptVersion ?? "-"} />
          </dl>

          {trace.isLoading ? (
            <p className="text-sm text-outline">Loading the recorded payloads...</p>
          ) : null}
          {trace.error ? (
            trace.error instanceof APIError && trace.error.status === 404 ? (
              // Not every attempt has a stored body, and that is a design, not
              // a gap: the Order Agent's dispatch path records digests only,
              // because its payloads carry customer rows and the telemetry row
              // is not allowed to hold them. Saying so beats a bare 404 --
              // "refuse rather than guess" includes refusing to look broken.
              <div className="flex flex-col gap-3 rounded-xl bg-surface-container-low px-4 py-3">
                <p className="text-sm text-on-surface-variant">
                  No payload record exists for this attempt. This task&apos;s dispatch path
                  stores only digests -- its payloads carry customer data and are
                  deliberately not persisted in the clear. A request held for a human is
                  readable, sealed, under Interceptions.
                </p>
                <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm md:grid-cols-2">
                  <Field label="Request digest" value={attempt.requestDigest} mono />
                  <Field label="Response digest" value={attempt.responseDigest ?? "-"} mono />
                </dl>
              </div>
            ) : (
              <p role="alert" className="text-sm text-error">{trace.error.message}</p>
            )
          ) : null}

          {detail ? (
            <>
              <section className="flex flex-col gap-2">
                <h3 className="premium-kicker">Request</h3>
                <Payload label="System prompt" text={detail.systemPrompt} />
                <Payload
                  label="Redacted input (what the model received)"
                  text={JSON.stringify(detail.redactedInput, null, 2)}
                />
              </section>

              <section className="flex flex-col gap-2">
                <h3 className="premium-kicker">Response</h3>
                {detail.responseText !== null && detail.responseText !== "" ? (
                  <Payload label="Response text" text={detail.responseText} />
                ) : (
                  <p className="rounded-xl bg-surface-container-low px-4 py-3 text-sm text-on-surface-variant">
                    No response was recorded
                    {detail.errorCode ? ` -- the attempt failed with ${detail.errorCode}` : ""}.
                  </p>
                )}
                {detail.decision !== null || detail.explanation !== null ? (
                  <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm md:grid-cols-3">
                    <Field label="Decision" value={detail.decision ?? "-"} />
                    <Field
                      label="Confidence"
                      value={
                        detail.confidenceMillionths === null
                          ? "-"
                          : `${(detail.confidenceMillionths / 10_000).toFixed(1)}%`
                      }
                    />
                    {detail.explanation !== null && detail.explanation !== detail.responseText ? (
                      <div className="col-span-2 md:col-span-3">
                        <Field label="Explanation" value={detail.explanation} />
                      </div>
                    ) : null}
                  </dl>
                ) : null}
              </section>

              <dl className="grid grid-cols-1 gap-x-6 gap-y-3 border-t border-outline-variant/70 pt-4 text-sm md:grid-cols-2">
                <Field label="Request digest" value={detail.requestDigest} mono />
                <Field label="Response digest" value={detail.responseDigest ?? "-"} mono />
              </dl>
            </>
          ) : null}

          <div className="border-t border-outline-variant/70 pt-1">
            <ReplayControls traceId={attempt.traceId} />
          </div>
        </div>
      </section>
    </div>
  );
}

/**
 * One recorded body, scrollable and reachable by keyboard. Focusable because it
 * scrolls and holds nothing focusable -- the same WCAG 2.1.1 correction the
 * interception payload view already carries.
 */
function Payload({ label, text }: { label: string; text: string }) {
  return (
    <div className="flex flex-col gap-1">
      <p className="text-xs text-on-surface-variant">{label}</p>
      <pre
        tabIndex={0}
        aria-label={label}
        className="max-h-72 overflow-auto whitespace-pre-wrap rounded-xl bg-surface-container-low px-4 py-3 font-mono text-xs leading-relaxed text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
      >
        {text}
      </pre>
    </div>
  );
}

/**
 * `pricingStatus` is why this is not a division.
 *
 * An unpriced attempt has `estimatedCostMicros === null`, and rendering it as
 * "0.0000 USD" would put the exact defect W4.11 removed from the backend back
 * into the screen the backend feeds.
 */
function formatCost(attempt: AIUsageAttemptView): string {
  if (attempt.pricingStatus !== "PRICED" || attempt.estimatedCostMicros === null) {
    return "unknown -- no price in the active release";
  }
  return `${(attempt.estimatedCostMicros / 1_000_000).toFixed(6)} ${attempt.pricingCurrency ?? ""}`.trim();
}

/**
 * Replay and compare, wired into S8.
 *
 * Both routes shipped and neither was reachable, so the first question anyone
 * asks about a bad answer -- "is it the model or the prompt?" -- could only be
 * answered from a database. Both produce *new* traces; the original is evidence
 * and is never modified.
 *
 * Gated on `ai.replay.read` -- the capability the backend routes require. It is
 * named `.read` and grants a provider call, which is a naming wart the platform
 * already has; matching it is better than inventing a second answer here.
 */
function ReplayControls({ traceId }: { traceId: string }) {
  const { can } = useCapabilities();
  const [provider, setProvider] = useState("");
  const replay = useMutation({
    mutationFn: () => aiControlCenterApi.replayRequest(traceId, provider || undefined),
  });
  const compare = useMutation({
    mutationFn: () => aiControlCenterApi.compareRequest(traceId, COMPARE_PROVIDERS),
  });

  if (!can("ai.replay.read")) return null;

  return (
    <div className="mt-3 flex flex-col gap-2 border-t border-outline-variant pt-3">
      <label className="text-xs uppercase tracking-wide text-outline" htmlFor="replay-provider">
        Replay provider
      </label>
      <select
        id="replay-provider"
        className="rounded border border-outline-control p-1 text-sm"
        value={provider}
        onChange={(event) => { setProvider(event.target.value); }}
      >
        <option value="">Same routing as recorded</option>
        {COMPARE_PROVIDERS.map((name) => (
          <option key={name} value={name}>{name}</option>
        ))}
      </select>
      <div className="flex gap-2">
        <button
          type="button"
          className="rounded bg-primary px-2 py-1 text-sm text-white disabled:opacity-50"
          disabled={replay.isPending}
          onClick={() => { replay.mutate(); }}
        >
          Replay
        </button>
        <button
          type="button"
          className="rounded border border-outline-control px-2 py-1 text-sm disabled:opacity-50"
          disabled={compare.isPending}
          onClick={() => { compare.mutate(); }}
        >
          Compare providers
        </button>
      </div>
      {replay.error ? <p className="text-xs text-error">{replay.error.message}</p> : null}
      {compare.error ? <p className="text-xs text-error">{compare.error.message}</p> : null}
      {replay.data ? (
        <p className="text-xs text-on-surface-variant">
          Replayed as trace <span className="font-mono">{replay.data.id}</span>:{" "}
          {replay.data.provider ?? "-"} / {replay.data.model ?? "-"} -&gt;{" "}
          {replay.data.decision ?? replay.data.status}
        </p>
      ) : null}
      {compare.data ? (
        <ul className="flex flex-col gap-1 text-xs text-on-surface-variant">
          {compare.data.map((trace) => (
            <li key={trace.id}>
              {trace.provider ?? "-"} / {trace.model ?? "-"} -&gt;{" "}
              {trace.decision ?? trace.status}
              {trace.errorCode ? ` (${trace.errorCode})` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * Deliberately excludes SIMULATOR: the backend refuses it in production, and
 * offering an option that 422s in the only environment that matters is worse
 * than not offering it.
 */
const COMPARE_PROVIDERS = ["GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC"] as const;

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-outline">{label}</dt>
      <dd className={mono ? "break-all font-mono text-xs text-on-surface" : "text-on-surface"}>
        {value}
      </dd>
    </div>
  );
}

/**
 * The two hold points, as an operator reads them.
 *
 * The backend deliberately reuses one set of statuses for both points -- one
 * state machine rather than two wearing one enum -- so the *labels* are where
 * the difference has to appear. `ANSWERED` on a request means a human wrote the
 * answer; on a response it means a human rewrote the model's. Showing the raw
 * status for both would make an edit indistinguishable from a substitution on
 * the one screen where that distinction is the entire point.
 */
const POINT_LABELS: Record<
  InterceptionPoint,
  {
    readonly badge: string;
    readonly open: string;
    readonly accept: string;
    readonly reject: string;
    readonly submit: string;
    readonly outcomes: Readonly<Record<string, string>>;
  }
> = {
  REQUEST: {
    badge: "Request",
    open: "Respond manually",
    accept: "Allow model",
    reject: "Cancel",
    submit: "Submit answer",
    outcomes: { ANSWERED: "Answered by human", ALLOWED: "Allowed", CANCELLED: "Cancelled" },
  },
  RESPONSE: {
    badge: "Response",
    open: "Review response",
    accept: "Accept unchanged",
    reject: "Reject",
    submit: "Submit edit",
    outcomes: { ANSWERED: "Edited by human", ALLOWED: "Accepted", CANCELLED: "Rejected" },
  },
};

function labelsFor(point: InterceptionPoint | undefined) {
  return POINT_LABELS[point === "RESPONSE" ? "RESPONSE" : "REQUEST"];
}

function InterceptionsTab({ canRead }: { canRead: boolean }) {
  const { can } = useCapabilities();
  const canAct = can("ai.interception.act");
  const [open, setOpen] = useState<InterceptionRow | null>(null);
  // Ticked on the same cadence the queue is polled, so a row that lapses
  // between fetches stops offering actions at roughly the moment it stops being
  // answerable rather than whenever the page next happens to re-render.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = setInterval(() => {
      setNow(Date.now());
    }, 15_000);
    return () => {
      clearInterval(tick);
    };
  }, []);
  // Polled, because a held request expires on a clock and this screen used to
  // fetch once per mount and then freeze. `staleTime` is 30s and
  // `refetchOnWindowFocus` is off, so a queue opened at 05:12 still offered
  // Respond, Allow and Cancel on a row that lapsed at 05:13 -- and pressing
  // them returned 409 or 404. The audit recorded that as "expired rows are
  // never reaped"; the reaper was fine, the page was stale.
  const interceptions = useQuery({
    queryKey: ["ai", "interceptions"],
    queryFn: () => aiControlCenterApi.listInterceptions(),
    enabled: canRead,
    refetchInterval: 15_000,
  });
  // Terminal records, for the counts. A separate query so the operator queue
  // keeps its own meaning: this one is history and is never actionable.
  const history = useQuery({
    queryKey: ["ai", "interceptions", "history"],
    queryFn: () =>
      aiControlCenterApi.listInterceptions(["ANSWERED", "ALLOWED", "CANCELLED", "EXPIRED"]),
    enabled: canRead,
    refetchInterval: 60_000,
  });
  const cancel = useMutation({
    mutationFn: (interceptionId: string) => aiControlCenterApi.cancelInterception(interceptionId),
    onSuccess: async () => {
      await interceptions.refetch();
    },
  });
  const allow = useMutation({
    mutationFn: (interceptionId: string) => aiControlCenterApi.allowInterception(interceptionId),
    onSuccess: async () => {
      await interceptions.refetch();
    },
  });

  if (!canRead) {
    return (
      <p className="text-sm text-on-surface-variant">
        Viewing interceptions requires ai.interception.read.
      </p>
    );
  }
  if (interceptions.isLoading) return <p className="text-sm text-outline">Loading...</p>;
  if (interceptions.error) {
    return <p className="text-sm text-error">{interceptions.error.message}</p>;
  }

  const rows: readonly InterceptionRow[] = interceptions.data ?? [];
  // Counted across both queries. The pending queue alone can only ever answer
  // "how many are pending", which is why three of these tiles read zero in
  // every deployment.
  const counted: readonly InterceptionRow[] = [...rows, ...(history.data ?? [])];
  const byStatus = (status: string) =>
    counted.filter((row) => row.status.toUpperCase() === status).length;
  // A row whose deadline has passed is not actionable, whatever its stored
  // status says. The sweep settles it on an interval and this renders on a
  // clock, so the two disagree for as long as that interval is -- and the
  // buttons were gated on status alone.
  //
  // Compared against `now` from state rather than `Date.now()` inline: reading
  // the clock during render is impure, and a value that changes without a
  // render would leave a lapsed row still offering buttons until something else
  // happened to re-render the page.
  const hasLapsed = (row: InterceptionRow) => new Date(row.expiresAt).getTime() <= now;
  const byPoint = (point: InterceptionPoint) =>
    rows.filter((row) => labelsFor(row.point) === POINT_LABELS[point]).length;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        {/* The five statuses `InterceptionStatus` actually has. This counted
            CLAIMED and RESPONDED, which the backend never emits, and then
            counted four real ones against a pending-only list -- so three still
            read zero in every deployment, which is indistinguishable from a
            quiet queue. `ALLOWED` had no tile at all, so allowing a request
            removed it from the operator's world entirely. */}
        <Stat label="Pending" value={byStatus("PENDING")} />
        <Stat label="Answered" value={byStatus("ANSWERED")} />
        <Stat label="Allowed" value={byStatus("ALLOWED")} />
        <Stat label="Cancelled" value={byStatus("CANCELLED")} />
        <Stat label="Expired" value={byStatus("EXPIRED")} />
        {/* One queue, two jobs. The split matters operationally: a held request
            is waiting on somebody to write an answer, a held response is
            waiting on somebody to read one, and the second kind is blocking a
            live coroutine while it waits. */}
        <Stat label="Held requests" value={byPoint("REQUEST")} />
        <Stat label="Held responses" value={byPoint("RESPONSE")} />
      </div>

      <div className="premium-panel overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-outline-variant/70">
              <th className="premium-kicker px-4 py-3 font-semibold">Interception</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Point</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Status</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Task</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Expires</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Actioned by</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-on-surface-variant">
                  No interceptions. Nothing is waiting on a human right now.
                </td>
              </tr>
            ) : null}
            {rows.map((row) => {
              const labels = labelsFor(row.point);
              const status = row.status.toUpperCase();
              return (
              <tr key={row.interceptionId} className="border-t border-outline-variant/50 transition-colors hover:bg-surface-container-low/60">
                <td className="px-4 py-2.5 font-mono text-xs">{row.interceptionId}</td>
                <td className="px-4 py-2.5">
                  <span
                    className={
                      labels === POINT_LABELS.RESPONSE
                        ? "rounded bg-violet-100 px-2 py-0.5 text-xs font-medium text-violet-800"
                        : "rounded bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-800"
                    }
                  >
                    {labels.badge}
                  </span>
                </td>
                {/* The raw status, plus what it means at this point. An
                    `ANSWERED` request is a substitution and an `ANSWERED`
                    response is an edit, and the queue is where somebody has to
                    be able to tell those apart. */}
                <td className="px-4 py-2.5">
                  {row.status}
                  {labels.outcomes[status] ? (
                    <span className="ml-1 text-xs text-outline">
                      ({labels.outcomes[status]})
                    </span>
                  ) : null}
                </td>
                <td className="px-4 py-2.5">{row.taskId}</td>
                <td className="px-4 py-2.5">{row.expiresAt}</td>
                {/* A human answer must never read as a model's. `answeredBy` is
                    the operator's own subject, recorded by the backend. */}
                <td className="px-4 py-2.5">{row.answeredBy ?? "-"}</td>
                <td className="px-4 py-2.5">
                  {status === "PENDING" && canAct && !hasLapsed(row) ? (
                    <div className="flex gap-1">
                      <button
                        type="button"
                        className="rounded border border-outline-control px-2 py-1 text-xs hover:bg-surface-container-low"
                        onClick={() => {
                          setOpen(row);
                        }}
                      >
                        {labels.open}
                      </button>
                      <button
                        type="button"
                        disabled={allow.isPending}
                        className="rounded border border-outline-control px-2 py-1 text-xs hover:bg-surface-container-low disabled:opacity-40"
                        onClick={() => {
                          allow.mutate(row.interceptionId);
                        }}
                      >
                        {labels.accept}
                      </button>
                      <button
                        type="button"
                        disabled={cancel.isPending}
                        className="rounded border border-outline-control px-2 py-1 text-xs hover:bg-surface-container-low disabled:opacity-40"
                        onClick={() => {
                          cancel.mutate(row.interceptionId);
                        }}
                      >
                        {labels.reject}
                      </button>
                    </div>
                  ) : null}
                </td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {open !== null ? (
        <ManualResponder
          key={open.interceptionId}
          row={open}
          onClose={() => {
            setOpen(null);
          }}
          onAnswered={() => {
            setOpen(null);
            void interceptions.refetch();
          }}
        />
      ) : null}

      {cancel.error ? (
        <p role="alert" className="text-sm text-error">
          {cancel.error.message}
        </p>
      ) : null}
      {allow.error ? (
        <p role="alert" className="text-sm text-error">
          {allow.error.message}
        </p>
      ) : null}

      <p className="text-sm text-outline">
        All three actions go through the operator API. A held <strong>request</strong>{" "}
        transitions and the resume bridge signals the waiting workflow separately, so the
        queue may show <code>ANSWERED</code> a moment before the work resumes. A held{" "}
        <strong>response</strong> has a caller blocked on it right now: the moment you
        decide, that caller continues. An edit is not trusted more than the model&apos;s
        own reply -- it goes through the same schema parse and the same output safety
        check, and a malformed edit fails validation exactly as a malformed completion
        does.
      </p>
      <p className="text-sm text-outline">
        Claim, Generate Candidate, Replay and Release are still not offered.{" "}
        <code>/api/ai</code> has no route for them, and two of the four are hard to justify
        at all: an interception exists <em>because</em> the model could not be called, so
        generating a candidate answer with a model contradicts why the request was held.
        Claim and Release would need a new concept in the store, and the conditional write
        on answering already makes a second answer fail rather than overwrite the first.
      </p>
    </div>
  );
}

function RoutesTab() {
  const routes = useQuery({ queryKey: ["ai", "routes"], queryFn: aiControlCenterApi.listRoutes });
  const tasks = useQuery({ queryKey: ["ai", "tasks"], queryFn: aiControlCenterApi.listTasks });

  if (routes.isLoading || tasks.isLoading) {
    return <p className="text-sm text-outline">Loading...</p>;
  }
  const error = routes.error ?? tasks.error;
  if (error) return <p className="text-sm text-error">{error.message}</p>;

  return (
    <div className="flex flex-col gap-4">
      <div className="premium-panel overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-outline-variant/70">
              <th className="premium-kicker px-4 py-3 font-semibold">Route</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Provider</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Model</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Tier</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Circuit</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Active</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Req/min</th>
            </tr>
          </thead>
          <tbody>
            {/* Keyed on route *and* tier. `route_id` is
                `provider/model/credential` and the tier loop sits outside it, so
                a provider offering one model at both tiers -- which MANUAL does,
                unconditionally -- yields two rows with one id. React warned that
                children may be duplicated or omitted, on the screen an operator
                reads to see which provider is live. */}
            {(routes.data ?? []).map((route) => (
              <tr key={`${route.routeId}:${route.tier}`} className="border-t border-outline-variant/50 transition-colors hover:bg-surface-container-low/60">
                <td className="px-4 py-2.5 font-mono text-xs">{route.routeId}</td>
                <td className="px-4 py-2.5">{route.provider}</td>
                <td className="px-4 py-2.5">{route.model}</td>
                <td className="px-4 py-2.5">{route.tier}</td>
                <td className="px-4 py-2.5">
                  <span
                    className={
                      route.circuitState === "CLOSED"
                        ? "text-emerald-700"
                        : route.circuitState === "OPEN"
                          ? "text-error"
                          : "text-amber-700"
                    }
                  >
                    {route.circuitState}
                  </span>
                  {!route.configured ? (
                    <span className="ml-1 text-xs text-outline">unconfigured</span>
                  ) : null}
                </td>
                <td className="px-4 py-2.5">{route.activeRequests}</td>
                <td className="px-4 py-2.5">{route.requestsThisMinute}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="premium-panel overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-outline-variant/70">
              <th className="premium-kicker px-4 py-3 font-semibold">Task</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Tier</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Prompt version</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Fallback</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Escalation</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Allowed providers</th>
            </tr>
          </thead>
          <tbody>
            {(tasks.data ?? []).map((task) => (
              <tr key={task.taskId} className="border-t border-outline-variant/50 transition-colors hover:bg-surface-container-low/60">
                <td className="px-4 py-2.5 font-mono text-xs">{task.taskId}</td>
                <td className="px-4 py-2.5">{task.tier}</td>
                <td className="px-4 py-2.5">{task.promptVersion}</td>
                <td className="px-4 py-2.5">{task.fallbackStrategy}</td>
                <td className="px-4 py-2.5">{task.allowTierEscalation ? "yes" : "no"}</td>
                <td className="px-4 py-2.5">{task.allowedProviders.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


/**
 * The model's reply as it came back, if this hold has one.
 *
 * Read defensively rather than typed: the sealed payload's request half varies
 * by task, and a hold written before response interception existed has no
 * `modelResponse` at all. A missing or malformed one yields `null`, which the
 * caller renders as "nothing to pre-fill" instead of crashing the one screen an
 * operator has for unblocking a stuck caller.
 */
function modelResponseOf(payload: InterceptionRequest | undefined): {
  readonly text: string;
  readonly provider: string;
  readonly model: string;
} | null {
  const raw: unknown = payload?.modelResponse;
  if (typeof raw !== "object" || raw === null) return null;
  const record = raw as Record<string, unknown>;
  if (typeof record.text !== "string") return null;
  return {
    text: record.text,
    provider: typeof record.provider === "string" ? record.provider : "unknown",
    model: typeof record.model === "string" ? record.model : "unknown",
  };
}

/**
 * Unseal one held item and supply the text a caller will receive.
 *
 * The payload is fetched only when this opens, never with the queue: it is
 * sealed at rest because it can carry rows read out of a customer's database,
 * and decrypting every pending item to render a list would defeat that. A held
 * *response* is sealed for the same reason -- an answer summarising those rows
 * carries them too.
 *
 * **The pre-fill is the whole ergonomic difference between the two points.** At
 * `REQUEST` an operator writes an answer from nothing. At `RESPONSE` they start
 * from what the model actually said, because the job is to change it, not to
 * retype it -- and an editor that made you retype it would produce edits that
 * are rewrites, which is a different and worse provenance story.
 *
 * Submitting is disabled while the payload is still loading. Answering a prompt
 * you have not seen is exactly the failure a manual path exists to prevent, and
 * the backend cannot tell the difference.
 */
function ManualResponder({
  row,
  onClose,
  onAnswered,
}: {
  row: InterceptionRow;
  onClose: () => void;
  onAnswered: () => void;
}) {
  const interceptionId = row.interceptionId;
  const labels = labelsFor(row.point);
  const isResponse = labels === POINT_LABELS.RESPONSE;
  // The draft is what the operator has typed, or `null` for "has not typed".
  // The pre-fill is then *derived* rather than copied into state by an effect:
  // copying would mean deciding when to stop copying, and the obvious answers
  // (on first arrival, on every settle) either discard a slow edit or fight the
  // query cache. Nullable draft has neither problem -- before you type you see
  // the model's reply, and the moment you type your text wins.
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const errorId = useId();
  const [submitting, setSubmitting] = useState(false);

  const request = useQuery({
    queryKey: ["ai", "interception", interceptionId, "request"],
    queryFn: () => aiControlCenterApi.readInterceptionRequest(interceptionId),
  });
  const original = modelResponseOf(request.data);
  const text = draft ?? original?.text ?? "";
  const setText = setDraft;

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await aiControlCenterApi.answerInterception(interceptionId, text);
      onAnswered();
    } catch (caught) {
      // A 409 means somebody else answered first. Surfaced verbatim rather than
      // retried: two operators answering one prompt is a real situation, and
      // the second one needs to know their text was not recorded.
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="premium-panel flex flex-col gap-3 p-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">
          {isResponse ? "Review" : "Respond to"}{" "}
          <code className="font-mono text-xs">{interceptionId}</code>
        </h3>
        <button type="button" className="text-xs text-outline hover:underline" onClick={onClose}>
          Close
        </button>
      </div>

      {request.isLoading ? <p className="text-sm text-outline">Unsealing request...</p> : null}
      {request.error ? (
        <p className="text-sm text-error">{request.error.message}</p>
      ) : null}
      {request.data ? (
        <pre
          // Focusable because it scrolls and holds nothing focusable: a keyboard
          // user could not reach the rest of the payload otherwise (WCAG 2.1.1).
          tabIndex={0}
          aria-label="Request payload"
          className="max-h-64 overflow-auto rounded bg-surface-container-low p-3 text-xs text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
        >
          {JSON.stringify(request.data, null, 2)}
        </pre>
      ) : null}

      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium">{isResponse ? "The response" : "Your answer"}</span>
        <textarea
          className="min-h-24 rounded border border-outline-control p-2 text-sm"
          aria-invalid={error !== null}
          // The submit refusal below described this answer and was attached to
          // nothing, so it was read out as a loose sentence somewhere on the
          // page rather than as this field's problem.
          aria-describedby={error === null ? undefined : errorId}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
          }}
          placeholder={
            isResponse
              ? "Edit the model's reply. Leaving it unchanged still records it as edited -- use Accept unchanged instead."
              : "Answer as the model would have, in the shape the task expects."
          }
        />
      </label>

      {/* A live region here, unlike the per-keystroke shape checks elsewhere:
          this is set by a submit that has already come back refused, so it
          announces once and interrupts nothing. */}
      {error ? (
        <p id={errorId} role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}

      <div className="flex items-center gap-2">
        <button
          type="button"
          className="rounded bg-primary px-3 py-1.5 text-sm text-white disabled:opacity-40"
          disabled={submitting || text.trim().length === 0 || !request.data}
          onClick={() => void submit()}
        >
          {submitting ? "Submitting..." : labels.submit}
        </button>
        <span className="text-xs text-outline">
          {isResponse && original !== null ? (
            <>
              Delivered as <code>HUMAN_EDITED</code>, recording that{" "}
              <code>
                {original.provider}/{original.model}
              </code>{" "}
              produced the substance and that you changed it. Never as the model, and never
              as a plain manual answer.
            </>
          ) : (
            "Recorded as your own subject, never as a model response."
          )}
        </span>
      </div>
    </section>
  );
}
