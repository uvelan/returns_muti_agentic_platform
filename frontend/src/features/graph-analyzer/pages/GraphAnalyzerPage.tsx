import { Bot, CheckCircle2, CircleAlert, Clock3, Database, Network } from "lucide-react";
import { Link } from "wouter";

import { startAnalysis } from "../../../api/graphAnalyzer";
import type { AnalyzerSource, SourceObject } from "../../../contracts/graphAnalyzer";
import { AnalyzerLayout } from "../components/AnalyzerLayout";
import { SourceTree } from "../components/SourceTree";
import { useGraphAnalyzer } from "../GraphAnalyzerContext";
import { useAnalysis, useAnalyzerBootstrap, useAnalyzerMutation } from "../analyzerQueries";
import { useState } from "react";

/**
 * Choose what to analyze, say why, and analyze it.
 *
 * Connection configuration used to sit at the top of this screen. It moved to
 * the Data Sources section, which is where a connection is added, validated and
 * inspected -- so the two screens no longer both manage sources, and the
 * checkboxes here are unambiguously the selection the Analyze button reads.
 *
 * The tree spans every configured source at once. A scope is any mix: one
 * collection, a whole source, or objects from several sources together.
 */
export function GraphAnalyzerPage() {
  const bootstrap = useAnalyzerBootstrap();
  const ui = useGraphAnalyzer();
  const [analysisId, setAnalysisId] = useState<string | null>(
    bootstrap.data?.activeAnalysis?.id ?? null,
  );
  const analysis = useAnalysis(analysisId);
  const analyze = useAnalyzerMutation(
    ({ selected, context }: { readonly selected: readonly string[]; readonly context: string }) =>
      startAnalysis(selected, context),
  );
  const sources = bootstrap.data?.sources ?? [];

  return (
    <AnalyzerLayout>
      <div className="space-y-5">
        <header>
          <p className="text-xs font-semibold uppercase tracking-[.2em] text-emerald-500">
            Primary workspace
          </p>
          <h2 className="mt-1 text-2xl font-semibold text-white">Select the scope. Analyze it.</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-400">
            Tick objects across any of your configured sources. Only what you select is read, and
            only what you select is analyzed.
          </p>
        </header>

        {bootstrap.isError ? (
          <div
            role="alert"
            className="flex items-center justify-between gap-4 rounded-xl border border-red-900 bg-red-950/30 px-4 py-3 text-sm text-red-100"
          >
            <span>Configured sources could not be loaded. {bootstrap.error.message}</span>
            <button
              type="button"
              onClick={() => {
                void bootstrap.refetch();
              }}
              className="rounded-md border border-red-700 px-3 py-1.5"
            >
              Retry
            </button>
          </div>
        ) : null}

        {!bootstrap.isLoading && !bootstrap.isError && sources.length === 0 ? (
          <div className="rounded-xl border border-dashed border-emerald-900 p-10 text-center">
            <Database className="mx-auto text-slate-400" />
            <p className="mt-2 font-medium text-slate-300">No sources configured</p>
            <p className="mt-1 text-sm text-slate-400">
              Connections are managed in Data Sources. Add one there and it appears here.
            </p>
            <Link
              href="/graph-schema/data-sources"
              className="mt-3 inline-block rounded-lg bg-emerald-400 px-4 py-2 text-sm font-semibold text-emerald-950"
            >
              Go to Data Sources
            </Link>
          </div>
        ) : (
          <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
            <section className="rounded-xl border border-emerald-950 bg-[#0a1714] p-4">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h3 className="font-semibold text-white">Source explorer</h3>
                  <p className="text-xs text-slate-400">Select analysis scope</p>
                </div>
                <span className="rounded-md border border-emerald-900 px-2 py-1 text-[10px] font-semibold text-emerald-400">
                  READ ONLY
                </span>
              </div>
              {bootstrap.isLoading ? (
                <div className="space-y-2">
                  {[0, 1, 2, 3].map((id) => (
                    <div key={id} className="h-8 animate-pulse rounded bg-white/[.035]" />
                  ))}
                </div>
              ) : (
                <SourceTree
                  sources={sources}
                  selectedIds={ui.selectedObjectIds}
                  activeId={ui.selectedObjectId}
                  onSelectionChange={ui.setSelectedObjectIds}
                  onActivate={(sourceId, objectId) => {
                    ui.setSelectedSourceId(sourceId);
                    ui.setSelectedObjectId(objectId);
                  }}
                />
              )}
            </section>

            <AnalysisPanel
              summary={summariseScope(sources, ui.selectedObjectIds)}
              context={ui.analysisContext}
              running={analyze.isPending || analysis.data?.status === "RUNNING"}
              error={analyze.error?.message ?? analysis.error?.message ?? null}
              stage={analysis.data?.stage ?? bootstrap.data?.activeAnalysis?.stage ?? null}
              onContextChange={ui.setAnalysisContext}
              onAnalyze={() => {
                analyze.mutate(
                  { selected: [...ui.selectedObjectIds], context: ui.analysisContext },
                  {
                    onSuccess: (run) => {
                      setAnalysisId(run.id);
                    },
                  },
                );
              }}
              onChat={() => {
                ui.openChat({
                  workspace: "ANALYZER",
                  selectedSourceId: ui.selectedSourceId ?? undefined,
                  selectedObjectId: ui.selectedObjectId ?? undefined,
                  selectedScope: [...ui.selectedObjectIds],
                });
              }}
            />
          </div>
        )}
      </div>
    </AnalyzerLayout>
  );
}

