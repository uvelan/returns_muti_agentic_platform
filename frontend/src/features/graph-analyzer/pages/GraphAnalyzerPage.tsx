import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bot, CheckCircle2, ChevronDown, CircleAlert, Clock3, Database, Network, Pencil, Plus, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { previewSourceObject, refreshSource, removeSource, saveSource, startAnalysis, validateSource } from "../../../api/graphAnalyzer";
import type { AnalyzerSource, PreviewGraph, SourceInput, SourceObject } from "../../../contracts/graphAnalyzer";
import { ConfirmationDialog } from "../components/ConfirmationDialog";
import { AnalyzerLayout } from "../components/AnalyzerLayout";
import { SourceDialog } from "../components/SourceDialog";
import { SourceTree } from "../components/SourceTree";
import { useGraphAnalyzer } from "../GraphAnalyzerContext";
import { analyzerKeys, useAnalysis, useAnalyzerBootstrap, useAnalyzerMutation } from "../analyzerQueries";

type InspectorTab = "STRUCTURE" | "TABLE" | "JSON" | "GRAPH";

export function GraphAnalyzerPage() {
  const bootstrap = useAnalyzerBootstrap();
  const ui = useGraphAnalyzer();
  const [sourceDialogOpen, setSourceDialogOpen] = useState(false);
  const [editingSource, setEditingSource] = useState<AnalyzerSource | null>(null);
  const [deleteSource, setDeleteSource] = useState<AnalyzerSource | null>(null);
  const [managerOpen, setManagerOpen] = useState(true);
  const [analysisId, setAnalysisId] = useState<string | null>(bootstrap.data?.activeAnalysis?.id ?? null);
  const analysis = useAnalysis(analysisId);
  const save = useAnalyzerMutation(({ input, sourceId }: { readonly input: SourceInput; readonly sourceId?: string }) => saveSource(input, sourceId));
  const remove = useAnalyzerMutation(removeSource);
  const validate = useAnalyzerMutation(validateSource);
  const refresh = useAnalyzerMutation(refreshSource);
  const analyze = useAnalyzerMutation(({ selected, context }: { readonly selected: readonly string[]; readonly context: string }) => startAnalysis(selected, context));
  const sources = bootstrap.data?.sources ?? [];
  const activeSource = sources.find((source) => source.id === ui.selectedSourceId) ?? null;
  const activeObject = activeSource === null ? null : findObject(activeSource.objects, ui.selectedObjectId);

  const runAnalysis = () => {
    analyze.mutate({ selected: [...ui.selectedObjectIds], context: ui.analysisContext }, { onSuccess: (run) => { setAnalysisId(run.id); } });
  };

  return <AnalyzerLayout>
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4"><div><p className="text-xs font-semibold uppercase tracking-[.2em] text-emerald-500">Primary workspace</p><h2 className="mt-1 text-2xl font-semibold text-white">Explore sources. Design the graph.</h2><p className="mt-1 max-w-2xl text-sm text-slate-400">Inspect trusted metadata and bounded samples across sources, then analyze only the objects you select.</p></div><button type="button" onClick={() => { setEditingSource(null); setSourceDialogOpen(true); }} className="inline-flex items-center gap-2 rounded-lg bg-emerald-400 px-4 py-2.5 text-sm font-semibold text-emerald-950 shadow-lg shadow-black/20 hover:bg-emerald-300"><Plus size={16} />Add data source</button></div>

      <section className="overflow-hidden rounded-xl border border-emerald-950 bg-[#0a1714]"><button type="button" onClick={() => { setManagerOpen((value) => !value); }} className="flex w-full items-center justify-between px-4 py-3 text-left"><span className="flex items-center gap-2 text-sm font-semibold text-slate-200"><Database size={16} className="text-emerald-400" />Connections <span className="font-normal text-slate-600">{sources.length} configured</span></span><ChevronDown size={16} className={`text-slate-500 transition ${managerOpen ? "rotate-180" : ""}`} /></button>{managerOpen ? <div className="grid gap-3 border-t border-emerald-950 p-4 lg:grid-cols-2 xl:grid-cols-4">{sources.map((source) => <SourceCard key={source.id} source={source} busy={validate.isPending || refresh.isPending || remove.isPending} onOpen={() => { ui.setSelectedSourceId(source.id); ui.setSelectedObjectId(null); }} onEdit={() => { setEditingSource(source); setSourceDialogOpen(true); }} onDelete={() => { setDeleteSource(source); }} onValidate={() => { validate.mutate(source.id); }} onRefresh={() => { refresh.mutate(source.id); }} />)}{sources.length === 0 && !bootstrap.isLoading ? (bootstrap.isError ? <div role="alert" className="col-span-full rounded-lg border border-red-900 bg-red-950/30 p-6 text-center"><CircleAlert className="mx-auto text-red-400" /><p className="mt-2 font-medium text-red-100">Configured sources could not be loaded</p><p className="mt-1 text-sm text-red-200/80">{bootstrap.error.message}</p><button type="button" onClick={() => { void bootstrap.refetch(); }} className="mt-3 rounded-md border border-red-700 px-3 py-1.5 text-sm text-red-100">Retry</button></div> : <div className="col-span-full rounded-lg border border-dashed border-emerald-900 p-6 text-center"><Database className="mx-auto text-slate-600" /><p className="mt-2 font-medium text-slate-300">No sources configured</p><p className="mt-1 text-sm text-slate-500">Add a read-only connection to begin discovery.</p></div>) : null}{bootstrap.isLoading ? [0, 1].map((id) => <div key={id} className="h-24 animate-pulse rounded-lg bg-white/[.035]" />) : null}</div> : null}</section>

      <div className="grid min-h-[570px] gap-4 xl:grid-cols-[330px_minmax(0,1fr)]">
        <section className="rounded-xl border border-emerald-950 bg-[#0a1714] p-4"><div className="mb-4 flex items-center justify-between"><div><h3 className="font-semibold text-white">Source explorer</h3><p className="text-xs text-slate-500">Select analysis scope</p></div><span className="rounded-md border border-emerald-900 px-2 py-1 text-[10px] font-semibold text-emerald-400">READ ONLY</span></div><SourceTree sources={sources} selectedIds={ui.selectedObjectIds} activeId={ui.selectedObjectId} onSelectionChange={ui.setSelectedObjectIds} onActivate={(sourceId, objectId) => { ui.setSelectedSourceId(sourceId); ui.setSelectedObjectId(objectId); }} /></section>
        <div className="space-y-4"><ObjectInspector source={activeSource} object={activeObject} /><AnalysisPanel summary={summariseScope(sources, ui.selectedObjectIds)} selectedIds={ui.selectedObjectIds} context={ui.analysisContext} running={analyze.isPending || analysis.data?.status === "RUNNING"} error={analyze.error?.message ?? analysis.error?.message ?? null} stage={analysis.data?.stage ?? bootstrap.data?.activeAnalysis?.stage ?? null} onContextChange={ui.setAnalysisContext} onAnalyze={runAnalysis} onChat={() => { ui.openChat({ workspace: "ANALYZER", selectedSourceId: ui.selectedSourceId ?? undefined, selectedObjectId: ui.selectedObjectId ?? undefined, selectedScope: [...ui.selectedObjectIds] }); }} /></div>
      </div>
    </div>
    <SourceDialog key={`${String(sourceDialogOpen)}:${editingSource?.id ?? "new"}`} source={editingSource} open={sourceDialogOpen} saving={save.isPending} error={save.error?.message ?? null} onClose={() => { if (!save.isPending) setSourceDialogOpen(false); }} onSave={(input) => { save.mutate({ input, sourceId: editingSource?.id }, { onSuccess: () => { setSourceDialogOpen(false); } }); }} />
    <ConfirmationDialog isOpen={deleteSource !== null} title="Remove source configuration?" description={`${deleteSource?.name ?? "This source"} will be removed from Analyzer selections and mappings. No database, schema, or business record in the source will be deleted.`} confirmText={remove.isPending ? "Removing…" : "Remove configuration"} isDestructive onCancel={() => { if (!remove.isPending) setDeleteSource(null); }} onConfirm={() => { if (deleteSource !== null && !remove.isPending) remove.mutate(deleteSource.id, { onSuccess: () => { setDeleteSource(null); } }); }} />
  </AnalyzerLayout>;
}

