/* eslint-disable @typescript-eslint/restrict-template-expressions */
/* eslint-disable @typescript-eslint/no-unnecessary-condition */
 
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "wouter";
import { ArrowRight, Ban, Plus } from "lucide-react";

import { cancelReturn, getReturn, listEvents, listReturns } from "../../api/operations";
import type { TimelineEvent } from "../../contracts/operations";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { LoadingState } from "../../components/LoadingState";
import { PageHeader } from "../../components/PageHeader";
import { useToast } from "../../components/ToastProvider";
import {
  dangerButton,
  formatDate,
  inputClass,
  KeyValue,
  Metric,
  Panel,
  primaryButton,
  secondaryButton,
  ToneBadge,
  JsonBlock,
} from "./shared";

const returnKeys = {
  all: ["operational-returns"] as const,
  detail: (id: string) => ["operational-returns", id] as const,
  events: (id: string) => ["operational-return-events", id] as const,
};

function mergeEvent(current: readonly TimelineEvent[] | undefined, incoming: TimelineEvent): readonly TimelineEvent[] {
  const bySequence = new Map((current ?? []).map((event) => [event.sequence, event]));
  bySequence.set(incoming.sequence, incoming);
  return [...bySequence.values()].sort((left, right) => left.sequence - right.sequence);
}

