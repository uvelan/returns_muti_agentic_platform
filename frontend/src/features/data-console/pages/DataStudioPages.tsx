import { useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Bot, CheckCircle2, Database, Network, RefreshCw, ShieldAlert } from "lucide-react";

import {
  applyAIStudioProposal,
  applyGraphSchema,
  executeGraphSync,
  generateAIStudioProposal,
  getAIStudioProposal,
  getSchemaRegistry,
  listAIStudioProposals,
  listFeedbackLearning,
  listGraphSyncRuns,
} from "../../../api/dataStudio";
import type { DataAssetSchema } from "../../../contracts/dataStudio";
import { EmptyState } from "../../../components/EmptyState";
import { ErrorState } from "../../../components/ErrorState";
import { LoadingState } from "../../../components/LoadingState";
import { PageHeader } from "../../../components/PageHeader";

const schemaKey = ["data-console", "schema"] as const;
const studioKey = ["data-console", "ai-studio"] as const;
const syncKey = ["data-console", "graph-sync"] as const;
const feedbackKey = ["data-console", "feedback-learning"] as const;

const primaryButton = "inline-flex items-center gap-2 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton = "inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-800 disabled:cursor-not-allowed disabled:opacity-50";

function Badge({ children }: { readonly children: ReactNode }) {
  return <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700">{children}</span>;
}

function AssetCard({ asset }: { readonly asset: DataAssetSchema }) {
  return (
    <details className="rounded-xl border border-slate-200 bg-white shadow-sm">
      <summary className="cursor-pointer list-none p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{asset.engine} · {asset.ownership}</p>
            <h2 className="mt-1 font-semibold text-slate-900">{asset.namespace ? `${asset.namespace}.` : ""}{asset.name}</h2>
            <p className="mt-1 text-sm text-slate-600">{asset.description}</p>
          </div>
          <div className="flex gap-2"><Badge>{asset.fields.length} fields</Badge>{asset.writable_in_sandbox ? <Badge>Sandbox writable</Badge> : null}</div>
        </div>
      </summary>
      <div className="overflow-x-auto border-t border-slate-200">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500"><tr><th className="px-4 py-3">Field</th><th className="px-4 py-3">Type</th><th className="px-4 py-3">Rules</th><th className="px-4 py-3">Description</th></tr></thead>
          <tbody className="divide-y divide-slate-100">{asset.fields.map((field) => <tr key={field.name}><td className="px-4 py-3 font-mono text-xs">{field.name}</td><td className="px-4 py-3">{field.type}</td><td className="px-4 py-3">{field.key ? "KEY " : ""}{field.required ? "REQUIRED" : "OPTIONAL"}</td><td className="px-4 py-3 text-slate-600">{field.description}</td></tr>)}</tbody>
        </table>
      </div>
    </details>
  );
}

export function SchemaCatalogPage() {
  const query = useQuery({ queryKey: schemaKey, queryFn: ({ signal }) => getSchemaRegistry(signal) });
  const [section, setSection] = useState<"MONGODB" | "SQLSERVER" | "GRAPH">("MONGODB");
  if (query.isLoading) return <LoadingState message="Loading model registry..." />;
  if (query.isError || !query.data) return <ErrorState message={query.error?.message ?? "Schema registry unavailable"} />;
  const registry = query.data;
  const assets = section === "GRAPH" ? [] : registry.assets.filter((asset) => asset.engine === section);
  return <div><PageHeader title="Model & Schema Catalog" description="Version-controlled MongoDB collections, SQL tables, Neo4j nodes, relationships, ownership, keys, and sandbox write boundaries." />
    <div className="mb-6 flex flex-wrap gap-2">{(["MONGODB", "SQLSERVER", "GRAPH"] as const).map((value) => <button key={value} type="button" onClick={() => { setSection(value); }} className={section === value ? primaryButton : secondaryButton}>{value}</button>)}</div>
    {section !== "GRAPH" ? <div className="space-y-4">{assets.map((asset) => <AssetCard key={asset.asset_id} asset={asset} />)}</div> : <div className="grid gap-6 lg:grid-cols-2"><section className="rounded-xl border border-slate-200 bg-white p-5"><h2 className="flex items-center gap-2 font-semibold"><Database size={18} />Node labels</h2><div className="mt-4 space-y-3">{registry.graph.nodes.map((node) => <div key={node.label} className="rounded-lg border border-slate-200 p-4"><div className="flex justify-between gap-3"><strong>{node.label}</strong><code className="text-xs">{node.key_property}</code></div><p className="mt-2 text-xs text-slate-500">Sources: {node.source_assets.join(", ")}</p><div className="mt-2 flex flex-wrap gap-1">{node.properties.map((property) => <Badge key={property}>{property}</Badge>)}</div></div>)}</div></section><section className="rounded-xl border border-slate-200 bg-white p-5"><h2 className="flex items-center gap-2 font-semibold"><Network size={18} />Relationships</h2><div className="mt-4 space-y-3">{registry.graph.relationships.map((relationship) => <div key={relationship.type} className="rounded-lg border border-slate-200 p-4"><strong>{relationship.type}</strong><p className="mt-1 font-mono text-xs text-slate-600">({relationship.from_label}.{relationship.from_key}) → ({relationship.to_label}.{relationship.to_key})</p></div>)}</div></section></div>}
  </div>;
}