function SourceCard({ source, busy, onOpen, onEdit, onDelete, onValidate, onRefresh }: { readonly source: AnalyzerSource; readonly busy: boolean; readonly onOpen: () => void; readonly onEdit: () => void; readonly onDelete: () => void; readonly onValidate: () => void; readonly onRefresh: () => void }) {
  const connected = source.status === "CONNECTED";
  return <div className="rounded-xl border border-emerald-950 bg-[#07120f] p-3"><button type="button" onClick={onOpen} className="flex w-full items-start gap-3 text-left"><span className={`grid size-9 shrink-0 place-items-center rounded-lg ${connected ? "bg-emerald-950 text-emerald-300" : "bg-amber-950 text-amber-300"}`}><Database size={16} /></span><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-white">{source.name}</span><span className="block truncate text-xs text-slate-500">{source.engine} · {source.database}</span></span><span className={`mt-1 size-2 rounded-full ${connected ? "bg-emerald-400" : "bg-amber-400"}`} /></button><div className="mt-3 flex items-center justify-between"><span className="text-[10px] font-semibold text-emerald-500">READ ONLY</span><div className="flex gap-1"><IconButton label="Validate source" disabled={busy} onClick={onValidate}><ShieldCheck size={14} /></IconButton><IconButton label="Refresh metadata" disabled={busy} onClick={onRefresh}><RefreshCw size={14} /></IconButton><IconButton label="Edit connection" disabled={busy} onClick={onEdit}><Pencil size={14} /></IconButton><IconButton label="Remove configuration" disabled={busy} onClick={onDelete}><Trash2 size={14} /></IconButton></div></div></div>;
}

