import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  RETURN_EVENT_TYPES,
  returnsApi,
  type ReturnEventType,
  type ReturnSessionView,
} from "../../api/returnsDomain";
import { useCapabilities } from "../../hooks/capabilityContext";
import { QUEUES, type QueueId } from "./queues";

/**
 * The Return Business Copilot (Phase 18).
 *
 * **One screen, not separate support and warehouse products.** Queues are
 * views over the same session list, and selecting one never changes which
 * workspace renders -- that is the consolidation this phase exists to do.
 *
 * **Actions are events, and there is exactly one action endpoint.** D4 published
 * `POST /api/returns/{id}/events`; every structured action on this screen goes
 * through it, including cancelling. There is no per-action endpoint and no
 * generic advance: the operator names an event that *happened* and the evidence
 * for it, and the state machine decides what stage that implies.
 *
 * **The screen does not know which events are legal, on purpose.** Preconditions
 * live in `_validate_transition`, and reproducing them here would be a second
 * copy that drifts from the first -- the exact failure mode this whole
 * consolidation exists to remove. So every event type is offered, the backend
 * refuses the ones that do not apply, and the refusal is shown verbatim. A 409
 * that says "RECEIPT_CONFIRMED is already recorded" is more useful than a
 * disabled button with no explanation.
 *
 * **Queue visibility is presentation only, and honestly so.** `/api/returns`
 * authorizes on read roles alone, so every reader receives every session --
 * hiding a queue would restrict nothing. Per-action RBAC is real, though: the
 * action panel is gated on `returns.session.write`, and the backend gates each
 * individual event type on the actor's roles regardless.
 */

export function ReturnsOperationsPage() {
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
        <h1 className="text-2xl font-semibold text-slate-900">Returns Operations</h1>
        <p className="mt-1 text-sm text-slate-600">
          Queues, timelines, and the one event endpoint every action goes through.
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

      <ConversationPanel sessionId={sessionId} />
      <ActionPanel sessionId={sessionId} />
    </section>
  );
}

