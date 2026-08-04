import {
  useEffect,
  useRef,
  useState,
  type SyntheticEvent,
} from "react";
import { useMutation } from "@tanstack/react-query";
import {
  Bot,
  CircleUserRound,
  FileSearch,
  Info,
  Menu,
  PackageSearch,
  Plus,
  RotateCcw,
  Send,
  Sparkles,
  TriangleAlert,
  X,
} from "lucide-react";

import {
  ORDER_AGENT_ID,
  processOrderAgentTurn,
} from "../../api/orderAgent";
import { ErrorState } from "../../components/ErrorState";
import type {
  OrderAgentQueryEvidence,
  OrderAgentTurnRequest,
  OrderAgentTurnResult,
  StructuredOrderAgentResponse,
} from "../../contracts/orderAgent";

const suggestions = [
  {
    icon: RotateCcw,
    title: "Customer Jane Doe wants to return a faucet",
    description: "Search by customer and item",
  },
  {
    icon: FileSearch,
    title: "Find a recent order in zip 90210",
    description: "Regional search",
  },
  {
    icon: TriangleAlert,
    title: "Order SO-00010001 arrived completely scratched",
    description: "Report an issue directly",
  },
  {
    icon: PackageSearch,
    title: "Looking for a brass showerhead purchased last week",
    description: "Semantic graph search",
  },
] as const;

type UiMessage = {
  readonly id: string;
  readonly role: "ASSOCIATE" | "AI_ASSISTANT";
  readonly content: string;
};

function newId(): string {
  return crypto.randomUUID();
}

function responseText(response: StructuredOrderAgentResponse): string {
  const parts = response.statements
    .map((statement) => statement.text.trim())
    .filter((text) => text.length > 0);
  if (response.requested_input && !parts.includes(response.requested_input)) {
    parts.push(response.requested_input);
  }
  return parts.join("\n\n") || "No displayable response was returned.";
}

function Conversation({
  messages,
  pending,
}: {
  readonly messages: readonly UiMessage[];
  readonly pending: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current && messages.length > 0) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages.length, pending]);

  return (
    <div
      ref={scrollRef}
      className="mx-auto w-full max-w-4xl flex-1 space-y-5 overflow-y-auto px-5 py-8 md:px-8"
    >
      {messages.map((message) => {
        const assistant = message.role === "AI_ASSISTANT";
        return (
          <article
            key={message.id}
            className={`flex gap-3 ${assistant ? "" : "justify-end"}`}
          >
            {assistant ? (
              <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-[#00685f] text-white">
                <Bot size={18} />
              </span>
            ) : null}
            <div
              className={`max-w-[88%] rounded-2xl px-4 py-3 text-[15px] leading-6 shadow-sm ${
                assistant
                  ? "border border-[#bcc9c6] bg-white"
                  : "bg-[#00685f] text-white"
              }`}
            >
              <p className="mb-1 text-[11px] font-semibold uppercase opacity-70">
                {assistant ? "Order Agent" : "You"}
              </p>
              <p className="whitespace-pre-wrap">{message.content}</p>
            </div>
          </article>
        );
      })}
      {pending ? (
        <div className="flex items-center gap-3 text-sm text-[#3d4947]">
          <Bot className="animate-pulse" size={18} />
          Resolving verified graph evidence…
        </div>
      ) : null}
    </div>
  );
}