export function AIStudioPage() {
  const queryClient = useQueryClient();
  const schema = useQuery({ queryKey: schemaKey, queryFn: ({ signal }) => getSchemaRegistry(signal) });
  const proposals = useQuery({ queryKey: studioKey, queryFn: ({ signal }) => listAIStudioProposals(signal) });
  const allAssets = useMemo(() => schema.data?.assets ?? [], [schema.data]);
  const [selected, setSelected] = useState<string[]>([]);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const preview = useQuery({
    queryKey: [...studioKey, "proposal", previewId],
    queryFn: ({ signal }) => getAIStudioProposal(previewId ?? "", signal),
    enabled: previewId !== null,
  });
  const [scenarioName, setScenarioName] = useState("return-sandbox");
  const [recordsPerAsset, setRecordsPerAsset] = useState(5);
  const [seed, setSeed] = useState(20260724);
  const generation = useMutation({ mutationFn: generateAIStudioProposal, onSuccess: () => queryClient.invalidateQueries({ queryKey: studioKey }) });
  const apply = useMutation({ mutationFn: applyAIStudioProposal, onSuccess: () => queryClient.invalidateQueries({ queryKey: studioKey }) });
  if (schema.isLoading || proposals.isLoading) return <LoadingState message="Loading AI Studio..." />;
  if (schema.isError || proposals.isError) return <ErrorState message={schema.error?.message ?? proposals.error?.message ?? "AI Studio unavailable"} />;
  const toggle = (assetId: string) => { setSelected((current) => current.includes(assetId) ? current.filter((item) => item !== assetId) : [...current, assetId]); };
  return <div><PageHeader title="AI Studio" description="Generate coherent schema-bound synthetic-data proposals. Review the digest and records before applying only to explicit development/test sandbox assets." />
    <div className="grid gap-6 xl:grid-cols-[1fr_1.2fr]"><section className="rounded-xl border border-slate-200 bg-white p-5"><h2 className="flex items-center gap-2 font-semibold"><Bot size={18} />Proposal builder</h2><div className="mt-4 grid gap-4"><label className="text-sm">Scenario name<input className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2" value={scenarioName} onChange={(event) => { setScenarioName(event.target.value); }} /></label><div className="grid grid-cols-2 gap-3"><label className="text-sm">Records per asset<input type="number" min={1} max={500} className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2" value={recordsPerAsset} onChange={(event) => { setRecordsPerAsset(Number(event.target.value)); }} /></label><label className="text-sm">Deterministic seed<input type="number" min={0} className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2" value={seed} onChange={(event) => { setSeed(Number(event.target.value)); }} /></label></div><fieldset><legend className="text-sm font-medium">Assets</legend><div className="mt-2 max-h-80 space-y-2 overflow-auto rounded-lg border border-slate-200 p-3">{allAssets.map((asset) => <label key={asset.asset_id} className="flex items-start gap-2 text-sm"><input type="checkbox" checked={selected.includes(asset.asset_id)} onChange={() => { toggle(asset.asset_id); }} /><span><span className="flex flex-wrap items-center gap-2"><strong>{asset.asset_id}</strong><Badge>{asset.writable_in_sandbox ? "Apply allowed" : "Proposal only"}</Badge></span><span className="block text-xs text-slate-500">{asset.description}</span></span></label>)}</div></fieldset><button type="button" className={primaryButton} disabled={selected.length === 0 || generation.isPending} onClick={() => { generation.mutate({ assetIds: selected, recordsPerAsset, seed, mode: "DETERMINISTIC", scenarioName }); }}><Bot size={16} />Generate proposal</button>{generation.isError ? <p className="text-sm text-red-700">{generation.error.message}</p> : null}</div></section>
    <section className="rounded-xl border border-slate-200 bg-white p-5"><h2 className="font-semibold">Proposal history</h2>{(proposals.data?.length ?? 0) === 0 ? <div className="mt-4"><EmptyState title="No proposals" description="Select model assets and generate the first governed proposal." /></div> : <div className="mt-4 space-y-3">{proposals.data?.map((proposal) => <div key={proposal.id} className="rounded-lg border border-slate-200 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><strong>{proposal.scenarioName}</strong><p className="mt-1 text-xs text-slate-500">{new Date(proposal.createdAt).toLocaleString()} · {proposal.assetIds.length} assets · {proposal.recordsPerAsset} each</p></div><Badge>{proposal.status}</Badge></div><p className="mt-3 break-all font-mono text-[11px] text-slate-500">{proposal.digest}</p><div className="mt-3 flex flex-wrap gap-2">{Object.entries(proposal.recordCounts).map(([asset, count]) => <Badge key={asset}>{asset}: {count}</Badge>)}</div><div className="mt-4 flex flex-wrap gap-2"><button type="button" className={secondaryButton} onClick={() => { setPreviewId(proposal.id); }}>Preview records</button>{proposal.status !== "APPLIED" ? <button type="button" className={secondaryButton} disabled={apply.isPending} onClick={() => { if (window.confirm("Apply or retry this digest-bound proposal against permitted sandbox assets?")) apply.mutate(proposal); }}><CheckCircle2 size={16} />Apply permitted assets</button> : null}</div>{proposal.appliedAssets.length > 0 ? <p className="mt-3 text-xs text-emerald-700">Applied: {proposal.appliedAssets.join(", ")}</p> : null}{proposal.blockedAssets.length > 0 ? <p className="mt-3 text-xs text-amber-700">Proposal only by ownership rules: {proposal.blockedAssets.join(", ")}</p> : null}{Object.keys(proposal.applyErrors).length > 0 ? <p className="mt-3 text-xs text-red-700">Apply failures: {Object.entries(proposal.applyErrors).map(([asset, error]) => `${asset} (${error})`).join(", ")}</p> : null}</div>)}</div>}</section></div>
  {previewId ? <section className="mt-6 rounded-xl border border-slate-200 bg-white p-5"><div className="flex items-start justify-between gap-3"><div><h2 className="font-semibold">Proposal record preview</h2><p className="text-sm text-slate-500">Review generated records before applying. Secrets are never generated or displayed.</p></div><button type="button" className={secondaryButton} onClick={() => { setPreviewId(null); }}>Close</button></div>{preview.isLoading ? <div className="mt-4"><LoadingState message="Loading proposal records..." /></div> : preview.isError ? <div className="mt-4"><ErrorState message={preview.error.message} /></div> : <pre className="mt-4 max-h-[36rem] overflow-auto rounded-lg bg-slate-950 p-4 text-xs text-slate-100">{JSON.stringify(preview.data?.records ?? {}, null, 2)}</pre>}</section> : null}
  </div>;
}

export function GraphSyncPage() {
  const queryClient = useQueryClient();
  const runs = useQuery({ queryKey: syncKey, queryFn: ({ signal }) => listGraphSyncRuns(signal), refetchInterval: 10_000 });
  const sync = useMutation({ mutationFn: executeGraphSync, onSuccess: () => queryClient.invalidateQueries({ queryKey: syncKey }) });
  const schema = useMutation({ mutationFn: applyGraphSchema, onSuccess: () => queryClient.invalidateQueries({ queryKey: syncKey }) });
  if (runs.isLoading) return <LoadingState message="Loading graph sync evidence..." />;
  if (runs.isError) return <ErrorState message={runs.error.message} />;
  const run = (mode: "FULL" | "SOURCE_MONGODB" | "SQLSERVER") => { sync.mutate({ mode, maxRecordsPerAsset: 1000, applySchema: true }); };
  return <div><PageHeader title="Graph Sync" description="Rebuild the Neo4j projection from authoritative source MongoDB and SQL Server data using fixed parameterized mappings." /><div className="mb-6 flex flex-wrap gap-3"><button className={primaryButton} type="button" disabled={sync.isPending} onClick={() => { run("FULL"); }}><RefreshCw size={16} />Full sync</button><button className={secondaryButton} type="button" disabled={sync.isPending} onClick={() => { run("SOURCE_MONGODB"); }}>Mongo only</button><button className={secondaryButton} type="button" disabled={sync.isPending} onClick={() => { run("SQLSERVER"); }}>SQL only</button><button className={secondaryButton} type="button" disabled={schema.isPending} onClick={() => { schema.mutate(); }}><Network size={16} />Apply constraints</button></div>{sync.isError || schema.isError ? <ErrorState message={sync.error?.message ?? schema.error?.message ?? "Graph operation failed"} /> : null}<div className="space-y-3">{runs.data?.map((item) => <div key={item.id} className="rounded-xl border border-slate-200 bg-white p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-semibold">{item.mode}</h2><p className="text-xs text-slate-500">{new Date(item.startedAt).toLocaleString()} · {item.startedBy}</p></div><Badge>{item.status}</Badge></div><div className="mt-4 grid gap-3 sm:grid-cols-3"><div><p className="text-xs text-slate-500">Nodes</p><p className="text-xl font-semibold">{item.nodeWrites}</p></div><div><p className="text-xs text-slate-500">Relationships</p><p className="text-xl font-semibold">{item.relationshipWrites}</p></div><div><p className="text-xs text-slate-500">Constraints</p><p className="text-xl font-semibold">{item.constraintsApplied.length}</p></div></div><div className="mt-3 flex flex-wrap gap-2">{Object.entries(item.sourceCounts).map(([source, count]) => <Badge key={source}>{source}: {count}</Badge>)}</div>{item.errorCode ? <p className="mt-3 text-sm text-red-700">{item.errorCode}</p> : null}</div>)}</div></div>;
}

export function FeedbackLearningPage() {
  const query = useQuery({ queryKey: feedbackKey, queryFn: ({ signal }) => listFeedbackLearning(signal), refetchInterval: 15_000 });
  if (query.isLoading) return <LoadingState message="Loading feedback-learning evidence..." />;
  if (query.isError) return <ErrorState message={query.error.message} />;
  if ((query.data?.length ?? 0) === 0) return <EmptyState title="No feedback records" description="Complete a return flow to generate governed learning recommendations." />;
  return <div><PageHeader title="Feedback Learning" description="Review missing fields, support rework, graph gaps, source usage, bay outcomes, and recommendations. Recommendations never self-apply." /><div className="space-y-4">{query.data?.map((record) => <article key={record.id} className="rounded-xl border border-slate-200 bg-white p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-semibold">Session {record.sessionId}</h2><p className="text-xs text-slate-500">{record.finalOutcome} · {new Date(record.createdAt).toLocaleString()}</p></div><Badge>{record.reviewStatus}</Badge></div><div className="mt-5 grid gap-5 lg:grid-cols-2"><section><h3 className="flex items-center gap-2 text-sm font-semibold"><ShieldAlert size={16} />Observed signals</h3><ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-600">{[...record.missingFieldInsights, ...record.supportReworkInsights, ...record.graphSyncInsights, ...record.bayAssignmentInsights].map((item) => <li key={item}>{item}</li>)}</ul></section><section><h3 className="flex items-center gap-2 text-sm font-semibold"><Activity size={16} />Review recommendations</h3><ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-600">{record.recommendations.map((item) => <li key={item}>{item}</li>)}</ul></section></div><p className="mt-4 break-all font-mono text-[11px] text-slate-400">Evidence {record.evidenceDigest}</p></article>)}</div></div>;
}
