import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Database,
  Eye,
  GitBranch,
  Layers,
  Network,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  Terminal,
  Wrench,
} from "lucide-react";

import {
  continueCopilotOperationSession,
  listCopilotOperationSessions,
  startCopilotOperationSession,
} from "../../../../api/copilotOperations";
import { useActiveSnapshot } from "../../../../api/configurationQueries";
import { PageHeader } from "../../../../components/PageHeader";
import type { AssociateConversation } from "../../../../contracts/associateReturns";
import { ToneBadge } from "../../../operations/shared";

const validationScenarios = [
  {
    id: "partial_name",
    name: "Partial customer name",
    description: "Resolve a customer from an incomplete name and product description.",
    message: "I have a customer named Smith looking to return a drill.",
  },
  {
    id: "spelling_error",
    name: "Typo-tolerant discovery",
    description: "Exercise controlled full-text retrieval for misspelled names and products.",
    message: "Find the order for Jhn Smtih who bought a cordles dril last week.",
  },
  {
    id: "exact_reference",
    name: "Exact order reference",
    description: "Exercise the deterministic exact-anchor path.",
    message: "Look up order RET-ORD-8812 for item line 1.",
  },
  {
    id: "address_disambiguation",
    name: "Customer disambiguation",
    description: "Exercise requested-slot binding when multiple customer candidates remain.",
    message: "The customer is David Miller in postal code 90210.",
  },
  {
    id: "high_value_vendor",
    name: "Return workflow handoff",
    description: "Complete discovery and continue through the configured return path.",
    message: "Order RET-ORD-9901, item line 1, direct vendor pickup.",
  },
] as const;

type OperationTraceItem = {
  readonly id: string;
  readonly timestamp: string;
  readonly event: string;
  readonly source: string;
  readonly state: string;
  readonly latencyMs: number;
  readonly details: string;
};

function uniqueCandidateSources(conversation: AssociateConversation): string {
  const values = conversation.candidates
    .map((candidate) => candidate.evidenceSource.trim())
    .filter((value) => value.length > 0);
  const unique = [...new Set(values)];
  return unique.length > 0 ? unique.join(", ") : "No candidate source reported";
}

function formatLatency(value: number | null): string {
  return value === null ? "No samples" : `${String(value)} ms`;
}

