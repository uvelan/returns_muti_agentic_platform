import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { returnsApi, type ReturnSessionView } from "../../api/returnsDomain";
import { useCapabilities } from "../../hooks/capabilityContext";
import { QUEUES, type QueueId } from "./queues";

/**
 * The Return Business Copilot (Phase 18), read-only.
 *
 * **One screen, not separate support and warehouse products.** Queues are
 * views over the same session list, and selecting one never changes which
 * workspace renders -- that is the consolidation this phase exists to do.
 *
 * **Read-only because `/api/returns` is.** Three GETs, no writes: the
 * canonical write surface is deliberately held back until the nine legacy
 * return routers are reconciled (D4). So there are no structured actions, no
 * decision controls, and no approvals here. Rendering them against legacy
 * endpoints would add a tenth way to mutate a return, which is precisely what
 * the backend is holding the line against.
 *
 * **Queue visibility is presentation only, and honestly so.** `/api/returns`
 * authorizes on read roles alone, so every reader receives every session --
 * hiding a queue would restrict nothing. Queues are therefore shown to anyone
 * who can read, and per-action RBAC becomes meaningful when the write surface
 * lands and there are actions to gate.
 */

export function ReturnCopilotPage() {
  const { can } = useCapabilities();
  const [queue, setQueue] = useState<QueueId>("mine");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const sessions = useQuery({ queryKey: ["returns", "list"], queryFn: returnsApi.list });

  if (!can("returns.session.read")) {
    return <p className="text-sm text-slate-600">You do not have access to returns.</p>;
  }

  const all = sessions.data ?? [];
  const active = QUEUES.find((q) => q.id === queue) ?? QUEUES[0];
  const visible = all.filter(active.match);
  const selected = all.find((s) => s.id === selectedId) ?? null;

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Return Business Copilot</h1>
        <p className="mt-1 text-sm text-slate-600">
          Discovery through resolution, one operational screen.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[16rem_1fr_20rem]">
        <QueueColumn
          queues={QUEUES.map((q) => ({ id: q.id, label: q.label, count: all.filter(q.match).length }))}
          active={queue}
          onSelect={setQueue}
          sessions={visible}
          selectedId={selectedId}
          onSelectSession={setSelectedId}
          isLoading={sessions.isLoading}
          error={sessions.error}
        />
        <Workspace sessionId={selected?.id ?? null} />
        <ContextColumn session={selected} />
      </div>
    </div>
  );
}

