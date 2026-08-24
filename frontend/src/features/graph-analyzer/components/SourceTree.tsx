import { useMemo, useState } from "react";
import { ChevronRight, Database, FileKey, Folder, Network, Search, Table2 } from "lucide-react";
import type { AnalyzerSource, SourceObject } from "../../../contracts/graphAnalyzer";

type Props = {
  readonly sources: readonly AnalyzerSource[];
  readonly selectedIds: ReadonlySet<string>;
  readonly activeId: string | null;
  readonly onSelectionChange: (ids: ReadonlySet<string>) => void;
  readonly onActivate: (sourceId: string, objectId: string | null) => void;
  readonly selectionEnabled?: boolean;
};

function descendants(node: SourceObject): readonly string[] {
  return [node.id, ...node.children.flatMap(descendants)];
}

export function SourceTree({ sources, selectedIds, activeId, onSelectionChange, onActivate, selectionEnabled = true }: Props) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set(sources.map((source) => source.id)));
  const [query, setQuery] = useState("");
  const [showSelected, setShowSelected] = useState(false);
  const normalized = query.trim().toLowerCase();

  const visibleSources = useMemo(() => sources.map((source) => ({ ...source, objects: filterNodes(source.objects, normalized, showSelected ? selectedIds : null) })).filter((source) => normalized.length === 0 || source.name.toLowerCase().includes(normalized) || source.objects.length > 0), [sources, normalized, showSelected, selectedIds]);

  const toggleExpanded = (id: string) => { setExpanded((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; }); };
  const toggleNode = (node: SourceObject) => {
    const ids = descendants(node);
    const next = new Set(selectedIds);
    const shouldSelect = ids.some((id) => !next.has(id));
    for (const id of ids) { if (shouldSelect) next.add(id); else next.delete(id); }
    onSelectionChange(next);
  };

  return <div className="flex min-h-0 flex-col">
    <div className="relative"><Search className="absolute left-3 top-2.5 text-analyzer-on-surface-variant" size={15} /><input value={query} onChange={(event) => { setQuery(event.target.value); }} placeholder="Search source objects"
      aria-label="Search source objects" className="w-full rounded-lg border border-analyzer-outline-control bg-analyzer-surface-container py-2 pl-9 pr-3 text-sm text-white outline-none placeholder:text-analyzer-on-surface-variant focus:border-emerald-600" /></div>
    <div className="mt-3 flex items-center justify-between text-xs"><span className="text-analyzer-on-surface-variant">{selectedIds.size} selected</span><div className="flex gap-3"><button type="button" onClick={() => { setShowSelected((value) => !value); }} className={showSelected ? "text-analyzer-accent" : "text-analyzer-on-surface-variant"}>Show selected</button>{selectionEnabled ? <><button type="button" onClick={() => { onSelectionChange(new Set(sources.flatMap((source) => source.objects.flatMap(descendants)))); }} className="text-analyzer-on-surface-variant hover:text-white">Select all</button><button type="button" onClick={() => { onSelectionChange(new Set()); }} className="text-analyzer-on-surface-variant hover:text-white">Clear</button></> : null}</div></div>
    <div className="mt-3 max-h-[520px] overflow-y-auto pr-1">
      {visibleSources.map((source) => <div key={source.id} className="mb-1">
        <TreeRow id={source.id} name={source.name} kind="source" depth={0} expanded={expanded.has(source.id)} active={activeId === null && source.id.length > 0} checked={false} partial={false} readOnlyStatus={source.status} selectionEnabled={false} hasChildren={source.objects.length > 0} onExpand={() => { toggleExpanded(source.id); }} onActivate={() => { onActivate(source.id, null); }} onToggle={() => undefined} />
        {expanded.has(source.id) ? source.objects.map((node) => <NodeRow key={node.id} node={node} sourceId={source.id} depth={1} expanded={expanded} selectedIds={selectedIds} activeId={activeId} selectionEnabled={selectionEnabled} onExpand={toggleExpanded} onActivate={onActivate} onToggle={toggleNode} />) : null}
      </div>)}
      {visibleSources.length === 0 ? <div className="rounded-lg border border-dashed border-analyzer-outline-variant p-5 text-center text-sm text-analyzer-on-surface-variant">No source objects match this view.</div> : null}
    </div>
  </div>;
}

function filterNodes(nodes: readonly SourceObject[], query: string, selected: ReadonlySet<string> | null): readonly SourceObject[] {
  return nodes.flatMap((node) => {
    const children = filterNodes(node.children, query, selected);
    const matches = (query.length === 0 || node.name.toLowerCase().includes(query)) && (selected === null || selected.has(node.id));
    return matches || children.length > 0 ? [{ ...node, children }] : [];
  });
}

