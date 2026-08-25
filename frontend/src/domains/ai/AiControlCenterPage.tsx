import { useEffect, useId, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronDown, Plus, X } from "lucide-react";

import { APIError } from "../../api/client";
import { configApi } from "../../api/configuration";
import {
  aiControlCenterApi,
  type AIRouteHealthView,
  type AISafetyTestResult,
  type AITaskView,
  type AITraceDetailView,
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

// Every tab is backed now. Audit reads the durable trace store, Safety runs the
// deterministic input inspector, and Configuration edits the active release's
// AI providers through the one release lifecycle the platform has.

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
  switch (tab) {
    case "Overview":
      return <MetricsTab />;
    case "Requests":
      return <RequestsTab />;
    case "Interceptions":
      return <InterceptionsTab canRead={canReadInterceptions} />;
    case "Providers & Models":
      return <ProvidersTab />;
    case "Routes & Tasks":
      return <RoutesTab />;
    case "Safety":
      return <SafetyTab />;
    case "Configuration":
      return <TasksConfigTab />;
    case "Audit":
      return <AuditTab />;
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

/**
 * What the detail dialog needs to render its header and metadata grid, however
 * the caller found the trace. Requests builds one from an attempt row; Audit
 * builds one from the stored trace itself. Normalised here rather than
 * union-typed in the dialog, so the dialog stays one renderer instead of two
 * renderers wearing one component.
 */
type DialogSubject = {
  readonly traceId: string;
  readonly taskId: string;
  readonly status: string;
  readonly fallbackUsed: boolean;
  readonly provider: string | null;
  readonly model: string | null;
  readonly latencyMs: number;
  readonly createdAt: string;
  readonly attemptNumber: string;
  readonly selectionReason: string;
  readonly routeId: string | null;
  readonly configuredTier: string;
  readonly selectedTier: string | null;
  readonly safetyStatus: string;
  readonly tokens: string;
  readonly rateLimitWaitMs: number;
  readonly cost: string;
  readonly errorCode: string | null;
  readonly fallbackReason: string | null;
  readonly correlationId: string | null;
  readonly caseId: string | null;
  readonly promptVersion: string | null;
  readonly requestDigest: string;
  readonly responseDigest: string | null;
};

function subjectFromAttempt(attempt: AIUsageAttemptView): DialogSubject {
  return {
    traceId: attempt.traceId,
    taskId: attempt.taskId,
    status: attempt.status,
    fallbackUsed: attempt.fallbackUsed,
    provider: attempt.provider,
    model: attempt.model,
    latencyMs: attempt.latencyMs,
    createdAt: attempt.createdAt,
    attemptNumber: String(attempt.attemptNumber),
    selectionReason: attempt.selectionReason,
    routeId: attempt.routeId,
    configuredTier: attempt.configuredTier,
    selectedTier: attempt.selectedTier,
    safetyStatus: attempt.safetyStatus,
    tokens: `${String(attempt.inputTokens)} / ${String(attempt.outputTokens)}`,
    rateLimitWaitMs: attempt.rateLimitWaitMs,
    cost: formatCostParts(
      attempt.pricingStatus,
      attempt.estimatedCostMicros,
      attempt.pricingCurrency,
    ),
    errorCode: attempt.errorCode,
    fallbackReason: attempt.fallbackReason,
    correlationId: attempt.correlationId,
    caseId: attempt.caseId,
    promptVersion: attempt.promptVersion,
    requestDigest: attempt.requestDigest,
    responseDigest: attempt.responseDigest,
  };
}

function subjectFromTrace(trace: AITraceDetailView): DialogSubject {
  return {
    traceId: trace.id,
    taskId: trace.taskId,
    status: trace.status,
    fallbackUsed: trace.fallbackUsed,
    provider: trace.provider,
    model: trace.model,
    latencyMs: trace.latencyMs ?? 0,
    createdAt: trace.createdAt,
    attemptNumber: `${String(trace.attempts)} attempt${trace.attempts === 1 ? "" : "s"}`,
    selectionReason: trace.selectionReason ?? "-",
    routeId: trace.routeId,
    configuredTier: trace.configuredTier,
    selectedTier: trace.selectedTier,
    safetyStatus: trace.safetyStatus,
    tokens: `${String(trace.inputTokens ?? 0)} / ${String(trace.outputTokens ?? 0)}`,
    rateLimitWaitMs: trace.rateLimitWaitMs,
    cost: formatCostParts(trace.pricingStatus, trace.estimatedCostMicros, trace.pricingCurrency),
    errorCode: trace.errorCode,
    fallbackReason: null,
    correlationId: null,
    caseId: null,
    promptVersion: trace.promptVersion,
    requestDigest: trace.requestDigest,
    responseDigest: trace.responseDigest,
  };
}

function RequestsTab() {
  const [selected, setSelected] = useState<DialogSubject | null>(null);
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
                onClick={() => { setSelected(subjectFromAttempt(attempt)); }}
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
                    onClick={() => { setSelected(subjectFromAttempt(attempt)); }}
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
          subject={selected}
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
  subject,
  onClose,
}: {
  subject: DialogSubject;
  onClose: () => void;
}) {
  const titleId = useId();
  const trace = useQuery({
    queryKey: ["ai", "trace", subject.traceId],
    queryFn: () => aiControlCenterApi.getRequest(subject.traceId),
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
              {subject.taskId}
            </h2>
            <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-on-surface-variant">
              <StatusPill status={subject.status} fallbackUsed={subject.fallbackUsed} />
              <span>{subject.provider ?? "-"} / {subject.model ?? "-"}</span>
              <span className="tabular-nums">{subject.latencyMs} ms</span>
              <span className="tabular-nums">{formatWhen(subject.createdAt)}</span>
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
          {subject.errorCode || subject.fallbackReason ? (
            <div className="rounded-xl border border-error-container bg-error-container/25 px-4 py-3 text-sm">
              {subject.errorCode ? (
                <p className="font-medium text-on-error-container">{subject.errorCode}</p>
              ) : null}
              {subject.fallbackReason ? (
                <p className="mt-0.5 text-on-error-container/90">{subject.fallbackReason}</p>
              ) : null}
            </div>
          ) : null}

          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm md:grid-cols-3">
            <Field label="Trace" value={subject.traceId} mono />
            <Field label="Attempt" value={subject.attemptNumber} />
            <Field label="Selection reason" value={subject.selectionReason} />
            <Field label="Route" value={subject.routeId ?? "-"} mono />
            <Field
              label="Tier"
              value={
                subject.selectedTier && subject.selectedTier !== subject.configuredTier
                  ? `${subject.configuredTier} -> ${subject.selectedTier}`
                  : subject.configuredTier
              }
            />
            <Field label="Safety" value={subject.safetyStatus} />
            <Field
              label="Tokens in / out"
              value={subject.tokens}
            />
            <Field label="Rate-limit wait" value={`${String(subject.rateLimitWaitMs)} ms`} />
            <Field label="Cost" value={subject.cost} />
            {/* W4.12: the business dimension. Ids only -- no customer data
                reaches this surface, by design of the record itself. */}
            <Field label="Correlation" value={subject.correlationId ?? "-"} mono />
            <Field label="Case" value={subject.caseId ?? "-"} mono />
            <Field label="Prompt version" value={subject.promptVersion ?? "-"} />
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
                  <Field label="Request digest" value={subject.requestDigest} mono />
                  <Field label="Response digest" value={subject.responseDigest ?? "-"} mono />
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
            <ReplayControls traceId={subject.traceId} />
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
function formatCostParts(
  pricingStatus: string,
  estimatedCostMicros: number | null,
  pricingCurrency: string | null,
): string {
  if (pricingStatus !== "PRICED" || estimatedCostMicros === null) {
    return "unknown -- no price in the active release";
  }
  return `${(estimatedCostMicros / 1_000_000).toFixed(6)} ${pricingCurrency ?? ""}`.trim();
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

/**
 * Audit -- the durable record of every invocation, as stored.
 *
 * Different grain from Requests, and the difference is the point: a Requests
 * row is one *attempt* against one route, so a call that failed over twice is
 * three rows; an Audit row is the one durable trace per invocation, carrying
 * the prompt, the redacted input and the response. Requests answers "what did
 * the routing do"; Audit answers "what was asked and what came back".
 */
function AuditTab() {
  const [selected, setSelected] = useState<DialogSubject | null>(null);
  const traces = useQuery({
    queryKey: ["ai", "traces"],
    queryFn: aiControlCenterApi.listRequests,
  });

  if (traces.isLoading) return <p className="text-sm text-outline">Loading...</p>;
  if (traces.error) return <p className="text-sm text-error">{traces.error.message}</p>;

  const rows = traces.data ?? [];

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-outline">
        The durable record: one row per invocation, with its stored prompt, input and
        response. Requests shows the same traffic at attempt grain -- one row per route
        tried -- which is why a failed-over call appears once here and several times
        there.
      </p>
      <div className="premium-panel overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-outline-variant/70">
              <th className="premium-kicker px-4 py-3 font-semibold">Task</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Provider / model</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Status</th>
              <th className="premium-kicker px-4 py-3 font-semibold">Payloads</th>
              <th className="premium-kicker px-4 py-3 text-right font-semibold">Tokens</th>
              <th className="premium-kicker px-4 py-3 text-right font-semibold">When</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-on-surface-variant">
                  No traces recorded yet. Every AI invocation this process makes writes one.
                </td>
              </tr>
            ) : null}
            {rows.map((trace) => (
              <tr
                key={trace.id}
                onClick={() => { setSelected(subjectFromTrace(trace)); }}
                className="group cursor-pointer border-t border-outline-variant/50 transition-colors hover:bg-surface-container-low"
              >
                <td className="px-4 py-2.5">
                  <button
                    type="button"
                    onClick={() => { setSelected(subjectFromTrace(trace)); }}
                    className="w-full text-left font-medium text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary group-hover:text-primary"
                  >
                    {trace.taskId}
                  </button>
                </td>
                <td className="px-4 py-2.5">
                  <span className="text-on-surface">{trace.provider ?? "-"}</span>
                  <span className="block text-xs text-on-surface-variant">{trace.model ?? "-"}</span>
                </td>
                <td className="px-4 py-2.5">
                  <StatusPill status={trace.status} fallbackUsed={trace.fallbackUsed} />
                </td>
                <td className="px-4 py-2.5 text-xs text-on-surface-variant">
                  {trace.responseText !== null && trace.responseText !== ""
                    ? "prompt + response"
                    : "prompt only"}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-on-surface-variant">
                  {trace.totalTokens ?? 0}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-xs text-on-surface-variant">
                  {formatWhen(trace.createdAt)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected !== null ? (
        <RequestDetailDialog subject={selected} onClose={() => { setSelected(null); }} />
      ) : null}
    </div>
  );
}

/**
 * Safety -- what the deterministic input inspector would say, before any model
 * is asked. The tester exists in development and test only; production answers
 * 403 and this screen says so instead of hiding the tab.
 */
function SafetyTab() {
  const summary = useQuery({
    queryKey: ["ai", "metrics", "summary"],
    queryFn: aiControlCenterApi.getSummary,
  });
  const tasks = useQuery({ queryKey: ["ai", "tasks"], queryFn: aiControlCenterApi.listTasks });
  const [taskId, setTaskId] = useState("");
  const [payloadText, setPayloadText] = useState(
    '{\n  "utterance": "I want to return the pump from order CW273354"\n}',
  );
  const [parseError, setParseError] = useState<string | null>(null);
  const test = useMutation({
    mutationFn: (input: { taskId: string; payload: Record<string, unknown> }) =>
      aiControlCenterApi.safetyTest(input.taskId, input.payload),
  });

  const taskOptions = tasks.data ?? [];
  const effectiveTask = taskId || taskOptions[0]?.taskId || "";
  const result: AISafetyTestResult | undefined = test.data;
  const disabledInProduction = test.error instanceof APIError && test.error.status === 403;

  const run = () => {
    setParseError(null);
    let payload: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(payloadText);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("The payload must be a JSON object.");
      }
      payload = parsed as Record<string, unknown>;
    } catch (caught) {
      setParseError(caught instanceof Error ? caught.message : String(caught));
      return;
    }
    test.mutate({ taskId: effectiveTask, payload });
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        <Stat
          label="Blocked by safety"
          value={summary.data?.blockedBySafety ?? "-"}
          tone={(summary.data?.blockedBySafety ?? 0) > 0 ? "text-error" : "text-on-surface"}
        />
        <Stat label="Attempts inspected" value={summary.data?.attempts ?? "-"} />
      </div>

      <section className="premium-panel flex flex-col gap-3 p-5">
        <div>
          <h3 className="text-sm font-semibold text-on-surface">Test the input inspector</h3>
          <p className="mt-1 text-xs text-on-surface-variant">
            Runs the same deterministic checks every request passes before a provider is
            called -- no model is invoked. Development and test environments only.
          </p>
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="premium-kicker">Task</span>
          <select
            className="premium-field max-w-md"
            value={effectiveTask}
            onChange={(event) => { setTaskId(event.target.value); }}
          >
            {taskOptions.map((task) => (
              <option key={task.taskId} value={task.taskId}>{task.taskId}</option>
            ))}
            {taskOptions.length === 0 ? <option value="">No tasks configured</option> : null}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="premium-kicker">Payload (JSON object)</span>
          <textarea
            className="premium-field min-h-32 font-mono text-xs"
            value={payloadText}
            onChange={(event) => { setPayloadText(event.target.value); }}
          />
        </label>
        {parseError !== null ? (
          <p role="alert" className="text-xs text-error">{parseError}</p>
        ) : null}
        <button
          type="button"
          disabled={test.isPending || effectiveTask === ""}
          onClick={run}
          className="self-start rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary transition disabled:opacity-40"
        >
          {test.isPending ? "Inspecting..." : "Run inspection"}
        </button>

        {disabledInProduction ? (
          <p className="rounded-xl bg-surface-container-low px-4 py-3 text-sm text-on-surface-variant">
            The safety tester is disabled in this environment: a surface that injects
            arbitrary payloads has no business existing in production. Per-request safety
            status is still recorded on every attempt under Requests.
          </p>
        ) : test.error ? (
          <p role="alert" className="text-sm text-error">{test.error.message}</p>
        ) : null}

        {result ? (
          <div className="flex flex-col gap-2 rounded-xl bg-surface-container-low px-4 py-3">
            <p className="flex items-center gap-2 text-sm">
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${
                  result.allowed
                    ? "bg-primary-container/15 text-primary"
                    : "bg-error-container/60 text-on-error-container"
                }`}
              >
                <span
                  aria-hidden="true"
                  className={`size-1.5 rounded-full ${result.allowed ? "bg-primary" : "bg-error"}`}
                />
                {result.allowed ? "ALLOWED" : result.status}
              </span>
              <span className="text-on-surface-variant">for {result.taskId}</span>
            </p>
            {result.signals.length > 0 ? (
              <p className="flex flex-wrap gap-1.5">
                {result.signals.map((signal) => (
                  <span
                    key={signal}
                    className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800"
                  >
                    {signal}
                  </span>
                ))}
              </p>
            ) : null}
            <Payload
              label="Deterministic response the caller would receive"
              text={JSON.stringify(result.deterministicResponse, null, 2)}
            />
          </div>
        ) : null}
      </section>
    </div>
  );
}

// --- Providers & Models -----------------------------------------------------
//
// Configured providers as cards, ranked; a card opens the full editor --
// models with display name, id, rank and an enable switch; credential
// references added and removed by name. Models, keys and provider order are
// release-owned configuration (`runtime_integrations.ai_providers`), so every
// edit stages locally and publishes through the one release lifecycle the
// platform has: draft -> patch -> VALIDATED -> RELEASED. Two honest limits the
// screen states rather than hides: a credential is a *reference* whose secret
// value lives in the process environment, and publishing needs
// `config.release.promote`.

type DraftModel = Record<string, unknown> & {
  model_id: string;
  model_class: "LIGHTWEIGHT" | "STANDARD";
  task_keys: string[];
  priority: number;
  display_name?: string | null;
  enabled?: boolean;
};

type DraftCredential = Record<string, unknown> & {
  profile_key: string;
};

type DraftProvider = Record<string, unknown> & {
  provider_key: string;
  enabled: boolean;
  base_url: string;
  priority: number;
  credentials: DraftCredential[];
  models: DraftModel[];
};

const PROVIDER_KEYS = ["GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA"] as const;

const DEFAULT_BASE_URLS: Record<string, string> = {
  GOOGLE: "https://generativelanguage.googleapis.com",
  NVIDIA: "https://integrate.api.nvidia.com",
  OPENAI: "https://api.openai.com",
  ANTHROPIC: "https://api.anthropic.com",
  OLLAMA: "http://localhost:11434",
};

type RuntimeSummary = {
  releaseId: string;
  headRevision: number | null;
  providers: DraftProvider[];
};

function runtimeSummaryOf(snapshot: Readonly<Record<string, unknown>>): RuntimeSummary {
  const configuration = snapshot.configuration as
    | { runtime_integrations?: { ai_providers?: unknown } }
    | undefined;
  const raw = configuration?.runtime_integrations?.ai_providers;
  const providers = Array.isArray(raw) ? (raw as DraftProvider[]) : [];
  const head = snapshot.head_revision;
  const releaseId = snapshot.release_id;
  return {
    releaseId: typeof releaseId === "string" ? releaseId : "unknown",
    headRevision: typeof head === "number" ? head : null,
    providers,
  };
}

type PublishStep = { name: string; state: "PENDING" | "RUNNING" | "DONE" | "FAILED" };

/**
 * The release lifecycle as one call: draft, patch one domain, validate,
 * release. Shared by the provider editor and the task/prompt editor, because
 * two hand-rolled copies of a four-step mutation is how the two screens would
 * come to publish differently.
 */
async function runPublishPipeline(options: {
  releaseId: string;
  domainKey: string;
  patch: Readonly<Record<string, unknown>>;
  headRevision: number | null;
  onSteps: (steps: readonly PublishStep[]) => void;
}): Promise<void> {
  const plan: PublishStep[] = [
    { name: `Create draft release ${options.releaseId}`, state: "PENDING" },
    { name: `Patch ${options.domainKey} domain`, state: "PENDING" },
    { name: "Promote to VALIDATED", state: "PENDING" },
    { name: "Promote to RELEASED", state: "PENDING" },
  ];
  const mark = (index: number, state: PublishStep["state"]) => {
    plan[index] = { ...plan[index], state };
    options.onSteps([...plan]);
  };
  options.onSteps([...plan]);
  const step = async (index: number, act: () => Promise<unknown>) => {
    mark(index, "RUNNING");
    try {
      await act();
    } catch (caught) {
      mark(index, "FAILED");
      throw caught;
    }
    mark(index, "DONE");
  };
  await step(0, () => configApi.createRelease(options.releaseId));
  await step(1, () => configApi.patchDomain(options.releaseId, options.domainKey, options.patch));
  await step(2, () => configApi.promote(options.releaseId, "VALIDATED"));
  await step(3, () =>
    configApi.promote(options.releaseId, "RELEASED", options.headRevision ?? undefined),
  );
}

function defaultReleaseId(prefix: string): string {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${prefix}-${String(now.getFullYear())}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

/** A premium on/off control; a checkbox is the accessible engine underneath. */
function Switch({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2">
      <span className="relative inline-flex h-5 w-9 shrink-0">
        <input
          type="checkbox"
          className="peer sr-only"
          checked={checked}
          onChange={(event) => { onChange(event.target.checked); }}
          aria-label={label}
        />
        <span className="absolute inset-0 rounded-full bg-surface-container-highest transition-colors peer-checked:bg-primary peer-focus-visible:ring-2 peer-focus-visible:ring-primary peer-focus-visible:ring-offset-2" />
        <span className="absolute left-0.5 top-0.5 size-4 rounded-full bg-surface-container-lowest shadow-sm transition-transform peer-checked:translate-x-4" />
      </span>
    </label>
  );
}

/** How the publish run is going, drawn once for both editors. */
function PublishProgress({
  steps,
  error,
  published,
  publishedNote,
}: {
  steps: readonly PublishStep[];
  error: string | null;
  published: boolean;
  publishedNote: string;
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

/**
 * The tasks a model may serve by default: every task whose tier matches the
 * model's class and whose allowedProviders names this provider. This mirrors
 * `bootstrap_runtime_integrations._task_keys`, which is how the platform itself
 * seeds bindings -- the first version of this editor defaulted to the first
 * task alphabetically, which bound every new model to
 * CUSTOMER_NOTIFICATION_DRAFT_V1 for no reason anyone chose.
 */
function eligibleTaskKeys(
  tasks: readonly AITaskView[],
  providerKey: string,
  modelClass: "LIGHTWEIGHT" | "STANDARD",
): string[] {
  return tasks
    .filter(
      (task) => task.tier === modelClass && task.allowedProviders.includes(providerKey),
    )
    .map((task) => task.taskId);
}



/** One button, one menu: the providers not yet in the release, ready to add. */
function AddProviderMenu({
  absentKeys,
  onAdd,
}: {
  absentKeys: readonly string[];
  onAdd: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (containerRef.current !== null && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (absentKeys.length === 0) return null;

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => { setOpen((value) => !value); }}
        className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-on-primary transition hover:bg-primary-container focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
      >
        <Plus size={15} aria-hidden="true" />
        Add provider
        <ChevronDown
          size={14}
          aria-hidden="true"
          className={`transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open ? (
        <ul className="absolute right-0 top-full z-10 mt-1 w-48 overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest py-1 shadow-lg">
          {absentKeys.map((key) => (
            <li key={key}>
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  onAdd(key);
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-on-surface transition-colors hover:bg-surface-container-low"
              >
                {key}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** A provider present only in the live environment routing, not in the release. */
type EnvProviderView = {
  provider: string;
  models: { model: string; tier: string; rank: number }[];
  credentialIds: string[];
};

function envProvidersOf(
  routes: readonly AIRouteHealthView[],
  releaseKeys: ReadonlySet<string>,
): EnvProviderView[] {
  const grouped = new Map<string, EnvProviderView>();
  for (const route of routes) {
    if (releaseKeys.has(route.provider)) continue;
    const entry = grouped.get(route.provider) ?? {
      provider: route.provider,
      models: [],
      credentialIds: [],
    };
    if (!entry.models.some((model) => model.model === route.model && model.tier === route.tier)) {
      entry.models.push({
        model: route.model,
        tier: route.tier,
        rank: entry.models.filter((model) => model.tier === route.tier).length + 1,
      });
    }
    if (!entry.credentialIds.includes(route.credentialId)) {
      entry.credentialIds.push(route.credentialId);
    }
    grouped.set(route.provider, entry);
  }
  return [...grouped.values()];
}

function ProvidersTab() {
  const { can } = useCapabilities();
  const canPublish = can("config.release.promote");
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });
  const routes = useQuery({ queryKey: ["ai", "routes"], queryFn: aiControlCenterApi.listRoutes });
  const tasks = useQuery({ queryKey: ["ai", "tasks"], queryFn: aiControlCenterApi.listTasks });

  const [drafts, setDrafts] = useState<DraftProvider[] | null>(null);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [releaseId, setReleaseId] = useState(() => defaultReleaseId("ai-providers"));
  const [steps, setSteps] = useState<readonly PublishStep[]>([]);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [published, setPublished] = useState(false);
  const [confirmingReset, setConfirmingReset] = useState(false);

  const summary = runtime.data === undefined ? null : runtimeSummaryOf(runtime.data);
  const providers = drafts ?? summary?.providers ?? [];
  const releaseKeys = new Set(providers.map((provider) => provider.provider_key));
  const envProviders = envProvidersOf(routes.data ?? [], releaseKeys);
  const dirty = drafts !== null;

  const stage = (next: DraftProvider[]) => {
    setDrafts(next);
    setPublished(false);
  };
  const update = (key: string, next: Partial<DraftProvider>) => {
    stage(
      providers.map((provider) =>
        provider.provider_key === key ? { ...provider, ...next } : provider,
      ),
    );
  };
  const addProvider = (key: string, seeded?: Partial<DraftProvider>) => {
    stage([
      ...providers,
      {
        provider_key: key,
        enabled: false,
        base_url: DEFAULT_BASE_URLS[key] ?? "https://",
        priority: providers.length + 1,
        credentials: [],
        models: [],
        ...seeded,
      },
    ]);
    setOpenKey(key);
  };
  const removeProvider = (key: string) => {
    stage(providers.filter((provider) => provider.provider_key !== key));
    setOpenKey(null);
  };
  const adoptEnvProvider = (view: EnvProviderView) => {
    addProvider(view.provider, {
      credentials: view.credentialIds
        .filter((id) => !id.endsWith("-local"))
        .map((id) => ({ profile_key: id, bootstrap_managed: true })),
      models: view.models.map((model, index) => ({
        model_id: model.model,
        model_class: model.tier === "LIGHTWEIGHT" ? "LIGHTWEIGHT" : "STANDARD",
        task_keys: [],
        priority: index + 1,
        enabled: true,
      })),
    });
  };

  const publish = async () => {
    if (summary === null) return;
    setPublished(false);
    setPublishError(null);
    try {
      // Task bindings are derived, not asked for: a model serves every task of
      // its tier that allows its provider -- the router matches on tier alone,
      // so per-model task curation was a knob that never steered anything. The
      // catalogue-wide fallback keeps a valid binding even for a provider no
      // task names yet.
      const catalogue = tasks.data ?? [];
      const normalized = providers.map((provider) => ({
        ...provider,
        models: provider.models.map((model) => {
          const eligible = eligibleTaskKeys(catalogue, provider.provider_key, model.model_class);
          const tierWide = catalogue
            .filter((task) => task.tier === model.model_class)
            .map((task) => task.taskId);
          return {
            ...model,
            task_keys: eligible.length > 0 ? eligible : tierWide,
          };
        }),
      }));
      await runPublishPipeline({
        releaseId,
        domainKey: "RETURN_PLATFORM",
        patch: { runtime_integrations: { ai_providers: normalized } },
        headRevision: summary.headRevision,
        onSteps: setSteps,
      });
      setPublished(true);
      setDrafts(null);
      setReleaseId(defaultReleaseId("ai-providers"));
      await runtime.refetch();
      await routes.refetch();
    } catch (caught) {
      setPublishError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  /**
   * Clear every release-held provider and key reference and publish that as a
   * release. With the release declaring nothing, routing falls back to the
   * process environment (PLATFORM_AI_PROVIDER_ORDER and the per-provider key
   * and model variables) -- in a production environment, which never falls
   * back, it means no providers until some are declared again. Secrets are
   * untouched either way: only references lived here.
   */
  const resetToEnvironment = async () => {
    if (summary === null) return;
    setConfirmingReset(false);
    setPublished(false);
    setPublishError(null);
    try {
      await runPublishPipeline({
        releaseId: defaultReleaseId("ai-providers-reset"),
        domainKey: "RETURN_PLATFORM",
        patch: { runtime_integrations: { ai_providers: [] } },
        headRevision: summary.headRevision,
        onSteps: setSteps,
      });
      setPublished(true);
      setDrafts(null);
      setOpenKey(null);
      setReleaseId(defaultReleaseId("ai-providers"));
      await runtime.refetch();
      await routes.refetch();
    } catch (caught) {
      setPublishError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  if (runtime.isLoading) return <p className="text-sm text-outline">Loading...</p>;
  if (runtime.error) return <p className="text-sm text-error">{runtime.error.message}</p>;
  if (summary === null) return null;

  const openProvider = providers.find((provider) => provider.provider_key === openKey) ?? null;
  const absentKeys = PROVIDER_KEYS.filter((key) => !releaseKeys.has(key));
  const ranked = [...providers].sort((a, b) => a.priority - b.priority);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-outline">
          Release <span className="font-mono">{summary.releaseId}</span> · providers are
          tried in rank order; models rank within their provider and tier.
        </p>
        <div className="flex items-center gap-2">
          {confirmingReset ? (
            <span className="flex items-center gap-2 rounded-lg border border-error-container bg-error-container/25 px-2.5 py-1.5 text-xs">
              <span className="text-on-error-container">
                Remove every release-held provider and key reference and return routing to
                the environment?
              </span>
              <button
                type="button"
                onClick={() => void resetToEnvironment()}
                className="rounded-md bg-error px-2 py-1 font-semibold text-on-error"
              >
                Reset
              </button>
              <button
                type="button"
                onClick={() => { setConfirmingReset(false); }}
                className="text-on-surface-variant hover:underline"
              >
                Cancel
              </button>
            </span>
          ) : (
            <button
              type="button"
              disabled={
                !canPublish ||
                providers.length === 0 ||
                steps.some((step) => step.state === "RUNNING")
              }
              onClick={() => { setConfirmingReset(true); }}
              title={
                providers.length === 0
                  ? "The release already declares no providers"
                  : "Clear release-held providers and keys; routing returns to the environment"
              }
              className="rounded-lg border border-outline-control px-3 py-2 text-sm font-medium text-on-surface-variant transition hover:border-error hover:text-error disabled:opacity-40"
            >
              Reset to environment
            </button>
          )}
          <AddProviderMenu absentKeys={absentKeys} onAdd={addProvider} />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {ranked.map((provider) => {
          const enabledModels = provider.models.filter((model) => model.enabled !== false).length;
          return (
            <button
              key={provider.provider_key}
              type="button"
              onClick={() => { setOpenKey(provider.provider_key); }}
              className="premium-panel group flex flex-col gap-3 p-5 text-left transition hover:-translate-y-0.5 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              <span className="flex items-center justify-between">
                <span className="flex items-center gap-2.5">
                  <span
                    aria-hidden="true"
                    className={`size-2 rounded-full ${provider.enabled ? "bg-primary" : "bg-outline-variant"}`}
                  />
                  <span className="text-base font-semibold text-on-surface group-hover:text-primary">
                    {provider.provider_key}
                  </span>
                </span>
                <span className="rounded-full bg-surface-container px-2 py-0.5 text-xs font-medium tabular-nums text-on-surface-variant">
                  Rank {provider.priority}
                </span>
              </span>
              <span className="text-xs text-on-surface-variant">
                {provider.enabled ? "Enabled" : "Disabled"} ·{" "}
                {enabledModels}/{provider.models.length} model
                {provider.models.length === 1 ? "" : "s"} active ·{" "}
                {provider.credentials.length} key
                {provider.credentials.length === 1 ? "" : "s"}
              </span>
              <span className="truncate font-mono text-xs text-outline">{provider.base_url}</span>
              <span className="flex flex-wrap gap-1">
                {provider.models.slice(0, 3).map((model) => (
                  <span
                    key={model.model_id || String(provider.models.indexOf(model))}
                    className={`rounded-full px-2 py-0.5 text-[11px] ${
                      model.enabled === false
                        ? "bg-surface-container text-outline line-through"
                        : "bg-primary-container/15 text-primary"
                    }`}
                  >
                    {model.display_name?.trim() ? model.display_name : model.model_id || "unnamed"}
                  </span>
                ))}
                {provider.models.length > 3 ? (
                  <span className="rounded-full bg-surface-container px-2 py-0.5 text-[11px] text-on-surface-variant">
                    +{provider.models.length - 3}
                  </span>
                ) : null}
              </span>
            </button>
          );
        })}

        {envProviders.map((view) => (
          <div key={view.provider} className="premium-panel flex flex-col gap-3 border-dashed p-5">
            <span className="flex items-center justify-between">
              <span className="flex items-center gap-2.5">
                <span aria-hidden="true" className="size-2 rounded-full bg-amber-500" />
                <span className="text-base font-semibold text-on-surface">{view.provider}</span>
              </span>
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800">
                Environment
              </span>
            </span>
            <span className="text-xs text-on-surface-variant">
              Serving {view.models.length} model{view.models.length === 1 ? "" : "s"} from
              process environment variables -- not yet governed by the release.
            </span>
            <span className="flex flex-wrap gap-1">
              {view.models.slice(0, 3).map((model) => (
                <span
                  key={`${model.model}:${model.tier}`}
                  className="rounded-full bg-surface-container px-2 py-0.5 text-[11px] text-on-surface-variant"
                >
                  {model.model}
                </span>
              ))}
              {view.models.length > 3 ? (
                <span className="rounded-full bg-surface-container px-2 py-0.5 text-[11px] text-on-surface-variant">
                  +{view.models.length - 3}
                </span>
              ) : null}
            </span>
            <button
              type="button"
              onClick={() => { adoptEnvProvider(view); }}
              className="self-start rounded-lg border border-outline-control px-2.5 py-1.5 text-xs font-medium text-on-surface-variant transition hover:bg-surface-container-low hover:text-on-surface"
            >
              Bring under release control
            </button>
          </div>
        ))}

        {ranked.length === 0 && envProviders.length === 0 ? (
          <p className="premium-panel col-span-full px-4 py-8 text-center text-sm text-on-surface-variant">
            No providers configured anywhere -- neither the release nor the process
            environment declares one. Add a provider to begin.
          </p>
        ) : null}
      </div>

      <section className="premium-panel flex flex-col gap-3 p-5">
        <div>
          <h3 className="text-sm font-semibold text-on-surface">Publish</h3>
          <p className="mt-1 text-xs text-on-surface-variant">
            Staged edits publish as a new configuration release: draft, patch, validate,
            release. A credential is a named reference -- its secret value comes from the
            process environment and is never entered here.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="premium-kicker">Release id</span>
            <input
              className="premium-field w-80 font-mono text-xs"
              value={releaseId}
              onChange={(event) => { setReleaseId(event.target.value); }}
            />
          </label>
          <button
            type="button"
            disabled={!canPublish || !dirty || steps.some((step) => step.state === "RUNNING")}
            onClick={() => void publish()}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary transition disabled:opacity-40"
          >
            Publish release
          </button>
          {!canPublish ? (
            <span className="text-xs text-outline">
              Publishing requires config.release.promote, which you do not hold.
            </span>
          ) : !dirty ? (
            <span className="text-xs text-outline">Nothing staged yet.</span>
          ) : (
            <span className="text-xs text-amber-700">Unpublished edits staged.</span>
          )}
        </div>
        <PublishProgress
          steps={steps}
          error={publishError}
          published={published}
          publishedNote="Released. Every process adopts on its own poll; routes reflect this process once it has."
        />
      </section>

      {openProvider !== null ? (
        <ProviderDialog
          provider={openProvider}
          onChange={(next) => { update(openProvider.provider_key, next); }}
          onRemove={() => { removeProvider(openProvider.provider_key); }}
          onClose={() => { setOpenKey(null); }}
        />
      ) : null}
    </div>
  );
}

function ProviderDialog({
  provider,
  onChange,
  onRemove,
  onClose,
}: {
  provider: DraftProvider;
  onChange: (next: Partial<DraftProvider>) => void;
  onRemove: () => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const [newProfileKey, setNewProfileKey] = useState("");

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); };
  }, [onClose]);

  const updateModel = (index: number, next: Partial<DraftModel>) => {
    onChange({
      models: provider.models.map((model, at) => (at === index ? { ...model, ...next } : model)),
    });
  };

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
          <div className="flex items-center gap-3">
            <h2 id={titleId} className="text-base font-semibold text-on-surface">
              {provider.provider_key}
            </h2>
            <Switch
              checked={provider.enabled}
              onChange={(enabled) => { onChange({ enabled }); }}
              label={`${provider.provider_key} enabled`}
            />
            <span className="text-xs text-on-surface-variant">
              {provider.enabled ? "Enabled" : "Disabled"}
            </span>
          </div>
          <button
            type="button"
            autoFocus
            onClick={onClose}
            aria-label="Close provider configuration"
            className="rounded-lg p-1.5 text-outline transition-colors hover:bg-surface-container-low hover:text-on-surface"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="flex flex-col gap-5 overflow-y-auto px-6 py-5">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-[8rem_1fr]">
            <label className="flex flex-col gap-1 text-sm">
              <span className="premium-kicker">Rank</span>
              <input
                type="number"
                min={1}
                max={100}
                value={provider.priority}
                onChange={(event) => { onChange({ priority: Number(event.target.value) || 1 }); }}
                className="premium-field tabular-nums"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="premium-kicker">Base URL</span>
              <input
                className="premium-field font-mono text-xs"
                value={provider.base_url}
                onChange={(event) => { onChange({ base_url: event.target.value }); }}
              />
            </label>
          </div>

          <section className="flex flex-col gap-2">
            <h3 className="premium-kicker">
              API keys ({provider.credentials.length})
              {provider.provider_key === "OLLAMA" ? " -- none needed for a local server" : ""}
            </h3>
            <ul className="flex flex-col gap-1.5">
              {provider.credentials.map((credential, index) => (
                <li
                  key={credential.profile_key}
                  className="flex items-center justify-between gap-3 rounded-xl bg-surface-container-low px-3 py-2"
                >
                  <span className="flex items-center gap-2">
                    <span className="font-mono text-xs text-on-surface">
                      {credential.profile_key}
                    </span>
                    <span className="rounded-full bg-surface-container px-2 py-0.5 text-[11px] text-on-surface-variant">
                      {typeof credential.vault_reference === "string"
                        ? "value from Vault"
                        : "value from environment"}
                    </span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[11px] ${
                        credential.bootstrap_managed === true
                          ? "bg-surface-container text-on-surface-variant"
                          : typeof credential.validation_receipt_id === "string"
                            ? "bg-primary-container/15 text-primary"
                            : "bg-amber-100 text-amber-800"
                      }`}
                    >
                      {credential.bootstrap_managed === true
                        ? "bootstrap-managed"
                        : typeof credential.validation_receipt_id === "string"
                          ? "validated"
                          : "needs validation receipt"}
                    </span>
                  </span>
                  <button
                    type="button"
                    aria-label={`Remove key ${credential.profile_key}`}
                    onClick={() => {
                      onChange({
                        credentials: provider.credentials.filter((_, at) => at !== index),
                      });
                    }}
                    className="text-outline transition hover:text-error"
                  >
                    <X size={14} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
            <div className="flex items-center gap-2">
              <input
                className="premium-field w-64 font-mono text-xs"
                placeholder={`e.g. ${provider.provider_key.toLowerCase()}-key-${String(provider.credentials.length + 1)}`}
                value={newProfileKey}
                onChange={(event) => { setNewProfileKey(event.target.value); }}
              />
              <button
                type="button"
                disabled={newProfileKey.trim() === ""}
                onClick={() => {
                  onChange({
                    credentials: [
                      ...provider.credentials,
                      { profile_key: newProfileKey.trim(), bootstrap_managed: true },
                    ],
                  });
                  setNewProfileKey("");
                }}
                className="rounded-lg border border-outline-control px-2.5 py-1.5 text-xs font-medium text-on-surface-variant transition hover:bg-surface-container-low disabled:opacity-40"
              >
                Add key
              </button>
            </div>
            <p className="text-xs text-outline">
              A key here is a named reference; its secret is supplied by the process
              environment. To rotate a key, change the value in the environment -- the
              reference stays.
            </p>
          </section>

          <section className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <div className="flex flex-col gap-0.5">
                <h3 className="premium-kicker">Models ({provider.models.length})</h3>
                <span className="text-[11px] text-outline">
                  A STANDARD model serves standard tasks, a LIGHTWEIGHT model serves
                  lightweight ones -- the router matches tiers automatically.
                </span>
              </div>
              <button
                type="button"
                onClick={() => {
                  onChange({
                    models: [
                      ...provider.models,
                      {
                        model_id: "",
                        model_class: "STANDARD",
                        task_keys: [],
                        priority: provider.models.length + 1,
                        enabled: true,
                      },
                    ],
                  });
                }}
                className="rounded-lg border border-outline-control px-2.5 py-1 text-xs font-medium text-on-surface-variant transition hover:bg-surface-container-low"
              >
                Add model
              </button>
            </div>
            {provider.models.length === 0 ? (
              <p className="rounded-xl bg-surface-container-low px-3 py-4 text-center text-sm text-on-surface-variant">
                No models yet. An enabled provider needs at least one enabled model.
              </p>
            ) : null}
            <ul className="flex flex-col gap-2">
              {[...provider.models]
                .map((model, index) => ({ model, index }))
                .sort((a, b) => a.model.priority - b.model.priority)
                .map(({ model, index }) => (
                  <li
                    key={String(index)}
                    className={`flex flex-col gap-2.5 rounded-xl px-3 py-3 ${
                      model.enabled === false
                        ? "bg-surface-container-low opacity-70"
                        : "bg-surface-container-low"
                    }`}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <input
                        className="premium-field w-44 text-sm"
                        aria-label="Model display name"
                        placeholder="Display name"
                        value={model.display_name ?? ""}
                        onChange={(event) => {
                          updateModel(index, {
                            display_name: event.target.value === "" ? null : event.target.value,
                          });
                        }}
                      />
                      <input
                        className="premium-field min-w-56 flex-1 font-mono text-xs"
                        aria-label="Model id"
                        placeholder="model id, e.g. models/gemini-3.6-flash"
                        value={model.model_id}
                        onChange={(event) => {
                          updateModel(index, { model_id: event.target.value });
                        }}
                      />
                      <label className="flex items-center gap-1 text-xs text-on-surface-variant">
                        rank
                        <input
                          type="number"
                          min={1}
                          max={100}
                          className="premium-field w-14 px-2 py-1 text-xs tabular-nums"
                          aria-label="Model rank"
                          value={model.priority}
                          onChange={(event) => {
                            updateModel(index, { priority: Number(event.target.value) || 1 });
                          }}
                        />
                      </label>
                      <select
                        className="premium-field w-36 text-xs"
                        aria-label="Model tier"
                        value={model.model_class}
                        onChange={(event) => {
                          updateModel(index, {
                            model_class: event.target.value as DraftModel["model_class"],
                          });
                        }}
                      >
                        <option value="STANDARD">STANDARD</option>
                        <option value="LIGHTWEIGHT">LIGHTWEIGHT</option>
                      </select>
                      <div className="ml-auto flex items-center gap-2">
                        <Switch
                          checked={model.enabled !== false}
                          onChange={(enabled) => { updateModel(index, { enabled }); }}
                          label={`Model ${model.model_id || "unnamed"} enabled`}
                        />
                        <button
                          type="button"
                          aria-label={`Remove model ${model.model_id || "unnamed"}`}
                          onClick={() => {
                            onChange({
                              models: provider.models.filter((_, at) => at !== index),
                            });
                          }}
                          className="text-outline transition hover:text-error"
                        >
                          <X size={14} aria-hidden="true" />
                        </button>
                      </div>
                    </div>
                  </li>
                ))}
            </ul>
          </section>

          <div className="flex justify-between border-t border-outline-variant/70 pt-4">
            <button
              type="button"
              onClick={onRemove}
              className="text-xs text-outline transition hover:text-error"
            >
              Remove provider from release
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary"
            >
              Done
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

// --- Configuration: agent tasks and prompts ---------------------------------
//
// Every AI task the release configures -- the prompt each agent runs on, its
// tier, budgets, fallback and provider allowances -- as cards, each opening a
// full editor. Edits stage locally and publish through the release lifecycle
// against the AI_GATEWAY domain. One rule the editor enforces because the
// backend does: for a task written as named sections, the sections are the
// source of truth -- the composed systemPrompt is recomputed, never edited.

type DraftSection = { name: string; text: string };

type DraftTask = {
  tier: "LIGHTWEIGHT" | "STANDARD";
  promptVersion: string;
  systemPrompt: string;
  systemPromptSections: DraftSection[];
  fallbackStrategy: "TEMPLATE" | "MANUAL_REVIEW";
  fallbackTemplate: string;
  maximumOutputTokens: number | null;
  maximumInputTokens: number;
  allowTierEscalation: boolean;
  allowedProviders: string[];
  allowedInputKeys: string[];
};

const ALL_TASK_PROVIDERS = [
  "GOOGLE",
  "NVIDIA",
  "OPENAI",
  "ANTHROPIC",
  "OLLAMA",
  "SIMULATOR",
  "MANUAL",
] as const;

function gatewayTasksOf(
  snapshot: Readonly<Record<string, unknown>>,
): Record<string, DraftTask> {
  const gateway = snapshot.ai_gateway_configuration as
    | { tasks?: Record<string, Record<string, unknown>> }
    | undefined;
  const raw = gateway?.tasks ?? {};
  const tasks: Record<string, DraftTask> = {};
  for (const [taskId, task] of Object.entries(raw)) {
    const sections = Array.isArray(task.systemPromptSections)
      ? (task.systemPromptSections as DraftSection[])
      : [];
    tasks[taskId] = {
      tier: task.tier === "LIGHTWEIGHT" ? "LIGHTWEIGHT" : "STANDARD",
      promptVersion: typeof task.promptVersion === "string" ? task.promptVersion : "",
      systemPrompt: typeof task.systemPrompt === "string" ? task.systemPrompt : "",
      systemPromptSections: sections.map((section) => ({
        name: typeof section.name === "string" ? section.name : "",
        text: typeof section.text === "string" ? section.text : "",
      })),
      fallbackStrategy: task.fallbackStrategy === "MANUAL_REVIEW" ? "MANUAL_REVIEW" : "TEMPLATE",
      fallbackTemplate: typeof task.fallbackTemplate === "string" ? task.fallbackTemplate : "",
      maximumOutputTokens:
        typeof task.maximumOutputTokens === "number" ? task.maximumOutputTokens : null,
      maximumInputTokens:
        typeof task.maximumInputTokens === "number" ? task.maximumInputTokens : 4000,
      allowTierEscalation: task.allowTierEscalation === true,
      allowedProviders: Array.isArray(task.allowedProviders)
        ? (task.allowedProviders as string[])
        : [],
      allowedInputKeys: Array.isArray(task.allowedInputKeys)
        ? (task.allowedInputKeys as string[])
        : [],
    };
  }
  return tasks;
}

/**
 * The merge patch for one edited task. Sections are the source of truth: for a
 * sectioned task the composed `systemPrompt` is nulled so the backend
 * recomposes it -- leaving the stale composed copy in place is exactly the
 * disagreement `_compose_system_prompt` refuses.
 */
function taskPatchOf(task: DraftTask): Record<string, unknown> {
  const usesSections = task.systemPromptSections.length > 0;
  return {
    tier: task.tier,
    promptVersion: task.promptVersion,
    systemPrompt: usesSections ? null : task.systemPrompt,
    systemPromptSections: usesSections
      ? task.systemPromptSections.map((section) => ({ name: section.name, text: section.text }))
      : null,
    fallbackStrategy: task.fallbackStrategy,
    fallbackTemplate: task.fallbackTemplate,
    maximumOutputTokens: task.maximumOutputTokens,
    maximumInputTokens: task.maximumInputTokens,
    allowTierEscalation: task.allowTierEscalation,
    allowedProviders: task.allowedProviders,
    allowedInputKeys: task.allowedInputKeys,
  };
}

/** Which agent surface a task belongs to, for grouping the card grid. */
function taskGroupOf(taskId: string): string {
  if (taskId.startsWith("ORDER_AGENT_")) return "Order Agent";
  if (taskId.startsWith("GRAPH_SCHEMA_")) return "Graph Schema Analyzer";
  if (taskId.startsWith("ORDER_CANDIDATE_")) return "Order Analysis";
  if (taskId.startsWith("SIMULATOR_")) return "Dependency Simulator";
  return "Returns Workflow";
}

function TasksConfigTab() {
  const { can } = useCapabilities();
  const canPublish = can("config.release.promote");
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });

  const [edits, setEdits] = useState<Record<string, DraftTask>>({});
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);
  const [releaseId, setReleaseId] = useState(() => defaultReleaseId("ai-tasks"));
  const [steps, setSteps] = useState<readonly PublishStep[]>([]);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [published, setPublished] = useState(false);

  const summary = runtime.data === undefined ? null : runtimeSummaryOf(runtime.data);
  const baseline = runtime.data === undefined ? {} : gatewayTasksOf(runtime.data);
  const taskIds = Object.keys(baseline).sort();
  const dirtyIds = Object.keys(edits);

  const taskOf = (taskId: string): DraftTask | undefined => edits[taskId] ?? baseline[taskId];

  const publish = async () => {
    if (summary === null || dirtyIds.length === 0) return;
    setPublished(false);
    setPublishError(null);
    try {
      await runPublishPipeline({
        releaseId,
        domainKey: "AI_GATEWAY",
        patch: {
          tasks: Object.fromEntries(
            dirtyIds.map((taskId) => [taskId, taskPatchOf(edits[taskId])]),
          ),
        },
        headRevision: summary.headRevision,
        onSteps: setSteps,
      });
      setPublished(true);
      setEdits({});
      setReleaseId(defaultReleaseId("ai-tasks"));
      await runtime.refetch();
    } catch (caught) {
      setPublishError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  if (runtime.isLoading) return <p className="text-sm text-outline">Loading...</p>;
  if (runtime.error) return <p className="text-sm text-error">{runtime.error.message}</p>;
  if (summary === null) return null;

  const groups = new Map<string, string[]>();
  for (const taskId of taskIds) {
    const group = taskGroupOf(taskId);
    groups.set(group, [...(groups.get(group) ?? []), taskId]);
  }
  const openTask = openTaskId === null ? undefined : taskOf(openTaskId);

  return (
    <div className="flex flex-col gap-5">
      <p className="text-xs text-outline">
        Every AI task in release <span className="font-mono">{summary.releaseId}</span> --
        the prompt each agent runs on, its tier, budgets, fallback and provider
        allowances. Open a task to edit it; edits stage until published.
      </p>

      {[...groups.entries()].map(([group, ids]) => (
        <section key={group} className="flex flex-col gap-2.5">
          <h3 className="text-sm font-semibold text-on-surface">{group}</h3>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {ids.map((taskId) => {
              const task = taskOf(taskId);
              if (task === undefined) return null;
              const edited = taskId in edits;
              const promptChars = task.systemPromptSections.length > 0
                ? task.systemPromptSections.reduce((sum, section) => sum + section.text.length, 0)
                : task.systemPrompt.length;
              return (
                <button
                  key={taskId}
                  type="button"
                  onClick={() => { setOpenTaskId(taskId); }}
                  className="premium-panel group flex flex-col gap-2.5 p-4 text-left transition hover:-translate-y-0.5 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                >
                  <span className="flex items-start justify-between gap-2">
                    <span className="break-all font-mono text-xs font-medium text-on-surface group-hover:text-primary">
                      {taskId}
                    </span>
                    {edited ? (
                      <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800">
                        edited
                      </span>
                    ) : null}
                  </span>
                  <span className="flex flex-wrap gap-1.5 text-[11px]">
                    <span className="rounded-full bg-surface-container px-2 py-0.5 text-on-surface-variant">
                      {task.tier}
                    </span>
                    <span className="rounded-full bg-surface-container px-2 py-0.5 text-on-surface-variant">
                      {task.systemPromptSections.length > 0
                        ? `${String(task.systemPromptSections.length)} prompt sections`
                        : "single prompt"}
                    </span>
                    <span className="rounded-full bg-surface-container px-2 py-0.5 tabular-nums text-on-surface-variant">
                      {promptChars.toLocaleString()} chars
                    </span>
                  </span>
                  <span className="truncate text-xs text-on-surface-variant">
                    {task.promptVersion} · fallback {task.fallbackStrategy}
                  </span>
                </button>
              );
            })}
          </div>
        </section>
      ))}

      <section className="premium-panel flex flex-col gap-3 p-5">
        <div>
          <h3 className="text-sm font-semibold text-on-surface">Publish</h3>
          <p className="mt-1 text-xs text-on-surface-variant">
            {dirtyIds.length === 0
              ? "No task edited yet."
              : `${String(dirtyIds.length)} task${dirtyIds.length === 1 ? "" : "s"} staged: ${dirtyIds.join(", ")}.`}{" "}
            Publishing validates the whole gateway configuration -- prompt budgets
            included -- before anything is released. Bump the prompt version when the
            text changes: published versions have recorded attempts stamped against
            them and are never amended in place.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="premium-kicker">Release id</span>
            <input
              className="premium-field w-80 font-mono text-xs"
              value={releaseId}
              onChange={(event) => { setReleaseId(event.target.value); }}
            />
          </label>
          <button
            type="button"
            disabled={
              !canPublish || dirtyIds.length === 0 || steps.some((step) => step.state === "RUNNING")
            }
            onClick={() => void publish()}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary transition disabled:opacity-40"
          >
            Publish release
          </button>
          {!canPublish ? (
            <span className="text-xs text-outline">
              Publishing requires config.release.promote, which you do not hold.
            </span>
          ) : null}
        </div>
        <PublishProgress
          steps={steps}
          error={publishError}
          published={published}
          publishedNote="Released. Every process adopts the new prompts on its own poll."
        />
      </section>

      {openTaskId !== null && openTask !== undefined ? (
        <TaskDialog
          taskId={openTaskId}
          task={openTask}
          edited={openTaskId in edits}
          onChange={(next) => {
            setEdits((current) => ({ ...current, [openTaskId]: { ...openTask, ...next } }));
            setPublished(false);
          }}
          onRevert={() => {
            setEdits((current) =>
              Object.fromEntries(
                Object.entries(current).filter(([taskId]) => taskId !== openTaskId),
              ),
            );
          }}
          onClose={() => { setOpenTaskId(null); }}
        />
      ) : null}
    </div>
  );
}

function TaskDialog({
  taskId,
  task,
  edited,
  onChange,
  onRevert,
  onClose,
}: {
  taskId: string;
  task: DraftTask;
  edited: boolean;
  onChange: (next: Partial<DraftTask>) => void;
  onRevert: () => void;
  onClose: () => void;
}) {
  const titleId = useId();
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); };
  }, [onClose]);

  const usesSections = task.systemPromptSections.length > 0;
  const updateSection = (index: number, next: Partial<DraftSection>) => {
    onChange({
      systemPromptSections: task.systemPromptSections.map((section, at) =>
        at === index ? { ...section, ...next } : section,
      ),
    });
  };
  const toggleProvider = (provider: string) => {
    onChange({
      allowedProviders: task.allowedProviders.includes(provider)
        ? task.allowedProviders.filter((entry) => entry !== provider)
        : [...task.allowedProviders, provider],
    });
  };

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
        className="premium-panel flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden"
      >
        <header className="flex items-start justify-between gap-4 border-b border-outline-variant/70 px-6 py-4">
          <div>
            <h2 id={titleId} className="break-all font-mono text-sm font-semibold text-on-surface">
              {taskId}
            </h2>
            <p className="mt-1 text-xs text-on-surface-variant">
              {usesSections
                ? "Prompt written as named sections; the composed prompt is rebuilt from them on publish."
                : "Prompt written as one string."}
            </p>
          </div>
          <button
            type="button"
            autoFocus
            onClick={onClose}
            aria-label="Close task configuration"
            className="rounded-lg p-1.5 text-outline transition-colors hover:bg-surface-container-low hover:text-on-surface"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="flex flex-col gap-5 overflow-y-auto px-6 py-5">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <label className="flex flex-col gap-1 text-sm">
              <span className="premium-kicker">Prompt version</span>
              <input
                className="premium-field font-mono text-xs"
                value={task.promptVersion}
                onChange={(event) => { onChange({ promptVersion: event.target.value }); }}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="premium-kicker">Tier</span>
              <select
                className="premium-field text-xs"
                value={task.tier}
                onChange={(event) => {
                  onChange({ tier: event.target.value as DraftTask["tier"] });
                }}
              >
                <option value="LIGHTWEIGHT">LIGHTWEIGHT</option>
                <option value="STANDARD">STANDARD</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="premium-kicker">Max output tokens</span>
              <input
                type="number"
                min={32}
                max={8192}
                className="premium-field text-xs tabular-nums"
                placeholder="provider default"
                value={task.maximumOutputTokens ?? ""}
                onChange={(event) => {
                  onChange({
                    maximumOutputTokens:
                      event.target.value === "" ? null : Number(event.target.value),
                  });
                }}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="premium-kicker">Max input tokens</span>
              <input
                type="number"
                min={256}
                max={200000}
                className="premium-field text-xs tabular-nums"
                value={task.maximumInputTokens}
                onChange={(event) => {
                  onChange({ maximumInputTokens: Number(event.target.value) || 256 });
                }}
              />
            </label>
          </div>

          <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="premium-kicker">Fallback strategy</span>
              <select
                className="premium-field text-xs"
                value={task.fallbackStrategy}
                onChange={(event) => {
                  onChange({
                    fallbackStrategy: event.target.value as DraftTask["fallbackStrategy"],
                  });
                }}
              >
                <option value="TEMPLATE">TEMPLATE</option>
                <option value="MANUAL_REVIEW">MANUAL_REVIEW</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="premium-kicker">Fallback template</span>
              <input
                className="premium-field font-mono text-xs"
                value={task.fallbackTemplate}
                onChange={(event) => { onChange({ fallbackTemplate: event.target.value }); }}
              />
            </label>
            <div className="flex items-end gap-2 pb-1.5">
              <Switch
                checked={task.allowTierEscalation}
                onChange={(allowTierEscalation) => { onChange({ allowTierEscalation }); }}
                label="Allow tier escalation"
              />
              <span className="text-xs text-on-surface-variant">Allow tier escalation</span>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="premium-kicker">Allowed providers</span>
            <div className="flex flex-wrap gap-1.5">
              {ALL_TASK_PROVIDERS.map((provider) => {
                const active = task.allowedProviders.includes(provider);
                return (
                  <button
                    key={provider}
                    type="button"
                    aria-pressed={active}
                    onClick={() => { toggleProvider(provider); }}
                    className={`rounded-full px-2.5 py-1 text-xs font-medium transition ${
                      active
                        ? "bg-primary text-on-primary"
                        : "bg-surface-container text-on-surface-variant hover:bg-surface-container-high"
                    }`}
                  >
                    {provider}
                  </button>
                );
              })}
            </div>
          </div>

          <label className="flex flex-col gap-1 text-sm">
            <span className="premium-kicker">Allowed input keys</span>
            <input
              className="premium-field font-mono text-xs"
              title="The payload keys this task accepts, comma separated"
              value={task.allowedInputKeys.join(", ")}
              onChange={(event) => {
                onChange({
                  allowedInputKeys: event.target.value
                    .split(",")
                    .map((key) => key.trim())
                    .filter((key) => key !== ""),
                });
              }}
            />
          </label>

          {usesSections ? (
            <section className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <h3 className="premium-kicker">
                  Prompt sections ({task.systemPromptSections.length})
                </h3>
                <button
                  type="button"
                  onClick={() => {
                    onChange({
                      systemPromptSections: [
                        ...task.systemPromptSections,
                        { name: "new-section", text: "" },
                      ],
                    });
                  }}
                  className="rounded-lg border border-outline-control px-2.5 py-1 text-xs font-medium text-on-surface-variant transition hover:bg-surface-container-low"
                >
                  Add section
                </button>
              </div>
              <ul className="flex flex-col gap-2">
                {task.systemPromptSections.map((section, index) => (
                  <li
                    key={String(index)}
                    className="flex flex-col gap-2 rounded-xl bg-surface-container-low px-3 py-3"
                  >
                    <div className="flex items-center gap-2">
                      <input
                        className="premium-field w-72 font-mono text-xs"
                        aria-label={`Section ${String(index + 1)} name`}
                        value={section.name}
                        onChange={(event) => { updateSection(index, { name: event.target.value }); }}
                      />
                      <span className="ml-auto text-[11px] tabular-nums text-outline">
                        {section.text.length.toLocaleString()} chars
                      </span>
                      <button
                        type="button"
                        aria-label={`Remove section ${section.name}`}
                        onClick={() => {
                          onChange({
                            systemPromptSections: task.systemPromptSections.filter(
                              (_, at) => at !== index,
                            ),
                          });
                        }}
                        className="text-outline transition hover:text-error"
                      >
                        <X size={14} aria-hidden="true" />
                      </button>
                    </div>
                    <textarea
                      className="premium-field min-h-24 font-mono text-xs leading-relaxed"
                      aria-label={`Section ${section.name} text`}
                      value={section.text}
                      onChange={(event) => { updateSection(index, { text: event.target.value }); }}
                    />
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <label className="flex flex-col gap-1 text-sm">
              <span className="flex items-center justify-between">
                <span className="premium-kicker">System prompt</span>
                <span className="text-[11px] tabular-nums text-outline">
                  {task.systemPrompt.length.toLocaleString()} chars
                </span>
              </span>
              <textarea
                className="premium-field min-h-48 font-mono text-xs leading-relaxed"
                value={task.systemPrompt}
                onChange={(event) => { onChange({ systemPrompt: event.target.value }); }}
              />
            </label>
          )}

          <div className="flex justify-between border-t border-outline-variant/70 pt-4">
            <button
              type="button"
              disabled={!edited}
              onClick={onRevert}
              className="text-xs text-outline transition hover:text-error disabled:opacity-40"
            >
              Discard edits to this task
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary"
            >
              Done
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