export function CopilotOperationsConsolePage() {
  const queryClient = useQueryClient();
  const [selectedScenario, setSelectedScenario] = useState<string>("partial_name");
  const [customInput, setCustomInput] = useState<string>("");
  const [activeTab, setActiveTab] = useState<"trace" | "state" | "candidates">("trace");
  const [currentConversation, setCurrentConversation] = useState<AssociateConversation | null>(null);
  const [operationTraces, setOperationTraces] = useState<OperationTraceItem[]>([]);

  const snapshotQuery = useActiveSnapshot();
  const sessionsQuery = useQuery({
    queryKey: ["associate-conversations"],
    queryFn: ({ signal }) => listCopilotOperationSessions(signal),
    refetchInterval: 10_000,
  });

  const executeMutation = useMutation({
    mutationFn: async (text: string) => {
      const startTime = performance.now();
      const conversation = currentConversation
        ? await continueCopilotOperationSession({
          conversationId: currentConversation.id,
          message: text,
          expectedVersion: currentConversation.version,
        })
        : await startCopilotOperationSession({ message: text });
      return {
        conversation,
        duration: Math.round(performance.now() - startTime),
        text,
      };
    },
    onSuccess: ({ conversation, duration, text }) => {
      setCurrentConversation(conversation);
      setOperationTraces((previous) => [
        {
          id: `trace-${String(Date.now())}`,
          timestamp: new Date().toLocaleTimeString(),
          event: `CONVERSATION_TURN (${text.slice(0, 32)})`,
          source: uniqueCandidateSources(conversation),
          state: conversation.activeDialogueState,
          latencyMs: duration,
          details: [
            `Anchor: ${conversation.anchorValueMasked || "not resolved"}.`,
            `Candidates: ${String(conversation.candidates.length)}.`,
            `Conversation version: ${String(conversation.version)}.`,
            `Configuration release: ${conversation.configurationReleaseId ?? "not reported"}.`,
          ].join(" "),
        },
        ...previous,
      ]);
      void queryClient.invalidateQueries({ queryKey: ["associate-conversations"] });
    },
  });

  const activeScenario = validationScenarios.find((scenario) => scenario.id === selectedScenario)
    ?? validationScenarios[0];

  const averageLatency = useMemo(() => {
    if (operationTraces.length === 0) {
      return null;
    }
    const total = operationTraces.reduce((sum, item) => sum + item.latencyMs, 0);
    return Math.round(total / operationTraces.length);
  }, [operationTraces]);

  function handleExecuteTurn() {
    const text = customInput.trim() || activeScenario.message;
    executeMutation.mutate(text);
  }

  function handleResetSession() {
    setCurrentConversation(null);
    setOperationTraces([]);
  }

  const activeSnapshot = snapshotQuery.data;
  const executionError = executeMutation.error instanceof Error
    ? executeMutation.error.message
    : null;

  return (
    <div className="space-y-6 pb-12">
      <PageHeader
        title="Copilot Operations Console"
        description="Internal operations and validation interface for the production Order Discovery Copilot, published configuration, source routing, and conversation state."
      />

      <div className="grid gap-6 lg:grid-cols-4">
        <div className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
              <Network size={16} className="text-teal-600" />
              Configuration Source
            </span>
            <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700">
              {activeSnapshot?.source ?? "Unavailable"}
            </span>
          </div>
          <p className="mt-3 break-all text-lg font-bold text-slate-900">
            {activeSnapshot?.release_id ?? "No active release"}
          </p>
          <p className="mt-1 break-all text-xs text-slate-500">
            {activeSnapshot
              ? `Revision ${String(activeSnapshot.head_revision)} · ${activeSnapshot.checksum_sha256}`
              : "The active configuration endpoint has not returned a release."}
          </p>
        </div>

        <div className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
              <Activity size={16} className="text-sky-600" />
              Active Sessions
            </span>
            <span className="rounded-full bg-sky-50 px-2.5 py-0.5 text-xs font-semibold text-sky-700">
              Runtime
            </span>
          </div>
          <p className="mt-3 text-2xl font-bold text-slate-900">{sessionsQuery.data?.length ?? 0}</p>
          <p className="mt-1 text-xs text-slate-500">Persistent sessions with optimistic concurrency</p>
        </div>

        <div className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
              <ShieldCheck size={16} className="text-indigo-600" />
              Discovery Locks
            </span>
            <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-semibold text-indigo-700">
              Confirmed
            </span>
          </div>
          <p className="mt-3 text-2xl font-bold text-slate-900">
            {sessionsQuery.data?.filter((session) => session.discoveryLock !== null).length ?? 0}
          </p>
          <p className="mt-1 text-xs text-slate-500">Server-confirmed customer, order, and line evidence</p>
        </div>

        <div className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
              <Activity size={16} className="text-amber-600" />
              Measured Turn Latency
            </span>
            <span className="rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-semibold text-amber-700">
              Current Console
            </span>
          </div>
          <p className="mt-3 text-2xl font-bold text-slate-900">{formatLatency(averageLatency)}</p>
          <p className="mt-1 text-xs text-slate-500">Calculated only from turns executed in this view</p>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[1fr_1.4fr]">
        <div className="space-y-6">
          <section className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between border-b border-stone-100 pb-3">
              <h2 className="flex items-center gap-2 font-semibold text-slate-900">
                <Wrench size={18} className="text-teal-600" />
                Validation Scenarios
              </h2>
              <button
                type="button"
                onClick={handleResetSession}
                className="flex items-center gap-1 text-xs font-medium text-slate-500 transition hover:text-slate-800"
              >
                <RefreshCw size={13} />
                Reset Conversation
              </button>
            </div>

            <div className="mt-4 space-y-4">
              <div>
                <label className="mb-2 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Select validation scenario
                </label>
                <div className="space-y-2">
                  {validationScenarios.map((scenario) => (
                    <button
                      key={scenario.id}
                      type="button"
                      onClick={() => {
                        setSelectedScenario(scenario.id);
                        setCustomInput("");
                      }}
                      className={`w-full rounded-xl border p-3 text-left transition ${
                        selectedScenario === scenario.id
                          ? "border-teal-600 bg-teal-50/60 ring-1 ring-teal-600/30"
                          : "border-stone-200 hover:bg-stone-50"
                      }`}
                    >
                      <span className="font-medium text-sm text-slate-900">{scenario.name}</span>
                      <p className="mt-1 text-xs text-slate-500">{scenario.description}</p>
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Conversation input
                </label>
                <textarea
                  rows={3}
                  value={customInput || activeScenario.message}
                  onChange={(event) => { setCustomInput(event.target.value); }}
                  className="w-full rounded-xl border border-stone-300 bg-stone-50/50 p-3 text-sm text-slate-800 outline-none transition focus:border-teal-600 focus:ring-2 focus:ring-teal-100"
                />
              </div>

              {executionError ? (
                <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">
                  {executionError}
                </div>
              ) : null}

              <button
                type="button"
                disabled={executeMutation.isPending}
                onClick={handleExecuteTurn}
                className="flex w-full items-center justify-center gap-2 rounded-xl bg-teal-950 px-4 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-teal-900 disabled:opacity-50"
              >
                <Play size={16} />
                {executeMutation.isPending ? "Executing conversation turn..." : "Execute conversation turn"}
              </button>
            </div>
          </section>

          <section className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
            <h2 className="flex items-center gap-2 border-b border-stone-100 pb-3 font-semibold text-slate-900">
              <Database size={18} className="text-sky-600" />
              Published configuration
            </h2>
            <div className="mt-4 space-y-3 text-xs text-slate-600">
              <div className="flex items-center justify-between gap-4 rounded-lg border border-stone-200 bg-stone-50 p-2.5">
                <span className="font-mono text-slate-800">Release</span>
                <span className="break-all text-right font-semibold text-slate-900">
                  {activeSnapshot?.release_id ?? "Unavailable"}
                </span>
              </div>
              <div className="flex items-center justify-between gap-4 rounded-lg border border-stone-200 bg-stone-50 p-2.5">
                <span className="font-mono text-slate-800">HeadRevision</span>
                <span className="font-semibold text-slate-900">
                  {activeSnapshot ? String(activeSnapshot.head_revision) : "Unavailable"}
                </span>
              </div>
              <div className="flex items-center justify-between gap-4 rounded-lg border border-stone-200 bg-stone-50 p-2.5">
                <span className="font-mono text-slate-800">LoadedAt</span>
                <span className="text-right font-semibold text-slate-900">
                  {activeSnapshot?.loaded_at ?? "Unavailable"}
                </span>
              </div>
              <div className="rounded-lg border border-stone-200 bg-stone-50 p-2.5">
                <p className="font-mono text-slate-800">Checksum</p>
                <p className="mt-1 break-all font-semibold text-slate-900">
                  {activeSnapshot?.checksum_sha256 ?? "Unavailable"}
                </p>
              </div>
            </div>
          </section>
        </div>

        <div className="flex flex-col overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-stone-200 bg-stone-50/80 px-5 py-3">
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => { setActiveTab("trace"); }}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                  activeTab === "trace" ? "bg-white text-teal-950 shadow-sm" : "text-slate-500 hover:text-slate-900"
                }`}
              >
                <Terminal size={14} />
                Execution trace
              </button>
              <button
                type="button"
                onClick={() => { setActiveTab("state"); }}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                  activeTab === "state" ? "bg-white text-teal-950 shadow-sm" : "text-slate-500 hover:text-slate-900"
                }`}
              >
                <GitBranch size={14} />
                Conversation state
              </button>
              <button
                type="button"
                onClick={() => { setActiveTab("candidates"); }}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                  activeTab === "candidates" ? "bg-white text-teal-950 shadow-sm" : "text-slate-500 hover:text-slate-900"
                }`}
              >
                <Layers size={14} />
                Candidates
              </button>
            </div>

            {currentConversation ? <ToneBadge value={currentConversation.status} /> : null}
          </div>

          <div className="max-h-[640px] flex-1 overflow-y-auto p-5">
            {activeTab === "trace" ? (
              operationTraces.length > 0 ? (
                <div className="space-y-3">
                  {operationTraces.map((trace) => (
                    <div
                      key={trace.id}
                      className="rounded-xl border border-stone-200 bg-stone-50/70 p-3.5 font-mono text-xs"
                    >
                      <div className="mb-1.5 flex items-center justify-between text-[11px] text-slate-500">
                        <span className="flex items-center gap-2">
                          <span className="font-semibold text-slate-700">{trace.timestamp}</span>
                          <span className="rounded bg-stone-200 px-1.5 py-0.5 text-[10px] text-slate-700">
                            {trace.source}
                          </span>
                        </span>
                        <span className="font-semibold text-emerald-600">{String(trace.latencyMs)} ms</span>
                      </div>
                      <div className="flex items-center justify-between font-sans">
                        <span className="text-sm font-semibold text-slate-900">{trace.event}</span>
                        <span className="rounded border border-stone-200 bg-white px-2 py-0.5 font-mono text-xs text-slate-700">
                          {trace.state}
                        </span>
                      </div>
                      <p className="mt-2 border-t border-stone-200/60 pt-2 font-sans text-xs leading-5 text-slate-600">
                        {trace.details}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="py-12 text-center text-slate-400">
                  <Terminal size={32} className="mx-auto mb-2 opacity-50" />
                  <p className="text-sm">No conversation turns have been executed in this view.</p>
                </div>
              )
            ) : null}

            {activeTab === "state" ? (
              currentConversation ? (
                <div className="space-y-4 font-mono text-xs">
                  <div className="overflow-x-auto rounded-xl border border-stone-200 bg-stone-900 p-4 text-emerald-400">
                    <pre>{JSON.stringify(currentConversation, null, 2)}</pre>
                  </div>
                </div>
              ) : (
                <div className="py-12 text-center text-slate-400">
                  <Eye size={32} className="mx-auto mb-2 opacity-50" />
                  <p className="text-sm font-sans">No active operations conversation.</p>
                  <p className="mt-1 text-xs font-sans">Execute a conversation turn to inspect server state.</p>
                </div>
              )
            ) : null}

            {activeTab === "candidates" ? (
              currentConversation?.candidates && currentConversation.candidates.length > 0 ? (
                <div className="space-y-3">
                  {currentConversation.candidates.map((candidate, index) => (
                    <div
                      key={`${candidate.orderReference}:${candidate.customerReference}`}
                      className="rounded-xl border border-stone-200 bg-white p-4"
                    >
                      <div className="mb-2 flex items-center justify-between gap-3">
                        <span className="text-sm font-semibold text-slate-900">
                          #{String(index + 1)} {candidate.orderReference}
                        </span>
                        <span className="rounded-full bg-teal-50 px-2.5 py-0.5 text-xs font-semibold text-teal-800">
                          {candidate.evidenceSource || "Source not reported"}
                        </span>
                      </div>
                      <p className="text-xs text-slate-600">
                        {candidate.customerName ?? "Customer name unavailable"} · {candidate.customerReference}
                      </p>
                      {candidate.retrievalScore !== null ? (
                        <p className="mt-1 text-xs text-slate-500">
                          Retrieval rank score: {candidate.retrievalScore.toFixed(4)}
                        </p>
                      ) : null}
                      <div className="mt-2 border-t border-stone-100 pt-2 text-xs text-slate-500">
                        Lines: {candidate.lines.map((line) => line.productDescription ?? line.sku ?? line.productId).join(", ")}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="py-12 text-center text-slate-400">
                  <Search size={32} className="mx-auto mb-2 opacity-50" />
                  <p className="text-sm font-sans">No candidates in the current conversation state.</p>
                  <p className="mt-1 text-xs font-sans">Submit an exact identifier or a configured natural-language search.</p>
                </div>
              )
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
