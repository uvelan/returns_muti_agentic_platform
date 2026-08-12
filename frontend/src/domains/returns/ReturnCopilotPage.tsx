import { useRef, useState } from "react";
import { skipToken, useMutation, useQuery } from "@tanstack/react-query";
import {
  Bot,
  CircleCheck,
  CircleDashed,
  History,
  Plus,
  Send,
  ShieldCheck,
  Tag,
} from "lucide-react";

import {
  newConversationId,
  orderAgentApi,
  type AgentTurnResult,
  type ConversationSummary,
  type ResponseStatement,
} from "../../api/orderAgent";
import { casesApi, type CaseReturnRecord } from "../../api/cases";
import { returnsApi, type ReturnSessionView } from "../../api/returnsDomain";
import { useCapabilities } from "../../hooks/capabilityContext";

/**
 * The Return Business Copilot: order discovery by conversation.
 *
 * **This screen replaces a queue browser that was never the copilot.** What
 * used to be here -- queues, a session list, a timeline and an event form --
 * is returns *operations*, and moved to that domain. The copilot's job is the
 * one the backend has had a durable agent for since Wave C3 and no UI at all:
 * take an associate's partial description of an order and find it.
 *
 * **Three panes, from the approved design.** Chat on the left, the agent's
 * progress in the middle, resolved context on the right.
 *
 * **The middle pane is progress, not a working trace.** It shows where the
 * return has got to and nothing about how -- no model name, no graph
 * generation, no note about which stages the API can report on. Those are
 * true and they are the platform talking about itself; an associate mid-return
 * needs the stage.
 *
 * Progress is still *derived*, never assumed: a clarifying question means the
 * agent is still identifying, evidence with candidates means it has searched,
 * and a stage the turn result cannot speak to stays pending. The temptation is
 * a bar that advances on a timer, which would look finished and mean nothing.
 */

type ChatMessage =
  | { role: "associate"; id: string; text: string }
  | { role: "agent"; id: string; statements: ResponseStatement[]; status: string }
  // Replayed from a stored transcript. The record keeps role and text, not the
  // typed statements, so a reopened conversation renders as plain speech --
  // which is honest: the provenance markers below mean "this turn cited
  // evidence", and a replay cannot vouch for that.
  | { role: "restored"; id: string; author: "associate" | "agent"; text: string };

/**
 * The return's milestones, in order, each owned by one agent.
 *
 * These are business events, not the platform's internal workflow stages --
 * `ProductionReturnStage` has sixteen, most of which mean nothing to an
 * associate (`PRODUCT_DISPOSITION`, `VENDOR_RECOVERY`). Six are what someone
 * standing at a counter needs: has the order been found, is the case raised,
 * where is the parcel.
 *
 * `output` is what that agent produced, read from the return session rather
 * than described. A milestone with no output yet renders as the label alone.
 */
type Milestone = {
  readonly label: string;
  readonly agent: string;
  /** Reached only when this returns true. Absence of evidence is not progress. */
  readonly reached: (context: ProgressContext) => boolean;
  /** What the agent produced, as label/value pairs. */
  readonly output: (context: ProgressContext) => readonly (readonly [string, string])[];
};

type ProgressContext = {
  readonly turn: AgentTurnResult | null;
  readonly candidates: readonly Record<string, unknown>[];
  readonly session: ReturnSessionView | null;
};

/** Statuses that mean "nothing has happened here yet". */
const IDLE = new Set(["NOT_STARTED", "NOT_REQUIRED_OR_PENDING", "PENDING", "OPEN", ""]);

function live(value: string | null | undefined): value is string {
  return typeof value === "string" && value.length > 0 && !IDLE.has(value);
}

/** The value if it means something, else nothing to show. */
function shown(value: string | null | undefined): string | null {
  return live(value) ? value : null;
}

function pairs(
  ...entries: readonly (readonly [string, string | null | undefined])[]
): readonly (readonly [string, string])[] {
  return entries.flatMap(([label, value]) =>
    value != null && value !== "" ? [[label, value] as const] : [],
  );
}

