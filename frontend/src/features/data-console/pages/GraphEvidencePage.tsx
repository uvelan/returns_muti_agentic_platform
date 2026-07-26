import { useState, type SyntheticEvent } from "react";
import { AlertCircle, RefreshCw, Search } from "lucide-react";
import { APIError } from "../../../api/client";
import {
  getGraphEvidenceByDocumentId,
  getGraphEvidenceByReportDigest,
  getGraphEvidenceBySyncRunId,
} from "../../../api/graphEvidence";
import { useGraphEvidenceList, useLatestGraphEvidence } from "../../../api/graphEvidenceQueries";
import type { GraphEvidenceSummary } from "../../../contracts/graphEvidence";
import { GraphEvidenceInspector } from "../components/graph-evidence/GraphEvidenceInspector";
import { GraphEvidenceStatusCard } from "../components/graph-evidence/GraphEvidenceStatusCard";
import { GraphEvidenceTable } from "../components/graph-evidence/GraphEvidenceTable";

type LookupKind = "document" | "sync-run" | "report";

function errorDetail(error: unknown): { message: string; correlationId?: string } {
  if (error instanceof APIError) return { message: error.message, correlationId: error.correlationId };
  return { message: "Graph evidence could not be loaded." };
}

export function GraphEvidencePage() {
  const [cursor, setCursor] = useState<string>();
  const [cursorHistory, setCursorHistory] = useState<readonly (string | undefined)[]>([]);
  const [selected, setSelected] = useState<GraphEvidenceSummary | null>(null);
  const [lookupKind, setLookupKind] = useState<LookupKind>("document");
  const [lookupValue, setLookupValue] = useState("");
  const [lookupError, setLookupError] = useState<string>();
  const [lookupRequestId, setLookupRequestId] = useState<string>();
  const [isLookingUp, setIsLookingUp] = useState(false);
  const latest = useLatestGraphEvidence();
  const history = useGraphEvidenceList(cursor);

  async function submitLookup(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = lookupValue.trim();
    if (!value) { setLookupError("Enter an exact immutable identifier."); return; }
    setIsLookingUp(true); setLookupError(undefined); setLookupRequestId(undefined);
    try {
      const response = lookupKind === "document"
        ? await getGraphEvidenceByDocumentId(value)
        : lookupKind === "sync-run"
          ? await getGraphEvidenceBySyncRunId(value)
          : await getGraphEvidenceByReportDigest(value);
      setSelected(response.data);
      setLookupRequestId(response.meta.request_id);
    } catch (error) {
      const detail = errorDetail(error);
      setLookupError(detail.message); setLookupRequestId(detail.correlationId);
    } finally { setIsLookingUp(false); }
  }

  const hardError = latest.isError && history.isError;
  const primaryError = errorDetail(latest.error ?? history.error);

  if (hardError) {
    return (
      <section role="alert" className="flex min-h-[50vh] flex-col items-center justify-center gap-4 rounded-2xl border border-red-200 bg-red-50 p-6 text-center">
        <AlertCircle size={40} className="text-red-600" aria-hidden="true" />
        <h1 className="text-xl font-semibold text-red-950">Graph evidence is unavailable</h1>
        <p className="max-w-lg text-sm text-red-800">{primaryError.message}</p>
        {primaryError.correlationId ? <p className="font-mono text-xs text-red-700">Request ID: {primaryError.correlationId}</p> : null}
        <button type="button" onClick={() => { void latest.refetch(); void history.refetch(); }} className="inline-flex items-center gap-2 rounded-md bg-white px-4 py-2 text-sm font-medium text-red-800 ring-1 ring-red-300"><RefreshCw size={16} aria-hidden="true" />Retry</button>
      </section>
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-600">Data Console</p><h1 className="mt-1 text-3xl font-bold tracking-tight text-slate-950">Customer graph evidence</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">Read-only immutable evidence for completed Customer graph synchronization and validation. No graph mutation is exposed.</p></div>
        <button type="button" onClick={() => { void latest.refetch(); void history.refetch(); }} disabled={latest.isFetching || history.isFetching} className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium shadow-sm disabled:opacity-50"><RefreshCw size={16} className={(latest.isFetching || history.isFetching) ? "animate-spin motion-reduce:animate-none" : ""} aria-hidden="true" />Refresh</button>
      </header>

      {latest.isPending ? <div role="status" className="h-56 animate-pulse rounded-2xl bg-slate-200"><span className="sr-only">Loading latest graph validation</span></div> : latest.data?.data ? <GraphEvidenceStatusCard evidence={latest.data.data} /> : <div role="status" className="rounded-2xl border border-slate-200 bg-white p-8 text-center text-slate-500">No latest validation evidence is available.</div>}
      {latest.data ? <p className="text-right font-mono text-[11px] text-slate-500">Latest request ID: {latest.data.meta.request_id}</p> : null}

      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" aria-labelledby="lookup-heading">
        <h2 id="lookup-heading" className="text-lg font-semibold text-slate-950">Exact evidence lookup</h2>
        <form onSubmit={(event) => { void submitLookup(event); }} className="mt-4 grid gap-3 md:grid-cols-[12rem_1fr_auto]">
          <label><span className="sr-only">Identifier type</span><select value={lookupKind} onChange={(event) => { setLookupKind(event.target.value as LookupKind); }} className="h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm"><option value="document">Document ID</option><option value="sync-run">Sync-run ID</option><option value="report">Report digest</option></select></label>
          <label><span className="sr-only">Exact identifier</span><input value={lookupValue} onChange={(event) => { setLookupValue(event.target.value); }} placeholder="Enter exact immutable identifier" className="h-10 w-full rounded-md border border-slate-300 px-3 font-mono text-sm" /></label>
          <button type="submit" disabled={isLookingUp} className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-indigo-600 px-4 text-sm font-medium text-white disabled:opacity-50"><Search size={16} aria-hidden="true" />Look up</button>
        </form>
        {lookupError ? <p role="alert" className="mt-3 text-sm text-red-700">{lookupError}</p> : null}
        {lookupRequestId ? <p className="mt-2 font-mono text-[11px] text-slate-500">Lookup request ID: {lookupRequestId}</p> : null}
      </section>

      {history.isPending ? <div role="status" className="h-64 animate-pulse rounded-2xl bg-slate-200"><span className="sr-only">Loading evidence history</span></div> : history.isError ? <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{errorDetail(history.error).message}<button type="button" onClick={() => { void history.refetch(); }} className="ml-3 underline">Retry</button></div> : <><GraphEvidenceTable items={history.data.data} canPrevious={cursorHistory.length > 0} canNext={history.data.page.has_more} onInspect={setSelected} onPrevious={() => { const prior = cursorHistory.at(-1); setCursorHistory((items) => items.slice(0, -1)); setCursor(prior); }} onNext={() => { const next = history.data.page.next_cursor; if (next) { setCursorHistory((items) => [...items, cursor]); setCursor(next); } }} /><p className="text-right font-mono text-[11px] text-slate-500">History request ID: {history.data.meta.request_id}</p></>}

      {selected ? <GraphEvidenceInspector evidence={selected} onClose={() => { setSelected(null); }} /> : null}
    </div>
  );
}