export function CustomerReturnsPage() {
  const [status, setStatus] = useState("");
  const query = useQuery({
    queryKey: [...returnKeys.all, status],
    queryFn: ({ signal }) => listReturns(status || undefined, signal),
    refetchInterval: 5_000,
  });

  return (
    <div>
      <PageHeader title="Customer Returns" description="Create and track return requests through the real workflow.">
        <Link href="/associate/returns" className={primaryButton}><Plus size={16} /> Start with assistant</Link>
      </PageHeader>
      <div className="mb-5 flex max-w-xs flex-col">
        <label className="text-sm font-medium text-slate-700" htmlFor="return-status">Status</label>
        <select id="return-status" className={inputClass} value={status} onChange={(event) => { setStatus(event.target.value); }}>
          <option value="">All statuses</option>
          {[
            "QUEUED", "RUNNING", "INTERCEPTION_PENDING", "REVIEW_REQUIRED", "APPROVED", "REJECTED", "COMPLETED", "FAILED", "CANCELLED",
          ].map((value) => <option key={value}>{value}</option>)}
        </select>
      </div>
      {query.isLoading && <LoadingState message="Loading returns..." />}
      {query.isError && <ErrorState message={query.error.message} />}
      {query.data?.length === 0 && <EmptyState title="No returns found" description="Create a return or adjust the status filter." />}
      {query.data && query.data.length > 0 && (
        <Panel>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr><th className="px-3 py-3">Order</th><th className="px-3 py-3">Customer</th><th className="px-3 py-3">Stage</th><th className="px-3 py-3">Status</th><th className="px-3 py-3">Updated</th><th className="px-3 py-3" /></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {query.data.map((session) => (
                  <tr key={session.id} className="hover:bg-slate-50">
                    <td className="px-3 py-3 font-medium text-slate-900">{session.orderReference}</td>
                    <td className="px-3 py-3 text-slate-600">{session.customerReference}</td>
                    <td className="px-3 py-3 text-slate-600">{session.currentStage} · {session.progressPercentage}%</td>
                    <td className="px-3 py-3"><ToneBadge value={session.status} /></td>
                    <td className="px-3 py-3 text-slate-500">{formatDate(session.updatedAt)}</td>
                    <td className="px-3 py-3 text-right"><Link className="inline-flex items-center gap-1 font-medium text-slate-700 hover:text-slate-950" href={`/customer/returns/${session.id}`}>Open <ArrowRight size={14} /></Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}

export function CustomerReturnDetailPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId ?? "";
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const sessionQuery = useQuery({
    queryKey: returnKeys.detail(sessionId),
    queryFn: ({ signal }) => getReturn(sessionId, signal),
    enabled: Boolean(sessionId),
    refetchInterval: 4_000,
  });
  const eventsQuery = useQuery({
    queryKey: returnKeys.events(sessionId),
    queryFn: ({ signal }) => listEvents(sessionId, signal),
    enabled: Boolean(sessionId),
    refetchInterval: 10_000,
  });
  const cancelMutation = useMutation({
    mutationFn: cancelReturn,
    onSuccess: (session) => {
      queryClient.setQueryData(returnKeys.detail(session.id), session);
      toast({ title: "Return cancelled", type: "success" });
    },
  });

  useEffect(() => {
    if (!sessionId) return undefined;
    const source = new EventSource(`/api/v1/returns/${encodeURIComponent(sessionId)}/stream`);
    const receive = (raw: Event) => {
      if (!(raw instanceof MessageEvent) || typeof raw.data !== "string") return;
      try {
        const event = JSON.parse(raw.data) as TimelineEvent;
        queryClient.setQueryData<readonly TimelineEvent[]>(returnKeys.events(sessionId), (current) => mergeEvent(current, event));
        void queryClient.invalidateQueries({ queryKey: returnKeys.detail(sessionId) });
      } catch {
        source.close();
      }
    };
    source.addEventListener("return-event", receive);
    return () => { source.removeEventListener("return-event", receive); source.close(); };
  }, [queryClient, sessionId]);

  if (sessionQuery.isLoading) return <LoadingState message="Loading return..." />;
  if (sessionQuery.isError || !sessionQuery.data) return <ErrorState message={sessionQuery.error?.message ?? "Return not found"} />;
  const session = sessionQuery.data;
  const terminal = ["COMPLETED", "FAILED", "CANCELLED", "REJECTED"].includes(session.status);

  return (
    <div>
      <PageHeader title={`Return ${session.orderReference}`} description={`Session ${session.id}`}>
        {!terminal && <button className={dangerButton} disabled={cancelMutation.isPending} onClick={() => { cancelMutation.mutate(session); }} type="button"><Ban size={16} /> Cancel</button>}
      </PageHeader>
      {cancelMutation.isError && <div className="mb-4"><ErrorState message={cancelMutation.error.message} /></div>}
      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Status" value={<ToneBadge value={session.status} />} />
        <Metric label="Progress" value={`${session.progressPercentage}%`} />
        <Metric label="Stage" value={session.currentStage} />
        <Metric label="Decision" value={session.eligibilityDecision ? <ToneBadge value={session.eligibilityDecision} /> : "Pending"} />
      </div>
      <div className="grid gap-6 lg:grid-cols-3">
        <Panel title="Return details">
          <dl><KeyValue label="Customer" value={session.customerReference} /><KeyValue label="Order" value={session.orderReference} /><KeyValue label="Order line" value={session.itemReferences.join(", ")} /><KeyValue label="Product" value={session.productReferences.join(", ")} /><KeyValue label="Product type" value={session.productType} /><KeyValue label="Warehouse" value={session.processingWarehouseReference} /><KeyValue label="Reason" value={session.reasonCode} /><KeyValue label="Return quantity" value={String(session.returnQuantity)} /><KeyValue label="Package count" value={String(session.packageCount)} /><KeyValue label="Shipping path" value={session.shippingPathExpectation} /><KeyValue label="Support ticket" value={session.supportTicketReference} /><KeyValue label="RMA" value={session.returnReference} /><KeyValue label="Tracking" value={session.trackingReference} /><KeyValue label="Bay" value={session.bayReference} /><KeyValue label="Feedback" value={session.feedbackReference} /><KeyValue label="Updated" value={formatDate(session.updatedAt)} /></dl>
          <div className="mt-4 flex flex-wrap gap-2">
            {session.aiRequestId && <Link className={secondaryButton} href={`/ai-gateway/requests/${session.aiRequestId}`}>Open AI request</Link>}
            {session.supportCaseId && <Link className={secondaryButton} href="/support/review-queue">Open support queue</Link>}
          </div>
        </Panel>
        <Panel title="Real-time timeline" className="lg:col-span-2">
          {eventsQuery.isLoading && <LoadingState message="Loading events..." />}
          {eventsQuery.isError && <ErrorState message={eventsQuery.error.message} />}
          {eventsQuery.data?.length === 0 && <p className="text-sm text-slate-500">No events recorded.</p>}
          <ol className="space-y-4">
            {eventsQuery.data?.map((event) => (
              <li key={event.id} className="relative border-l-2 border-slate-200 pl-5">
                <span className="absolute -left-[7px] top-1 size-3 rounded-full bg-slate-700" />
                <div className="flex flex-wrap items-center justify-between gap-2"><p className="font-medium text-slate-900">#{event.sequence} {event.eventType}</p><time className="text-xs text-slate-500">{formatDate(event.occurredAt)}</time></div>
                <p className="mt-1 text-xs text-slate-500">{event.actorType} · {event.actorId}</p>
                {Object.keys(event.payload).length > 0 && <div className="mt-2"><JsonBlock value={event.payload} /></div>}
              </li>
            ))}
          </ol>
        </Panel>
      </div>
    </div>
  );
}