type ScopeSummary = {
  /**
   * Selected nodes that are actually analyzable, which is not the size of the
   * selection: ticking a database or a namespace cascades to its subtree, so
   * those container ids are in the set too. The backend drops them, and a
   * headline that counted them promised an analysis of more than would run.
   */
  readonly objectCount: number;
  readonly sourceCount: number;
  readonly bySource: readonly { readonly sourceId: string; readonly sourceName: string; readonly kinds: ReadonlyMap<string, number> }[];
  readonly unavailable: readonly string[];
};

/**
 * Summarise the selection hierarchically.
 *
 * Rendering one chip per selected object is unusable at the scale this tree
 * supports -- a schema with a thousand columns produces a thousand chips -- so
 * the summary counts by source and object kind, and lists only the ids that no
 * longer resolve, which are the ones a user has to act on.
 */
function summariseScope(sources: readonly AnalyzerSource[], selected: ReadonlySet<string>): ScopeSummary {
  const bySource: { readonly sourceId: string; readonly sourceName: string; readonly kinds: Map<string, number> }[] = [];
  const seen = new Set<string>();
  let objectCount = 0;
  for (const source of sources) {
    const kinds = new Map<string, number>();
    const walk = (nodes: readonly SourceObject[]): void => {
      for (const node of nodes) {
        if (selected.has(node.id)) {
          seen.add(node.id);
          kinds.set(node.kind, (kinds.get(node.kind) ?? 0) + 1);
          if (node.children.length === 0) objectCount += 1;
        }
        walk(node.children);
      }
    };
    walk(source.objects);
    if (kinds.size > 0) bySource.push({ sourceId: source.id, sourceName: source.name, kinds });
  }
  return {
    objectCount,
    sourceCount: bySource.length,
    bySource,
    unavailable: [...selected].filter((id) => !seen.has(id)),
  };
}

function ScopeSummaryView({ summary }: { readonly summary: ScopeSummary }) {
  if (summary.bySource.length === 0 && summary.unavailable.length === 0) {
    return <p className="mt-3 text-sm text-slate-400">Nothing is selected yet. Choose source objects in the explorer to define the analysis scope.</p>;
  }
  return <div className="mt-3 space-y-2">
    {summary.bySource.map((entry) => <div key={entry.sourceId} className="flex flex-wrap items-center gap-2 rounded-lg border border-emerald-950 bg-[#07120f] px-3 py-2 text-xs">
      <span className="font-medium text-slate-200">{entry.sourceName}</span>
      {[...entry.kinds].sort(([left], [right]) => left.localeCompare(right)).map(([kind, count]) => <span key={kind} className="rounded bg-emerald-950 px-2 py-0.5 text-emerald-300">{count} {kind}{count === 1 ? "" : "s"}</span>)}
    </div>)}
    {summary.unavailable.length > 0 ? <p role="alert" className="rounded-lg border border-amber-800 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">
      <CircleAlert className="mr-1 inline" size={12} />{summary.unavailable.length} selected object(s) are no longer available in their source and will be skipped. Refresh the source metadata to resolve this.
    </p> : null}
  </div>;
}

function AnalysisPanel({ summary, context, running, error, stage, onContextChange, onAnalyze, onChat }: { readonly summary: ScopeSummary; readonly context: string; readonly running: boolean; readonly error: string | null; readonly stage: string | null; readonly onContextChange: (value: string) => void; readonly onAnalyze: () => void; readonly onChat: () => void }) { return <section className="rounded-xl border border-emerald-950 bg-[#0a1714] p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold text-white">Analysis scope & context</h3><p className="mt-1 text-sm text-slate-400">Only {summary.objectCount} explicitly selected source objects across {summary.sourceCount} source(s) will be analyzed.</p></div><button type="button" onClick={onChat} className="inline-flex items-center gap-2 text-sm text-emerald-300"><Bot size={15} />Discuss scope</button></div><ScopeSummaryView summary={summary} /><textarea value={context} aria-label="Analysis context" onChange={(event) => { onContextChange(event.target.value); }} rows={4} maxLength={12_000} placeholder="Add business context, relationship expectations, identifier knowledge, naming guidance, or graph modeling constraints…" className="mt-4 w-full resize-y rounded-lg border border-emerald-950 bg-[#07120f] p-3 text-sm leading-6 text-white outline-none placeholder:text-slate-400 focus:border-emerald-600" /><div className="mt-3 flex flex-wrap items-center justify-between gap-3"><div>{stage !== null ? <span className="inline-flex items-center gap-2 text-xs capitalize text-emerald-300">{running ? <Clock3 size={14} className="animate-pulse" /> : <CheckCircle2 size={14} />}{stage.replaceAll("_", " ").toLowerCase()}</span> : <span className="text-xs text-slate-400">No analysis has been run in this workspace.</span>}{error !== null ? <span role="alert" className="ml-3 inline-flex items-center gap-1 text-xs text-red-300"><CircleAlert size={13} />{error}</span> : null}</div><button type="button" onClick={onAnalyze} disabled={running || summary.objectCount === 0} className="inline-flex items-center gap-2 rounded-lg bg-emerald-400 px-4 py-2.5 text-sm font-semibold text-emerald-950 disabled:cursor-not-allowed disabled:opacity-40"><Network size={16} />{running ? "Analyzing selected scope…" : "Analyze selected sources"}</button></div></section>; }