function ConversationPanel({ sessionId }: { sessionId: string }) {
  const conversation = useQuery({
    queryKey: ["returns", "conversation", sessionId],
    queryFn: () => returnsApi.conversation(sessionId),
  });

  const messages = Array.isArray(conversation.data?.messages)
    ? (conversation.data.messages as Record<string, unknown>[])
    : [];

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-900">Discovery conversation</h2>
      {conversation.isLoading ? <p className="mt-2 text-sm text-slate-500">Loading...</p> : null}
      {conversation.error ? (
        <p className="mt-2 text-sm text-red-700">{conversation.error.message}</p>
      ) : null}
      {!conversation.isLoading && conversation.data === null ? (
        // Not an error and not an empty conversation: this return never had one.
        // Most SYSTEM-channel returns will land here.
        <p className="mt-2 text-sm text-slate-600">
          This return did not come from a discovery conversation.
        </p>
      ) : null}
      {messages.length > 0 ? (
        <ol className="mt-3 flex flex-col gap-2">
          {messages.map((message, index) => (
            <li key={text(message.id, String(index))} className="rounded-md bg-slate-50 p-2">
              <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                {text(message.role, text(message.author, "message"))}
              </p>
              <p className="text-sm text-slate-900">{text(message.content, text(message.text, ""))}</p>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function ActionPanel({ sessionId }: { sessionId: string }) {
  const { can } = useCapabilities();
  const queryClient = useQueryClient();
  const [eventType, setEventType] = useState<ReturnEventType>(RETURN_EVENT_TYPES[0]);
  const [evidence, setEvidence] = useState("");

  const record = useMutation({
    mutationFn: () =>
      returnsApi.recordEvent(sessionId, {
        // The idempotency key. Generated per submission rather than per render,
        // so a retry of *this* click is a no-op while a deliberate second
        // action is a distinct event.
        eventId: `ui-${sessionId}-${eventType}-${String(Date.now())}`,
        eventType,
        evidenceReference: evidence,
      }),
    onSuccess: async () => {
      setEvidence("");
      // The event moves the stage and appends to the timeline, so both the
      // session list and this session's timeline are stale.
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["returns", "list"] }),
        queryClient.invalidateQueries({ queryKey: ["returns", "timeline", sessionId] }),
      ]);
    },
  });

  if (!can("returns.session.write")) {
    // A button that 403s is worse than no button. The backend gates each event
    // type on the actor's roles as well, so this is presentation, not the
    // boundary.
    return (
      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-slate-900">Actions</h2>
        <p className="mt-2 text-sm text-slate-600">
          You have read access to returns but cannot record events.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-900">Record an event</h2>
      <p className="mt-1 text-xs text-slate-500">
        Every action is an event carrying its evidence, including cancelling the return.
        Which events apply depends on the return&apos;s current state, which the workflow
        decides -- an event that does not apply is refused with the reason.
      </p>

      <form
        className="mt-3 flex flex-col gap-2"
        onSubmit={(submitEvent) => {
          submitEvent.preventDefault();
          record.mutate();
        }}
      >
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-slate-700">Event</span>
          <select
            aria-label="Event type"
            value={eventType}
            onChange={(changeEvent) => {
              setEventType(changeEvent.target.value as ReturnEventType);
            }}
            className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            {RETURN_EVENT_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-slate-700">Evidence reference</span>
          <input
            aria-label="Evidence reference"
            value={evidence}
            onChange={(changeEvent) => {
              setEvidence(changeEvent.target.value);
            }}
            placeholder="scan id, document reference, ticket"
            className="rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          />
        </label>

        <button
          type="submit"
          // The backend requires at least three characters. Enforced here too so
          // the common mistake is caught without a round trip -- but the backend
          // remains the boundary, not this.
          disabled={evidence.trim().length < 3 || record.isPending}
          className="self-start rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
        >
          {record.isPending ? "Recording..." : "Record event"}
        </button>
      </form>

      {record.error ? (
        // Shown verbatim. The backend distinguishes "already recorded" from
        // "out of order" from "the workflow service is unavailable", and
        // flattening those into "something went wrong" would throw away the
        // only thing that tells an operator what to do next.
        <p role="alert" className="mt-2 text-sm text-red-700">
          {record.error.message}
        </p>
      ) : null}
      {record.isSuccess ? (
        <p className="mt-2 text-sm text-green-700">
          Recorded. Stage is now {record.data.stage}
          {record.data.cancelled ? " (cancelled)" : ""}
          {record.data.caseFullyClosed ? " (closed)" : ""}.
        </p>
      ) : null}
    </div>
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

      <SupportGroup sessionId={session.id} />

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

/**
 * Render an `unknown` that the UI expects to be scalar.
 *
 * These payloads are `dict[str, Any]` on the backend because their shapes
 * belong to the support and conversation modules, not to returns. `String(x)`
 * on a nested object produces `[object Object]`, which looks like data and is
 * not -- so anything non-scalar falls back instead. Silently showing the
 * fallback is the right failure here: the alternative is a row that appears to
 * carry a value.
 */
function text(value: unknown, fallback = "-"): string {
  if (typeof value === "string") return value === "" ? fallback : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function SupportGroup({ sessionId }: { sessionId: string }) {
  const support = useQuery({
    queryKey: ["returns", "support", sessionId],
    queryFn: () => returnsApi.support(sessionId),
  });

  if (support.isLoading || support.error) {
    return null;
  }

  const platformCase = support.data?.case ?? null;
  const workItem = support.data?.workItem ?? null;
  if (platformCase === null && workItem === null) {
    return null;
  }

  return (
    <Group title="Support">
      {/* Two records, kept apart. A case means the *platform* raised this
          because a flow failed; a work item means a *person* is working it.
          Collapsing them into one "support status" would lose which. */}
      {platformCase !== null ? (
        <>
          <Row label="Case" value={text(platformCase.caseType)} />
          <Row label="Case status" value={text(platformCase.status)} />
          <Row label="Priority" value={text(platformCase.priority)} />
          {platformCase.slaBreached === true ? <Row label="SLA" value="Breached" /> : null}
        </>
      ) : null}
      {workItem !== null ? (
        <>
          <Row label="Work item" value={text(workItem.subject, text(workItem.id))} />
          <Row label="Work status" value={text(workItem.status)} />
          <Row label="Queue" value={text(workItem.queue)} />
        </>
      ) : null}
    </Group>
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
