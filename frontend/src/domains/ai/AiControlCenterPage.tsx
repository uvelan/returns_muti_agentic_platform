import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import {
  aiControlCenterApi,
  type AIUsageAttemptView,
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
 * Generate Candidate, Replay Same/Alternate Route and Release still have no
 * route and are still named as unavailable rather than rendered as buttons that
 * would 404.
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
        Estimated cost: {(s.estimatedCostMicrousd / 1_000_000).toFixed(4)} USD.
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
            <Field label="Request digest" value={selected.requestDigest} mono />
            <Field label="Response digest" value={selected.responseDigest ?? "-"} mono />
            <p className="mt-2 text-xs text-slate-500">
              Digests, not bodies. Prompt and response payloads are deliberately not
              served by this surface.
            </p>
          </dl>
        )}
      </aside>
    </div>
  );
}

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

function InterceptionsTab({ canRead }: { canRead: boolean }) {
  const { can } = useCapabilities();
  const canAct = can("ai.interception.act");
  const [openId, setOpenId] = useState<string | null>(null);
  const interceptions = useQuery({
    queryKey: ["ai", "interceptions"],
    queryFn: aiControlCenterApi.listInterceptions,
    enabled: canRead,
  });
  const onOpen = setOpenId;
  const cancel = useMutation({
    mutationFn: (interceptionId: string) => aiControlCenterApi.cancelInterception(interceptionId),
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

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {/* The four statuses `InterceptionStatus` actually has. This counted
            CLAIMED and RESPONDED, which the backend never emits -- both tiles
            read zero in every deployment, which is indistinguishable from a
            quiet queue. */}
        <Stat label="Pending" value={byStatus("PENDING")} />
        <Stat label="Answered" value={byStatus("ANSWERED")} />
        <Stat label="Cancelled" value={byStatus("CANCELLED")} />
        <Stat label="Expired" value={byStatus("EXPIRED")} />
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="p-2">Interception</th>
              <th className="p-2">Status</th>
              <th className="p-2">Task</th>
              <th className="p-2">Expires</th>
              <th className="p-2">Answered by</th>
              <th className="p-2" />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-3 text-slate-600">No interceptions.</td>
              </tr>
            ) : null}
            {rows.map((row) => (
              <tr key={row.interceptionId} className="border-t border-slate-200">
                <td className="p-2 font-mono text-xs">{row.interceptionId}</td>
                <td className="p-2">{row.status}</td>
                <td className="p-2">{row.taskId}</td>
                <td className="p-2">{row.expiresAt}</td>
                {/* A human answer must never read as a model's. `answeredBy` is
                    the operator's own subject, recorded by the backend. */}
                <td className="p-2">{row.answeredBy ?? "-"}</td>
                <td className="p-2">
                  {row.status.toUpperCase() === "PENDING" && canAct ? (
                    <div className="flex gap-1">
                      <button
                        type="button"
                        className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
                        onClick={() => {
                          onOpen(row.interceptionId);
                        }}
                      >
                        Respond manually
                      </button>
                      <button
                        type="button"
                        disabled={cancel.isPending}
                        className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-40"
                        onClick={() => {
                          cancel.mutate(row.interceptionId);
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {openId !== null ? (
        <ManualResponder
          interceptionId={openId}
          onClose={() => {
            setOpenId(null);
          }}
          onAnswered={() => {
            setOpenId(null);
            void interceptions.refetch();
          }}
        />
      ) : null}

      {cancel.error ? (
        <p role="alert" className="text-sm text-red-700">
          {cancel.error.message}
        </p>
      ) : null}

      <p className="text-sm text-slate-500">
        Respond Manually and Cancel both go through the operator API. Answering or
        cancelling transitions the interception, and the resume bridge signals the waiting
        workflow separately, so the queue may show <code>ANSWERED</code> a moment before
        the work resumes.
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
 * Unseal one held request and answer it.
 *
 * The prompt is fetched only when this opens, never with the queue: it is
 * sealed at rest because it can carry rows read out of a customer's database,
 * and decrypting every pending prompt to render a list would defeat that.
 *
 * Submitting is disabled while the request is still loading. Answering a prompt
 * you have not seen is exactly the failure a manual path exists to prevent, and
 * the backend cannot tell the difference.
 */
function ManualResponder({
  interceptionId,
  onClose,
  onAnswered,
}: {
  interceptionId: string;
  onClose: () => void;
  onAnswered: () => void;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const request = useQuery({
    queryKey: ["ai", "interception", interceptionId, "request"],
    queryFn: () => aiControlCenterApi.readInterceptionRequest(interceptionId),
  });

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
          Respond to <code className="font-mono text-xs">{interceptionId}</code>
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
        <span className="font-medium">Your answer</span>
        <textarea
          className="min-h-24 rounded border border-slate-300 p-2 text-sm"
          value={text}
          onChange={(event) => {
            setText(event.target.value);
          }}
          placeholder="Answer as the model would have, in the shape the task expects."
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
          {submitting ? "Submitting..." : "Submit answer"}
        </button>
        <span className="text-xs text-slate-500">
          Recorded as your own subject, never as a model response.
        </span>
      </div>
    </section>
  );
}
