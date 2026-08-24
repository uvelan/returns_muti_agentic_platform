import { useState } from "react";
import { Bot, Check, CornerDownLeft, ShieldCheck, Sparkles, X } from "lucide-react";
import { askAnalyzer, reviewRecommendation } from "../../../api/graphAnalyzer";
import type { AgentMessage, AgentRecommendation } from "../../../contracts/graphAnalyzer";
import { useGraphAnalyzer } from "../GraphAnalyzerContext";
import { useAnalyzerMutation } from "../analyzerQueries";
import { useMutation } from "@tanstack/react-query";

export function AgentDrawer() {
  const ui = useGraphAnalyzer();
  const [input, setInput] = useState("");
  const [recommendation, setRecommendation] = useState<AgentRecommendation | null>(null);
  // Asking a question changes nothing server-side, so it does not invalidate the
  // analyzer queries. `useAnalyzerMutation` refetches bootstrap, schemas and every
  // active run on success, which meant each chat message reloaded the whole
  // workspace. Reviewing a recommendation *does* change the proposal, so that one
  // keeps the invalidating wrapper.
  const ask = useMutation({ mutationFn: ({ message }: { readonly message: string }) => askAnalyzer(message, ui.agentContext) });
  const review = useAnalyzerMutation(({ id, decision }: { readonly id: string; readonly decision: "APPLY" | "REJECT" }) => reviewRecommendation(id, decision));

  if (!ui.chatOpen) return null;

  const submit = () => {
    const content = input.trim();
    if (content.length === 0 || ask.isPending) return;
    const userMessage: AgentMessage = { id: crypto.randomUUID(), role: "USER", content, createdAt: new Date().toISOString() };
    ui.appendMessage(userMessage);
    setInput("");
    ask.mutate({ message: content }, { onSuccess: (reply) => { ui.appendMessage(reply.message); setRecommendation(reply.recommendation); } });
  };

  return <div className="fixed inset-0 z-50 flex justify-end bg-black/55 backdrop-blur-sm" onMouseDown={(event) => { if (event.target === event.currentTarget) ui.closeChat(); }}>
    <aside className="flex h-full w-full max-w-md flex-col border-l border-analyzer-outline bg-analyzer-surface-container shadow-2xl" aria-label="Graph Schema Analyzer Agent">
      <header className="flex items-center justify-between border-b border-analyzer-outline-variant px-5 py-4"><div className="flex items-center gap-3"><span className="grid size-9 place-items-center rounded-lg bg-analyzer-primary text-analyzer-on-primary"><Bot size={18} /></span><div><h2 className="font-semibold text-white">Analyzer Agent</h2><p className="text-xs text-analyzer-on-surface-variant">System graph guidance only</p></div></div><button type="button" onClick={ui.closeChat} className="rounded-lg p-2 text-analyzer-on-surface-variant hover:bg-white/5 hover:text-white" aria-label="Close Analyzer Agent"><X size={18} /></button></header>
      <div className="border-b border-analyzer-outline-variant bg-analyzer-primary-container/20 px-5 py-3 text-xs text-emerald-200"><ShieldCheck className="mr-2 inline" size={14} />Source systems are read-only. Suggested changes can target only the system graph.</div>
      <div className="flex-1 space-y-4 overflow-y-auto p-5">
        {ui.messages.length === 0 ? <div className="rounded-xl border border-dashed border-analyzer-outline p-5 text-sm text-analyzer-on-surface-variant"><Sparkles className="mb-3 text-analyzer-primary" size={20} />Ask why an entity was proposed, review an identifier, explore a mapping, or request a system-graph change.</div> : null}
        {ui.messages.map((message) => <div key={message.id} className={`max-w-[88%] rounded-xl px-4 py-3 text-sm leading-6 ${message.role === "USER" ? "ml-auto bg-emerald-500 text-analyzer-on-primary" : "border border-analyzer-outline-variant bg-analyzer-surface-raised text-analyzer-on-surface-emphasis"}`}>{message.content}</div>)}
        {ask.isPending ? <div className="text-sm text-analyzer-on-surface-variant">Reviewing the active workspace context…</div> : null}
        {ask.isError ? <div role="alert" className="rounded-lg border border-red-900 bg-red-950/40 p-3 text-sm text-red-200">Agent request failed: {ask.error.message}</div> : null}
        {recommendation?.status === "PENDING" ? <div className="rounded-xl border border-amber-700/60 bg-amber-950/25 p-4"><div className="mb-2 flex items-center justify-between"><span className="text-xs font-semibold uppercase tracking-wider text-analyzer-warning">Change preview</span><span className="rounded-full bg-analyzer-primary-container px-2 py-1 text-[10px] text-analyzer-accent">TARGET: SYSTEM GRAPH</span></div><p className="font-medium text-white">{recommendation.summary}</p><p className="mt-1 text-sm text-analyzer-on-surface-variant">{recommendation.rationale}</p><div className="mt-4 flex gap-2"><button type="button" disabled={review.isPending} onClick={() => { review.mutate({ id: recommendation.id, decision: "APPLY" }, { onSuccess: (result) => { setRecommendation(result.recommendation); } }); }} className="inline-flex items-center gap-2 rounded-lg bg-analyzer-primary px-3 py-2 text-sm font-semibold text-analyzer-on-primary disabled:opacity-50"><Check size={15} />Apply to proposal</button><button type="button" disabled={review.isPending} onClick={() => { review.mutate({ id: recommendation.id, decision: "REJECT" }, { onSuccess: (result) => { setRecommendation(result.recommendation); } }); }} className="rounded-lg border border-analyzer-outline-control-neutral px-3 py-2 text-sm text-analyzer-on-surface disabled:opacity-50">Reject</button></div></div> : null}
      </div>
      <form className="border-t border-analyzer-outline-variant p-4" onSubmit={(event) => { event.preventDefault(); submit(); }}><div className="flex items-end gap-2 rounded-xl border border-analyzer-outline bg-analyzer-surface-sunken p-2 focus-within:border-emerald-500"><textarea value={input} aria-label="Message to the Analyzer Agent" onChange={(event) => { setInput(event.target.value); }} rows={2} placeholder="Ask about the active selection…" className="min-h-12 flex-1 resize-none bg-transparent px-2 py-1 text-sm text-white outline-none placeholder:text-analyzer-on-surface-variant" /><button type="submit" disabled={ask.isPending || input.trim().length === 0} className="grid size-9 place-items-center rounded-lg bg-analyzer-primary text-analyzer-on-primary disabled:opacity-40" aria-label="Send message"><CornerDownLeft size={16} /></button></div></form>
    </aside>
  </div>;
}