function IconButton({ label, disabled, onClick, children }: { readonly label: string; readonly disabled: boolean; readonly onClick: () => void; readonly children: React.ReactNode }) { return <button type="button" title={label} aria-label={label} disabled={disabled} onClick={onClick} className="rounded-md p-1.5 text-slate-500 hover:bg-white/5 hover:text-white disabled:opacity-40">{children}</button>; }

function ObjectInspector({ source, object }: { readonly source: AnalyzerSource | null; readonly object: SourceObject | null }) {
  const [tab, setTab] = useState<InspectorTab>("STRUCTURE");
  const [page, setPage] = useState(1);
  const preview = useQuery({ queryKey: [...analyzerKeys.all, "preview", source?.id, object?.id, page], queryFn: ({ signal }) => previewSourceObject(source?.id ?? "", object?.id ?? "", page, signal), enabled: source !== null && object !== null && (tab === "TABLE" || tab === "JSON" || tab === "GRAPH") });
  if (source === null || object === null) return <section className="grid min-h-72 place-items-center rounded-xl border border-dashed border-emerald-900 bg-[#0a1714] p-8 text-center"><div><Network className="mx-auto text-slate-700" size={30} /><h3 className="mt-3 font-medium text-slate-300">Choose a source object</h3><p className="mt-1 text-sm text-slate-500">Structure and bounded data previews appear here. Source content is never editable.</p></div></section>;
  const tabs: readonly InspectorTab[] = source.engine === "NEO4J" ? ["STRUCTURE", "TABLE", "JSON", "GRAPH"] : ["STRUCTURE", "TABLE", "JSON"];
  return <section className="overflow-hidden rounded-xl border border-emerald-950 bg-[#0a1714]"><header className="flex flex-wrap items-center justify-between gap-3 border-b border-emerald-950 px-5 py-4"><div><div className="flex items-center gap-2"><h3 className="font-semibold text-white">{object.name}</h3><span className="rounded bg-slate-800 px-2 py-0.5 text-[10px] uppercase text-slate-400">{object.kind}</span></div><p className="mt-1 text-xs text-slate-500">{object.path.join(" / ")}</p></div><span className="inline-flex items-center gap-1 rounded-md border border-emerald-900 px-2 py-1 text-[10px] text-emerald-300"><ShieldCheck size={12} />Read-only inspection</span></header><div className="flex gap-1 border-b border-emerald-950 px-4 pt-2">{tabs.map((item) => <button key={item} type="button" onClick={() => { setTab(item); setPage(1); }} className={`border-b-2 px-3 py-2 text-xs font-medium ${tab === item ? "border-emerald-400 text-emerald-300" : "border-transparent text-slate-500 hover:text-slate-300"}`}>{item}</button>)}</div><div className="min-h-52 p-5">{tab === "STRUCTURE" ? <StructureView object={object} /> : tab === "GRAPH" ? <GraphPreview loading={preview.isLoading} error={preview.error?.message ?? null} graph={preview.data?.graph} onRetry={() => { void preview.refetch(); }} /> : <PreviewView json={tab === "JSON"} loading={preview.isLoading} error={preview.error?.message ?? null} data={preview.data} page={page} onPage={setPage} />}</div></section>;
}

