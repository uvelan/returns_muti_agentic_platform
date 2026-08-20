import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

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
 * **Never expose hidden chain-of-thought.** Nothing here renders model
 * reasoning text, and there is none to render: an attempt carries a
 * `requestDigest`/`responseDigest` rather than bodies, which is the design
 * working as intended. Trace, task, provider, model, timing, tokens, fallback
 * and safety status are shown -- structured observability, not private
 * reasoning.
 */

const AI_DOMAIN = requireDomain("/ai");

/** Mirrors `AI_DOMAIN.sections`; the registry drives the sidebar, this drives the body. */
type Tab = (typeof AI_SECTIONS)[number];

/** Tabs with no backing route on `/api/ai`. Named, not silently dropped. */
const UNBACKED: Partial<Record<Tab, string>> = {
  Audit:
    "Prompts and completions are not stored. A telemetry row holds a digest of the request and nothing else, so there is no payload here to show -- see Requests for what every call recorded, and Interceptions for the one place a full request and its answer exist, because a held request has to be readable by the human answering it.",
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
    return <p className="text-sm text-slate-600">You do not have access to the AI Control Center.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">AI Control Center</h1>
        <p className="mt-1 text-sm text-slate-600">
          Requests, interceptions, metrics, routes, and safety.
        </p>
      </header>


      <TabBody tab={tab} canReadInterceptions={can("ai.interception.read")} />
    </div>
  );
}

function TabBody({ tab, canReadInterceptions }: { tab: Tab; canReadInterceptions: boolean }) {
  const unbacked = UNBACKED[tab];
  if (unbacked) return <p className="text-sm text-slate-500">{unbacked}</p>;

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

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  );
}

