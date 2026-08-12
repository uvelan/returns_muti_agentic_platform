import { useState } from "react";
import { skipToken, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Inbox, PackageCheck, Send, UserRound } from "lucide-react";

import {
  supportApi,
  type ReturnOutcomeRecordInput,
  type SupportMessage,
  type SupportWorkItem,
} from "../../api/support";
import { useCapabilities } from "../../hooks/capabilityContext";

/**
 * S3 -- the Support console.
 *
 * Channel B: where the platform talks to Returns Support, and where a person
 * plays the Support role while Teams is not connected. The backend for this
 * has existed since Wave C with no operator surface, which is why no return
 * could be driven end to end: there was nobody to answer the agent.
 *
 * **Two panes, not three.** The copilot's third pane is search evidence; here
 * the equivalent would be the return's own detail, and Support already has it
 * in the request the agent sent. A pane restating it would be the platform
 * talking about itself again.
 *
 * The reply composer sends the version the reader saw. The backend refuses a
 * write built on a stale view rather than clobbering it, so two people
 * answering the same thread is a visible conflict rather than a lost message.
 */

const QUEUES = [
  { label: "Open", value: "" },
  { label: "New", value: "NEW" },
  { label: "In progress", value: "IN_PROGRESS" },
  { label: "Completed", value: "COMPLETED" },
] as const;

export function SupportConsolePage() {
  const { can } = useCapabilities();
  const client = useQueryClient();
  const [queue, setQueue] = useState<string>("");
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const workItems = useQuery({
    queryKey: ["support", "work-items", queue],
    queryFn: () => supportApi.listWorkItems(queue),
    enabled: can("returns.session.read"),
  });

  const detail = useQuery({
    queryKey: ["support", "work-item", selected],
    queryFn: selected === null ? skipToken : () => supportApi.readWorkItem(selected),
  });

  const messages = useQuery({
    queryKey: ["support", "messages", selected],
    queryFn: selected === null ? skipToken : () => supportApi.listMessages(selected),
  });

  const reply = useMutation({
    mutationFn: (text: string) => {
      if (selected === null || detail.data === undefined) {
        throw new Error("Select a request before replying.");
      }
      return supportApi.reply(selected, {
        messageText: text,
        expectedVersion: detail.data.version,
      });
    },
    onSuccess: async () => {
      setDraft("");
      // Both: the thread gained a message and the work item gained a version,
      // and replying against the old one would be refused.
      await client.invalidateQueries({ queryKey: ["support", "messages", selected] });
      await client.invalidateQueries({ queryKey: ["support", "work-item", selected] });
    },
  });

  /**
   * Support's answer, going back to the case rather than onto the work item.
   *
   * The endpoint signals the case's workflow, so what happens next -- the
   * return record, the case status, the fact the associate's next turn reads --
   * is decided in one place. Pressing this twice is safe: the workflow ignores
   * a second response, so this deliberately does not disable itself on a
   * pending network request alone.
   */
  const outcome = useMutation({
    mutationFn: (record: ReturnOutcomeRecordInput) => {
      if (selected === null) throw new Error("Select a request before issuing an RMA.");
      return supportApi.submitReturnOutcome(selected, { records: [record] });
    },
    onSuccess: async () => {
      // The workflow writes asynchronously, so this is optimistic about
      // timing, not about the result: a refetch that lands early shows the
      // thread unchanged and the next one shows it settled.
      await client.invalidateQueries({ queryKey: ["support", "work-item", selected] });
      await client.invalidateQueries({ queryKey: ["support", "messages", selected] });
      await client.invalidateQueries({ queryKey: ["support", "work-items"] });
    },
  });

  if (!can("returns.session.read")) {
    return <p className="text-sm text-on-surface-variant">You do not have access to support.</p>;
  }

  return (
    <div className="grid h-[calc(100vh-3rem)] grid-cols-1 gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,9fr)]">
      <QueuePane
        queue={queue}
        onQueueChange={setQueue}
        items={workItems.data ?? []}
        error={workItems.error}
        loading={workItems.isPending}
        selected={selected}
        onSelect={(id) => {
          setSelected(id);
          setDraft("");
        }}
      />
      <ThreadPane
        item={detail.data ?? null}
        messages={messages.data ?? []}
        loading={selected !== null && messages.isPending}
        draft={draft}
        onDraftChange={setDraft}
        onSend={() => {
          if (draft.trim().length > 0) reply.mutate(draft.trim());
        }}
        sending={reply.isPending}
        error={reply.error}
        onIssueOutcome={(record) => { outcome.mutate(record); }}
        onResetOutcome={() => { outcome.reset(); }}
        issuing={outcome.isPending}
        issued={outcome.isSuccess}
        outcomeError={outcome.error}
      />
    </div>
  );
}

function Pane({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest">
      <h2 className="border-b border-outline-variant px-4 py-3 text-sm font-semibold text-on-surface">
        {title}
      </h2>
      {children}
    </section>
  );
}

