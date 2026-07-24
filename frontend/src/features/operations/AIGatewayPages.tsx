/* eslint-disable react-hooks/set-state-in-effect */
/* eslint-disable @typescript-eslint/restrict-template-expressions */
/* eslint-disable @typescript-eslint/no-unnecessary-condition */
/* eslint-disable @typescript-eslint/no-deprecated */
import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "wouter";
import { ArrowRight, Bot, Save, ShieldAlert, TestTube2 } from "lucide-react";

import {
  getAISettings,
  compareAI,
  getAITrace,
  interceptAI,
  listAITraces,
  replayAI,
  simulateAI,
  updateAISettings,
} from "../../api/operations";
import type { AIDecision, AITrace } from "../../contracts/operations";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { LoadingState } from "../../components/LoadingState";
import { PageHeader } from "../../components/PageHeader";
import { useToast } from "../../components/ToastProvider";
import {
  formatDate,
  inputClass,
  JsonBlock,
  KeyValue,
  Metric,
  Panel,
  primaryButton,
  secondaryButton,
  ToneBadge,
} from "./shared";

const aiKeys = {
  list: ["ai-traces"] as const,
  detail: (id: string) => ["ai-traces", id] as const,
  settings: ["ai-settings"] as const,
};

export function AIRequestsPage() {
  const [status, setStatus] = useState("");
  const query = useQuery({
    queryKey: [...aiKeys.list, status],
    queryFn: ({ signal }) => listAITraces(status || undefined, signal),
    refetchInterval: 4_000,
  });
  return (
    <div>
      <PageHeader title="AI Requests" description="Redacted prompts, provider dispatch, response validation, token usage, and persisted decisions." />
      <div className="mb-5 max-w-sm"><label className="text-sm font-medium text-slate-700">Status<select className={inputClass} value={status} onChange={(event) => { setStatus(event.target.value); }}><option value="">All statuses</option>{["INTERCEPTION_PENDING", "DISPATCHED", "DECISION_PERSISTED", "MANUAL_OVERRIDE", "TIMEOUT", "PROVIDER_UNAVAILABLE", "RESPONSE_INVALID", "CANCELLED"].map((value) => <option key={value}>{value}</option>)}</select></label></div>
      {query.isLoading && <LoadingState message="Loading AI requests..." />}
      {query.isError && <ErrorState message={query.error.message} />}
      {query.data?.length === 0 && <EmptyState title="No AI requests" description="Submit a return or run the simulator." />}
      {query.data && query.data.length > 0 && <Panel><div className="overflow-x-auto"><table className="min-w-full divide-y divide-slate-200 text-sm"><thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500"><tr><th className="px-3 py-3">Created</th><th className="px-3 py-3">Status</th><th className="px-3 py-3">Provider / model</th><th className="px-3 py-3">Decision</th><th className="px-3 py-3">Latency</th><th className="px-3 py-3">Tokens</th><th className="px-3 py-3" /></tr></thead><tbody className="divide-y divide-slate-100">{query.data.map((trace) => <tr key={trace.id}><td className="px-3 py-3 text-slate-500">{formatDate(trace.createdAt)}</td><td className="px-3 py-3"><ToneBadge value={trace.status} /></td><td className="px-3 py-3 text-slate-700">{trace.provider ?? "—"}<span className="block text-xs text-slate-500">{trace.model ?? "—"}</span></td><td className="px-3 py-3">{trace.decision ? <ToneBadge value={trace.decision} /> : "—"}</td><td className="px-3 py-3">{trace.latencyMs === null ? "—" : `${trace.latencyMs} ms`}</td><td className="px-3 py-3">{trace.totalTokens ?? "—"}</td><td className="px-3 py-3 text-right"><Link className="inline-flex items-center gap-1 font-medium text-slate-700" href={`/ai-gateway/requests/${trace.id}`}>Inspect <ArrowRight size={14} /></Link></td></tr>)}</tbody></table></div></Panel>}
    </div>
  );
}

export function AIRequestDetailPage() {
  const params = useParams<{ requestId: string }>();
  const requestId = params.requestId ?? "";
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const traceQuery = useQuery({ queryKey: aiKeys.detail(requestId), queryFn: ({ signal }) => getAITrace(requestId, signal), enabled: Boolean(requestId), refetchInterval: 4_000 });
  const [action, setAction] = useState<"APPROVE" | "REJECT" | "REVIEW_REQUIRED" | "EDIT_AND_DISPATCH" | "CANCEL">("APPROVE");
  const [reason, setReason] = useState("Operator reviewed the redacted request and policy evidence.");
  const [editedSystemPrompt, setEditedSystemPrompt] = useState("");
  const [replayProvider, setReplayProvider] = useState("GOOGLE");
  const [comparison, setComparison] = useState<readonly AITrace[]>([]);
  useEffect(() => { if (traceQuery.data && !editedSystemPrompt) setEditedSystemPrompt(traceQuery.data.systemPrompt); }, [editedSystemPrompt, traceQuery.data]);
  const mutation = useMutation({
    mutationFn: interceptAI,
    onSuccess: (trace) => {
      queryClient.setQueryData(aiKeys.detail(trace.id), trace);
      void queryClient.invalidateQueries({ queryKey: aiKeys.list });
      toast({ title: "AI interception committed", description: trace.status, type: "success" });
    },
  });
  const replay = useMutation({
    mutationFn: replayAI,
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: aiKeys.list });
      toast({ title: "AI replay persisted", description: `Trace ${result.id}`, type: "success" });
    },
  });
  const compare = useMutation({
    mutationFn: compareAI,
    onSuccess: (results) => {
      setComparison(results);
      void queryClient.invalidateQueries({ queryKey: aiKeys.list });
    },
  });
  if (traceQuery.isLoading) return <LoadingState message="Loading AI trace..." />;
  if (traceQuery.isError || !traceQuery.data) return <ErrorState message={traceQuery.error?.message ?? "AI request not found"} />;
  const trace = traceQuery.data;
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    mutation.mutate({ traceId: trace.id, action, reason, expectedVersion: trace.version, editedSystemPrompt: action === "EDIT_AND_DISPATCH" ? editedSystemPrompt : undefined });
  }
  return (
    <div>
      <PageHeader title="AI Request Inspector" description={`Trace ${trace.id}`}>
        {trace.sessionId && <Link className={secondaryButton} href={`/support/returns/${trace.sessionId}`}>Open return</Link>}
      </PageHeader>
      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><Metric label="Status" value={<ToneBadge value={trace.status} />} /><Metric label="Provider" value={trace.provider ?? "—"} /><Metric label="Latency" value={trace.latencyMs === null ? "—" : `${trace.latencyMs} ms`} /><Metric label="Tokens" value={trace.totalTokens ?? "—"} /></div>
      <Panel title="Replay and model comparison" className="mb-6"><div className="flex flex-wrap items-end gap-3"><label className="text-sm font-medium text-slate-700">Provider<select className={inputClass} value={replayProvider} onChange={(event) => { setReplayProvider(event.target.value); }}>{["GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC", "OLLAMA", "SIMULATOR"].map((provider) => <option key={provider}>{provider}</option>)}</select></label><button type="button" className={primaryButton} disabled={replay.isPending} onClick={() => { replay.mutate({ traceId: trace.id, provider: replayProvider }); }}>Replay with provider</button><button type="button" className={secondaryButton} disabled={compare.isPending} onClick={() => { compare.mutate({ traceId: trace.id, providers: ["GOOGLE", "NVIDIA"] }); }}>Compare Google vs NVIDIA</button></div>{(replay.isError || compare.isError) && <p className="mt-3 text-sm text-red-600">{replay.error?.message ?? compare.error?.message}</p>}{comparison.length > 0 && <div className="mt-4 overflow-x-auto"><table className="min-w-full text-sm"><thead><tr className="border-b text-left text-slate-500"><th className="py-2">Provider</th><th>Model</th><th>Decision</th><th>Latency</th><th>Error</th></tr></thead><tbody>{comparison.map((item) => <tr key={item.id} className="border-b"><td className="py-2">{item.provider}</td><td>{item.model}</td><td>{item.decision ?? "—"}</td><td>{item.latencyMs === null ? "—" : `${item.latencyMs} ms`}</td><td>{item.errorCode ?? "—"}</td></tr>)}</tbody></table></div>}</Panel>
      {mutation.isError && <div className="mb-4"><ErrorState message={mutation.error.message} /></div>}
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-6"><Panel title="Lifecycle and integrity"><dl><KeyValue label="Decision" value={trace.decision ? <ToneBadge value={trace.decision} /> : null} /><KeyValue label="Confidence" value={trace.confidenceMillionths === null ? null : `${(trace.confidenceMillionths / 10_000).toFixed(2)}%`} /><KeyValue label="Prompt version" value={trace.promptVersion} /><KeyValue label="Request digest" value={<code className="text-xs">{trace.requestDigest}</code>} /><KeyValue label="Original digest" value={<code className="text-xs">{trace.originalRequestDigest}</code>} /><KeyValue label="Response digest" value={<code className="text-xs">{trace.responseDigest}</code>} /><KeyValue label="Attempts" value={trace.attempts} /><KeyValue label="Error" value={trace.errorCode} /><KeyValue label="Updated" value={formatDate(trace.updatedAt)} /></dl></Panel><Panel title="Redacted request"><JsonBlock value={trace.redactedInput} /></Panel></div>
        <div className="space-y-6"><Panel title="System prompt"><pre className="whitespace-pre-wrap rounded-lg bg-slate-50 p-4 text-sm text-slate-800">{trace.systemPrompt}</pre></Panel><Panel title="Provider response"><pre className="whitespace-pre-wrap rounded-lg bg-slate-950 p-4 text-sm text-slate-100">{trace.responseText ?? "No response captured."}</pre>{trace.explanation && <p className="mt-3 text-sm text-slate-700">{trace.explanation}</p>}</Panel></div>
      </div>
      {trace.status === "INTERCEPTION_PENDING" && <Panel title="Intercept before provider dispatch" className="mt-6"><form className="grid gap-5 lg:grid-cols-2" onSubmit={submit}><label className="text-sm font-medium text-slate-700">Action<select className={inputClass} value={action} onChange={(event) => { setAction(event.target.value as typeof action); }}>{["APPROVE", "REJECT", "REVIEW_REQUIRED", "EDIT_AND_DISPATCH", "CANCEL"].map((value) => <option key={value}>{value}</option>)}</select></label><label className="text-sm font-medium text-slate-700">Reason<textarea className={inputClass} rows={3} value={reason} onChange={(event) => { setReason(event.target.value); }} required /></label>{action === "EDIT_AND_DISPATCH" && <label className="text-sm font-medium text-slate-700 lg:col-span-2">Edited system prompt<textarea className={inputClass} rows={8} minLength={10} value={editedSystemPrompt} onChange={(event) => { setEditedSystemPrompt(event.target.value); }} required /></label>}<div className="lg:col-span-2"><button className={primaryButton} disabled={mutation.isPending} type="submit"><ShieldAlert size={16} /> {mutation.isPending ? "Committing..." : "Commit interception"}</button></div></form></Panel>}
    </div>
  );
}

