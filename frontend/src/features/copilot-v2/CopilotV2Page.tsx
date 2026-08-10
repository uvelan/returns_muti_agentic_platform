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
  Lock,
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
  GraphQueryRowResult,
  OrderAgentQueryEvidence,
  OrderAgentTurnRequest,
  OrderAgentTurnResult,
  OrderSearchCandidate,
  OrderSearchResult,
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
  return parts.join("\n\n") || "I didn't have anything to share back on that one — could you try rephrasing?";
}

function isOrderSearchResult(value: unknown): value is OrderSearchResult {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return Array.isArray(record.candidates) && typeof record.total_found === "number";
}

function isGraphQueryRowResult(value: unknown): value is GraphQueryRowResult {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return Array.isArray(record.rows) && typeof record.count === "number";
}

// A direct GRAPH_QUERY (e.g. a traversal to order_line for product detail)
// has no "intent" or ranking — normalize its rows into the same shape the
// candidate cards already render, so a customer search followed by a
// traversal shows the real order/product data, not the stale customer list.
function normalizeGraphQueryResult(result: GraphQueryRowResult): OrderSearchResult {
  return {
    intent: {},
    candidates: result.rows.map((row) => ({ data: row, score: 1, matches: [] })),
    total_found: result.count,
    unsupported_signals: [],
  };
}

// Latest result that actually has something to show, scanning from the most
// recent turn's evidence backward — a later empty page (e.g. "show next"
// with nothing left) shouldn't blank out the last useful result.
function latestOrderSearchResult(
  evidence: readonly OrderAgentQueryEvidence[],
): OrderSearchResult | null {
  for (let index = evidence.length - 1; index >= 0; index -= 1) {
    const { result } = evidence[index];
    if (isOrderSearchResult(result) && result.candidates.length > 0) {
      return result;
    }
    if (isGraphQueryRowResult(result) && result.rows.length > 0) {
      return normalizeGraphQueryResult(result);
    }
  }
  return null;
}

const FIELD_LABELS: Readonly<Record<string, string>> = {
  sales_order_number: "Order",
  customer_name: "Customer",
  customer_id: "Customer ID",
  product_description: "Product",
  order_status: "Status",
  delivered_at: "Delivered",
  ordered_quantity: "Qty",
  sku: "SKU",
  shipping_method: "Shipping",
};

const TITLE_FIELD_PRIORITY = [
  "sales_order_number",
  "customer_name",
  "product_description",
  "sku",
  "customer_id",
] as const;

function formatFieldValue(field: string, value: unknown): string {
  if (field === "delivered_at" && typeof value === "string") {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    }
  }
  return String(value);
}

function candidateTitle(candidate: OrderSearchCandidate): {
  field: string | null;
  text: string;
} {
  for (const field of TITLE_FIELD_PRIORITY) {
    const value = candidate.data[field];
    if (typeof value === "string" && value.trim().length > 0) {
      return { field, text: value };
    }
  }
  return { field: null, text: "Result" };
}

function candidateDetailFields(
  candidate: OrderSearchCandidate,
  titleField: string | null,
): readonly (readonly [string, unknown])[] {
  return Object.entries(candidate.data).filter(
    ([field, value]) =>
      field !== titleField &&
      field in FIELD_LABELS &&
      value !== null &&
      value !== undefined &&
      value !== "",
  );
}

function candidateSelectValue(candidate: OrderSearchCandidate): string {
  const { text } = candidateTitle(candidate);
  return text;
}

// The panel's section label should say what these records actually are, not
// always "orders" — a customer-name search returns customers, not orders,
// until a follow-up narrows it down to a specific order.
function candidateGroupLabel(candidates: readonly OrderSearchCandidate[]): string {
  const titleFields = new Set(
    candidates.map((candidate) => candidateTitle(candidate).field),
  );
  if (titleFields.has("sales_order_number")) return "Matching orders";
  if (titleFields.has("customer_name")) return "Matching customers";
  if (titleFields.has("product_description") || titleFields.has("sku")) {
    return "Matching products";
  }
  return "Matching results";
}

function CandidateCard({
  candidate,
  disabled,
  onSelect,
}: {
  readonly candidate: OrderSearchCandidate;
  readonly disabled: boolean;
  readonly onSelect: (value: string) => void;
}) {
  const { field: titleField, text: title } = candidateTitle(candidate);
  const details = candidateDetailFields(candidate, titleField);
  const isFuzzy = candidate.matches.includes("customer_name_fuzzy");

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => {
        onSelect(candidateSelectValue(candidate));
      }}
      className="w-full rounded-xl border border-[#dee4e1] bg-white p-3 text-left shadow-sm transition hover:border-[#00685f] hover:shadow disabled:cursor-not-allowed disabled:opacity-50"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[#f0f5f2] text-[#00685f]">
            <PackageSearch size={16} />
          </span>
          <span className="text-sm font-semibold text-[#171d1c]">{title}</span>
        </div>
        <Lock size={14} className="mt-1 shrink-0 text-[#6d7a77]" />
      </div>
      <dl className="mt-2 space-y-1 pl-10">
        {details.map(([field, value]) => (
          <div key={field} className="flex justify-between gap-3 text-xs">
            <dt className="text-[#6d7a77]">{FIELD_LABELS[field]}</dt>
            <dd className="text-right font-medium text-[#171d1c]">
              {formatFieldValue(field, value)}
            </dd>
          </div>
        ))}
        {isFuzzy ? (
          <div className="pt-1 text-[11px] font-medium text-[#924628]">
            Approximate spelling match
          </div>
        ) : null}
      </dl>
    </button>
  );
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

  function selectCandidate(value: string) {
    send(value);
    setContextOpen(false);
  }

  const candidateResult = latestOrderSearchResult(evidence);

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

      {candidateResult ? (
        <div className="mt-4">
          <div className="mb-2 flex items-baseline justify-between">
            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#00685f]">
              {candidateGroupLabel(candidateResult.candidates)}
            </p>
            <p className="text-xs text-[#6d7a77]">
              Showing {candidateResult.candidates.length} of{" "}
              {candidateResult.total_found}
            </p>
          </div>
          <div className="space-y-2">
            {candidateResult.candidates.map((candidate, index) => (
              <CandidateCard
                key={`${candidateSelectValue(candidate)}-${String(index)}`}
                candidate={candidate}
                disabled={chat.isPending}
                onSelect={selectCandidate}
              />
            ))}
          </div>
          <p className="mt-2 text-[11px] text-[#6d7a77]">
            Select an order or customer to lock it in and continue.
          </p>
        </div>
      ) : null}
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
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.ctrlKey) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
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