const MILESTONES: readonly Milestone[] = [
  {
    label: "Orders identified",
    agent: "Order Discovery",
    reached: ({ candidates }) => candidates.length > 0,
    output: ({ candidates }) =>
      pairs(["Matches", candidates.length > 0 ? String(candidates.length) : null]),
  },
  {
    label: "Order selected",
    agent: "Order Discovery",
    reached: ({ session }) => session !== null,
    output: ({ session }) =>
      pairs(["Order", session?.orderReference], ["Customer", session?.customerReference]),
  },
  {
    label: "Case created",
    agent: "Return Workflow",
    reached: ({ session }) => live(session?.returnReference),
    output: ({ session }) =>
      pairs(
        ["RMA", session?.returnReference],
        ["Tracking", session?.trackingReference],
        ["Support ticket", session?.supportTicketReference],
      ),
  },
  {
    label: "Shipment in progress",
    agent: "Return Fulfillment",
    reached: ({ session }) => live(session?.physicalReturnStatus),
    output: ({ session }) =>
      pairs(
        ["Status", shown(session?.physicalReturnStatus)],
        ["Method", session?.approvedReturnMethod],
      ),
  },
  {
    label: "Reached warehouse",
    agent: "Bay Allocation",
    reached: ({ session }) => live(session?.warehouseStatus),
    output: ({ session }) =>
      pairs(
        ["Status", shown(session?.warehouseStatus)],
        ["Bay", session?.bayReference],
      ),
  },
  {
    label: "Completed",
    agent: "Return Session",
    reached: ({ session }) => session?.status === "COMPLETED",
    output: ({ session }) =>
      pairs(
        ["Resolution", shown(session?.customerResolutionStatus)],
        ["Closure", shown(session?.caseClosureStatus)],
      ),
  },
];

/** Anchors the associate has supplied, as the agent recorded them. */
function anchors(history: readonly ChatMessage[]): ResponseStatement[] {
  return history
    .filter((message): message is Extract<ChatMessage, { role: "agent" }> => message.role === "agent")
    .flatMap((message) => message.statements)
    .filter((statement) => statement.statement_type === "USER_PROVIDED_FACT");
}

/**
 * Rows the agent's searches returned, for the context pane.
 *
 * `QueryEvidence.result` is `unknown` in the contract because its shape
 * belongs to whichever query ran. Narrowed with a guard rather than a cast:
 * an ORDER_SEARCH carries `candidates[].data`, a GRAPH_QUERY does not, and
 * asserting the shape would render `[object Object]` for the second kind.
 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * What this turn's searches found, or `null` when the turn did not search.
 *
 * The distinction is the point. An identified order belongs to the
 * conversation, not to one turn: once the associate has narrowed to
 * CW273354, asking "and was it shipped" runs a GRAPH_QUERY whose evidence
 * carries `rows`, no `candidates`. Deriving the pane from the latest turn
 * alone made that follow-up read as "the agent has not matched an order yet"
 * and walked the progress rail backwards off *Orders identified* -- the
 * associate had lost nothing, only asked a question.
 *
 * An empty array is still an answer, so a search that genuinely finds nothing
 * clears the previous match rather than leaving a stale one on screen.
 */
function turnCandidates(turn: AgentTurnResult): Record<string, unknown>[] | null {
  // Collect the candidate arrays first rather than setting a flag inside the
  // callback: whether the turn searched is then a property of the collection,
  // which the type checker can see, instead of a mutation it cannot follow.
  const searches = turn.query_evidence.flatMap((evidence) =>
    isRecord(evidence.result) && Array.isArray(evidence.result.candidates)
      ? [evidence.result.candidates]
      : [],
  );
  if (searches.length === 0) return null;
  return searches
    .flat()
    .flatMap((candidate: unknown) =>
      isRecord(candidate) && isRecord(candidate.data) ? [candidate.data] : [],
    );
}

/**
 * The order reference discovery has settled on.
 *
 * Exactly one candidate, not "the top one": a ranked list with two entries has
 * not resolved anything, and picking its head would show a return session for
 * an order the associate never confirmed.
 */
function resolvedOrderReference(candidates: readonly Record<string, unknown>[]): string | null {
  if (candidates.length !== 1) return null;
  const value = candidates[0].sales_order_number;
  return typeof value === "string" ? value : null;
}