function QueueColumn({
  queues,
  active,
  onSelect,
  sessions,
  selectedId,
  onSelectSession,
  isLoading,
  error,
}: {
  queues: readonly { id: QueueId; label: string; count: number }[];
  active: QueueId;
  onSelect: (id: QueueId) => void;
  sessions: readonly ReturnSessionView[];
  selectedId: string | null;
  onSelectSession: (id: string) => void;
  isLoading: boolean;
  error: Error | null;
}) {
  return (
    <aside className="rounded-lg border border-slate-200 bg-white p-3">
      <nav aria-label="Queues" className="flex flex-col gap-1">
        {queues.map((q) => (
          <button
            key={q.id}
            type="button"
            aria-current={active === q.id ? "true" : undefined}
            onClick={() => { onSelect(q.id); }}
            className={[
              "flex items-center justify-between rounded-md px-3 py-2 text-sm font-medium transition",
              active === q.id ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100",
            ].join(" ")}
          >
            <span>{q.label}</span>
            <span className="text-xs opacity-80">{q.count}</span>
          </button>
        ))}
      </nav>

      <div className="mt-4 border-t border-slate-200 pt-3">
        {isLoading ? <p className="text-sm text-slate-500">Loading...</p> : null}
        {error ? <p className="text-sm text-red-700">{error.message}</p> : null}
        {!isLoading && !error && sessions.length === 0 ? (
          <p className="text-sm text-slate-600">This queue is empty.</p>
        ) : null}
        <ul className="flex flex-col gap-1">
          {sessions.map((session) => (
            <li key={session.id}>
              <button
                type="button"
                onClick={() => { onSelectSession(session.id); }}
                aria-current={selectedId === session.id ? "true" : undefined}
                className={[
                  "w-full rounded-md px-2 py-2 text-left text-sm transition",
                  selectedId === session.id ? "bg-slate-100" : "hover:bg-slate-50",
                ].join(" ")}
              >
                <span className="block truncate font-medium text-slate-900">
                  {session.orderReference}
                </span>
                <span className="block truncate text-xs text-slate-500">
                  {session.status} - {session.currentStage}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}

function Workspace({ sessionId }: { sessionId: string | null }) {
  const timeline = useQuery({
    queryKey: ["returns", "timeline", sessionId],
    queryFn: () => returnsApi.timeline(sessionId ?? ""),
    enabled: sessionId !== null,
  });

  if (sessionId === null) {
    return (
      <section className="rounded-lg border border-slate-200 bg-white p-6">
        <p className="text-sm text-slate-600">Select a return.</p>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-slate-900">Timeline</h2>
        {timeline.isLoading ? <p className="mt-2 text-sm text-slate-500">Loading...</p> : null}
        {timeline.error ? (
          <p className="mt-2 text-sm text-red-700">{timeline.error.message}</p>
        ) : null}
        <ol className="mt-3 flex flex-col gap-3">
          {(timeline.data ?? []).map((event) => (
            <li key={event.id} className="border-l-2 border-slate-200 pl-3">
              <p className="text-sm font-medium text-slate-900">{event.eventType}</p>
              <p className="text-xs text-slate-500">
                {/* Actor type is shown because an event produced by an agent
                    and one produced by a person are different evidence. */}
                {event.actorType}: {event.actorId} - {new Date(event.occurredAt).toLocaleString()}
              </p>
            </li>
          ))}
          {timeline.data?.length === 0 ? (
            <li className="text-sm text-slate-600">No events recorded.</li>
          ) : null}
        </ol>
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-slate-900">Conversation and actions</h2>
        <p className="mt-2 text-sm text-slate-500">
          Not built. <code>/api/returns</code> serves the session, its timeline and the
          session list -- there is no conversation route and no action route. Structured
          actions, decision controls and approvals arrive with the canonical write surface,
          which is held back until the nine legacy return routers are reconciled.
        </p>
      </div>
    </section>
  );
}

function ContextColumn({ session }: { session: ReturnSessionView | null }) {
  if (session === null) {
    return (
      <aside className="rounded-lg border border-slate-200 bg-white p-4">
        <p className="text-sm text-slate-600">No return selected.</p>
      </aside>
    );
  }

  return (
    <aside className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4">
      <div>
        <h2 className="text-sm font-semibold text-slate-900">Return context</h2>
        <div className="mt-2 h-1.5 w-full rounded-full bg-slate-200">
          <div
            className="h-1.5 rounded-full bg-slate-900"
            style={{ width: `${String(session.progressPercentage)}%` }}
          />
        </div>
        <p className="mt-1 text-xs text-slate-500">
          {session.currentStage} - {session.progressPercentage}%
        </p>
      </div>

      <Group title="Customer and order">
        <Row label="Customer" value={session.customerReference} />
        <Row label="Order" value={session.orderReference} />
        <Row label="Source" value={session.orderSource} />
        <Row label="Channel" value={session.channel} />
      </Group>

      <Group title="Items">
        <Row label="Quantity" value={String(session.returnQuantity)} />
        <Row label="Packages" value={String(session.packageCount)} />
        <Row label="Items" value={session.itemReferences.join(", ") || "-"} />
        <Row label="Reason" value={session.reasonCode} />
      </Group>

      <Group title="Decision and RMA">
        <Row label="Status" value={session.status} />
        <Row label="Return reference" value={session.returnReference ?? "-"} />
        <Row label="Approved method" value={session.approvedReturnMethod ?? "-"} />
        <Row label="Support ticket" value={session.supportTicketReference ?? "-"} />
      </Group>

      <Group title="Fulfillment and warehouse">
        <Row label="Tracking" value={session.trackingReference ?? "-"} />
        <Row label="Physical return" value={session.physicalReturnStatus} />
        <Row label="Warehouse" value={session.warehouseStatus} />
        <Row label="Bay" value={session.bayReference ?? "-"} />
      </Group>

      <Group title="Resolution">
        <Row label="Customer resolution" value={session.customerResolutionStatus} />
        <Row label="Vendor recovery" value={session.vendorRecoveryStatus} />
        <Row label="Case closure" value={session.caseClosureStatus} />
      </Group>

      {session.failureCode !== null ? (
        <Group title="Failure">
          <Row label="Code" value={session.failureCode} />
          <Row label="Message" value={session.failureMessage ?? "-"} />
        </Group>
      ) : null}

      {session.aiRequestId !== null ? (
        <p className="text-xs text-slate-500">
          {/* The AI call itself lives on the AI surface; duplicating it here
              would create a second, drifting view of the same attempt. */}
          AI request <span className="font-mono">{session.aiRequestId}</span> is
          inspectable in the AI Control Center.
        </p>
      ) : null}
    </aside>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-slate-200 pt-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      <dl className="mt-1 flex flex-col gap-1">{children}</dl>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 text-sm">
      <dt className="shrink-0 text-slate-500">{label}</dt>
      <dd className="truncate text-right text-slate-900" title={value}>
        {value}
      </dd>
    </div>
  );
}