function StructureView({ object }: { readonly object: SourceObject }) { const fields = object.fields ?? []; return fields.length === 0 ? <p className="text-sm text-slate-500">No field metadata is available. Refresh the source or revalidate the connection.</p> : <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-xs uppercase tracking-wider text-slate-600"><tr><th className="pb-3">Field</th><th className="pb-3">Type</th><th className="pb-3">Nullable</th><th className="pb-3">Evidence</th></tr></thead><tbody>{fields.map((field) => <tr key={field.name} className="border-t border-emerald-950"><td className="py-3 font-mono text-emerald-200">{field.name}</td><td className="py-3 text-slate-400">{field.dataType}</td><td className="py-3 text-slate-400">{field.nullable ? "Yes" : "Required"}</td><td className="py-3"><div className="flex gap-2">{field.identifier ? <span className="rounded bg-violet-950 px-2 py-0.5 text-xs text-violet-300">Identifier</span> : null}{field.indexed ? <span className="rounded bg-sky-950 px-2 py-0.5 text-xs text-sky-300">Indexed</span> : null}</div></td></tr>)}</tbody></table></div>; }

function PreviewView({ json, loading, error, data, page, onPage }: { readonly json: boolean; readonly loading: boolean; readonly error: string | null; readonly data: Awaited<ReturnType<typeof previewSourceObject>> | undefined; readonly page: number; readonly onPage: (page: number) => void }) { if (loading) return <div className="space-y-2">{[0, 1, 2].map((id) => <div key={id} className="h-9 animate-pulse rounded bg-white/[.035]" />)}</div>; if (error !== null) return <p role="alert" className="text-sm text-red-300">Data preview failed: {error}</p>; if (data === undefined || data.rows.length === 0) return <p className="text-sm text-slate-500">No accessible records were returned for this page.</p>; if (json) return <pre className="max-h-72 overflow-auto rounded-lg bg-[#050c0a] p-4 text-xs leading-5 text-emerald-100">{JSON.stringify(data.rows, null, 2)}</pre>; return <><div className="overflow-auto"><table className="min-w-full text-left text-xs"><thead className="text-slate-500"><tr>{data.columns.map((column) => <th key={column} className="whitespace-nowrap border-b border-emerald-950 px-3 py-2">{column}</th>)}</tr></thead><tbody>{data.rows.map((row, rowIndex) => <tr key={rowIndex}>{data.columns.map((column) => <td key={column} className="max-w-56 truncate border-b border-emerald-950/70 px-3 py-2 font-mono text-slate-300">{formatCell(row[column])}</td>)}</tr>)}</tbody></table></div><div className="mt-3 flex justify-end gap-2"><button type="button" disabled={page === 1} onClick={() => { onPage(page - 1); }} className="rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-30">Previous</button><span className="px-2 py-1 text-xs text-slate-500">Page {page}</span><button type="button" disabled={data.rows.length < data.pageSize} onClick={() => { onPage(page + 1); }} className="rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-30">Next</button></div></>; }

/**
 * A bounded, read-only sample of an external graph source.
 *
 * This used to be a hardcoded SVG -- three circles labelled "Selected" and
 * "Related" that were drawn whatever the source contained, for a source it had
 * never read. It is now driven entirely by `PreviewPage.graph`, and shows an
 * honest empty state when the backend returns none, because a graph explorer
 * that invents its own nodes is worse than no graph explorer.
 */
function GraphPreview({ loading, error, graph, onRetry }: { readonly loading: boolean; readonly error: string | null; readonly graph: PreviewGraph | null | undefined; readonly onRetry: () => void }) {
  const [focusId, setFocusId] = useState<string | null>(null);
  const layout = useMemo(() => graphLayout(graph), [graph]);
  if (loading) return <div className="h-64 animate-pulse rounded-lg bg-white/[.035]" />;
  if (error !== null) return <div role="alert" className="rounded-lg border border-red-900 bg-red-950/30 p-4 text-sm text-red-200"><p>Graph preview failed: {error}</p><button type="button" onClick={onRetry} className="mt-2 rounded border border-red-700 px-2 py-1 text-xs">Retry</button></div>;
  if (graph === null || graph === undefined || graph.nodes.length === 0) return <div className="grid h-64 place-items-center rounded-lg border border-dashed border-emerald-900 bg-[#050c0a] text-center"><div><Network className="mx-auto text-slate-700" size={26} /><p className="mt-2 text-sm text-slate-400">No graph sample was returned for this object.</p><p className="mt-1 text-xs text-slate-600">Refresh the source metadata or revalidate the connection.</p></div></div>;
  const focused = focusId === null ? null : graph.nodes.find((node) => node.id === focusId) ?? null;
  return <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px]">
    <div className="relative h-64 overflow-hidden rounded-lg border border-emerald-950 bg-[#050c0a]" style={{ backgroundImage: "radial-gradient(rgba(52,211,153,.12) 1px, transparent 1px)", backgroundSize: "20px 20px" }}>
      <svg className="absolute inset-0 size-full" aria-label="Read-only sample of the external graph source">
        <defs><marker id="analyzer-preview-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6 z" fill="#276d55" /></marker></defs>
        {graph.edges.map((edge) => { const from = layout.get(edge.fromId); const to = layout.get(edge.toId); if (from === undefined || to === undefined) return null; return <g key={edge.id}><line x1={`${String(from.x)}%`} y1={`${String(from.y)}%`} x2={`${String(to.x)}%`} y2={`${String(to.y)}%`} stroke="#276d55" strokeWidth={1.5} markerEnd="url(#analyzer-preview-arrow)" /><text x={`${String((from.x + to.x) / 2)}%`} y={`${String((from.y + to.y) / 2)}%`} fill="#64748b" fontSize="9" textAnchor="middle">{edge.type}</text></g>; })}
      </svg>
      {graph.nodes.map((node) => { const point = layout.get(node.id); if (point === undefined) return null; return <button key={node.id} type="button" onClick={() => { setFocusId(node.id); }} style={{ left: `${String(point.x)}%`, top: `${String(point.y)}%` }} className={`absolute max-w-28 -translate-x-1/2 -translate-y-1/2 truncate rounded-full border px-3 py-2 text-[10px] ${focusId === node.id ? "border-emerald-400 bg-emerald-900 text-emerald-100" : "border-emerald-800 bg-emerald-950 text-emerald-200"}`}>{node.labels[0] ?? "Node"}</button>; })}
      <span className="absolute right-3 top-3 rounded bg-black/60 px-2 py-1 text-[10px] text-emerald-300">READ ONLY</span>
    </div>
    <aside className="rounded-lg border border-emerald-950 bg-[#07120f] p-3 text-xs">
      {focused === null ? <p className="text-slate-500">Select a node to inspect its labels and properties. Nothing here is editable.</p> : <><p className="font-semibold text-emerald-200">{focused.labels.join(", ") || "Node"}</p><dl className="mt-2 max-h-44 space-y-1 overflow-y-auto">{Object.entries(focused.properties).map(([key, value]) => <div key={key}><dt className="text-slate-600">{key}</dt><dd className="truncate font-mono text-slate-300">{formatCell(value)}</dd></div>)}</dl></>}
      <p className="mt-3 border-t border-emerald-950 pt-2 text-[10px] text-slate-600">{graph.nodes.length} nodes · {graph.edges.length} relationships in this bounded sample.</p>
    </aside>
  </div>;
}