function NodeRow({ node, sourceId, depth, expanded, selectedIds, activeId, selectionEnabled, onExpand, onActivate, onToggle }: { readonly node: SourceObject; readonly sourceId: string; readonly depth: number; readonly expanded: ReadonlySet<string>; readonly selectedIds: ReadonlySet<string>; readonly activeId: string | null; readonly selectionEnabled: boolean; readonly onExpand: (id: string) => void; readonly onActivate: (sourceId: string, objectId: string) => void; readonly onToggle: (node: SourceObject) => void }) {
  const childIds = descendants(node);
  const count = childIds.filter((id) => selectedIds.has(id)).length;
  const checked = count === childIds.length;
  const partial = count > 0 && !checked;
  return <><TreeRow id={node.id} name={node.name} kind={node.kind} depth={depth} expanded={expanded.has(node.id)} active={activeId === node.id} checked={checked} partial={partial} selectionEnabled={selectionEnabled && node.selectable} hasChildren={node.children.length > 0} onExpand={() => { onExpand(node.id); }} onActivate={() => { onActivate(sourceId, node.id); }} onToggle={() => { onToggle(node); }} />{expanded.has(node.id) ? node.children.map((child) => <NodeRow key={child.id} node={child} sourceId={sourceId} depth={depth + 1} expanded={expanded} selectedIds={selectedIds} activeId={activeId} selectionEnabled={selectionEnabled} onExpand={onExpand} onActivate={onActivate} onToggle={onToggle} />) : null}</>;
}

function TreeRow({ id, name, kind, depth, expanded, active, checked, partial, readOnlyStatus, selectionEnabled, hasChildren, onExpand, onActivate, onToggle }: { readonly id: string; readonly name: string; readonly kind: SourceObject["kind"] | "source"; readonly depth: number; readonly expanded: boolean; readonly active: boolean; readonly checked: boolean; readonly partial: boolean; readonly readOnlyStatus?: AnalyzerSource["status"]; readonly selectionEnabled: boolean; readonly hasChildren: boolean; readonly onExpand: () => void; readonly onActivate: () => void; readonly onToggle: () => void }) {
  const Icon = kind === "source" ? Database : kind === "table" || kind === "collection" ? Table2 : kind === "entity" || kind === "relationship" ? Network : kind === "field" ? FileKey : Folder;
  return <div data-tree-id={id} className={`group flex items-center gap-1 rounded-lg py-1.5 pr-2 text-sm ${active ? "bg-analyzer-primary-container text-emerald-100" : "text-analyzer-on-surface-variant hover:bg-white/[.035] hover:text-analyzer-on-surface-emphasis"}`} style={{ paddingLeft: 4 + depth * 16 }}>
    <button type="button" className={`grid size-5 place-items-center ${hasChildren ? "visible" : "invisible"}`} onClick={onExpand} aria-label={`${expanded ? "Collapse" : "Expand"} ${name}`}><ChevronRight size={14} className={`transition-transform ${expanded ? "rotate-90" : ""}`} /></button>
    {selectionEnabled ? <button type="button" role="checkbox" aria-label={`Select ${name}`} aria-checked={partial ? "mixed" : checked} onClick={onToggle} className={`grid size-4 shrink-0 place-items-center rounded border text-[10px] ${checked || partial ? "border-emerald-400 bg-analyzer-primary text-analyzer-on-primary" : "border-analyzer-outline-control-neutral"}`}>{checked ? "✓" : partial ? "−" : ""}</button> : null}
    <button type="button" onClick={onActivate} className="flex min-w-0 flex-1 items-center gap-2 text-left"><Icon size={14} className={kind === "source" ? "text-analyzer-primary" : "text-analyzer-on-surface-variant"} /><span className="truncate">{name}</span>{kind === "source" ? <><span className="ml-auto rounded border border-analyzer-outline px-1.5 py-0.5 text-[9px] font-semibold text-analyzer-primary">READ ONLY</span><span
      // Hue alone said whether the source was reachable, which is nothing to
      // a screen reader and nothing to a red-green colour-blind operator.
      // The dot stays; the word rides along in the accessible name.
      title={readOnlyStatus === "CONNECTED" ? "Connected" : "Not connected"}
      className={`size-1.5 shrink-0 rounded-full ${readOnlyStatus === "CONNECTED" ? "bg-analyzer-primary" : "bg-amber-400"}`}
    ><span className="sr-only">{readOnlyStatus === "CONNECTED" ? "Connected" : "Not connected"}</span></span></> : null}</button>
  </div>;
}