function QueuePane({
  queue,
  onQueueChange,
  items,
  error,
  loading,
  selected,
  onSelect,
}: {
  queue: string;
  onQueueChange: (value: string) => void;
  items: readonly SupportWorkItem[];
  error: Error | null;
  loading: boolean;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <Pane title="Return requests">
      <div className="flex flex-wrap gap-1.5 border-b border-outline-variant px-3 py-2">
        {QUEUES.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => { onQueueChange(option.value); }}
            className={`rounded-full border px-3 py-1 text-xs transition ${
              queue === option.value
                ? "border-primary text-primary"
                : "border-outline-variant text-on-surface-variant hover:border-primary hover:text-primary"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto">
        {/*
          Three states, not two. `data ?? []` would tell an operator their queue
          is empty when the truth is that we could not ask -- and an empty
          Support queue is exactly the thing they would act on by going home.
        */}
        {error !== null ? (
          <p role="alert" className="px-4 py-3 text-sm text-error">
            {error.message}
          </p>
        ) : loading ? (
          <p className="px-4 py-3 text-sm text-on-surface-variant">Loading...</p>
        ) : items.length === 0 ? (
          <p className="flex items-center gap-2 px-4 py-3 text-sm text-on-surface-variant">
            <Inbox size={15} aria-hidden="true" />
            Nothing waiting in this queue.
          </p>
        ) : (
          <ul>
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => { onSelect(item.id); }}
                  className={`flex w-full flex-col gap-0.5 border-b border-outline-variant px-4 py-2.5 text-left transition hover:bg-surface-container ${
                    item.id === selected ? "bg-surface-container" : ""
                  }`}
                >
                  <span className="truncate text-sm text-on-surface">{item.subject}</span>
                  <span className="flex items-center gap-2 text-[11px] text-outline">
                    <span>{item.status}</span>
                    {item.returnReference !== null ? <span>{item.returnReference}</span> : null}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Pane>
  );
}

function ThreadPane({
  item,
  messages,
  loading,
  draft,
  onDraftChange,
  onSend,
  sending,
  error,
  onIssueOutcome,
  onResetOutcome,
  issuing,
  issued,
  outcomeError,
}: {
  item: SupportWorkItem | null;
  messages: readonly SupportMessage[];
  loading: boolean;
  draft: string;
  onDraftChange: (value: string) => void;
  onSend: () => void;
  sending: boolean;
  error: Error | null;
  onIssueOutcome: (record: ReturnOutcomeRecordInput) => void;
  onResetOutcome: () => void;
  issuing: boolean;
  issued: boolean;
  outcomeError: Error | null;
}) {
  if (item === null) {
    return (
      <Pane title="Conversation">
        <div className="flex flex-1 items-center justify-center p-6 text-center">
          <p className="max-w-xs text-sm text-on-surface-variant">
            Pick a return request to read the conversation and reply.
          </p>
        </div>
      </Pane>
    );
  }

  return (
    <Pane title={item.subject}>
      <dl className="flex flex-wrap gap-x-6 gap-y-1 border-b border-outline-variant px-4 py-2 text-[11px]">
        {(
          [
            ["Status", item.status],
            ["RMA", item.returnReference],
            ["Pickup", item.shippingInstructionReference],
            ["Assigned", item.assignedTo],
          ] as const
        ).flatMap(([label, value]) =>
          value == null || value === ""
            ? []
            : [
                <div key={label} className="flex gap-1.5">
                  <dt className="text-outline">{label}</dt>
                  <dd className="text-on-surface">{value}</dd>
                </div>,
              ],
        )}
      </dl>

      <div className="flex-1 overflow-y-auto p-4">
        {loading ? (
          <p className="text-sm text-on-surface-variant">Loading...</p>
        ) : (
          <ol className="flex flex-col gap-4">
            {messages.map((message) => (
              <Message key={message.id} message={message} />
            ))}
          </ol>
        )}
      </div>

      {item.caseId === null ? null : (
        <IssueOutcomeForm
          onSubmit={onIssueOutcome}
          onReopen={onResetOutcome}
          issuing={issuing}
          issued={issued}
          error={outcomeError}
        />
      )}

      <form
        className="border-t border-outline-variant p-3"
        onSubmit={(event) => {
          event.preventDefault();
          onSend();
        }}
      >
        {error !== null ? (
          // Verbatim: a version conflict and an authorization failure need
          // different responses, and flattening them loses which one it was.
          <p role="alert" className="mb-2 text-sm text-error">
            {error.message}
          </p>
        ) : null}
        <div className="relative flex items-center">
          <input
            aria-label="Reply to the return request"
            value={draft}
            onChange={(event) => { onDraftChange(event.target.value); }}
            placeholder="Reply to the agent..."
            className="w-full rounded-lg border border-outline-variant bg-surface py-2.5 pl-3 pr-11 text-sm text-on-surface outline-none transition placeholder:text-outline focus:border-primary focus:ring-1 focus:ring-primary"
          />
          <button
            type="submit"
            aria-label="Send reply"
            disabled={draft.trim().length === 0 || sending}
            className="absolute right-2 flex size-8 items-center justify-center rounded bg-primary text-on-primary transition disabled:opacity-40"
          >
            <Send size={15} />
          </button>
        </div>
      </form>
    </Pane>
  );
}

/**
 * Issue the RMA -- the moment Channel B answers Channel A.
 *
 * Only shown for a work item that belongs to a case, because the outcome
 * travels to the case's workflow: for a work item without one there is nothing
 * to signal, and offering the form would be offering a button that 409s.
 *
 * Collapsed until asked for. Most of what Support does on a thread is talk, and
 * a permanently open form of empty reference fields reads as work outstanding.
 */
function IssueOutcomeForm({
  onSubmit,
  onReopen,
  issuing,
  issued,
  error,
}: {
  onSubmit: (record: ReturnOutcomeRecordInput) => void;
  onReopen: () => void;
  issuing: boolean;
  issued: boolean;
  error: Error | null;
}) {
  const [open, setOpen] = useState(false);
  const [returnReference, setReturnReference] = useState("");
  const [trackingReference, setTrackingReference] = useState("");
  const [returnLocation, setReturnLocation] = useState("");

  // Collapsed by the *outcome*, not by the click. Closing on submit would hide
  // the form -- and with it the error -- at the exact moment a refused RMA
  // needs reading, and Support would walk away believing they had sent one.
  // Derived rather than an effect, so there is one source of truth for "did
  // this land" and it is the mutation's.
  if (!open || issued) {
    return (
      <div className="border-t border-outline-variant px-3 py-2">
        <button
          type="button"
          onClick={() => {
            // Clears last time's answer as well as the fields: reopening is a
            // new RMA, and a success line above an empty form reads as though
            // the empty form had already been sent.
            onReopen();
            setReturnReference("");
            setTrackingReference("");
            setReturnLocation("");
            setOpen(true);
          }}
          className="flex items-center gap-1.5 text-xs text-primary transition hover:underline"
        >
          <PackageCheck size={14} aria-hidden="true" />
          Issue RMA
        </button>
        {issued ? (
          <p role="status" className="mt-1 text-[11px] text-on-surface-variant">
            Sent to the return. The associate will see it on their next message.
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <form
      className="flex flex-col gap-2 border-t border-outline-variant px-3 py-2.5"
      onSubmit={(event) => {
        event.preventDefault();
        if (returnReference.trim().length === 0) return;
        onSubmit({
          returnReference: returnReference.trim(),
          // Omitted rather than sent empty: "" would be recorded as a tracking
          // number that exists and is blank.
          ...(trackingReference.trim() === "" ? {} : { trackingReference: trackingReference.trim() }),
          ...(returnLocation.trim() === "" ? {} : { returnLocation: returnLocation.trim() }),
        });
      }}
    >
      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error.message}
        </p>
      ) : null}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {(
          [
            ["RMA number", returnReference, setReturnReference, true],
            ["Tracking", trackingReference, setTrackingReference, false],
            ["Return to", returnLocation, setReturnLocation, false],
          ] as const
        ).map(([label, value, set, required]) => (
          <label key={label} className="flex flex-col gap-1 text-[11px] text-outline">
            {label}
            {required ? <span className="sr-only">(required)</span> : null}
            <input
              value={value}
              required={required}
              onChange={(event) => { set(event.target.value); }}
              className="rounded border border-outline-variant bg-surface px-2 py-1.5 text-sm text-on-surface outline-none transition focus:border-primary focus:ring-1 focus:ring-primary"
            />
          </label>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={returnReference.trim().length === 0 || issuing}
          className="rounded bg-primary px-3 py-1.5 text-xs text-on-primary transition disabled:opacity-40"
        >
          Send to the return
        </button>
        <button
          type="button"
          onClick={() => { setOpen(false); }}
          className="text-xs text-on-surface-variant transition hover:text-on-surface"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

/**
 * One message, attributed.
 *
 * Support knows it is talking to an agent -- that is the stated design, not
 * something to hide -- so who said what is marked plainly rather than styled
 * to look human.
 */
function Message({ message }: { message: SupportMessage }) {
  const fromAgent = message.senderRole === "AGENT";
  const isReminder = "reminderKey" in message.businessPayload;
  return (
    <li className={`flex gap-3 ${fromAgent ? "" : "justify-end"}`}>
      {fromAgent ? (
        <span
          aria-hidden="true"
          className="mt-1 flex size-6 shrink-0 items-center justify-center rounded bg-surface-container text-on-surface-variant"
        >
          <Bot size={14} />
        </span>
      ) : null}
      <div
        className={`max-w-[80%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
          fromAgent
            ? "rounded-tl-sm border border-outline-variant bg-surface-container-low text-on-surface"
            : "rounded-tr-sm bg-primary text-on-primary"
        }`}
      >
        {message.messageText}
        {isReminder ? (
          <span className="mt-1.5 block text-[11px] italic text-outline">Follow-up</span>
        ) : null}
      </div>
      {fromAgent ? null : (
        <span
          aria-hidden="true"
          className="mt-1 flex size-6 shrink-0 items-center justify-center rounded bg-surface-container text-on-surface-variant"
        >
          <UserRound size={14} />
        </span>
      )}
    </li>
  );
}