/** Deterministic ring layout, so the same sample always renders the same way. */
function graphLayout(graph: PreviewGraph | null | undefined): ReadonlyMap<string, { readonly x: number; readonly y: number }> {
  const points = new Map<string, { readonly x: number; readonly y: number }>();
  const nodes = graph?.nodes ?? [];
  if (nodes.length === 1) { points.set(nodes[0].id, { x: 50, y: 50 }); return points; }
  nodes.forEach((node, index) => {
    const angle = (index / nodes.length) * Math.PI * 2 - Math.PI / 2;
    points.set(node.id, { x: 50 + Math.cos(angle) * 32, y: 50 + Math.sin(angle) * 34 });
  });
  return points;
}


type ScopeSummary = {
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
  for (const source of sources) {
    const kinds = new Map<string, number>();
    const walk = (nodes: readonly SourceObject[]): void => {
      for (const node of nodes) {
        if (selected.has(node.id)) {
          seen.add(node.id);
          kinds.set(node.kind, (kinds.get(node.kind) ?? 0) + 1);
        }
        walk(node.children);
      }
    };
    walk(source.objects);
    if (kinds.size > 0) bySource.push({ sourceId: source.id, sourceName: source.name, kinds });
  }
  return {
    sourceCount: bySource.length,
    bySource,
    unavailable: [...selected].filter((id) => !seen.has(id)),
  };
}