export function AISimulatorPage() {
  const [, setLocation] = useLocation();
  const [customerReference, setCustomerReference] = useState("CUST-1001");
  const [orderReference, setOrderReference] = useState("ORD-10001");
  const [reasonCode, setReasonCode] = useState("DAMAGED");
  const [requestedDecision, setRequestedDecision] = useState<AIDecision | "">("");
  const mutation = useMutation({ mutationFn: simulateAI, onSuccess: (trace) => { setLocation(`/ai-gateway/requests/${trace.id}`); } });
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); mutation.mutate({ customerReference, orderReferences: [orderReference], reasonCode, requestedDecision: requestedDecision || null }); }
  return <div><PageHeader title="AI Simulator" description="Development/test-only deterministic gateway execution. Results are persisted as normal traces." />{mutation.isError && <div className="mb-4"><ErrorState message={mutation.error.message} /></div>}<Panel className="max-w-3xl"><form className="grid gap-5 md:grid-cols-2" onSubmit={submit}><label className="text-sm font-medium text-slate-700">Customer<input className={inputClass} value={customerReference} onChange={(event) => { setCustomerReference(event.target.value); }} /></label><label className="text-sm font-medium text-slate-700">Order<input className={inputClass} value={orderReference} onChange={(event) => { setOrderReference(event.target.value); }} /></label><label className="text-sm font-medium text-slate-700">Reason code<input className={inputClass} value={reasonCode} onChange={(event) => { setReasonCode(event.target.value); }} /></label><label className="text-sm font-medium text-slate-700">Forced decision<select className={inputClass} value={requestedDecision} onChange={(event) => { setRequestedDecision(event.target.value as AIDecision | ""); }}><option value="">Gateway default</option><option>APPROVE</option><option>REJECT</option><option>REVIEW_REQUIRED</option></select></label><div className="md:col-span-2"><button className={primaryButton} disabled={mutation.isPending} type="submit"><TestTube2 size={16} /> {mutation.isPending ? "Running..." : "Run simulation"}</button></div></form></Panel></div>;
}

