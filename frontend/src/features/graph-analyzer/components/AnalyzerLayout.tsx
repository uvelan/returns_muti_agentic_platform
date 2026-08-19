import { Bot, Check, CircleAlert, Database, Network, RefreshCw, ShieldCheck } from "lucide-react";
import { Link, useLocation } from "wouter";
import type { ReactNode } from "react";
import { useAnalyzerBootstrap } from "../analyzerQueries";
import { useGraphAnalyzer } from "../GraphAnalyzerContext";
import { AgentDrawer } from "./AgentDrawer";

const tabs = [
  { path: "/graph-schema", label: "Graph Analyzer", icon: Database },
  { path: "/graph-schema/schema", label: "Schema", icon: Network },
  { path: "/graph-schema/sync", label: "Sync", icon: RefreshCw },
] as const;

export function AnalyzerLayout({ children }: { readonly children: ReactNode }) {
  const [location] = useLocation();
  const bootstrap = useAnalyzerBootstrap();
  const ui = useGraphAnalyzer();
  const data = bootstrap.data;
  const connected = data?.sources.filter((source) => source.status === "CONNECTED").length ?? 0;
  const validation = data?.validation?.status ?? "NOT_RUN";

  return (
    <section className="-m-4 min-h-[calc(100vh-2rem)] overflow-hidden rounded-2xl bg-[#07120f] text-slate-100 shadow-2xl sm:-m-6 lg:-m-8" aria-label="Graph Schema Analyzer">
      <header className="border-b border-emerald-950 bg-[#091814]/95 px-5 py-4 backdrop-blur-xl lg:px-7">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="grid size-10 place-items-center rounded-xl border border-emerald-700/60 bg-emerald-950 text-emerald-300 shadow-[0_0_25px_rgba(16,185,129,.12)]"><Network size={20} /></span>
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-emerald-500">Graph intelligence</p>
              <h1 className="text-lg font-semibold tracking-tight text-white">Schema Analyzer Agent</h1>
            </div>
          </div>
          <button type="button" onClick={() => { ui.openChat({ workspace: location.includes("/schema") ? "SCHEMA" : location.includes("/sync") ? "SYNC" : "ANALYZER", selectedSourceId: ui.selectedSourceId ?? undefined, selectedObjectId: ui.selectedObjectId ?? undefined, selectedScope: [...ui.selectedObjectIds] }); }} className="inline-flex items-center gap-2 rounded-lg border border-emerald-700/60 bg-emerald-950/70 px-3 py-2 text-sm font-medium text-emerald-100 transition hover:border-emerald-500 hover:bg-emerald-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400">
            <Bot size={16} /> Ask Analyzer
          </button>
        </div>
        <nav className="mt-5 flex gap-1" aria-label="Graph Schema Analyzer workflows">
          {tabs.map((tab) => {
            const active = tab.path === "/graph-schema" ? location === tab.path : location.startsWith(tab.path);
            return <Link key={tab.path} href={tab.path} className={`inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition ${active ? "bg-emerald-400 text-emerald-950 shadow-lg shadow-emerald-950" : "text-slate-400 hover:bg-white/5 hover:text-white"}`}><tab.icon size={15} />{tab.label}</Link>;
          })}
        </nav>
      </header>

      <div className="border-b border-emerald-950/80 bg-[#0a1714] px-5 py-3 lg:px-7">
        <div className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-5">
          <Status label="Sources" value={`${String(connected)}/${String(data?.sources.length ?? 0)} connected`} state={connected > 0 ? "complete" : "attention"} />
          <Status label="Selection" value={`${String(ui.selectedObjectIds.size)} objects`} state={ui.selectedObjectIds.size > 0 ? "complete" : "current"} />
          <Status label="Analysis" value={data?.activeAnalysis?.stage.replaceAll("_", " ").toLowerCase() ?? "not started"} state={data?.proposedSchema !== null && data?.proposedSchema !== undefined ? "complete" : "current"} />
          <Status label="Schema" value={data?.proposedSchema?.status.toLowerCase().replaceAll("_", " ") ?? "unavailable"} state={data?.proposedSchema?.status === "FINALIZED" ? "complete" : validation === "BLOCKING" ? "attention" : "current"} />
          <Status label="Last sync" value={data?.syncHistory.at(0)?.status.toLowerCase().replaceAll("_", " ") ?? "never"} state={data?.syncHistory.at(0)?.status === "COMPLETED" ? "complete" : "current"} />
        </div>
      </div>

      {bootstrap.isError ? <div role="alert" className="mx-5 mt-5 flex items-center justify-between gap-4 rounded-xl border border-red-900/80 bg-red-950/50 px-4 py-3 text-sm text-red-100"><span><CircleAlert className="mr-2 inline" size={16} />Analyzer state could not be loaded. {bootstrap.error.message}</span><button className="rounded-md border border-red-700 px-3 py-1.5" type="button" onClick={() => { void bootstrap.refetch(); }}>Retry</button></div> : null}
      <div className="p-5 lg:p-7">{children}</div>
      <AgentDrawer />
    </section>
  );
}

function Status({ label, value, state }: { readonly label: string; readonly value: string; readonly state: "complete" | "current" | "attention" }) {
  const Icon = state === "complete" ? Check : state === "attention" ? CircleAlert : ShieldCheck;
  return <div className="flex min-w-0 items-center gap-2"><Icon size={14} className={state === "complete" ? "text-emerald-400" : state === "attention" ? "text-amber-400" : "text-slate-500"} /><span className="min-w-0"><span className="block text-[10px] uppercase tracking-wider text-slate-500">{label}</span><span className="block truncate font-medium capitalize text-slate-300">{value}</span></span></div>;
}