function ScopeSummaryView({ summary }: { readonly summary: ScopeSummary }) {
  if (summary.bySource.length === 0 && summary.unavailable.length === 0) {
    return <p className="mt-3 text-sm text-slate-500">Nothing is selected yet. Choose source objects in the explorer to define the analysis scope.</p>;
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

function AnalysisPanel({ summary, selectedIds, context, running, error, stage, onContextChange, onAnalyze, onChat }: { readonly summary: ScopeSummary; readonly selectedIds: ReadonlySet<string>; readonly context: string; readonly running: boolean; readonly error: string | null; readonly stage: string | null; readonly onContextChange: (value: string) => void; readonly onAnalyze: () => void; readonly onChat: () => void }) { return <section className="rounded-xl border border-emerald-950 bg-[#0a1714] p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold text-white">Analysis scope & context</h3><p className="mt-1 text-sm text-slate-500">Only {selectedIds.size} explicitly selected source objects across {summary.sourceCount} source(s) will be analyzed.</p></div><button type="button" onClick={onChat} className="inline-flex items-center gap-2 text-sm text-emerald-300"><Bot size={15} />Discuss scope</button></div><ScopeSummaryView summary={summary} /><textarea value={context} onChange={(event) => { onContextChange(event.target.value); }} rows={4} maxLength={12_000} placeholder="Add business context, relationship expectations, identifier knowledge, naming guidance, or graph modeling constraints…" className="mt-4 w-full resize-y rounded-lg border border-emerald-950 bg-[#07120f] p-3 text-sm leading-6 text-white outline-none placeholder:text-slate-600 focus:border-emerald-600" /><div className="mt-3 flex flex-wrap items-center justify-between gap-3"><div>{stage !== null ? <span className="inline-flex items-center gap-2 text-xs capitalize text-emerald-300">{running ? <Clock3 size={14} className="animate-pulse" /> : <CheckCircle2 size={14} />}{stage.replaceAll("_", " ").toLowerCase()}</span> : <span className="text-xs text-slate-600">No analysis has been run in this workspace.</span>}{error !== null ? <span role="alert" className="ml-3 inline-flex items-center gap-1 text-xs text-red-300"><CircleAlert size={13} />{error}</span> : null}</div><button type="button" onClick={onAnalyze} disabled={running || selectedIds.size === 0} className="inline-flex items-center gap-2 rounded-lg bg-emerald-400 px-4 py-2.5 text-sm font-semibold text-emerald-950 disabled:cursor-not-allowed disabled:opacity-40"><Network size={16} />{running ? "Analyzing selected scope…" : "Analyze selected sources"}</button></div></section>; }

function findObject(nodes: readonly SourceObject[], id: string | null): SourceObject | null { if (id === null) return null; for (const node of nodes) { if (node.id === id) return node; const child = findObject(node.children, id); if (child !== null) return child; } return null; }
function formatCell(value: unknown): string { if (value === null) return "null"; if (value === undefined) return "undefined"; const serialized: string | undefined = JSON.stringify(value); return typeof serialized === "string" ? serialized : typeof value; }
