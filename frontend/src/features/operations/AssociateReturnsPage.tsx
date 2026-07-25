import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link } from "wouter";
import { Bot, CheckCircle2, LockKeyhole, Search, Send, UserRound } from "lucide-react";

import {
  confirmAssociateDiscovery,
  startAssociateConversation,
  submitAssociateReturnDetails,
} from "../../api/associateReturns";
import type {
  AnchorType,
  AssociateConversation,
} from "../../contracts/associateReturns";
import { ErrorState } from "../../components/ErrorState";
import { PageHeader } from "../../components/PageHeader";
import {
  inputClass,
  Panel,
  primaryButton,
  ToneBadge,
} from "./shared";

const anchorLabels: Readonly<Record<AnchorType, string>> = {
  ORDER_NUMBER: "Order number",
  CUSTOMER_ID: "Customer ID",
  PHONE: "Phone",
  EMAIL: "Email",
  TRACKING_NUMBER: "Tracking number",
  SKU: "SKU / product",
};

function ConversationPanel({ conversation }: { readonly conversation: AssociateConversation }) {
  return (
    <Panel title="Associate and AI conversation">
      <div className="space-y-3">
        {conversation.messages.map((message) => (
          <div
            key={message.id}
            className={`flex gap-3 rounded-xl p-4 ${message.role === "AI_ASSISTANT" ? "bg-sky-50" : "bg-slate-50"}`}
          >
            <div className="mt-0.5 rounded-full bg-white p-2 shadow-sm">
              {message.role === "AI_ASSISTANT" ? <Bot size={16} /> : <UserRound size={16} />}
            </div>
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                {message.role === "AI_ASSISTANT" ? "Returns Assistant" : "Associate"}
              </p>
              <p className="mt-1 text-sm text-slate-800">{message.content}</p>
            </div>
          </div>
        ))}
      </div>
      {conversation.nextQuestion ? (
        <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm font-medium text-amber-900">
          Next: {conversation.nextQuestion}
        </p>
      ) : null}
    </Panel>
  );
}

