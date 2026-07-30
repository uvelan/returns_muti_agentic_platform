import { useEffect, useRef, useState, type SyntheticEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  CircleUserRound,
  FileSearch,
  History,
  Info,
  Menu,
  PackageSearch,
  Plus,
  RotateCcw,
  Send,
  Sparkles,
  TriangleAlert,
  Workflow,
  X,
} from "lucide-react";

import {
  COPILOT_V2_BASE,
  confirmAssociateDiscovery,
  continueAssociateChat,
  listAssociateConversations,
  startAssociateChat,
  submitAssociateReturnDetails,
} from "../../api/associateReturns";
import { ErrorState } from "../../components/ErrorState";
import type { AssociateConversation } from "../../contracts/associateReturns";
import { OrderContextPanel } from "../operations/order_discovery";

const v2ConversationKey = ["copilot-v2-conversations"] as const;

const suggestions = [
  {
    icon: RotateCcw,
    title: "Return for order SO-00010001",
    description: "Search by exact ID",
  },
  {
    icon: FileSearch,
    title: "Customer ZIP 30301",
    description: "Find recent regional orders",
  },
  {
    icon: TriangleAlert,
    title: "Faucet arrived damaged",
    description: "Search by item issue",
  },
  {
    icon: PackageSearch,
    title: "Partial SKU 10001",
    description: "Fuzzy product match",
  },
] as const;

type ContextTab = "context" | "recent" | "progress";

function CopilotConversation({
  conversation,
  isPending,
}: {
  readonly conversation: AssociateConversation | null;
  readonly isPending: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const messages = conversation?.messages ?? [];

  useEffect(() => {
    if (scrollRef.current && messages.length > 0) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages.length, isPending]);

  if (messages.length === 0) {
    return null;
  }

  return (
    <div ref={scrollRef} className="mx-auto w-full max-w-4xl flex-1 space-y-5 overflow-y-auto px-5 py-8 md:px-8">
      {messages.map((message) => {
        const assistant = message.role === "AI_ASSISTANT";
        return (
          <article
            key={message.id}
            className={`flex items-start gap-3 ${assistant ? "" : "justify-end"}`}
          >
            {assistant ? (
              <span className="mt-1 flex size-9 shrink-0 items-center justify-center rounded-xl bg-[#00685f] text-white">
                <Bot size={18} aria-hidden="true" />
              </span>
            ) : null}
            <div
              className={`max-w-[88%] rounded-2xl px-4 py-3 text-[15px] leading-6 shadow-sm ${
                assistant
                  ? "border border-[#bcc9c6] bg-white text-[#171d1c]"
                  : "bg-[#00685f] text-white"
              }`}
            >
              <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.12em] opacity-70">
                {assistant ? "Copilot" : "You"}
              </p>
              <p className="whitespace-pre-wrap">{message.content}</p>
            </div>
          </article>
        );
      })}
      {isPending ? (
        <div className="flex items-center gap-3 text-sm text-[#3d4947]">
          <span className="flex size-9 items-center justify-center rounded-xl bg-[#00685f] text-white">
            <Bot className="animate-pulse" size={18} />
          </span>
          <span>Resolving verified order evidence…</span>
        </div>
      ) : null}
    </div>
  );
}

