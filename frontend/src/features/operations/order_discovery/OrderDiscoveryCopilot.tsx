import { useEffect, useRef, useState, type SyntheticEvent } from "react";
import { Bot, Loader2, Send, Sparkles, UserRound } from "lucide-react";
import type { AssociateConversation } from "../../../contracts/associateReturns";
import { ErrorState } from "../../../components/ErrorState";

export type OrderDiscoveryCopilotProps = {
  readonly conversation: AssociateConversation | null;
  readonly onSendMessage: (text: string) => void;
  readonly isPending?: boolean;
  readonly error?: Error | null;
}

export function OrderDiscoveryCopilot({
  conversation,
  onSendMessage,
  isPending = false,
  error = null,
}: OrderDiscoveryCopilotProps) {
  const [message, setMessage] = useState("");
  const messages = conversation?.messages ?? [];
  const scrollRef = useRef<HTMLDivElement>(null);
  const isComplete = conversation?.status === "SUBMITTED";

  useEffect(() => {
    if (scrollRef.current) {
      if (messages.length > 0) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      } else {
        scrollRef.current.scrollTop = 0;
      }
    }
  }, [messages.length, isPending]);

  function handleSend(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = message.trim();
    if (text && !isPending) {
      onSendMessage(text);
      setMessage("");
    }
  }

  function handleExampleClick(exampleText: string) {
    if (!isPending) {
      onSendMessage(exampleText);
    }
  }

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-stone-50/50">
      {error ? (
        <div className="mx-5 mt-4 sm:mx-8">
          <ErrorState message={error.message} />
        </div>
      ) : null}

      <div ref={scrollRef} className="flex-1 space-y-5 overflow-y-auto px-5 py-6 sm:px-8">
        {messages.length === 0 ? (
          <div className="mx-auto mt-6 max-w-2xl text-center sm:text-left">
            <div className="mb-4 inline-flex rounded-2xl bg-teal-950 p-3.5 text-white shadow-md">
              <Sparkles size={24} />
            </div>
            <h1 className="text-3xl font-bold tracking-tight text-slate-950 sm:text-4xl">
              Order Discovery Copilot
            </h1>
            <p className="mt-2 max-w-xl text-sm leading-6 text-slate-600">
              Powered by Neo4j graph relationships and controlled full-text retrieval.
              Describe the return naturally—even with partial names, typos, or incomplete identifiers.
            </p>
            <div className="mt-6">
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">
                Search with exact, partial, or misspelled details:
              </p>
              <div className="grid gap-2.5 sm:grid-cols-2">
                {[
                  "Return for order SO-00010001",
                  "Customer name starts with 'Joh' zip code 30301",
                  "Faucet arrived damaged tracking 1Z9999",
                  "I have partial SKU 10001 and wrong spelling 'Fusset'",
                ].map((example) => (
                  <button
                    key={example}
                    type="button"
                    onClick={() => { handleExampleClick(example); }}
                    className="rounded-xl border border-stone-200 bg-white p-3.5 text-left text-xs leading-5 text-slate-700 shadow-xs transition hover:border-teal-600 hover:bg-teal-50/50 hover:text-teal-950"
                  >
                    “{example}”
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-5">
            {messages.map((item) => {
              const assistant = item.role === "AI_ASSISTANT";
              return (
                <div key={item.id} className={`flex gap-3 ${assistant ? "" : "justify-end"}`}>
                  {assistant ? (
                    <div className="mt-1 h-fit rounded-xl bg-teal-950 p-2 text-white shadow-xs">
                      <Bot size={17} />
                    </div>
                  ) : null}
                  <div
                    className={`max-w-[85%] rounded-2xl px-4 py-3.5 text-sm leading-6 ${
                      assistant
                        ? "border border-stone-200 bg-white text-slate-800 shadow-xs"
                        : "bg-slate-900 text-white shadow-sm"
                    }`}
                  >
                    <div className="mb-1 flex items-center justify-between gap-4">
                      <span className="text-[11px] font-semibold uppercase tracking-[0.16em] opacity-60">
                        {assistant ? "Discovery Agent" : "Associate"}
                      </span>
                      {assistant ? (
                        <span className="inline-flex items-center gap-1 text-[10px] font-medium text-teal-700">
                          <Sparkles size={11} className="text-teal-600" />
                          Controlled response
                        </span>
                      ) : null}
                    </div>
                    <div className="whitespace-pre-wrap font-normal">{item.content}</div>
                  </div>
                  {!assistant ? (
                    <div className="mt-1 h-fit rounded-xl border border-stone-200 bg-white p-2 text-slate-700 shadow-xs">
                      <UserRound size={17} />
                    </div>
                  ) : null}
                </div>
              );
            })}
            {isPending ? (
              <div className="flex gap-3">
                <div className="mt-1 h-fit rounded-xl bg-teal-950 p-2 text-white shadow-xs">
                  <Bot size={17} />
                </div>
                <div className="max-w-[85%] rounded-2xl border border-teal-200 bg-teal-50/70 px-4 py-3 text-sm leading-6 text-teal-950 shadow-xs flex items-center gap-2.5 animate-pulse">
                  <Loader2 className="animate-spin text-teal-700 shrink-0" size={17} />
                  <span className="font-medium">
                    Copilot is resolving verified graph and source evidence…
                  </span>
                </div>
              </div>
            ) : null}
          </div>
        )}
      </div>

      {!isComplete ? (
        <div className="border-t border-stone-200 bg-white p-4 sm:px-8">
          <form className="mx-auto max-w-3xl" onSubmit={handleSend}>
            <div className="flex items-end gap-2 rounded-2xl border border-stone-300 bg-white p-2 shadow-xs transition-all focus-within:border-teal-700 focus-within:ring-2 focus-within:ring-teal-100">
              <textarea
                rows={2}
                className="min-h-12 flex-1 resize-none border-0 bg-transparent px-2.5 py-2 text-sm leading-6 text-slate-900 placeholder:text-slate-400 outline-none"
                value={message}
                onChange={(event) => { setMessage(event.target.value); }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                placeholder="Type partial customer name, wrong spelling, SKU, or answer the copilot…"
                aria-label="Message the Discovery Copilot"
              />
              <button
                className="rounded-xl bg-teal-950 p-3 text-white transition hover:bg-teal-900 disabled:cursor-not-allowed disabled:opacity-40 flex items-center justify-center min-w-[42px] min-h-[42px] shadow-xs"
                disabled={isPending || !message.trim()}
                type="submit"
                aria-label="Send message"
              >
                {isPending ? <Loader2 className="animate-spin" size={18} /> : <Send size={18} />}
              </button>
            </div>
            <div className="mt-2 flex items-center justify-between text-[11px] text-slate-400 px-1">
              <span>Press Enter to send, Shift+Enter for newline</span>
              <span>Exact identifiers first · controlled full-text retrieval</span>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  );
}