export function AssociateReturnsPage() {
  const [anchorType, setAnchorType] = useState<AnchorType>("ORDER_NUMBER");
  const [anchorValue, setAnchorValue] = useState("ORD-10001");
  const [conversation, setConversation] = useState<AssociateConversation | null>(null);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [orderLineId, setOrderLineId] = useState("");
  const [reasonCode, setReasonCode] = useState("DAMAGED");
  const [returnQuantity, setReturnQuantity] = useState(1);
  const [packageCount, setPackageCount] = useState(1);
  const [shippingPath, setShippingPath] = useState<"PPL" | "BOL" | "CUSTOMER_SHIP" | "NO_LABEL" | "DIRECT_VENDOR" | "FIELD_SCRAP">("PPL");
  const [notes, setNotes] = useState("");

  const startMutation = useMutation({
    mutationFn: startAssociateConversation,
    onSuccess: (value) => {
      setConversation(value);
      setCandidateIndex(0);
      setOrderLineId(value.candidates.at(0)?.lines.at(0)?.orderLineId ?? "");
    },
  });
  const confirmMutation = useMutation({
    mutationFn: confirmAssociateDiscovery,
    onSuccess: (value) => { setConversation(value); },
  });
  const detailsMutation = useMutation({
    mutationFn: submitAssociateReturnDetails,
    onSuccess: (value) => { setConversation(value.conversation); },
  });

  function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    startMutation.mutate({ anchorType, anchorValue });
  }

  const error = startMutation.error ?? confirmMutation.error ?? detailsMutation.error;

  return (
    <div>
      <PageHeader
        title="Returns Assistant"
        description="Start with one strong anchor. The assistant discovers candidates, asks for confirmation, seals the order line, collects only missing return details, and hands the request to Return Support."
      />
      {error ? <div className="mb-5"><ErrorState message={error.message} /></div> : null}
      <div className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
        <div className="space-y-6">
          <Panel title="1. Minimal evidence">
            <form className="space-y-4" onSubmit={start}>
              <label className="block text-sm font-medium text-slate-700">
                Evidence type
                <select className={inputClass} value={anchorType} onChange={(event) => { setAnchorType(event.target.value as AnchorType); }}>
                  {(Object.keys(anchorLabels) as AnchorType[]).map((value) => <option key={value} value={value}>{anchorLabels[value]}</option>)}
                </select>
              </label>
              <label className="block text-sm font-medium text-slate-700">
                {anchorLabels[anchorType]}
                <input className={inputClass} value={anchorValue} onChange={(event) => { setAnchorValue(event.target.value); }} required />
              </label>
              <button className={primaryButton} disabled={startMutation.isPending} type="submit"><Search size={16} />Discover orders</button>
            </form>
          </Panel>

          {conversation && conversation.candidates.length > 0 && !conversation.discoveryLock ? (
            <Panel title="2. Confirm and lock discovery">
              <div className="space-y-3">
                {conversation.candidates.map((candidate, index) => (
                  <label key={candidate.orderReference} className={`block cursor-pointer rounded-lg border p-4 ${candidateIndex === index ? "border-slate-900 ring-1 ring-slate-900" : "border-slate-200"}`}>
                    <div className="flex items-start gap-3">
                      <input type="radio" name="candidate" checked={candidateIndex === index} onChange={() => { setCandidateIndex(index); setOrderLineId(candidate.lines.at(0)?.orderLineId ?? ""); }} />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center justify-between gap-2"><strong>{candidate.orderReference}</strong><ToneBadge value={candidate.orderStatus ?? "UNKNOWN"} /></div>
                        <p className="mt-1 text-sm text-slate-600">{candidate.customerName ?? candidate.customerReference} · {candidate.evidenceSource} · {(candidate.confidenceMillionths / 10_000).toFixed(1)}%</p>
                        {candidateIndex === index ? (
                          <label className="mt-3 block text-sm font-medium text-slate-700">
                            Exact order line
                            <select className={inputClass} value={orderLineId} onChange={(event) => { setOrderLineId(event.target.value); }}>
                              {candidate.lines.map((line) => <option key={line.orderLineId} value={line.orderLineId}>{line.orderLineId} · {line.sku ?? line.productId} · {line.productDescription ?? "Product"}</option>)}
                            </select>
                          </label>
                        ) : null}
                      </div>
                    </div>
                  </label>
                ))}
              </div>
              <button className={`${primaryButton} mt-4`} type="button" disabled={!orderLineId || confirmMutation.isPending} onClick={() => { confirmMutation.mutate({ conversationId: conversation.id, candidateIndex, orderLineId, expectedVersion: conversation.version }); }}><LockKeyhole size={16} />Confirm and lock</button>
            </Panel>
          ) : null}

          {conversation?.discoveryLock && conversation.status !== "SUBMITTED" ? (
            <Panel title="3. Complete return details">
              <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900"><p className="flex items-center gap-2 font-semibold"><CheckCircle2 size={16} />Discovery locked</p><p className="mt-1">{conversation.discoveryLock.orderReference} · {conversation.discoveryLock.orderLineId}</p></div>
              <form className="grid gap-4 sm:grid-cols-2" onSubmit={(event) => { event.preventDefault(); detailsMutation.mutate({ conversationId: conversation.id, reasonCode, returnQuantity, packageCount, shippingPathExpectation: shippingPath, notes: notes || undefined, expectedVersion: conversation.version }); }}>
                <label className="text-sm font-medium text-slate-700">Reason<select className={inputClass} value={reasonCode} onChange={(event) => { setReasonCode(event.target.value); }}><option>DAMAGED</option><option>WRONG_ITEM</option><option>DEFECTIVE</option><option>NOT_AS_DESCRIBED</option><option>MISSING_PARTS</option><option>FRAUD_SUSPECTED</option><option>SERIAL_MISMATCH</option></select></label>
                <label className="text-sm font-medium text-slate-700">Shipping path<select className={inputClass} value={shippingPath} onChange={(event) => { setShippingPath(event.target.value as typeof shippingPath); }}><option>PPL</option><option>BOL</option><option>CUSTOMER_SHIP</option><option>NO_LABEL</option><option>DIRECT_VENDOR</option><option>FIELD_SCRAP</option></select></label>
                <label className="text-sm font-medium text-slate-700">Return quantity<input type="number" min={1} className={inputClass} value={returnQuantity} onChange={(event) => { setReturnQuantity(Number(event.target.value)); }} /></label>
                <label className="text-sm font-medium text-slate-700">Package count<input type="number" min={1} className={inputClass} value={packageCount} onChange={(event) => { setPackageCount(Number(event.target.value)); }} /></label>
                <label className="text-sm font-medium text-slate-700 sm:col-span-2">Support notes<textarea className={inputClass} rows={4} value={notes} onChange={(event) => { setNotes(event.target.value); }} /></label>
                <button className={`${primaryButton} sm:col-span-2`} disabled={detailsMutation.isPending} type="submit"><Send size={16} />Create Return Support request</button>
              </form>
            </Panel>
          ) : null}

          {conversation?.status === "SUBMITTED" && conversation.returnSessionId ? (
            <Panel title="Return submitted">
              <p className="text-sm text-slate-600">The order lock and complete return context were handed to the real-time workflow.</p>
              <Link className={`${primaryButton} mt-4`} href={`/customer/returns/${conversation.returnSessionId}`}>Open live return timeline</Link>
            </Panel>
          ) : null}
        </div>
        <div>{conversation ? <ConversationPanel conversation={conversation} /> : <Panel title="How it works"><ol className="list-decimal space-y-3 pl-5 text-sm text-slate-600"><li>Enter one strong anchor.</li><li>Graph-first discovery returns candidate orders and lines.</li><li>The associate confirms the exact line; the context is digest-locked.</li><li>The assistant collects reason, quantity, packages, shipping path, and notes.</li><li>Return Support creates and follows the ticket; SQL and Neo4j remain source-aligned.</li></ol></Panel>}</div>
      </div>
    </div>
  );
}
