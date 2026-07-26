import { useState, type SyntheticEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useParams } from "wouter";
import { RefreshCw, ShieldCheck } from "lucide-react";

import {
  getAIMetricsSummary,
  getProductionArtifacts,
  getReturnAgentConfiguration,
  listAIMetrics,
  listAIRoutes,
  listAITasks,
  listIntegrationOutbox,
  listReturns,
  listReturnSupportWorkItems,
  testAISafety,
  type OperationalRecord,
} from "../../api/operations";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { LoadingState } from "../../components/LoadingState";
import { PageHeader } from "../../components/PageHeader";
import { formatDate, inputClass, JsonBlock, Metric, Panel, primaryButton, secondaryButton, ToneBadge } from "./shared";

function RetryButton({ retry }: { retry: () => void }) {
  return <button className={secondaryButton} type="button" onClick={retry}><RefreshCw size={15} /> Retry</button>;
}

function display(value: unknown, fallback = "—"): string {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
    ? String(value)
    : fallback;
}

function RemoteRecords({
  title,
  description,
  empty,
  query,
}: {
  title: string;
  description: string;
  empty: string;
  query: ReturnType<typeof useQuery<readonly OperationalRecord[], Error>>;
}) {
  return (
    <div>
      <PageHeader title={title} description={description}>
        <RetryButton retry={() => { void query.refetch(); }} />
      </PageHeader>
      {query.isLoading && <LoadingState message={`Loading ${title.toLowerCase()}...`} />}
      {query.isError && <ErrorState message={query.error.message} action={<RetryButton retry={() => { void query.refetch(); }} />} />}
      {query.data?.length === 0 && <EmptyState title={empty} description="The authorized API returned no records." />}
      {query.data && query.data.length > 0 && (
        <div className="grid gap-4">
          {query.data.map((record, index) => (
            <Panel key={display(record.id ?? record.taskId ?? record.routeId, `record-${index.toString()}`)}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="font-medium text-slate-900">
                    {display(
                      record.name ?? record.taskId ?? record.routeId ?? record.topic ?? record.id,
                      `Record ${String(index + 1)}`,
                    )}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    {display(
                      record.updatedAt ?? record.createdAt ?? record.promptVersion,
                      "Current configuration",
                    )}
                  </p>
                </div>
                {record.status !== undefined && <ToneBadge value={display(record.status, "UNKNOWN")} />}
              </div>
              <div className="mt-4"><JsonBlock value={record} /></div>
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}

function ReturnQueue({
  title,
  description,
  empty,
}: {
  title: string;
  description: string;
  empty: string;
}) {
  const query = useQuery({ queryKey: ["returns", title], queryFn: ({ signal }) => listReturns(undefined, signal) });
  return (
    <div>
      <PageHeader title={title} description={description}>
        <RetryButton retry={() => { void query.refetch(); }} />
      </PageHeader>
      {query.isLoading && <LoadingState message="Loading return work..." />}
      {query.isError && <ErrorState message={query.error.message} action={<RetryButton retry={() => { void query.refetch(); }} />} />}
      {query.data?.length === 0 && <EmptyState title={empty} description="There are no authorized return sessions to display." />}
      {query.data && query.data.length > 0 && (
        <div className="grid gap-4">
          {query.data.map((item) => (
            <Panel key={item.id}>
              <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
                <div>
                  <div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold text-slate-900">{item.id}</h2><ToneBadge value={item.status} /></div>
                  <p className="mt-2 text-sm text-slate-600">{item.orderReference} · {item.reasonCode}</p>
                  <p className="mt-1 text-xs text-slate-500">Updated {formatDate(item.updatedAt)}</p>
                </div>
                <Link className={secondaryButton} href={`/operations/returns/${item.id}`}>Open operational record</Link>
              </div>
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}

export function OperationsReturnDetailPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;
  const query = useQuery({
    queryKey: ["production-artifacts", sessionId],
    queryFn: ({ signal }) => getProductionArtifacts(sessionId, signal),
    enabled: Boolean(sessionId),
  });
  return (
    <div>
      <PageHeader title="Operations Return Detail" description={`Authoritative and projected evidence for ${sessionId}.`}>
        <RetryButton retry={() => { void query.refetch(); }} />
      </PageHeader>
      {query.isLoading && <LoadingState message="Loading operational evidence..." />}
      {query.isError && <ErrorState message={query.error.message} action={<RetryButton retry={() => { void query.refetch(); }} />} />}
      {query.data && <Panel title="Return evidence"><JsonBlock value={query.data} /></Panel>}
    </div>
  );
}

export function ReturnAgentsPage() {
  const query = useQuery({ queryKey: ["return-agent-configuration"], queryFn: ({ signal }) => getReturnAgentConfiguration(signal) });
  return <div><PageHeader title="Return Agents" description="Typed responsibilities, prohibited actions, workflow policy, and active configuration." />{query.isLoading && <LoadingState message="Loading return-agent configuration..." />}{query.isError && <ErrorState message={query.error.message} action={<RetryButton retry={() => { void query.refetch(); }} />} />}{query.data && <Panel><JsonBlock value={query.data} /></Panel>}</div>;
}

export function ReturnSupportWorkbenchPage() {
  const query = useQuery({ queryKey: ["return-support-work-items"], queryFn: ({ signal }) => listReturnSupportWorkItems(undefined, signal) });
  return <RemoteRecords title="Returns Support Workbench" description="Internal assignment, acknowledgment, clarification, and shared-thread work items." empty="No support work items" query={query} />;
}

export function LogisticsReturnsPage() {
  return <ReturnQueue title="Logistics Returns" description="Parcel and freight setup, pickup coordination, carrier handoff, and exceptions." empty="No logistics work" />;
}

export function WarehouseReturnsPage() {
  return <ReturnQueue title="Warehouse Returns" description="Receipt, license-plate, disposition, bay-placement, and warehouse-processing evidence." empty="No warehouse work" />;
}

export function TrackingReturnsPage() {
  return <ReturnQueue title="Return Tracking" description="Shipment and lifecycle evidence without treating label or tender creation as physical handoff." empty="No returns to track" />;
}

export function IntegrationOutboxPage() {
  const query = useQuery({ queryKey: ["integration-outbox"], queryFn: ({ signal }) => listIntegrationOutbox(undefined, signal), refetchInterval: 5_000 });
  return <RemoteRecords title="Integration Outbox" description="Auditable delivery attempts, retry state, idempotency keys, and safe external references." empty="Outbox is empty" query={query} />;
}

export function AIRoutesPage() {
  const query = useQuery({ queryKey: ["ai-routes"], queryFn: ({ signal }) => listAIRoutes(signal), refetchInterval: 5_000 });
  return <RemoteRecords title="AI Gateway Routes" description="Provider, model, safe credential ID, circuit state, and route health." empty="No AI routes configured" query={query} />;
}

export function AITasksPage() {
  const query = useQuery({ queryKey: ["ai-tasks"], queryFn: ({ signal }) => listAITasks(signal) });
  return <RemoteRecords title="AI Gateway Tasks" description="Fixed task registry, complexity tier, prompt version, schemas, and fallback policy." empty="No AI tasks configured" query={query} />;
}

export function AIMetricsPage() {
  const attempts = useQuery({ queryKey: ["ai-attempt-metrics"], queryFn: ({ signal }) => listAIMetrics(signal), refetchInterval: 5_000 });
  const summary = useQuery({ queryKey: ["ai-attempt-summary"], queryFn: ({ signal }) => getAIMetricsSummary(signal), refetchInterval: 5_000 });
  return <div><PageHeader title="AI Gateway Metrics" description="Durable per-attempt routing, safety, schema, latency, token, fallback, and cost evidence." />{summary.isLoading && <LoadingState message="Loading AI metric summary..." />}{summary.isError && <ErrorState message={summary.error.message} action={<RetryButton retry={() => { void summary.refetch(); }} />} />}{summary.data && <div className="mb-6 grid gap-4 sm:grid-cols-3"><Metric label="Requests" value={display(summary.data.requestCount, "0")} /><Metric label="Attempts" value={display(summary.data.attemptCount, String(attempts.data?.length ?? 0))} /><Metric label="Fallbacks" value={display(summary.data.fallbackCount, "0")} /></div>}<RemoteRecords title="AI Attempts" description="Newest persisted attempts." empty="No AI attempts recorded" query={attempts} /></div>;
}

export function AISafetyPage() {
  const [taskId, setTaskId] = useState("RETURN_ELIGIBILITY_V1");
  const [text, setText] = useState("Customer asks whether this damaged item is eligible for return.");
  const mutation = useMutation({ mutationFn: testAISafety });
  function submit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    mutation.mutate({ taskId, payload: { customerText: text } });
  }
  return (
    <div>
      <PageHeader title="AI Gateway Safety" description="Development/test deterministic prompt-injection and domain-boundary inspection." />
      <Panel className="mb-6">
        <form className="grid gap-4" onSubmit={submit}>
          <label className="text-sm font-medium text-slate-700">Task ID<input className={inputClass} value={taskId} onChange={(event) => { setTaskId(event.target.value); }} /></label>
          <label className="text-sm font-medium text-slate-700">Typed input<textarea className={inputClass} rows={5} value={text} onChange={(event) => { setText(event.target.value); }} /></label>
          <button className={primaryButton} disabled={mutation.isPending} type="submit"><ShieldCheck size={16} /> {mutation.isPending ? "Inspecting..." : "Run safety inspection"}</button>
        </form>
      </Panel>
      {mutation.isError && <ErrorState message={mutation.error.message} />}
      {mutation.data && <Panel title="Deterministic result"><JsonBlock value={mutation.data} /></Panel>}
    </div>
  );
}
