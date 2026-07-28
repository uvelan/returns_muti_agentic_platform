import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "wouter";
import {
  Bot,
  CheckCircle2,
  Clock3,
  MessageSquarePlus,
} from "lucide-react";

import {
  confirmAssociateDiscovery,
  continueAssociateChat,
  listAssociateConversations,
  startAssociateChat,
  submitAssociateReturnDetails,
} from "../../api/associateReturns";
import type { AssociateConversation } from "../../contracts/associateReturns";
import {
  formatBadgeLabel,
  primaryButton,
  secondaryButton,
  ToneBadge,
} from "./shared";
import { OrderContextPanel, OrderDiscoveryCopilot } from "./order_discovery";

const conversationKey = ["associate-conversations"] as const;

export function AssociateReturnsPage() {
  const queryClient = useQueryClient();
  const [conversation, setConversation] = useState<AssociateConversation | null>(null);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [orderLineId, setOrderLineId] = useState("");

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

  return (
    <div className="-m-4 h-[calc(100vh-4rem)] bg-stone-50 sm:-m-6 overflow-hidden">
      <div className="grid h-full md:grid-cols-[minmax(0,1fr)_18rem] lg:grid-cols-[minmax(0,1fr)_21rem] xl:grid-cols-[17rem_minmax(0,1fr)_21rem]">
        <aside className="hidden h-full overflow-y-auto border-r border-stone-200 bg-white p-4 xl:block">
          <button
            type="button"
            className={`${primaryButton} w-full justify-center`}
            onClick={() => {
              setConversation(null);
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
                className={`w-full rounded-xl px-3 py-3 text-left transition ${
                  conversation?.id === item.id ? "bg-teal-50 text-teal-950 ring-1 ring-teal-200" : "hover:bg-stone-50"
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
                  <span>{formatBadgeLabel(item.status)}</span>
                </span>
              </button>
            ))}
          </div>
        </aside>

        <main className="flex h-full min-w-0 flex-col overflow-hidden">
          <header className="flex items-center justify-between border-b border-stone-200 bg-white/90 px-5 py-4 backdrop-blur sm:px-8">
            <div>
              <div className="flex items-center gap-2">
                <span className="rounded-lg bg-teal-950 p-1.5 text-white"><Bot size={17} /></span>
                <h2 className="font-semibold text-slate-950">Returns Assistant</h2>
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700 ring-1 ring-inset ring-emerald-600/20">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                  Agentic AI v2.0
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                Driven by graph-first order discovery and dynamic operational configuration.
              </p>
            </div>
            {conversation ? <ToneBadge value={conversation.status} /> : null}
          </header>

          <OrderDiscoveryCopilot
            conversation={conversation}
            onSendMessage={(text) => { chat.mutate(text); }}
            isPending={chat.isPending}
            error={error}
          />

          {isComplete && conversation.returnSessionId ? (
            <div className="border-t border-stone-200 bg-white p-5 text-center">
              <Link className={primaryButton} href={`/customer/returns/${conversation.returnSessionId}`}>
                <CheckCircle2 size={16} />Open live return timeline
              </Link>
            </div>
          ) : null}
        </main>

        <div className="flex h-full min-h-0 flex-col overflow-hidden">
          <div className="mb-4 shrink-0 p-4 pb-0 xl:hidden">
            <button
              type="button"
              className={`${secondaryButton} w-full justify-center`}
              onClick={() => { setConversation(null); setOrderLineId(""); }}
            >
              <MessageSquarePlus size={16} />New return conversation
            </button>
            <div className="mt-3 rounded-xl border border-stone-200 bg-white p-2 shadow-xs">
              <div className="flex items-center justify-between px-2 py-1 text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">
                <span className="flex items-center gap-1.5"><Clock3 size={13} />Recent sessions</span>
                <span>{String(sessions.data?.length ?? 0)}</span>
              </div>
              <div className="mt-1 max-h-40 space-y-1 overflow-y-auto">
                {sessions.isPending ? (
                  <p className="px-2 py-2 text-xs text-slate-500">Loading sessions...</p>
                ) : null}
                {sessions.isError ? (
                  <p className="px-2 py-2 text-xs text-rose-700">Unable to load sessions.</p>
                ) : null}
                {sessions.data?.slice(0, 8).map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`flex w-full items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-left text-xs transition ${
                      conversation?.id === item.id
                        ? "bg-teal-50 text-teal-950 ring-1 ring-teal-200"
                        : "hover:bg-stone-50"
                    }`}
                    onClick={() => {
                      setConversation(item);
                      setCandidateIndex(0);
                      setOrderLineId(item.candidates.at(0)?.lines.at(0)?.orderLineId ?? "");
                    }}
                  >
                    <span className="truncate font-medium">
                      {item.anchorValueMasked || "Return conversation"}
                    </span>
                    <span className="shrink-0 text-[10px] text-slate-500">
                      {formatBadgeLabel(item.status)}
                    </span>
                  </button>
                ))}
                {!sessions.isPending && sessions.data?.length === 0 ? (
                  <p className="px-2 py-2 text-xs text-slate-500">No sessions yet.</p>
                ) : null}
              </div>
            </div>
          </div>
          <OrderContextPanel
            conversation={conversation}
            candidateIndex={candidateIndex}
            selectedLineId={orderLineId}
            onSelectCandidate={setCandidateIndex}
            onSelectLine={setOrderLineId}
            onSelectClarification={(value) => { chat.mutate(value); }}
            onConfirmDiscovery={() => {
              if (conversation) {
                confirm.mutate({
                  conversationId: conversation.id,
                  candidateIndex,
                  orderLineId,
                  expectedVersion: conversation.version,
                  candidateSetId: conversation.candidateSetId,
                });
              }
            }}
            isConfirming={confirm.isPending}
            isClarifying={chat.isPending}
            onSubmitDetails={(payload) => {
              if (conversation) {
                details.mutate({
                  conversationId: conversation.id,
                  ...payload,
                  expectedVersion: conversation.version,
                });
              }
            }}
            isSubmittingDetails={details.isPending}
          />
        </div>
      </div>
    </div>
  );
}