export function CopilotV2Page() {
  const [conversationId, setConversationId] = useState(newId);
  const [version, setVersion] = useState(0);
  const [messages, setMessages] = useState<readonly UiMessage[]>([]);
  const [message, setMessage] = useState("");
  const [response, setResponse] =
    useState<StructuredOrderAgentResponse | null>(null);
  const [evidence, setEvidence] =
    useState<readonly OrderAgentQueryEvidence[]>([]);
  const [modelRoute, setModelRoute] = useState<string | null>(null);
  const [contextOpen, setContextOpen] = useState(false);
  const [failedTurn, setFailedTurn] =
    useState<OrderAgentTurnRequest | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const messageRef = useRef<readonly UiMessage[]>([]);

  const chat = useMutation<
    OrderAgentTurnResult,
    Error,
    OrderAgentTurnRequest
  >({
    mutationFn: (turn) => processOrderAgentTurn(turn),
    retry: false,
    onSuccess: (result) => {
      const nextMessages = [
        ...messageRef.current,
        {
          id: newId(),
          role: "AI_ASSISTANT" as const,
          content: responseText(result.response),
        },
      ];
      messageRef.current = nextMessages;
      setMessages(nextMessages);
      setConversationId(result.conversation_id);
      setVersion(result.conversation_version);
      setResponse(result.response);
      setEvidence(result.query_evidence);
      setModelRoute(`${result.model_provider} / ${result.model_name}`);
      setFailedTurn(null);
      setErrorMessage(null);
      setContextOpen(true);
    },
    onError: (error, turn) => {
      setFailedTurn(turn);
      setErrorMessage(error.message);
    },
  });

  function submitTurn(turn: OrderAgentTurnRequest) {
    if (!messageRef.current.some((item) => item.id === turn.message_id)) {
      const nextMessages = [
        ...messageRef.current,
        {
          id: turn.message_id,
          role: "ASSOCIATE" as const,
          content: turn.message,
        },
      ];
      messageRef.current = nextMessages;
      setMessages(nextMessages);
    }
    setFailedTurn(null);
    setErrorMessage(null);
    chat.mutate(turn);
  }

  function send(text: string) {
    const normalized = text.trim();
    if (!normalized || chat.isPending) return;
    submitTurn({
      conversation_id: conversationId,
      expected_conversation_version: version,
      client_turn_id: newId(),
      idempotency_key: newId(),
      message_id: newId(),
      message: normalized,
      agent_id: ORDER_AGENT_ID,
    });
    setMessage("");
  }

  function resetConversation() {
    messageRef.current = [];
    setConversationId(newId());
    setVersion(0);
    setMessages([]);
    setMessage("");
    setResponse(null);
    setEvidence([]);
    setModelRoute(null);
    setFailedTurn(null);
    setErrorMessage(null);
    setContextOpen(false);
    chat.reset();
  }

  function submitMessage(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    send(message);
  }

  const contextPanel = (
    <div className="flex-1 overflow-y-auto p-4">
      <div className="rounded-xl border border-[#bcc9c6] bg-white p-4">
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#00685f]">
          Verified graph context
        </p>
        <dl className="mt-4 space-y-3 text-sm">
          <div className="flex justify-between gap-4">
            <dt className="text-[#6d7a77]">Status</dt>
            <dd className="font-semibold">{response?.status ?? "READY"}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-[#6d7a77]">Version</dt>
            <dd className="font-semibold">{version}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-[#6d7a77]">Evidence queries</dt>
            <dd className="font-semibold">{evidence.length}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-[#6d7a77]">Model route</dt>
            <dd className="max-w-[190px] text-right font-semibold">
              {modelRoute ?? "Not invoked"}
            </dd>
          </div>
        </dl>
        {response?.requested_input ? (
          <p className="mt-4 rounded-lg bg-[#f0f5f2] p-3 text-sm">
            {response.requested_input}
          </p>
        ) : null}
      </div>
    </div>
  );

  return (
    <div className="flex h-dvh min-w-[320px] flex-col overflow-hidden bg-[#f5faf8] text-[#171d1c]">
      <header className="flex h-16 items-center justify-between border-b border-[#bcc9c6] bg-white px-4 md:px-6">
        <div className="flex items-center gap-3">
          <button
            type="button"
            aria-label="Open context"
            className="rounded-lg p-1.5 lg:hidden"
            onClick={() => {
              setContextOpen(true);
            }}
          >
            <Menu size={22} />
          </button>
          <RotateCcw className="text-[#00685f]" size={24} />
          <div>
            <h1 className="text-xl font-bold text-[#00685f]">
              Returns Assistant
            </h1>
            <p className="text-xs text-[#6d7a77]">
              Dynamic graph-first Order Discovery Agent
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg bg-[#00685f] px-3 py-2 text-sm font-semibold text-white"
            onClick={resetConversation}
          >
            <Plus size={16} />
            New return
          </button>
          <CircleUserRound
            className="text-[#00685f]"
            size={26}
            aria-label="Associate profile"
          />
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col bg-[#f9fafb]">
          {errorMessage ? (
            <div className="mx-5 mt-4 space-y-2">
              <ErrorState message={errorMessage} />
              {failedTurn ? (
                <button
                  type="button"
                  className="rounded-lg border border-[#00685f] px-3 py-2 text-sm font-semibold text-[#00685f]"
                  disabled={chat.isPending}
                  onClick={() => {
                    submitTurn(failedTurn);
                  }}
                >
                  Retry same turn
                </button>
              ) : null}
            </div>
          ) : null}

          {messages.length === 0 ? (
            <section className="flex flex-1 items-center overflow-y-auto px-5 py-8">
              <div className="mx-auto w-full max-w-4xl text-center">
                <Bot className="mx-auto text-[#00685f]" size={48} />
                <h2 className="mt-5 text-3xl font-semibold">
                  How can I help you today?
                </h2>
                <p className="mx-auto mt-2 max-w-2xl text-sm text-[#6d7a77]">
                  AI runs only after you submit a discovery request.
                </p>
                <div className="mt-8 grid gap-3 text-left sm:grid-cols-2">
                  {suggestions.map((suggestion) => (
                    <button
                      key={suggestion.title}
                      type="button"
                      className="rounded-xl border border-[#dee4e1] bg-white p-4 shadow-sm hover:border-[#00685f]"
                      onClick={() => {
                        send(suggestion.title);
                      }}
                    >
                      <suggestion.icon
                        className="mb-3 text-[#6d7a77]"
                        size={22}
                      />
                      <span className="block text-sm font-semibold">
                        {suggestion.title}
                      </span>
                      <span className="mt-1 block text-xs text-[#6d7a77]">
                        {suggestion.description}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </section>
          ) : (
            <Conversation messages={messages} pending={chat.isPending} />
          )}

          {response?.suggestions.length ? (
            <div className="mx-auto flex w-full max-w-4xl flex-wrap gap-2 px-5 pb-3">
              {response.suggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  disabled={chat.isPending}
                  className="rounded-full border border-[#00685f] bg-white px-3 py-2 text-sm text-[#00685f]"
                  onClick={() => {
                    send(suggestion);
                  }}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          ) : null}

          <div className="border-t border-[#bcc9c6] bg-white p-4">
            <form
              className="mx-auto flex max-w-4xl items-end gap-2"
              onSubmit={submitMessage}
            >
              <textarea
                id="copilot-composer"
                rows={2}
                className="min-h-12 flex-1 resize-none rounded-xl border border-[#bcc9c6] px-3 py-2"
                value={message}
                onChange={(event) => {
                  setMessage(event.target.value);
                }}
                placeholder="Search by order, customer, SKU, or natural language…"
                aria-label="Ask Copilot about returns"
              />
              <button
                type="submit"
                aria-label="Send message"
                disabled={chat.isPending || !message.trim()}
                className="flex size-12 items-center justify-center rounded-xl bg-[#00685f] text-white disabled:opacity-40"
              >
                {chat.isPending ? (
                  <Sparkles className="animate-pulse" size={20} />
                ) : (
                  <Send size={20} />
                )}
              </button>
            </form>
          </div>
        </main>

        <aside className="hidden w-[360px] border-l border-[#bcc9c6] bg-[#f5faf8] lg:flex">
          {contextPanel}
        </aside>
      </div>

      <button
        type="button"
        className="fixed bottom-24 right-4 rounded-full border bg-white px-4 py-2 text-sm font-semibold shadow-md lg:hidden"
        onClick={() => {
          setContextOpen(true);
        }}
      >
        <Info size={16} className="mr-1 inline" />
        Context
      </button>

      {contextOpen ? (
        <div
          className="fixed inset-0 z-50 bg-black/30 lg:hidden"
          onClick={() => {
            setContextOpen(false);
          }}
        >
          <aside
            className="absolute inset-y-0 right-0 flex w-[min(92vw,420px)] flex-col bg-[#f5faf8]"
            onClick={(event) => {
              event.stopPropagation();
            }}
          >
            <div className="flex h-16 items-center justify-between border-b bg-white px-4">
              <span className="font-semibold text-[#00685f]">
                Order context
              </span>
              <button
                type="button"
                aria-label="Close context"
                onClick={() => {
                  setContextOpen(false);
                }}
              >
                <X size={22} />
              </button>
            </div>
            {contextPanel}
          </aside>
        </div>
      ) : null}

      <span className="sr-only" aria-live="polite">
        {chat.isPending ? "Order Agent is searching" : ""}
      </span>
    </div>
  );
}
