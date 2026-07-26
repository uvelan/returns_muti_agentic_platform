/* eslint-disable @typescript-eslint/restrict-template-expressions */
/* eslint-disable @typescript-eslint/no-unnecessary-condition */
/* eslint-disable @typescript-eslint/no-deprecated */
import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "wouter";
import { ArrowRight, Headphones, PlayCircle } from "lucide-react";

import { getAITrace, getReturn, getSupportCase, listEvents, listReturns, listSupportCases, operateSupportCase } from "../../api/operations";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { LoadingState } from "../../components/LoadingState";
import { PageHeader } from "../../components/PageHeader";
import { useToast } from "../../components/ToastProvider";
import { formatDate, inputClass, Metric, Panel, primaryButton, ToneBadge } from "./shared";

const supportCasesKey = ["support-cases"] as const;
const supportReturnsKey = ["support-returns"] as const;

export function SupportReturnsPage() {
  const query = useQuery({
    queryKey: supportReturnsKey,
    queryFn: ({ signal }) => listReturns(undefined, signal),
    refetchInterval: 5_000,
  });
  return (
    <div>
      <PageHeader title="Support Returns" description="Operational view across all active and completed return sessions." />
      {query.isLoading && <LoadingState message="Loading operational returns..." />}
      {query.isError && <ErrorState message={query.error.message} />}
      {query.data?.length === 0 && <EmptyState title="No returns" description="No customer return sessions have been submitted." />}
      {query.data && query.data.length > 0 && (
        <Panel>
          <div className="overflow-x-auto"><table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500"><tr><th className="px-3 py-3">Session</th><th className="px-3 py-3">Order</th><th className="px-3 py-3">Status</th><th className="px-3 py-3">Stage</th><th className="px-3 py-3">Failure</th><th className="px-3 py-3" /></tr></thead>
            <tbody className="divide-y divide-slate-100">{query.data.map((session) => <tr key={session.id}>
              <td className="px-3 py-3 font-mono text-xs text-slate-600">{session.id}</td><td className="px-3 py-3 font-medium text-slate-900">{session.orderReference}</td><td className="px-3 py-3"><ToneBadge value={session.status} /></td><td className="px-3 py-3 text-slate-600">{session.currentStage} · {session.progressPercentage}%</td><td className="px-3 py-3 text-red-700">{session.failureCode ?? "—"}</td><td className="px-3 py-3 text-right"><Link className="inline-flex items-center gap-1 font-medium text-slate-700 hover:text-slate-950" href={`/support/returns/${session.id}`}>Inspect <ArrowRight size={14} /></Link></td>
            </tr>)}</tbody>
          </table></div>
        </Panel>
      )}
    </div>
  );
}

export function SupportReviewQueuePage() {
  const [, setLocation] = useLocation();
  const query = useQuery({
    queryKey: supportCasesKey,
    queryFn: ({ signal }) => listSupportCases(undefined, signal),
    refetchInterval: 4_000,
  });
  const active = query.data?.filter((item) => item.status === "OPEN" || item.status === "ASSIGNED") ?? [];
  return (
    <div>
      <PageHeader title="Support Review Queue" description="Human review cases created by policy, AI, and workflow exceptions." />
      {query.isLoading && <LoadingState message="Loading support queue..." />}
      {query.isError && <ErrorState message={query.error.message} />}
      {!query.isLoading && active.length === 0 && <EmptyState title="Queue is clear" description="No open support cases require action." />}
      <div className="grid gap-4">
        {active.map((item) => (
          <Panel key={item.id}>
            <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
              <div><div className="flex flex-wrap items-center gap-2"><ToneBadge value={item.priority} /><ToneBadge value={item.status} /><span className="text-sm font-medium text-slate-900">{item.caseType}</span></div><p className="mt-2 text-sm text-slate-700">{item.reason}</p><p className="mt-2 text-xs text-slate-500">Created {formatDate(item.createdAt)} · SLA due {formatDate(item.slaDueAt)} · Assigned {item.assignedTo ?? "unassigned"}</p>{item.slaBreached && <p className="mt-1 text-xs font-semibold text-red-700">SLA breached</p>}</div>
              <div className="flex flex-wrap gap-2"><Link className={primaryButton} href={`/support/returns/${item.sessionId}`}>View return</Link><button className={primaryButton} type="button" onClick={() => { setLocation(`/support/operations?caseId=${encodeURIComponent(item.id)}`); }}>Operate</button></div>
            </div>
          </Panel>
        ))}
      </div>
    </div>
  );
}

