import { Bot, History, Loader2, MapPin, Package, Plus, Send, Tag, User } from "lucide-react";
import type { ConversationSummary, ResponseStatement } from "../../../api/orderAgent";
import type { CaseSummary } from "../../../api/cases";
import { COPILOT_TOKENS } from "../copilotTokens";

export type ChatHistoryEntry =
  | { role: "associate"; id: string; text: string }
  | {
      role: "agent";
      id: string;
      statements: readonly ResponseStatement[];
      status: string;
    }
  | { role: "restored"; id: string; author: string; text: string };

export type ConversationPaneProps = {
  history: readonly ChatHistoryEntry[];
  draft: string;
  onDraftChange: (text: string) => void;
  onSubmit: (text: string) => void;
  onReset: () => void;
  isPending: boolean;
  /**
   * The composer cannot be used at all -- distinct from `isPending`, which says
   * a turn is in flight.
   *
   * They were the same lever until this prop existed, and the page had nothing
   * else to reach for when the agent id is unconfigured. Reusing `isPending`
   * there disabled the box correctly and also rendered "Searching order
   * graph..." underneath a configuration error, describing a search that was
   * never started. Fabricated state on screen is the one thing this surface
   * must not do, so the two conditions are now separate: `isPending` still owns
   * the spinner, `disabled` only takes the composer away.
   */
  disabled?: boolean;
  /**
   * Seconds the in-flight turn has been waiting.
   *
   * The screen had exactly two states -- "searching" and a terminal error -- so
   * a nine-minute wait and a nine-second one looked identical while they were
   * happening. An associate on a call had no signal and no estimate.
   */
  waitingSeconds?: number;
  /**
   * Withdraws from the wait. Absent when there is nothing to withdraw from.
   *
   * Client-side: the server keeps working on the turn it accepted. The control
   * says "Stop waiting" rather than "Cancel" because that is what it does, and
   * claiming to have cancelled server work this cannot reach would be the same
   * class of fabrication as the spinner that ran under a configuration error.
   */
  onStopWaiting?: () => void;
  error: Error | null;
  conversations: readonly ConversationSummary[];
  openCases: readonly CaseSummary[];
  /**
   * Resume a return, and say which case it is when the row knows.
   *
   * **An id, not state.** The rule this pane lives under is that only ids
   * survive a navigation and only in the URL, because a cached RMA or policy
   * decision is a copy of something the platform owns and a copy outlives the
   * fact. A case id is the opposite of that: it is the handle the page re-reads
   * the truth with.
   *
   * The open-cases rows carry `caseId` -- it is the React key one line down --
   * and used to drop it, leaving the page to re-learn it with a second
   * `casesApi.list(conversationId)` and take `.at(0)`. That is a round trip for
   * something already in hand, and its failure mode is silent: an empty or
   * failed lookup resolved to `null`, the case id came out of the URL, and the
   * associate got a resumed conversation with no return attached to it and a
   * progress stepper reading "Ready".
   *
   * `undefined` from the conversation-history rows, where the case genuinely is
   * unknown -- those are past *searches*, and most of them never raised a case.
   */
  onOpen: (conversationId: string, caseId?: string) => void;
  /**
   * Why the last attempt to resume a return did not open one.
   *
   * A row whose conversation the platform cannot serve -- deleted, or never
   * persisted -- answers 404, and the mutation behind `onOpen` failed with
   * nothing reading its error: the associate clicked a return in their own
   * history and the screen did nothing at all. A dead row has to say so, and
   * it must not take the rest of the list with it.
   */
  openError: Error | null;
  showHistory: boolean;
  onToggleHistory: () => void;
};