export function AIInterceptionsPage() {
  const traces = useQuery({ queryKey: [...aiKeys.list, "INTERCEPTION_PENDING"], queryFn: ({ signal }) => listAITraces("INTERCEPTION_PENDING", signal), refetchInterval: 3_000 });
  const settings = useQuery({ queryKey: aiKeys.settings, queryFn: ({ signal }) => getAISettings(signal) });
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const update = useMutation({ mutationFn: updateAISettings, onSuccess: (value) => { queryClient.setQueryData(aiKeys.settings, value); toast({ title: `Interception mode ${value.interceptMode ? "enabled" : "disabled"}`, type: "success" }); } });
  return <div><PageHeader title="AI Interceptions" description="Pause requests before provider dispatch and route them through operator inspection." />{settings.isError && <div className="mb-4"><ErrorState message={settings.error.message} /></div>}{settings.data && <Panel className="mb-6"><div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center"><div><p className="font-medium text-slate-900">Global interception mode</p><p className="text-sm text-slate-500">Provider order: {settings.data.providerOrder.join(" → ")}</p></div><button className={primaryButton} disabled={update.isPending} onClick={() => { update.mutate({ interceptMode: !settings.data.interceptMode, providerOrder: [...settings.data.providerOrder], expectedVersion: settings.data.version }); }} type="button"><Save size={16} /> {settings.data.interceptMode ? "Disable" : "Enable"}</button></div></Panel>}{traces.isLoading && <LoadingState message="Loading intercepted requests..." />}{traces.isError && <ErrorState message={traces.error.message} />}{traces.data?.length === 0 && <EmptyState title="No requests awaiting interception" description="Enable interception mode, then submit a return." />}{traces.data && traces.data.length > 0 && <div className="grid gap-4">{traces.data.map((trace) => <Panel key={trace.id}><div className="flex flex-col justify-between gap-4 md:flex-row md:items-center"><div><div className="flex items-center gap-2"><Bot size={18} /><ToneBadge value={trace.status} /><span className="text-sm text-slate-500">{formatDate(trace.createdAt)}</span></div><p className="mt-2 text-sm text-slate-700">{trace.sessionId ? `Return ${trace.sessionId}` : "Standalone request"}</p><code className="mt-1 block text-xs text-slate-500">{trace.requestDigest}</code></div><Link className={primaryButton} href={`/ai-gateway/requests/${trace.id}`}>Inspect and decide</Link></div></Panel>)}</div>}</div>;
}