export function ReturnCopilotPage() {
  const { can } = useCapabilities();
  // Settable, not fixed at mount. A `useState(newConversationId)` with no
  // setter meant one conversation per page load and no way to start another --
  // an associate finishing one return had to reload the browser to begin the
  // next, and the agent, whose memory is scoped to the conversation, would
  // otherwise carry the last customer into this one.
  const [conversationId, setConversationId] = useState(newConversationId);
  const [history, setHistory] = useState<readonly ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const [turn, setTurn] = useState<AgentTurnResult | null>(null);
  // Held across turns, not derived from the latest one: see `turnCandidates`.
  const [candidates, setCandidates] = useState<readonly Record<string, unknown>[]>([]);
  // The backend rejects a turn built on a stale view, so the version from the
  // last result is what the next request must carry.
  const versionRef = useRef(0);

  const conversations = useQuery({
    queryKey: ["order-agent", "conversations"],
    queryFn: () => orderAgentApi.listConversations(),
    enabled: can("returns.session.read"),
  });

  function startNewReturn() {
    setConversationId(newConversationId());
    setHistory([]);
    setTurn(null);
    setCandidates([]);
    setDraft("");
    // Back to 0: a new conversation has no committed version, and carrying the
    // previous one's would be rejected as stale on the very first turn.
    versionRef.current = 0;
  }

  const open = useMutation({
    mutationFn: (id: string) => orderAgentApi.readTranscript(id),
    onSuccess: (transcript) => {
      setConversationId(transcript.conversationId);
      setHistory(
        transcript.messages.map((message, index) => ({
          role: "restored" as const,
          id: `${transcript.conversationId}-${String(index)}`,
          author: message.role,
          text: message.text,
        })),
      );
      setTurn(null);
      setCandidates([]);
      // Continue where it left off rather than at 0, or the next turn is
      // rejected as built on a stale view of a conversation that has history.
      versionRef.current = transcript.conversationVersion;
    },
  });

  const send = useMutation({
    mutationFn: (message: string) =>
      orderAgentApi.sendTurn({
        conversationId,
        expectedConversationVersion: versionRef.current,
        message,
        // Must match `agent_policies` in the active schema exactly. A mismatch
        // is not a 404 -- the guard reports ORDER_AGENT_OUT_OF_SCOPE, which
        // reads like a permissions problem rather than a typo.
        agentId: "order-discovery-agent",
      }),
    onSuccess: (result) => {
      versionRef.current = result.conversation_version;
      setTurn(result);
      const found = turnCandidates(result);
      if (found !== null) setCandidates(found);
      setHistory((previous) => [
        ...previous,
        {
          role: "agent",
          id: result.client_turn_id,
          statements: result.response.statements,
          status: result.response.status,
        },
      ]);
    },
  });

  const orderReference = resolvedOrderReference(candidates);

  // The case, once the associate has confirmed an order.
  //
  // **This replaces a client-side join.** The copilot used to fetch every
  // return session and match `session.orderReference` against the top search
  // candidate -- so two open orders sharing a reference showed the wrong one,
  // and closing the tab lost the link entirely. `case_id` comes back on the
  // turn that confirmed, so there is nothing left to guess.
  const caseId = turn?.case_id ?? null;
  const caseDetail = useQuery({
    queryKey: ["cases", caseId],
    // `skipToken` rather than `enabled`, so the null case is narrowed by the
    // type system instead of asserted away: with `enabled` the closure still
    // sees `string | null` and needs a cast that says nothing the compiler can
    // check.
    queryFn: caseId === null ? skipToken : () => casesApi.read(caseId),
    // Support answers on their own clock, and the associate is standing at a
    // counter -- so the RMA has to appear without them sending a message to
    // provoke it. Polling stops the moment there is a return record: the wait
    // is for Support's first answer, not forever.
    refetchInterval: (query) =>
      (query.state.data?.returnRecords.length ?? 0) > 0 ? false : 10_000,
  });

  // The return session still backs the later milestones, which report on work
  // other agents did and have no case-level equivalent yet. Read by the case's
  // own order reference rather than by a search candidate, so it is at least
  // anchored to something the associate confirmed.
  const confirmedOrder = caseDetail.data?.case.confirmedOrderReference ?? orderReference;
  const sessions = useQuery({
    queryKey: ["returns", "list"],
    queryFn: returnsApi.list,
    enabled: confirmedOrder !== null,
  });
  const session =
    (sessions.data ?? []).find((candidate) => candidate.orderReference === confirmedOrder) ?? null;

  if (!can("returns.session.read")) {
    return <p className="text-sm text-on-surface-variant">You do not have access to returns.</p>;
  }

  function submit(message: string) {
    const trimmed = message.trim();
    if (trimmed.length === 0 || send.isPending) return;
    setHistory((previous) => [
      ...previous,
      { role: "associate", id: `me-${String(previous.length)}`, text: trimmed },
    ]);
    setDraft("");
    send.mutate(trimmed);
  }

  return (
    <div className="grid h-[calc(100vh-3rem)] grid-cols-1 gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,8fr)_minmax(0,7fr)]">
      <ChatPane
        history={history}
        draft={draft}
        onDraftChange={setDraft}
        onSubmit={submit}
        isPending={send.isPending}
        error={send.error}
        suggestions={turn?.response.suggestions ?? []}
        conversations={conversations.data ?? []}
        historyError={conversations.error}
        historyLoading={conversations.isPending}
        conversationId={conversationId}
        showHistory={showHistory}
        onToggleHistory={() => { setShowHistory((open) => !open); }}
        onNewReturn={startNewReturn}
        onOpen={(id) => { setShowHistory(false); open.mutate(id); }}
      />
      <ProgressPane
        context={{ turn, candidates, session }}
        anchors={anchors(history)}
        returnRecords={caseDetail.data?.returnRecords ?? []}
      />
      <ContextPane turn={turn} rows={candidates} />
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

function ChatPane({
  history,
  draft,
  onDraftChange,
  onSubmit,
  isPending,
  error,
  suggestions,
  conversations,
  historyError,
  historyLoading,
  conversationId,
  showHistory,
  onToggleHistory,
  onNewReturn,
  onOpen,
}: {
  history: readonly ChatMessage[];
  draft: string;
  onDraftChange: (value: string) => void;
  onSubmit: (value: string) => void;
  isPending: boolean;
  error: Error | null;
  suggestions: readonly string[];
  conversations: readonly ConversationSummary[];
  historyError: Error | null;
  historyLoading: boolean;
  conversationId: string;
  showHistory: boolean;
  onToggleHistory: () => void;
  onNewReturn: () => void;
  onOpen: (conversationId: string) => void;
}) {
  return (
    <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest">
      <header className="flex items-center gap-3 border-b border-outline-variant px-4 py-3">
        <span className="flex size-9 items-center justify-center rounded-full bg-secondary-container text-primary">
          <Bot size={18} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-on-surface">Discovery Agent</span>
          <span className="flex items-center gap-1.5 text-xs text-outline">
            <span
              aria-hidden="true"
              className={`size-1.5 rounded-full ${isPending ? "animate-pulse bg-tertiary" : "bg-primary"}`}
            />
            {isPending ? "Thinking..." : "Ready to help"}
          </span>
        </span>

        <button
          type="button"
          onClick={onNewReturn}
          className="flex items-center gap-1.5 rounded-full border border-outline-variant px-3 py-1.5 text-xs font-medium text-on-surface-variant transition hover:border-primary hover:text-primary"
        >
          <Plus size={14} />
          New return
        </button>
        <button
          type="button"
          aria-label="Previous returns"
          aria-expanded={showHistory}
          onClick={onToggleHistory}
          className={`flex size-8 items-center justify-center rounded-full border transition ${
            showHistory
              ? "border-primary text-primary"
              : "border-outline-variant text-on-surface-variant hover:border-primary hover:text-primary"
          }`}
        >
          <History size={15} />
        </button>
      </header>

      {showHistory ? (
        <div className="border-b border-outline-variant bg-surface-container-low">
          <p className="px-4 pt-3 text-[11px] font-medium uppercase tracking-wide text-outline">
            Previous returns
          </p>
          {/*
            Three states, not two. `data ?? []` collapses "the request failed"
            into "you have no history", which is a comfortable lie: the
            associate is told their previous returns do not exist when the
            truth is that we could not ask.
          */}
          {historyError !== null ? (
            <p role="alert" className="px-4 py-3 text-sm text-error">
              {historyError.message}
            </p>
          ) : historyLoading ? (
            <p className="px-4 py-3 text-sm text-on-surface-variant">Loading...</p>
          ) : conversations.length === 0 ? (
            <p className="px-4 py-3 text-sm text-on-surface-variant">
              Nothing yet. Conversations appear here once you have started one.
            </p>
          ) : (
            <ul className="max-h-56 overflow-y-auto py-1">
              {conversations.map((conversation) => (
                <li key={conversation.conversationId}>
                  <button
                    type="button"
                    onClick={() => { onOpen(conversation.conversationId); }}
                    className={`flex w-full flex-col gap-0.5 px-4 py-2 text-left transition hover:bg-surface-container ${
                      conversation.conversationId === conversationId ? "bg-surface-container" : ""
                    }`}
                  >
                    <span className="truncate text-sm text-on-surface">{conversation.title}</span>
                    <span className="text-[11px] text-outline">
                      {conversation.messageCount} message
                      {conversation.messageCount === 1 ? "" : "s"}
                      {conversation.updatedAt === null
                        ? ""
                        : ` -- ${new Date(conversation.updatedAt).toLocaleString()}`}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      <div className="flex-1 overflow-y-auto p-4">
        <ol className="flex flex-col gap-5">
          {history.length === 0 ? (
            <li className="flex gap-3">
              <AgentAvatar />
              <p className="max-w-[85%] rounded-2xl rounded-tl-sm border border-outline-variant bg-surface-container-low px-3.5 py-2.5 text-sm leading-relaxed text-on-surface">
                Hi -- let&apos;s find that order. Tell me whatever the customer gave you: an order
                number, their name, the product, roughly when it arrived. Anything is a start.
              </p>
            </li>
          ) : null}
          {history.map((message) => {
            if (message.role === "associate") return <Said key={message.id} text={message.text} />;
            if (message.role === "restored") {
              return message.author === "associate" ? (
                <Said key={message.id} text={message.text} />
              ) : (
                <li key={message.id} className="flex gap-3">
                  <AgentAvatar />
                  <p className="max-w-[85%] rounded-2xl rounded-tl-sm border border-outline-variant bg-surface-container-low px-3.5 py-2.5 text-sm leading-relaxed text-on-surface">
                    {message.text}
                  </p>
                </li>
              );
            }
            return (
              <li key={message.id} className="flex gap-3">
                <AgentAvatar />
                <div className="flex min-w-0 flex-col gap-2">
                  {message.statements.map((statement) => (
                    <Statement key={statement.statement_id} statement={statement} />
                  ))}
                </div>
              </li>
            );
          })}
          {isPending ? <Typing /> : null}
          {error ? (
            // Verbatim. The backend distinguishes a clarification budget from a
            // stale version from an unavailable model, and flattening those
            // loses the only thing that says what to do next.
            <li role="alert" className="text-sm text-error">
              {error.message}
            </li>
          ) : null}
        </ol>
      </div>

      {suggestions.length > 0 ? (
        <div className="flex flex-wrap gap-2 border-t border-outline-variant px-4 py-2">
          {suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => { onSubmit(suggestion); }}
              className="rounded-full border border-outline-variant px-3 py-1 text-xs text-on-surface-variant transition hover:border-primary hover:text-primary"
            >
              {suggestion}
            </button>
          ))}
        </div>
      ) : null}

      <form
        className="border-t border-outline-variant p-3"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(draft);
        }}
      >
        <div className="relative flex items-center">
          <input
            aria-label="Message the discovery agent"
            value={draft}
            onChange={(event) => { onDraftChange(event.target.value); }}
            placeholder="Type an identifier or message..."
            className="w-full rounded-lg border border-outline-variant bg-surface py-2.5 pl-3 pr-11 text-sm text-on-surface outline-none transition placeholder:text-outline focus:border-primary focus:ring-1 focus:ring-primary"
          />
          <button
            type="submit"
            aria-label="Send"
            disabled={draft.trim().length === 0 || isPending}
            className="absolute right-2 flex size-8 items-center justify-center rounded bg-primary text-on-primary transition disabled:opacity-40"
          >
            <Send size={15} />
          </button>
        </div>
        <p className="mt-2 text-center text-[11px] text-outline">Press Enter to send</p>
      </form>
    </section>
  );
}

function AgentAvatar() {
  return (
    <span
      aria-hidden="true"
      className="mt-1 flex size-6 shrink-0 items-center justify-center rounded bg-surface-container text-on-surface-variant"
    >
      <Bot size={14} />
    </span>
  );
}

/**
 * A statement, labelled by where it came from.
 *
 * The distinction is the point of the contract: a GRAPH_FACT is traceable to
 * query evidence and a REASONED_SUGGESTION is the model's inference. Rendering
 * them identically would throw away the one thing that says how much to trust
 * a line.
 */
function Statement({ statement }: { statement: ResponseStatement }) {
  const isQuestion = statement.statement_type === "CLARIFICATION_QUESTION";
  const isEvidenced = statement.statement_type === "GRAPH_FACT";
  return (
    <div
      className={[
        "max-w-[85%] rounded-2xl rounded-tl-sm border px-3.5 py-2.5 text-sm leading-relaxed",
        isQuestion
          ? "border-primary/40 bg-secondary-container text-on-surface"
          : "border-outline-variant bg-surface-container-low text-on-surface",
      ].join(" ")}
    >
      {statement.text}
      {/*
        Provenance as a quiet mark, not a banner.
        Every bubble used to be stamped with an uppercase GRAPH FACT /
        CLARIFICATION QUESTION header, which is the single thing that made this
        read like a machine filing a report rather than a colleague talking. The
        distinction still matters -- a GRAPH_FACT is traceable to query evidence
        and a REASONED_SUGGESTION is the model's inference -- so it stays, as a
        small marked line rather than a label shouted over the sentence. A
        question needs no label at all: it ends in a question mark.
      */}
      {isEvidenced ? (
        <span className="mt-1.5 flex items-center gap-1 text-[11px] text-outline">
          <ShieldCheck size={11} aria-hidden="true" />
          From order records
        </span>
      ) : null}
      {statement.statement_type === "REASONED_SUGGESTION" ? (
        <span className="mt-1.5 block text-[11px] italic text-outline">Suggestion</span>
      ) : null}
    </div>
  );
}

/** The associate's own message. */
function Said({ text }: { text: string }) {
  return (
    <li className="flex justify-end">
      <p className="max-w-[85%] rounded-2xl rounded-tr-sm bg-primary px-3.5 py-2.5 text-sm leading-relaxed text-on-primary">
        {text}
      </p>
    </li>
  );
}

/**
 * Three drifting dots while the agent works.
 *
 * A spinner labelled "Searching..." states what the machine is doing. This is
 * the convention every person already reads as "they are replying", and a turn
 * here can take a while -- the wait is the part of the conversation most likely
 * to feel like talking to nothing.
 */
function Typing() {
  return (
    <li className="flex gap-3" aria-live="polite" aria-label="The agent is replying">
      <AgentAvatar />
      <span className="flex items-center gap-1 rounded-2xl rounded-tl-sm border border-outline-variant bg-surface-container-low px-3.5 py-3">
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            aria-hidden="true"
            className="size-1.5 animate-bounce rounded-full bg-outline"
            style={{ animationDelay: `${String(delay)}ms` }}
          />
        ))}
      </span>
    </li>
  );
}

/**
 * The RMAs raised for this case, each with what belongs to it.
 *
 * One panel per record, not one panel with the fields of the "current" RMA. A
 * multi-item return can produce two RMAs with different labels going to
 * different locations, and a single-valued panel has to pick one -- which would
 * send half the shipment to the wrong dock and look correct doing it.
 */
function ReturnRecordsPanel({ records }: { records: readonly CaseReturnRecord[] }) {
  if (records.length === 0) return null;
  return (
    <div className="mt-3 flex flex-col gap-2">
      {records.map(({ record, items }) => (
        <div
          key={record.returnRecordId}
          className="rounded border border-outline-variant bg-surface-container-low p-2"
        >
          <div className="flex items-baseline justify-between gap-2">
            <span className="truncate text-xs font-semibold text-on-surface">
              {record.returnReference ?? "RMA pending"}
            </span>
            <span className="shrink-0 text-[11px] text-outline">{record.status}</span>
          </div>
          <dl className="mt-1 flex flex-col gap-0.5">
            {(
              [
                ["Tracking", record.trackingReference],
                ["Label", record.labelReference],
                ["Return to", record.returnLocation],
                ["Pickup", record.shippingInstructionReference],
              ] as const
            ).flatMap(([label, value]) =>
              value == null || value === ""
                ? []
                : [
                    <div key={label} className="flex gap-2 text-[11px]">
                      <dt className="shrink-0 text-outline">{label}</dt>
                      <dd className="min-w-0 truncate text-on-surface" title={value}>
                        {value}
                      </dd>
                    </div>,
                  ],
            )}
          </dl>
          {items.length > 0 ? (
            // Which lines this RMA covers. Without it, two RMAs on one case are
            // indistinguishable to whoever has to pack the boxes.
            <p className="mt-1 truncate text-[11px] text-outline">
              Covers {items.map((item) => item.orderLineReference).join(", ")}
            </p>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function ProgressPane({
  context,
  anchors: supplied,
  returnRecords,
}: {
  context: ProgressContext;
  anchors: readonly ResponseStatement[];
  returnRecords: readonly CaseReturnRecord[];
}) {
  // The furthest milestone reached, not the count of reached ones. A later
  // milestone can report before an earlier one does -- a warehouse scan can
  // land before the fulfillment status updates -- and counting would then show
  // a gap in the middle of a return that is demonstrably further along.
  const furthest = MILESTONES.reduce(
    (best, milestone, index) => (milestone.reached(context) ? index : best),
    -1,
  );

  return (
    <Pane title="Progress">
      <div className="flex-1 overflow-y-auto p-4">
        <ol className="flex flex-col">
          {MILESTONES.map((milestone, index) => {
            const done = index <= furthest;
            const active = index === furthest + 1 && context.session !== null;
            const output = milestone.output(context);
            return (
              <li key={milestone.label} className="flex gap-3">
                <span className="flex flex-col items-center">
                  <span
                    className={[
                      "flex size-7 shrink-0 items-center justify-center rounded-full",
                      done
                        ? "bg-primary text-on-primary"
                        : active
                          ? "bg-secondary-container text-primary"
                          : "bg-surface-container text-outline",
                    ].join(" ")}
                  >
                    {done ? <CircleCheck size={15} /> : <CircleDashed size={15} />}
                  </span>
                  {index < MILESTONES.length - 1 ? (
                    <span aria-hidden="true" className="my-1 w-px flex-1 bg-outline-variant" />
                  ) : null}
                </span>

                <span className="min-w-0 pb-5">
                  <span
                    className={`block text-sm font-medium ${done || active ? "text-on-surface" : "text-outline"}`}
                  >
                    {milestone.label}
                  </span>
                  <span className="block text-xs text-outline">{milestone.agent}</span>

                  {output.length > 0 && !(milestone.label === "Case created" && returnRecords.length > 0) ? (
                    <dl className="mt-2 flex flex-col gap-1">
                      {output.map(([label, value]) => (
                        <div key={label} className="flex gap-2 text-xs">
                          <dt className="shrink-0 text-outline">{label}</dt>
                          <dd className="min-w-0 truncate font-medium text-on-surface" title={value}>
                            {value}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  ) : null}

                  {/*
                    Under "Case created", because that is the milestone these
                    belong to. Rendered instead of the milestone's own scalar
                    RMA/Tracking pair whenever records exist: those come from
                    `ReturnSessionView`, which can hold one of each, and showing
                    both would put a single-valued summary next to the list that
                    contradicts it.
                  */}
                  {milestone.label === "Case created" ? (
                    <ReturnRecordsPanel records={returnRecords} />
                  ) : null}

                  {index === 0 && supplied.length > 0 ? (
                    <span className="mt-2 flex flex-wrap gap-1.5">
                      {supplied.map((anchor) => (
                        <span
                          key={anchor.statement_id}
                          className="inline-flex items-center gap-1 rounded border border-outline-variant bg-surface-container-low px-2 py-0.5 text-xs text-on-surface-variant"
                        >
                          <Tag size={11} />
                          {anchor.text}
                        </span>
                      ))}
                    </span>
                  ) : null}
                </span>
              </li>
            );
          })}
        </ol>
      </div>
    </Pane>
  );
}

function ContextPane({
  turn,
  rows,
}: {
  turn: AgentTurnResult | null;
  rows: readonly Record<string, unknown>[];
}) {
  if (turn === null || rows.length === 0) {
    return (
      <Pane title="Context">
        <div className="flex flex-1 items-center justify-center p-6 text-center">
          <p className="max-w-xs text-sm text-on-surface-variant">
            {turn === null
              ? "Start by describing the order in the chat. Matches and their evidence appear here."
              : "The agent has not matched an order yet."}
          </p>
        </div>
      </Pane>
    );
  }

  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];

  return (
    <Pane title={`Candidates (${String(rows.length)})`}>
      <div className="flex-1 overflow-auto p-2">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="text-outline">
              {columns.map((column) => (
                <th key={column} className="px-2 py-1.5 font-medium">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index} className="border-t border-outline-variant">
                {columns.map((column) => (
                  <td key={column} className="px-2 py-1.5 text-on-surface">
                    {scalar(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Pane>
  );
}

/** Graph rows are `unknown`; `String()` on an object yields "[object Object]". */
function scalar(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "-";
}