export function CopilotV2Page() {
  const queryClient = useQueryClient();
  const [conversation, setConversation] = useState<AssociateConversation | null>(null);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [orderLineId, setOrderLineId] = useState("");
  const [message, setMessage] = useState("");
  const [contextOpen, setContextOpen] = useState(false);
  const [contextTab, setContextTab] = useState<ContextTab>("context");

  const sessions = useQuery({
    queryKey: v2ConversationKey,
    queryFn: ({ signal }) => listAssociateConversations(signal, COPILOT_V2_BASE),
    refetchInterval: 15_000,
  });

  const chat = useMutation({
    mutationFn: async (text: string) => (
      conversation
        ? continueAssociateChat({
          conversationId: conversation.id,
          message: text,
          expectedVersion: conversation.version,
        }, COPILOT_V2_BASE)
        : startAssociateChat({ message: text }, COPILOT_V2_BASE)
    ),
    onSuccess: (value) => {
      setConversation(value);
      setCandidateIndex(0);
      setOrderLineId(value.candidates.at(0)?.lines.at(0)?.orderLineId ?? "");
      setContextOpen(true);
      void queryClient.invalidateQueries({ queryKey: v2ConversationKey });
    },
  });

  const confirm = useMutation({
    mutationFn: (payload: Parameters<typeof confirmAssociateDiscovery>[0]) => (
      confirmAssociateDiscovery(payload, COPILOT_V2_BASE)
    ),
    onSuccess: setConversation,
  });

  const details = useMutation({
    mutationFn: (payload: Parameters<typeof submitAssociateReturnDetails>[0]) => (
      submitAssociateReturnDetails(payload, COPILOT_V2_BASE)
    ),
    onSuccess: (value) => {
      setConversation(value.conversation);
      void queryClient.invalidateQueries({ queryKey: v2ConversationKey });
    },
  });

  const error = chat.error ?? confirm.error ?? details.error;
  const isComplete = conversation?.status === "SUBMITTED";
  const hasMessages = Boolean(conversation?.messages.length);

  function resetConversation() {
    setConversation(null);
    setCandidateIndex(0);
    setOrderLineId("");
    setMessage("");
    setContextOpen(false);
  }

  function send(text: string) {
    const normalized = text.trim();
    if (!normalized || chat.isPending) return;
    chat.mutate(normalized);
    setMessage("");
  }

  function submitMessage(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    send(message);
  }

  const contextContent = contextTab === "context" ? (
    <OrderContextPanel
      conversation={conversation}
      candidateIndex={candidateIndex}
      selectedLineId={orderLineId}
      onSelectCandidate={setCandidateIndex}
      onSelectLine={setOrderLineId}
      onSelectClarification={(value) => { send(value); }}
      onConfirmDiscovery={() => {
        if (!conversation) return;
        confirm.mutate({
          conversationId: conversation.id,
          candidateIndex,
          orderLineId,
          expectedVersion: conversation.version,
          candidateSetId: conversation.candidateSetId,
        });
      }}
      isConfirming={confirm.isPending}
      isClarifying={chat.isPending}
      onSubmitDetails={(payload) => {
        if (!conversation) return;
        details.mutate({
          conversationId: conversation.id,
          ...payload,
          expectedVersion: conversation.version,
        });
      }}
      isSubmittingDetails={details.isPending}
    />
  ) : contextTab === "recent" ? (
    <div className="flex-1 overflow-y-auto p-4">
      <p className="mb-3 text-xs font-semibold uppercase tracking-[0.12em] text-[#6d7a77]">
        Recent conversations
      </p>
      <div className="space-y-2">
        {sessions.data?.slice(0, 20).map((session) => (
          <button
            key={session.id}
            type="button"
            className="w-full rounded-xl border border-[#bcc9c6] bg-white p-3 text-left transition hover:border-[#00685f]"
            onClick={() => {
              setConversation(session);
              setCandidateIndex(0);
              setOrderLineId(session.candidates.at(0)?.lines.at(0)?.orderLineId ?? "");
              setContextTab("context");
            }}
          >
            <span className="block truncate text-sm font-semibold text-[#171d1c]">
              {session.anchorValueMasked || "Return conversation"}
            </span>
            <span className="mt-1 block text-xs text-[#6d7a77]">
              {new Date(session.updatedAt).toLocaleString()} · {session.status}
            </span>
          </button>
        ))}
        {!sessions.isPending && !sessions.data?.length ? (
          <p className="rounded-xl border border-dashed border-[#bcc9c6] p-6 text-center text-sm text-[#6d7a77]">
            No recent conversations.
          </p>
        ) : null}
      </div>
    </div>
  ) : (
    <div className="flex-1 p-5">
      <div className="rounded-xl border border-[#bcc9c6] bg-white p-4">
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#00685f]">
          Discovery progress
        </p>
        <dl className="mt-4 space-y-3 text-sm">
          <div className="flex justify-between gap-4">
            <dt className="text-[#6d7a77]">State</dt>
            <dd className="text-right font-semibold text-[#171d1c]">
              {conversation?.activeDialogueState ?? "Ready"}
            </dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-[#6d7a77]">Candidates</dt>
            <dd className="font-semibold">{conversation?.candidates.length ?? 0}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-[#6d7a77]">Evidence lock</dt>
            <dd className="font-semibold">{conversation?.discoveryLock ? "Verified" : "Pending"}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-[#6d7a77]">Return workflow</dt>
            <dd className="font-semibold">{isComplete ? "Submitted" : "Not submitted"}</dd>
          </div>
        </dl>
      </div>
    </div>
  );

  return (
    <div className="flex h-dvh min-w-[320px] flex-col overflow-hidden bg-[#f5faf8] text-[#171d1c]">
      <a href="#copilot-composer" className="sr-only focus:not-sr-only">Skip to Copilot message</a>
      <header className="z-30 flex h-16 shrink-0 items-center justify-between border-b border-[#bcc9c6] bg-white px-4 shadow-sm md:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            className="rounded-lg p-1.5 text-[#3d4947] hover:bg-[#f0f5f2] lg:hidden"
            onClick={() => { setContextOpen(true); }}
            aria-label="Open context"
          >
            <Menu size={22} />
          </button>
          <RotateCcw className="hidden shrink-0 text-[#00685f] sm:block" size={24} />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-xl font-bold tracking-tight text-[#00685f] md:text-2xl">
                Returns Assistant
              </h1>
              <span className="hidden text-[#6d7a77] md:inline">/</span>
              <span className="hidden truncate text-sm font-semibold md:inline">Order Discovery Copilot</span>
              <span className="hidden items-center gap-1 rounded-full bg-[#eaefed] px-2 py-1 text-[11px] font-semibold text-[#00685f] xl:inline-flex">
                <span className="size-2 rounded-full bg-[#008378]" />Connected
              </span>
            </div>
            <p className="text-xs text-[#555f6d] md:hidden">Agentic AI v2.0</p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <div className="hidden text-right lg:block">
            <p className="text-xs font-semibold uppercase tracking-[0.08em]">Agentic AI v2.0</p>
            <p className="text-xs text-[#6d7a77]">Graph-first order discovery</p>
          </div>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg bg-[#00685f] px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-[#005049]"
            onClick={resetConversation}
          >
            <Plus size={16} /><span className="hidden sm:inline">New return</span>
          </button>
          <CircleUserRound className="text-[#00685f]" size={26} aria-label="Associate profile" />
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col bg-[#f9fafb]">
          {error ? <div className="mx-5 mt-4"><ErrorState message={error.message} /></div> : null}
          {!hasMessages ? (
            <section className="flex flex-1 items-center overflow-y-auto px-5 py-8 md:px-8">
              <div className="mx-auto w-full max-w-4xl text-center">
                <span className="mx-auto flex size-16 items-center justify-center rounded-full border border-[#89f5e7] bg-[#e5f4f1] text-[#00685f] shadow-sm">
                  <Bot size={30} />
                </span>
                <h2 className="mt-5 text-3xl font-semibold tracking-[-0.02em] md:text-4xl">
                  How can I help you today?
                </h2>
                <p className="mx-auto mt-2 max-w-2xl text-[15px] leading-6 text-[#6d7a77]">
                  I can find orders using partial information, context clues, misspellings, or natural language.
                </p>
                <div className="mt-8 grid gap-3 text-left sm:grid-cols-2 xl:grid-cols-3">
                  {suggestions.map((suggestion) => (
                    <button
                      key={suggestion.title}
                      type="button"
                      className="rounded-xl border border-[#dee4e1] bg-white p-4 shadow-sm transition hover:-translate-y-0.5 hover:border-[#00685f] hover:shadow-md"
                      onClick={() => { send(suggestion.title); }}
                    >
                      <suggestion.icon className="mb-3 text-[#6d7a77]" size={22} />
                      <span className="block text-sm font-semibold">{suggestion.title}</span>
                      <span className="mt-1 block text-xs text-[#6d7a77]">{suggestion.description}</span>
                    </button>
                  ))}
                </div>
              </div>
            </section>
          ) : (
            <CopilotConversation conversation={conversation} isPending={chat.isPending} />
          )}

          {!isComplete ? (
            <div className="shrink-0 border-t border-[#bcc9c6] bg-white p-4 md:px-7 md:py-5">
              <form className="mx-auto flex max-w-4xl items-end gap-2" onSubmit={submitMessage}>
                <div className="flex flex-1 items-end rounded-xl border border-[#bcc9c6] bg-white px-3 py-2 shadow-sm focus-within:border-[#00685f] focus-within:ring-4 focus-within:ring-[#00685f]/10">
                  <textarea
                    id="copilot-composer"
                    rows={2}
                    className="max-h-28 min-h-12 flex-1 resize-none border-0 bg-transparent px-1 py-2 text-[15px] outline-none placeholder:text-[#6d7a77]"
                    value={message}
                    onChange={(event) => { setMessage(event.target.value); }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        event.currentTarget.form?.requestSubmit();
                      }
                    }}
                    placeholder="Search by order number, customer, SKU, or describe the situation…"
                    aria-label="Ask Copilot about returns"
                  />
                </div>
                <button
                  type="submit"
                  className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-[#00685f] text-white shadow-sm hover:bg-[#005049] disabled:opacity-40"
                  disabled={chat.isPending || !message.trim()}
                  aria-label="Send message"
                >
                  {chat.isPending ? <Sparkles className="animate-pulse" size={20} /> : <Send size={20} />}
                </button>
              </form>
            </div>
          ) : null}
        </main>

        <aside className="hidden w-[35%] min-w-[320px] max-w-[400px] flex-col border-l border-[#bcc9c6] bg-[#f5faf8] lg:flex">
          <div className="flex border-b border-[#bcc9c6] bg-white px-3 pt-1">
            {([
              ["context", Info, "Context"],
              ["recent", History, "Recent"],
              ["progress", Workflow, "Progress"],
            ] as const).map(([tab, Icon, label]) => (
              <button
                key={tab}
                type="button"
                className={`flex items-center gap-1.5 border-b-2 px-3 py-4 text-sm font-medium ${
                  contextTab === tab
                    ? "border-[#00685f] text-[#00685f]"
                    : "border-transparent text-[#3d4947]"
                }`}
                onClick={() => { setContextTab(tab); }}
              >
                <Icon size={17} />{label}
              </button>
            ))}
          </div>
          {contextContent}
        </aside>
      </div>

      <button
        type="button"
        className="fixed bottom-24 right-4 z-20 inline-flex items-center gap-2 rounded-full border border-[#bcc9c6] bg-white px-4 py-2 text-sm font-semibold shadow-md lg:hidden"
        onClick={() => { setContextOpen(true); }}
      >
        <Info size={16} />Context
      </button>

      {contextOpen ? (
        <div className="fixed inset-0 z-50 bg-black/30 lg:hidden" onClick={() => { setContextOpen(false); }}>
          <aside
            className="absolute inset-y-0 right-0 flex w-[min(92vw,420px)] flex-col bg-[#f5faf8] shadow-2xl"
            onClick={(event) => { event.stopPropagation(); }}
          >
            <div className="flex h-16 items-center justify-between border-b border-[#bcc9c6] bg-white px-4">
              <span className="font-semibold text-[#00685f]">Order context</span>
              <button type="button" onClick={() => { setContextOpen(false); }} aria-label="Close context">
                <X size={22} />
              </button>
            </div>
            <div className="flex border-b border-[#bcc9c6] bg-white px-2">
              {(["context", "recent", "progress"] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  className={`flex-1 border-b-2 px-2 py-3 text-xs font-semibold capitalize ${
                    contextTab === tab ? "border-[#00685f] text-[#00685f]" : "border-transparent"
                  }`}
                  onClick={() => { setContextTab(tab); }}
                >
                  {tab}
                </button>
              ))}
            </div>
            {contextContent}
          </aside>
        </div>
      ) : null}

      <span className="sr-only" aria-live="polite">
        {chat.isPending ? "Copilot is searching" : ""}
        {sessions.isPending ? " Loading recent conversations" : ""}
      </span>
    </div>
  );
}