function MetricsTab() {
  const summary = useQuery({
    queryKey: ["ai", "metrics", "summary"],
    queryFn: aiControlCenterApi.getSummary,
  });

  if (summary.isLoading) return <p className="text-sm text-slate-500">Loading...</p>;
  if (summary.error) return <p className="text-sm text-red-700">{summary.error.message}</p>;
  if (!summary.data) return null;

  const s = summary.data;
  const successRate = s.attempts > 0 ? Math.round((s.successes / s.attempts) * 100) : 0;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <Stat label="Attempts" value={s.attempts} />
        <Stat label="Success rate" value={`${String(successRate)}%`} />
        <Stat label="Failures" value={s.failures} />
        <Stat label="Fallbacks" value={s.fallbacks} />
        <Stat label="Blocked by safety" value={s.blockedBySafety} />
        <Stat label="Total tokens" value={s.totalTokens} />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Breakdown title="By provider" data={s.byProvider} />
        <Breakdown title="By model" data={s.byModel} />
        <Breakdown title="By task" data={s.byTask} />
        <Breakdown title="By tier" data={s.byTier} />
      </div>

      <p className="text-xs text-slate-500">
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
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-slate-600">No data.</p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1">
          {rows.map(([key, count]) => (
            <li key={key} className="flex justify-between text-sm">
              <span className="truncate text-slate-700">{key}</span>
              <span className="font-medium text-slate-900">{count}</span>
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

  if (attempts.isLoading) return <p className="text-sm text-slate-500">Loading...</p>;
  if (attempts.error) return <p className="text-sm text-red-700">{attempts.error.message}</p>;

  const rows = attempts.data ?? [];

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_22rem]">
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="p-2">Task</th>
              <th className="p-2">Provider</th>
              <th className="p-2">Model</th>
              <th className="p-2">Status</th>
              <th className="p-2">Latency</th>
              <th className="p-2">Tokens</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-3 text-slate-600">No requests recorded.</td>
              </tr>
            ) : null}
            {rows.map((attempt) => (
              <tr
                key={attempt.id}
                onClick={() => { setSelected(attempt); }}
                className="cursor-pointer border-t border-slate-200 hover:bg-slate-50"
              >
                <td className="p-2">{attempt.taskId}</td>
                <td className="p-2">{attempt.provider ?? "-"}</td>
                <td className="p-2">{attempt.model ?? "-"}</td>
                <td className="p-2">
                  {attempt.status}
                  {attempt.fallbackUsed ? (
                    <span className="ml-1 text-xs text-amber-700">fallback</span>
                  ) : null}
                </td>
                <td className="p-2">{attempt.latencyMs} ms</td>
                <td className="p-2">{attempt.totalTokens}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <aside className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-slate-900">Request inspection</h2>
        {selected === null ? (
          <p className="mt-2 text-sm text-slate-600">Select a request.</p>
        ) : (
          <dl className="mt-3 flex flex-col gap-2 text-sm">
            <Field label="Trace" value={selected.traceId} mono />
            <Field label="Attempt" value={String(selected.attemptNumber)} />
            <Field label="Selection reason" value={selected.selectionReason} />
            <Field label="Configured tier" value={selected.configuredTier} />
            <Field label="Selected tier" value={selected.selectedTier ?? "-"} />
            <Field label="Route" value={selected.routeId ?? "-"} mono />
            <Field label="Safety" value={selected.safetyStatus} />
            <Field label="Rate-limit wait" value={`${String(selected.rateLimitWaitMs)} ms`} />
            <Field label="Input / output tokens" value={`${String(selected.inputTokens)} / ${String(selected.outputTokens)}`} />
            <Field label="Error" value={selected.errorCode ?? "-"} />
            <Field label="Fallback reason" value={selected.fallbackReason ?? "-"} />
            <Field label="Cost" value={formatCost(selected)} />
            <Field label="Pricing version" value={selected.pricingVersion ?? "-"} />
            <Field label="Request digest" value={selected.requestDigest} mono />
            <Field label="Response digest" value={selected.responseDigest ?? "-"} mono />

            {/* W4.12: the business dimension. Ids only -- no customer data
                reaches this surface, by design of the record itself. */}
            <Field label="Correlation" value={selected.correlationId ?? "-"} mono />
            <Field label="Case" value={selected.caseId ?? "-"} mono />
            <Field label="Conversation" value={selected.conversationId ?? "-"} mono />
            <Field label="Agent" value={selected.agentId ?? "-"} />
            <Field label="Prompt version" value={selected.promptVersion ?? "-"} />

            <p className="mt-2 text-xs text-slate-500">
              Digests, not bodies. Prompt and response payloads are deliberately not
              served by this surface.
            </p>
            <ReplayControls traceId={selected.traceId} />
          </dl>
        )}
      </aside>
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
    <div className="mt-3 flex flex-col gap-2 border-t border-slate-200 pt-3">
      <label className="text-xs uppercase tracking-wide text-slate-500" htmlFor="replay-provider">
        Replay provider
      </label>
      <select
        id="replay-provider"
        className="rounded border border-slate-300 p-1 text-sm"
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
          className="rounded bg-slate-900 px-2 py-1 text-sm text-white disabled:opacity-50"
          disabled={replay.isPending}
          onClick={() => { replay.mutate(); }}
        >
          Replay
        </button>
        <button
          type="button"
          className="rounded border border-slate-300 px-2 py-1 text-sm disabled:opacity-50"
          disabled={compare.isPending}
          onClick={() => { compare.mutate(); }}
        >
          Compare providers
        </button>
      </div>
      {replay.error ? <p className="text-xs text-red-700">{replay.error.message}</p> : null}
      {compare.error ? <p className="text-xs text-red-700">{compare.error.message}</p> : null}
      {replay.data ? (
        <p className="text-xs text-slate-600">
          Replayed as trace <span className="font-mono">{replay.data.id}</span>:{" "}
          {replay.data.provider ?? "-"} / {replay.data.model ?? "-"} -&gt;{" "}
          {replay.data.decision ?? replay.data.status}
        </p>
      ) : null}
      {compare.data ? (
        <ul className="flex flex-col gap-1 text-xs text-slate-600">
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
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className={mono ? "break-all font-mono text-xs text-slate-800" : "text-slate-800"}>
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
  const interceptions = useQuery({
    queryKey: ["ai", "interceptions"],
    queryFn: aiControlCenterApi.listInterceptions,
    enabled: canRead,
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
      <p className="text-sm text-slate-600">
        Viewing interceptions requires ai.interception.read.
      </p>
    );
  }
  if (interceptions.isLoading) return <p className="text-sm text-slate-500">Loading...</p>;
  if (interceptions.error) {
    return <p className="text-sm text-red-700">{interceptions.error.message}</p>;
  }

  const rows: readonly InterceptionRow[] = interceptions.data ?? [];
  const byStatus = (status: string) =>
    rows.filter((row) => row.status.toUpperCase() === status).length;
  const byPoint = (point: InterceptionPoint) =>
    rows.filter((row) => labelsFor(row.point) === POINT_LABELS[point]).length;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        {/* The four statuses `InterceptionStatus` actually has. This counted
            CLAIMED and RESPONDED, which the backend never emits -- both tiles
            read zero in every deployment, which is indistinguishable from a
            quiet queue. */}
        <Stat label="Pending" value={byStatus("PENDING")} />
        <Stat label="Answered" value={byStatus("ANSWERED")} />
        <Stat label="Cancelled" value={byStatus("CANCELLED")} />
        <Stat label="Expired" value={byStatus("EXPIRED")} />
        {/* One queue, two jobs. The split matters operationally: a held request
            is waiting on somebody to write an answer, a held response is
            waiting on somebody to read one, and the second kind is blocking a
            live coroutine while it waits. */}
        <Stat label="Held requests" value={byPoint("REQUEST")} />
        <Stat label="Held responses" value={byPoint("RESPONSE")} />
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="p-2">Interception</th>
              <th className="p-2">Point</th>
              <th className="p-2">Status</th>
              <th className="p-2">Task</th>
              <th className="p-2">Expires</th>
              <th className="p-2">Actioned by</th>
              <th className="p-2" />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={7} className="p-3 text-slate-600">No interceptions.</td>
              </tr>
            ) : null}
            {rows.map((row) => {
              const labels = labelsFor(row.point);
              const status = row.status.toUpperCase();
              return (
              <tr key={row.interceptionId} className="border-t border-slate-200">
                <td className="p-2 font-mono text-xs">{row.interceptionId}</td>
                <td className="p-2">
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
                <td className="p-2">
                  {row.status}
                  {labels.outcomes[status] ? (
                    <span className="ml-1 text-xs text-slate-500">
                      ({labels.outcomes[status]})
                    </span>
                  ) : null}
                </td>
                <td className="p-2">{row.taskId}</td>
                <td className="p-2">{row.expiresAt}</td>
                {/* A human answer must never read as a model's. `answeredBy` is
                    the operator's own subject, recorded by the backend. */}
                <td className="p-2">{row.answeredBy ?? "-"}</td>
                <td className="p-2">
                  {status === "PENDING" && canAct ? (
                    <div className="flex gap-1">
                      <button
                        type="button"
                        className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
                        onClick={() => {
                          setOpen(row);
                        }}
                      >
                        {labels.open}
                      </button>
                      <button
                        type="button"
                        disabled={allow.isPending}
                        className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-40"
                        onClick={() => {
                          allow.mutate(row.interceptionId);
                        }}
                      >
                        {labels.accept}
                      </button>
                      <button
                        type="button"
                        disabled={cancel.isPending}
                        className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-40"
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
        <p role="alert" className="text-sm text-red-700">
          {cancel.error.message}
        </p>
      ) : null}
      {allow.error ? (
        <p role="alert" className="text-sm text-red-700">
          {allow.error.message}
        </p>
      ) : null}

      <p className="text-sm text-slate-500">
        All three actions go through the operator API. A held <strong>request</strong>{" "}
        transitions and the resume bridge signals the waiting workflow separately, so the
        queue may show <code>ANSWERED</code> a moment before the work resumes. A held{" "}
        <strong>response</strong> has a caller blocked on it right now: the moment you
        decide, that caller continues. An edit is not trusted more than the model&apos;s
        own reply -- it goes through the same schema parse and the same output safety
        check, and a malformed edit fails validation exactly as a malformed completion
        does.
      </p>
      <p className="text-sm text-slate-500">
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
    return <p className="text-sm text-slate-500">Loading...</p>;
  }
  const error = routes.error ?? tasks.error;
  if (error) return <p className="text-sm text-red-700">{error.message}</p>;

  return (
    <div className="flex flex-col gap-4">
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="p-2">Route</th>
              <th className="p-2">Provider</th>
              <th className="p-2">Model</th>
              <th className="p-2">Tier</th>
              <th className="p-2">Circuit</th>
              <th className="p-2">Active</th>
              <th className="p-2">Req/min</th>
            </tr>
          </thead>
          <tbody>
            {(routes.data ?? []).map((route) => (
              <tr key={route.routeId} className="border-t border-slate-200">
                <td className="p-2 font-mono text-xs">{route.routeId}</td>
                <td className="p-2">{route.provider}</td>
                <td className="p-2">{route.model}</td>
                <td className="p-2">{route.tier}</td>
                <td className="p-2">
                  <span
                    className={
                      route.circuitState === "CLOSED"
                        ? "text-emerald-700"
                        : route.circuitState === "OPEN"
                          ? "text-red-700"
                          : "text-amber-700"
                    }
                  >
                    {route.circuitState}
                  </span>
                  {!route.configured ? (
                    <span className="ml-1 text-xs text-slate-500">unconfigured</span>
                  ) : null}
                </td>
                <td className="p-2">{route.activeRequests}</td>
                <td className="p-2">{route.requestsThisMinute}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="p-2">Task</th>
              <th className="p-2">Tier</th>
              <th className="p-2">Prompt version</th>
              <th className="p-2">Fallback</th>
              <th className="p-2">Escalation</th>
              <th className="p-2">Allowed providers</th>
            </tr>
          </thead>
          <tbody>
            {(tasks.data ?? []).map((task) => (
              <tr key={task.taskId} className="border-t border-slate-200">
                <td className="p-2 font-mono text-xs">{task.taskId}</td>
                <td className="p-2">{task.tier}</td>
                <td className="p-2">{task.promptVersion}</td>
                <td className="p-2">{task.fallbackStrategy}</td>
                <td className="p-2">{task.allowTierEscalation ? "yes" : "no"}</td>
                <td className="p-2">{task.allowedProviders.join(", ")}</td>
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
    <section className="flex flex-col gap-3 rounded-lg border border-slate-300 bg-white p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">
          {isResponse ? "Review" : "Respond to"}{" "}
          <code className="font-mono text-xs">{interceptionId}</code>
        </h3>
        <button type="button" className="text-xs text-slate-500 hover:underline" onClick={onClose}>
          Close
        </button>
      </div>

      {request.isLoading ? <p className="text-sm text-slate-500">Unsealing request...</p> : null}
      {request.error ? (
        <p className="text-sm text-red-700">{request.error.message}</p>
      ) : null}
      {request.data ? (
        <pre className="max-h-64 overflow-auto rounded bg-slate-50 p-3 text-xs">
          {JSON.stringify(request.data, null, 2)}
        </pre>
      ) : null}

      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium">{isResponse ? "The response" : "Your answer"}</span>
        <textarea
          className="min-h-24 rounded border border-slate-300 p-2 text-sm"
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

      {error ? <p className="text-sm text-red-700">{error}</p> : null}

      <div className="flex items-center gap-2">
        <button
          type="button"
          className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-40"
          disabled={submitting || text.trim().length === 0 || !request.data}
          onClick={() => void submit()}
        >
          {submitting ? "Submitting..." : labels.submit}
        </button>
        <span className="text-xs text-slate-500">
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