export function ConversationPane({
  history,
  draft,
  onDraftChange,
  onSubmit,
  onReset,
  isPending,
  waitingSeconds,
  onStopWaiting,
  disabled = false,
  error,
  conversations,
  openCases,
  onOpen,
  openError,
  showHistory,
  onToggleHistory,
}: ConversationPaneProps) {
  const quickPrompts = [
    { icon: Tag, label: "Sales Order #", query: "Sales Order " },
    { icon: User, label: "Customer / Account", query: "Customer " },
    { icon: Package, label: "Product SKU", query: "SKU " },
    { icon: MapPin, label: "Branch / Location", query: "Branch " },
  ];

  return (
    <section className={COPILOT_TOKENS.layout.pane}>
      {/* 1. Locked Pane Header (56px) */}
      <header className={COPILOT_TOKENS.header.container}>
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary-container text-white shadow-sm">
            <Bot size={20} />
          </div>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-on-surface truncate">
              Discovery Agent
            </h2>
            <div className="flex items-center gap-1.5 text-xs text-outline">
              <span className="size-1.5 rounded-full bg-emerald-500" aria-hidden="true" />
              <span>Ready to help</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={onReset}
            className="flex items-center gap-1.5 rounded-lg bg-primary-container px-3.5 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:bg-primary-container/90"
          >
            <Plus size={14} />
            <span>New return</span>
          </button>
          <button
            type="button"
            aria-label="Previous returns"
            aria-expanded={showHistory}
            onClick={onToggleHistory}
            className={[
              "flex size-8 items-center justify-center rounded-lg border transition",
              showHistory
                ? "border-primary bg-primary/10 text-primary"
                : "border-outline-control text-on-surface-variant hover:border-primary hover:text-primary",
            ].join(" ")}
          >
            <History size={15} />
          </button>
        </div>
      </header>

      {/* When showHistory is TRUE -> Show Full-Height History Selection (Chat is minimized) */}
      {showHistory ? (
        <div className="flex-1 overflow-y-auto p-4 bg-surface-container-low/40 flex flex-col gap-4">
          <div>
            <div className="flex items-center justify-between border-b border-outline-variant/20 pb-2 mb-3">
              <span className="text-xs font-semibold uppercase tracking-wider text-outline">
                Resume Previous Conversation
              </span>
              <button
                type="button"
                onClick={onToggleHistory}
                className="text-xs font-medium text-primary hover:underline"
              >
                Back to chat
              </button>
            </div>

            {openError === null ? null : (
              <p
                role="alert"
                className="mb-3 rounded-xl border border-error/40 bg-error-container/40 p-3 text-xs text-on-error-container"
              >
                That return could not be opened: {openError.message} The other rows still work.
              </p>
            )}

            {openCases.length > 0 ? (
              <div className="mb-4">
                <span className="block text-xs font-semibold text-outline mb-1.5">Open returns</span>
                <ul className="flex flex-col gap-1.5">
                  {openCases.map((openCase) => (
                    <li key={openCase.caseId}>
                      <button
                        type="button"
                        disabled={openCase.channelAConversationId === null}
                        onClick={() => {
                          if (openCase.channelAConversationId !== null) {
                            // Both ids, because this row holds both.
                            onOpen(openCase.channelAConversationId, openCase.caseId);
                          }
                        }}
                        className="flex w-full items-center justify-between rounded-xl border border-outline-control bg-surface-container-lowest p-3 text-left text-xs transition hover:border-primary hover:bg-surface-container-lowest/90 shadow-xs disabled:opacity-40"
                      >
                        <span className="truncate font-semibold text-on-surface text-sm">
                          {openCase.confirmedOrderReference ?? openCase.caseId}
                        </span>
                        <span className="rounded-full bg-secondary-container px-2 py-0.5 text-xs font-medium text-primary">
                          {openCase.returnRecordCount > 0
                            ? `${String(openCase.returnRecordCount)} RMA${openCase.returnRecordCount === 1 ? "" : "s"}`
                            : openCase.status}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {conversations.length === 0 ? (
              <div className="flex flex-col items-center justify-center p-8 text-center text-xs text-outline rounded-xl border border-dashed border-outline-variant/40 bg-surface-container-lowest/50">
                <p>Nothing yet. Past searches and returns appear here.</p>
              </div>
            ) : (
              <div>
                <span className="block text-xs font-semibold text-outline mb-1.5">Past Searches</span>
                <ul className="flex flex-col gap-1.5">
                  {conversations.map((conversation) => (
                    <li key={conversation.conversationId}>
                      <button
                        type="button"
                        onClick={() => {
                          onOpen(conversation.conversationId);
                        }}
                        className="flex w-full items-center justify-between rounded-xl border border-outline-control bg-surface-container-lowest p-3 text-left text-xs transition hover:border-primary hover:bg-surface-container-lowest/90 shadow-xs"
                      >
                        <span className="truncate font-medium text-on-surface text-sm">
                          {conversation.title}
                        </span>
                        <span className="text-outline text-xs">
                          {conversation.updatedAt?.slice(0, 10) ?? ""}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      ) : (
        /* When showHistory is FALSE -> Show Standard Chat Stream & Dock */
        <>
          {/* 2. Message History Stream */}
          <div className={COPILOT_TOKENS.layout.messageStream}>
            <div className="flex flex-col gap-4">
              {/* Quick Prompts Bar */}
              {history.length === 0 ? (
                <div className="flex gap-1.5 overflow-x-auto pb-2 shrink-0 hide-scrollbar">
                  {quickPrompts.map((item) => (
                    <button
                      key={item.label}
                      type="button"
                      onClick={() => {
                        onDraftChange(item.query);
                      }}
                      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-outline-control bg-surface px-3 py-1 text-xs font-medium text-on-surface-variant transition hover:border-primary hover:bg-surface-variant"
                    >
                      <item.icon size={13} className="text-secondary" />
                      <span>{item.label}</span>
                    </button>
                  ))}
                </div>
              ) : null}

              <ol className="flex flex-col gap-4">
                {history.length === 0 ? (
                  <li className="flex gap-3 max-w-[90%]">
                    <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-secondary-container text-primary-container mt-0.5">
                      <Bot size={16} />
                    </div>
                    <div className="rounded-2xl rounded-tl-sm border border-outline-variant/20 bg-surface-container-low p-4 text-sm leading-relaxed text-on-surface shadow-[0_4px_24px_rgba(0,0,0,0.02)]">
                      <p>
                        Hi -- let&apos;s find that order. Tell me whatever the customer gave you: an order
                        number, their name, the product, roughly when it arrived. Anything is a start.
                      </p>
                    </div>
                  </li>
                ) : null}

                {history.map((message) => {
                  if (message.role === "associate") {
                    return (
                      <li key={message.id} className="flex justify-end">
                        <p className="max-w-[85%] rounded-2xl rounded-tr-sm bg-primary-container px-4 py-3 text-sm leading-relaxed text-white shadow-sm">
                          {message.text}
                        </p>
                      </li>
                    );
                  }

                  if (message.role === "restored") {
                    const isMe = message.author === "associate";
                    return (
                      <li key={message.id} className={`flex ${isMe ? "justify-end" : "justify-start"}`}>
                        <p
                          className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                            isMe
                              ? "rounded-tr-sm bg-primary-container text-white shadow-sm"
                              : "rounded-tl-sm border border-outline-variant/20 bg-surface-container-low text-on-surface"
                          }`}
                        >
                          {message.text}
                        </p>
                      </li>
                    );
                  }

                  return (
                    <li key={message.id} className="flex gap-3 max-w-[88%]">
                      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-secondary-container text-primary-container mt-0.5">
                        <Bot size={16} />
                      </div>
                      <div className="flex flex-col gap-2 flex-1">
                        {message.statements.length === 0 ? (
                          <p className="rounded-2xl rounded-tl-sm border border-outline-variant/20 bg-surface-container-low px-4 py-3 text-sm text-on-surface leading-relaxed shadow-[0_4px_24px_rgba(0,0,0,0.02)]">
                            Matches found in records.
                          </p>
                        ) : null}
                        {message.statements.map((statement) => (
                          <div
                            key={statement.statement_id}
                            className="rounded-2xl rounded-tl-sm border border-outline-variant/20 bg-surface-container-low px-4 py-3 text-sm text-on-surface leading-relaxed shadow-[0_4px_24px_rgba(0,0,0,0.02)]"
                          >
                            {statement.statement_type === "GRAPH_FACT" ? (
                              <span className="mb-1 block text-xs font-bold uppercase tracking-wider text-primary">
                                From order records
                              </span>
                            ) : null}
                            {statement.statement_type === "REASONED_SUGGESTION" ? (
                              <span className="mb-1 block text-xs font-bold uppercase tracking-wider text-secondary">
                                Suggestion
                              </span>
                            ) : null}
                            <p>{statement.text}</p>
                          </div>
                        ))}
                      </div>
                    </li>
                  );
                })}

                {isPending && !disabled ? (
                  <li className="flex gap-3">
                    <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-secondary-container text-primary animate-pulse">
                      <Bot size={16} />
                    </div>
                    {/* "Searching order graph" named the wrong subsystem and
                        named it confidently. The graph read is milliseconds; the
                        wait is the reasoning routes, tried in series, each with
                        its own timeout -- so the screen spent minutes crediting
                        a datastore that had already answered. What the associate
                        can actually act on is that work is still in flight, how
                        long it has been, and the way out, so that is all this
                        says now.

                        The spinner carries the motion because `.animate-spin` is
                        the one animation index.css exempts from reduce-motion:
                        it is the only signal that the screen is not hung, and a
                        frozen one reads as hung. The dots are decorative and
                        collapse with everything else under that setting. */}
                    <div
                      role="status"
                      className="flex items-center gap-2 rounded-2xl rounded-tl-sm border border-outline-variant/20 bg-surface-container-low px-4 py-2.5 text-xs text-outline"
                    >
                      <Loader2 size={14} className="animate-spin text-primary" aria-hidden="true" />
                      <span>
                        Searching
                        <span aria-hidden="true" className="ml-px inline-flex">
                          {[0, 1, 2].map((index) => (
                            <span
                              key={index}
                              className="animate-bounce"
                              style={{ animationDelay: `${String(index * 150)}ms` }}
                            >
                              .
                            </span>
                          ))}
                        </span>
                        {typeof waitingSeconds === "number" && waitingSeconds > 0
                          ? ` ${String(waitingSeconds)}s`
                          : ""}
                      </span>
                      {onStopWaiting ? (
                        <button
                          type="button"
                          onClick={onStopWaiting}
                          className="ml-1 rounded border border-outline-control px-2 py-0.5 text-[11px] text-on-surface hover:bg-surface-container"
                        >
                          Stop waiting
                        </button>
                      ) : null}
                    </div>
                  </li>
                ) : null}
              </ol>

              {error ? (
                <div role="alert" className="mt-2 rounded-xl border border-error/20 bg-error-container p-3 text-xs text-error">
                  {error.message}
                </div>
              ) : null}
            </div>
          </div>

          {/* 3. Locked Bottom Input Dock */}
          <form
            className={COPILOT_TOKENS.chatDock.container}
            onSubmit={(event) => {
              event.preventDefault();
              onSubmit(draft);
            }}
          >
            <div className="relative flex items-center">
              <input
                aria-label="Message the discovery agent"
                value={draft}
                disabled={isPending || disabled}
                onChange={(event) => {
                  onDraftChange(event.target.value);
                }}
                placeholder="Type an order number, customer name, SKU, or question..."
                className={COPILOT_TOKENS.chatDock.input}
              />
              <button
                type="submit"
                aria-label="Send"
                disabled={draft.trim().length === 0 || isPending || disabled}
                className="absolute right-2 flex size-9 items-center justify-center rounded-lg bg-secondary-container text-primary transition hover:bg-secondary-container/80 disabled:opacity-40"
              >
                <Send size={16} />
              </button>
            </div>
            <p className={COPILOT_TOKENS.chatDock.hint}>
              Press Enter to send message
            </p>
          </form>
        </>
      )}
    </section>
  );
}
