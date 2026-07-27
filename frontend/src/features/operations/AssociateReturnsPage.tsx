import { useState, type SyntheticEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "wouter";
import {
  Bot,
  Check,
  CheckCircle2,
  Clock3,
  LockKeyhole,
  MessageSquarePlus,
  Send,
  Sparkles,
  UserRound,
} from "lucide-react";

import {
  confirmAssociateDiscovery,
  continueAssociateChat,
  listAssociateConversations,
  startAssociateChat,
  submitAssociateReturnDetails,
} from "../../api/associateReturns";
import type {
  AssociateConversation,
  OrderCandidate,
} from "../../contracts/associateReturns";
import { ErrorState } from "../../components/ErrorState";
import {
  inputClass,
  primaryButton,
  secondaryButton,
  ToneBadge,
} from "./shared";

const conversationKey = ["associate-conversations"] as const;
const reasonCodes = [
  "DAMAGED",
  "WRONG_ITEM",
  "DEFECTIVE",
  "NOT_AS_DESCRIBED",
  "MISSING_PARTS",
] as const;
const shippingPaths = [
  "PPL",
  "BOL",
  "CUSTOMER_SHIP",
  "NO_LABEL",
  "DIRECT_VENDOR",
  "FIELD_SCRAP",
] as const;

function formatLabel(value: string) {
  return value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function ConversationMessages({ conversation }: { readonly conversation: AssociateConversation | null }) {
  const messages = conversation?.messages ?? [];
  return (
    <div className="flex-1 space-y-5 overflow-y-auto px-5 py-6 sm:px-8">
      {messages.length === 0 ? (
        <div className="mx-auto mt-8 max-w-2xl">
          <div className="mb-5 inline-flex rounded-2xl bg-teal-950 p-3 text-white shadow-sm">
            <Sparkles size={22} />
          </div>
          <h1 className="text-3xl font-semibold tracking-tight text-slate-950 sm:text-4xl">
            What can I help you return?
          </h1>
          <p className="mt-3 max-w-xl text-base leading-7 text-slate-600">
            Describe the situation naturally. I’ll identify useful evidence, look for the
            order, and ask one focused question when something is missing.
          </p>
          <div className="mt-8 grid gap-3 sm:grid-cols-2">
            {[
              "I need to return order SO-00010001",
              "The tracking number starts with 1Z and the faucet arrived damaged",
              "Customer CUST-10001 wants to return one item",
              "I only have the SKU from the product box",
            ].map((example) => (
              <div key={example} className="rounded-2xl border border-stone-200 bg-white p-4 text-sm leading-6 text-slate-600 shadow-sm">
                “{example}”
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="mx-auto max-w-3xl space-y-5">
          {messages.map((message) => {
            const assistant = message.role === "AI_ASSISTANT";
            return (
              <div key={message.id} className={`flex gap-3 ${assistant ? "" : "justify-end"}`}>
                {assistant ? (
                  <div className="mt-1 h-fit rounded-xl bg-teal-950 p-2 text-white">
                    <Bot size={17} />
                  </div>
                ) : null}
                <div className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-6 ${
                  assistant
                    ? "border border-stone-200 bg-white text-slate-800 shadow-sm"
                    : "bg-slate-900 text-white"
                }`}>
                  <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.16em] opacity-60">
                    {assistant ? "Returns Assistant" : "You"}
                  </p>
                  {message.content}
                </div>
                {!assistant ? (
                  <div className="mt-1 h-fit rounded-xl border border-stone-200 bg-white p-2 text-slate-700">
                    <UserRound size={17} />
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function CandidatePanel({
  conversation,
  candidateIndex,
  orderLineId,
  setCandidateIndex,
  setOrderLineId,
  confirm,
}: {
  readonly conversation: AssociateConversation;
  readonly candidateIndex: number;
  readonly orderLineId: string;
  readonly setCandidateIndex: (value: number) => void;
  readonly setOrderLineId: (value: string) => void;
  readonly confirm: () => void;
}) {
  return (
    <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-2">
        <LockKeyhole size={17} className="text-teal-800" />
        <h2 className="font-semibold text-slate-950">Confirm the match</h2>
      </div>
      <p className="mt-2 text-xs leading-5 text-slate-500">
        The agent can rank evidence, but you choose the exact order and item.
      </p>
      <div className="mt-4 space-y-3">
        {conversation.candidates.map((candidate: OrderCandidate, index) => (
          <div key={candidate.orderReference} className={`rounded-xl border p-3 ${
            candidateIndex === index ? "border-teal-700 bg-teal-50" : "border-stone-200"
          }`}>
            <button
              type="button"
              className="flex w-full items-center justify-between gap-2 text-left"
              onClick={() => {
                setCandidateIndex(index);
                setOrderLineId(candidate.lines.at(0)?.orderLineId ?? "");
              }}
            >
              <span>
                <strong className="block text-sm">{candidate.orderReference}</strong>
                <span className="text-xs text-slate-500">{candidate.customerName ?? candidate.customerReference}</span>
              </span>
              <ToneBadge value={candidate.orderStatus ?? "UNKNOWN"} />
            </button>
            {candidateIndex === index ? (
              <div className="mt-3 space-y-2 border-t border-teal-200 pt-3">
                {candidate.lines.map((line) => (
                  <button
                    key={line.orderLineId}
                    type="button"
                    onClick={() => { setOrderLineId(line.orderLineId); }}
                    className={`flex w-full items-start gap-2 rounded-lg border p-2 text-left text-xs ${
                      orderLineId === line.orderLineId
                        ? "border-teal-700 bg-white"
                        : "border-transparent bg-teal-100/50"
                    }`}
                  >
                    <span className={`mt-0.5 rounded-full p-0.5 ${
                      orderLineId === line.orderLineId ? "bg-teal-700 text-white" : "bg-white text-transparent"
                    }`}><Check size={12} /></span>
                    <span><strong className="block">{line.sku ?? line.productId}</strong>{line.productDescription ?? line.orderLineId}</span>
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        ))}
      </div>
      <button type="button" className={`${primaryButton} mt-4 w-full justify-center`} disabled={!orderLineId} onClick={confirm}>
        <LockKeyhole size={16} />Confirm and lock
      </button>
    </section>
  );
}

export function AssociateReturnsPage() {
  const queryClient = useQueryClient();
  const [conversation, setConversation] = useState<AssociateConversation | null>(null);
  const [message, setMessage] = useState("");
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [orderLineId, setOrderLineId] = useState("");
  const [reasonCode, setReasonCode] = useState<(typeof reasonCodes)[number]>("DAMAGED");
  const [returnQuantity, setReturnQuantity] = useState(1);
  const [packageCount, setPackageCount] = useState(1);
  const [shippingPath, setShippingPath] = useState<(typeof shippingPaths)[number]>("PPL");
  const [notes, setNotes] = useState("");

  const sessions = useQuery({
    queryKey: conversationKey,
    queryFn: ({ signal }) => listAssociateConversations(signal),
    refetchInterval: 15_000,
  });
  const chat = useMutation({
    mutationFn: async (text: string) => (
      conversation
        ? continueAssociateChat({
          conversationId: conversation.id,
          message: text,
          expectedVersion: conversation.version,
        })
        : startAssociateChat({ message: text })
    ),
    onSuccess: (value) => {
      setConversation(value);
      setCandidateIndex(0);
      setOrderLineId(value.candidates.at(0)?.lines.at(0)?.orderLineId ?? "");
      setMessage("");
      void queryClient.invalidateQueries({ queryKey: conversationKey });
    },
  });
  const confirm = useMutation({
    mutationFn: confirmAssociateDiscovery,
    onSuccess: setConversation,
  });
  const details = useMutation({
    mutationFn: submitAssociateReturnDetails,
    onSuccess: (value) => {
      setConversation(value.conversation);
      void queryClient.invalidateQueries({ queryKey: conversationKey });
    },
  });

  const error = chat.error ?? confirm.error ?? details.error;
  const isComplete = conversation?.status === "SUBMITTED";

  function send(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = message.trim();
    if (text) chat.mutate(text);
  }

  return (
    <div className="-m-4 min-h-[calc(100vh-4rem)] bg-stone-50 sm:-m-6">
      <div className="grid min-h-[calc(100vh-4rem)] xl:grid-cols-[17rem_minmax(0,1fr)_21rem]">
        <aside className="hidden border-r border-stone-200 bg-white p-4 xl:block">
          <button
            type="button"
            className={`${primaryButton} w-full justify-center`}
            onClick={() => {
              setConversation(null);
              setMessage("");
              setOrderLineId("");
            }}
          >
            <MessageSquarePlus size={16} />New return
          </button>
          <div className="mt-7 flex items-center gap-2 px-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">
            <Clock3 size={14} />Recent sessions
          </div>
          <div className="mt-3 space-y-1">
            {sessions.data?.slice(0, 12).map((item) => (
              <button
                key={item.id}
                type="button"
                className={`w-full rounded-xl px-3 py-3 text-left ${
                  conversation?.id === item.id ? "bg-teal-50 text-teal-950" : "hover:bg-stone-50"
                }`}
                onClick={() => {
                  setConversation(item);
                  setCandidateIndex(0);
                  setOrderLineId(item.candidates.at(0)?.lines.at(0)?.orderLineId ?? "");
                }}
              >
                <span className="block truncate text-sm font-medium">{item.anchorValueMasked || "Return conversation"}</span>
                <span className="mt-1 flex items-center justify-between gap-2 text-[11px] text-slate-500">
                  <span>{new Date(item.updatedAt).toLocaleDateString()}</span>
                  <span>{formatLabel(item.status)}</span>
                </span>
              </button>
            ))}
          </div>
        </aside>

        <main className="flex min-h-[calc(100vh-4rem)] min-w-0 flex-col">
          <header className="flex items-center justify-between border-b border-stone-200 bg-white/90 px-5 py-4 backdrop-blur sm:px-8">
            <div>
              <div className="flex items-center gap-2">
                <span className="rounded-lg bg-teal-950 p-1.5 text-white"><Bot size={17} /></span>
                <h2 className="font-semibold text-slate-950">Returns Assistant</h2>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                Conversation context is saved and available to the next workflow agent.
              </p>
            </div>
            {conversation ? <ToneBadge value={conversation.status} /> : null}
          </header>

          {error ? <div className="mx-5 mt-4 sm:mx-8"><ErrorState message={error.message} /></div> : null}
          <ConversationMessages conversation={conversation} />

          {!isComplete ? (
            <div className="border-t border-stone-200 bg-white p-4 sm:px-8">
              <form className="mx-auto max-w-3xl" onSubmit={send}>
                <div className="flex items-end gap-2 rounded-2xl border border-stone-300 bg-white p-2 shadow-sm focus-within:border-teal-700 focus-within:ring-2 focus-within:ring-teal-100">
                  <textarea
                    rows={2}
                    className="min-h-12 flex-1 resize-none border-0 bg-transparent px-2 py-2 text-sm leading-6 outline-none"
                    value={message}
                    onChange={(event) => { setMessage(event.target.value); }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        event.currentTarget.form?.requestSubmit();
                      }
                    }}
                    placeholder="Describe the return, paste an order detail, or answer the assistant…"
                    aria-label="Message the Returns Assistant"
                  />
                  <button
                    className="rounded-xl bg-teal-950 p-3 text-white disabled:cursor-not-allowed disabled:opacity-40"
                    disabled={chat.isPending || !message.trim()}
                    type="submit"
                    aria-label="Send message"
                  >
                    <Send size={18} />
                  </button>
                </div>
                <p className="mt-2 text-center text-[11px] text-slate-400">
                  The agent extracts configured anchors from your message and never confirms a return without you.
                </p>
              </form>
            </div>
          ) : conversation?.returnSessionId ? (
            <div className="border-t border-stone-200 bg-white p-5 text-center">
              <Link className={primaryButton} href={`/customer/returns/${conversation.returnSessionId}`}>
                <CheckCircle2 size={16} />Open live return timeline
              </Link>
            </div>
          ) : null}
        </main>

        <aside className="border-l border-stone-200 bg-stone-100/70 p-4">
          <div className="mb-4 xl:hidden">
            <button
              type="button"
              className={`${secondaryButton} w-full justify-center`}
              onClick={() => { setConversation(null); setMessage(""); }}
            >
              <MessageSquarePlus size={16} />New return conversation
            </button>
          </div>
          {conversation?.candidates.length && !conversation.discoveryLock ? (
            <CandidatePanel
              conversation={conversation}
              candidateIndex={candidateIndex}
              orderLineId={orderLineId}
              setCandidateIndex={setCandidateIndex}
              setOrderLineId={setOrderLineId}
              confirm={() => {
                confirm.mutate({
                  conversationId: conversation.id,
                  candidateIndex,
                  orderLineId,
                  expectedVersion: conversation.version,
                });
              }}
            />
          ) : null}

          {conversation?.discoveryLock && !isComplete ? (
            <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
              <p className="flex items-center gap-2 text-sm font-semibold text-teal-950">
                <CheckCircle2 size={17} />Order evidence locked
              </p>
              <p className="mt-1 text-xs text-slate-500">{conversation.discoveryLock.orderReference} · {conversation.discoveryLock.orderLineId}</p>
              <form
                className="mt-5 space-y-5"
                onSubmit={(event) => {
                  event.preventDefault();
                  details.mutate({
                    conversationId: conversation.id,
                    reasonCode,
                    returnQuantity,
                    packageCount,
                    shippingPathExpectation: shippingPath,
                    notes: notes || undefined,
                    expectedVersion: conversation.version,
                  });
                }}
              >
                <fieldset>
                  <legend className="text-xs font-semibold uppercase tracking-wide text-slate-500">Reason</legend>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {reasonCodes.map((value) => (
                      <button key={value} type="button" onClick={() => { setReasonCode(value); }} className={`rounded-full border px-3 py-1.5 text-xs ${reasonCode === value ? "border-teal-800 bg-teal-50 text-teal-950" : "border-stone-200"}`}>
                        {formatLabel(value)}
                      </button>
                    ))}
                  </div>
                </fieldset>
                <fieldset>
                  <legend className="text-xs font-semibold uppercase tracking-wide text-slate-500">Expected route</legend>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {shippingPaths.map((value) => (
                      <button key={value} type="button" onClick={() => { setShippingPath(value); }} className={`rounded-full border px-3 py-1.5 text-xs ${shippingPath === value ? "border-teal-800 bg-teal-50 text-teal-950" : "border-stone-200"}`}>
                        {formatLabel(value)}
                      </button>
                    ))}
                  </div>
                </fieldset>
                <div className="grid grid-cols-2 gap-3">
                  <label className="text-xs font-medium text-slate-600">Quantity<input type="number" min={1} className={inputClass} value={returnQuantity} onChange={(event) => { setReturnQuantity(Number(event.target.value)); }} /></label>
                  <label className="text-xs font-medium text-slate-600">Packages<input type="number" min={1} className={inputClass} value={packageCount} onChange={(event) => { setPackageCount(Number(event.target.value)); }} /></label>
                </div>
                <label className="block text-xs font-medium text-slate-600">Notes<textarea className={inputClass} rows={3} value={notes} onChange={(event) => { setNotes(event.target.value); }} /></label>
                <button className={`${primaryButton} w-full justify-center`} disabled={details.isPending} type="submit">
                  <Send size={16} />Send to workflow
                </button>
              </form>
            </section>
          ) : null}

          {!conversation?.candidates.length && !conversation?.discoveryLock ? (
            <section className="rounded-2xl border border-dashed border-stone-300 bg-white/70 p-5">
              <p className="text-sm font-semibold text-slate-800">Context appears here</p>
              <p className="mt-2 text-xs leading-5 text-slate-500">
                Matching orders, item confirmation, and collected return facts stay beside the conversation without interrupting it.
              </p>
            </section>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