export function SupportOperationsPage() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const query = useQuery({ queryKey: supportCasesKey, queryFn: ({ signal }) => listSupportCases(undefined, signal), refetchInterval: 5_000 });
  const queryCaseId = useMemo(() => new URLSearchParams(window.location.search).get("caseId") ?? "", []);
  const [caseId, setCaseId] = useState(queryCaseId);
  const [operation, setOperation] = useState<"ASSIGN" | "APPROVE" | "REJECT" | "RETRY" | "CANCEL" | "RESUME">("ASSIGN");
  const [reason, setReason] = useState("Support operator reviewed the available evidence.");
  const [assignee, setAssignee] = useState("support-agent");
  const actionableCases = query.data?.filter((item) => item.status === "OPEN" || item.status === "ASSIGNED") ?? [];
  const selected = actionableCases.find((item) => item.id === caseId);
  const mutation = useMutation({
    mutationFn: operateSupportCase,
    onSuccess: (updated) => {
      toast({ title: `Support ${operation.toLowerCase()} completed`, description: updated.id, type: "success" });
      void queryClient.invalidateQueries({ queryKey: supportCasesKey });
      void queryClient.invalidateQueries({ queryKey: supportReturnsKey });
    },
  });
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    mutation.mutate({ caseId: selected.id, operation, reason, assignee: operation === "ASSIGN" ? assignee : undefined, expectedVersion: selected.version });
  }
  const cases = query.data ?? [];
  const activeCount = actionableCases.length;
  const resolvedCount = cases.filter((item) => item.status === "RESOLVED").length;

  return (
    <div>
      <PageHeader title="Support Operations" description="Version-checked operator commands are committed atomically with return, AI, event, and audit updates." />
      <div className="mb-6 grid gap-4 sm:grid-cols-3"><Metric label="Open or assigned" value={activeCount} /><Metric label="Resolved" value={resolvedCount} /><Metric label="Total cases" value={cases.length} /></div>
      {query.isLoading && <LoadingState message="Loading cases..." />}
      {query.isError && <ErrorState message={query.error.message} />}
      {mutation.isError && <div className="mb-4"><ErrorState message={mutation.error.message} /></div>}
      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Execute operation">
          <form className="space-y-5" onSubmit={submit}>
            <label className="block text-sm font-medium text-slate-700">Case<select className={inputClass} value={caseId} onChange={(event) => { setCaseId(event.target.value); }} required><option value="">Select a case</option>{actionableCases.map((item) => <option key={item.id} value={item.id}>{item.priority} · {item.caseType} · {item.id}</option>)}</select></label>
            <label className="block text-sm font-medium text-slate-700">Operation<select className={inputClass} value={operation} onChange={(event) => { setOperation(event.target.value as typeof operation); }}>{["ASSIGN", "APPROVE", "REJECT", "RETRY", "RESUME", "CANCEL"].map((value) => <option key={value}>{value}</option>)}</select></label>
            {operation === "ASSIGN" && <label className="block text-sm font-medium text-slate-700">Assignee<input className={inputClass} value={assignee} onChange={(event) => { setAssignee(event.target.value); }} required /></label>}
            <label className="block text-sm font-medium text-slate-700">Reason<textarea className={inputClass} rows={4} value={reason} onChange={(event) => { setReason(event.target.value); }} minLength={3} required /></label>
            <button className={primaryButton} disabled={!selected || mutation.isPending} type="submit"><PlayCircle size={16} /> {mutation.isPending ? "Executing..." : "Execute command"}</button>
          </form>
        </Panel>
        <Panel title="Selected case evidence">
          {!selected && <p className="text-sm text-slate-500">Select a case to inspect its version and evidence.</p>}
          {selected && <dl className="space-y-2 text-sm"><div><dt className="text-slate-500">Status</dt><dd><ToneBadge value={selected.status} /></dd></div><div><dt className="text-slate-500">Session</dt><dd className="font-mono text-xs">{selected.sessionId}</dd></div><div><dt className="text-slate-500">Current version</dt><dd>{selected.version}</dd></div><div><dt className="text-slate-500">Reason</dt><dd>{selected.reason}</dd></div><div><dt className="text-slate-500">Resolution</dt><dd>{selected.resolution ?? "—"}</dd></div><Link className="mt-4 inline-flex items-center gap-2 font-medium text-slate-700" href={`/support/returns/${selected.sessionId}`}><Headphones size={16} /> Inspect return timeline</Link></dl>}
        </Panel>
      </div>
    </div>
  );
}
export function SupportReturnDetailPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId ?? "";
  const sessionQuery = useQuery({
    queryKey: ["support-return", sessionId],
    queryFn: ({ signal }) => getReturn(sessionId, signal),
    enabled: Boolean(sessionId),
    refetchInterval: 3_000,
  });
  const eventsQuery = useQuery({
    queryKey: ["support-return-events", sessionId],
    queryFn: ({ signal }) => listEvents(sessionId, signal),
    enabled: Boolean(sessionId),
    refetchInterval: 5_000,
  });
  const caseId = sessionQuery.data?.supportCaseId ?? "";
  const traceId = sessionQuery.data?.aiRequestId ?? "";
  const caseQuery = useQuery({
    queryKey: ["support-case", caseId],
    queryFn: ({ signal }) => getSupportCase(caseId, signal),
    enabled: Boolean(caseId),
    refetchInterval: 3_000,
  });
  const traceQuery = useQuery({
    queryKey: ["support-ai-trace", traceId],
    queryFn: ({ signal }) => getAITrace(traceId, signal),
    enabled: Boolean(traceId),
    refetchInterval: 3_000,
  });

  if (sessionQuery.isLoading) return <LoadingState message="Loading support evidence..." />;
  if (sessionQuery.isError || !sessionQuery.data) return <ErrorState message={sessionQuery.error?.message ?? "Return not found"} />;
  const session = sessionQuery.data;
  const supportCase = caseQuery.data;
  const trace = traceQuery.data;

  return (
    <div>
      <PageHeader title={`Support Return ${session.orderReference}`} description={`Session ${session.id}`}>
        {supportCase && (supportCase.status === "OPEN" || supportCase.status === "ASSIGNED") && (
          <Link className={primaryButton} href={`/support/operations?caseId=${encodeURIComponent(supportCase.id)}`}>Operate case</Link>
        )}
      </PageHeader>
      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Return status" value={<ToneBadge value={session.status} />} />
        <Metric label="Stage" value={session.currentStage} />
        <Metric label="Progress" value={`${session.progressPercentage}%`} />
        <Metric label="SLA" value={supportCase ? <ToneBadge value={supportCase.slaBreached ? "BREACHED" : "WITHIN_SLA"} /> : "No active case"} />
      </div>
      <div className="grid gap-6 xl:grid-cols-3">
        <Panel title="Return evidence">
          <dl className="space-y-3 text-sm">
            <div><dt className="text-slate-500">Customer</dt><dd>{session.customerReference}</dd></div>
            <div><dt className="text-slate-500">Items</dt><dd>{session.itemReferences.join(", ")}</dd></div>
            <div><dt className="text-slate-500">Reason</dt><dd>{session.reasonCode}</dd></div>
            <div><dt className="text-slate-500">RMA</dt><dd>{session.returnReference ?? "—"}</dd></div>
            <div><dt className="text-slate-500">Tracking</dt><dd>{session.trackingReference ?? "—"}</dd></div>
            <div><dt className="text-slate-500">Failure</dt><dd className="text-red-700">{session.failureCode ?? "—"}</dd></div>
          </dl>
        </Panel>
        <Panel title="Support case">
          {!caseId && <p className="text-sm text-slate-500">No support case is associated with this return.</p>}
          {caseQuery.isError && <ErrorState message={caseQuery.error.message} />}
          {supportCase && <dl className="space-y-3 text-sm">
            <div><dt className="text-slate-500">Type</dt><dd>{supportCase.caseType}</dd></div>
            <div><dt className="text-slate-500">Status</dt><dd><ToneBadge value={supportCase.status} /></dd></div>
            <div><dt className="text-slate-500">Priority</dt><dd><ToneBadge value={supportCase.priority} /></dd></div>
            <div><dt className="text-slate-500">SLA due</dt><dd>{formatDate(supportCase.slaDueAt)}</dd></div>
            <div><dt className="text-slate-500">Assigned</dt><dd>{supportCase.assignedTo ?? "Unassigned"}</dd></div>
            <div><dt className="text-slate-500">Reason</dt><dd>{supportCase.reason}</dd></div>
            <div><dt className="text-slate-500">Resolution</dt><dd>{supportCase.resolution ?? "—"}</dd></div>
          </dl>}
        </Panel>
        <Panel title="AI evidence">
          {!traceId && <p className="text-sm text-slate-500">No AI request has been created yet.</p>}
          {traceQuery.isError && <ErrorState message={traceQuery.error.message} />}
          {trace && <dl className="space-y-3 text-sm">
            <div><dt className="text-slate-500">Status</dt><dd><ToneBadge value={trace.status} /></dd></div>
            <div><dt className="text-slate-500">Provider/model</dt><dd>{trace.provider ?? "—"} / {trace.model ?? "—"}</dd></div>
            <div><dt className="text-slate-500">Decision</dt><dd>{trace.decision ? <ToneBadge value={trace.decision} /> : "—"}</dd></div>
            <div><dt className="text-slate-500">Latency</dt><dd>{trace.latencyMs === null ? "—" : `${trace.latencyMs} ms`}</dd></div>
            <div><dt className="text-slate-500">Request digest</dt><dd className="break-all font-mono text-xs">{trace.requestDigest}</dd></div>
            <Link className="font-medium text-slate-800 underline" href={`/ai-gateway/requests/${trace.id}`}>Open AI inspector</Link>
          </dl>}
        </Panel>
      </div>
      <Panel className="mt-6" title="Workflow timeline">
        {eventsQuery.isLoading && <LoadingState message="Loading timeline..." />}
        {eventsQuery.isError && <ErrorState message={eventsQuery.error.message} />}
        {eventsQuery.data && eventsQuery.data.length > 0 ? (
          <ol className="space-y-4">{eventsQuery.data.map((event) => <li key={event.id} className="border-l-2 border-slate-300 pl-4"><div className="flex flex-wrap items-center gap-2"><span className="font-medium text-slate-900">{event.eventType}</span><span className="text-xs text-slate-500">#{event.sequence} · {formatDate(event.occurredAt)}</span></div><p className="mt-1 text-xs text-slate-500">{event.actorType} / {event.actorId}</p></li>)}</ol>
        ) : <p className="text-sm text-slate-500">No events recorded.</p>}
      </Panel>
    </div>
  );
}
