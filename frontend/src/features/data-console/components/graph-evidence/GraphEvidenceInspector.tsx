import { LockKeyhole, X } from "lucide-react";
import { APIError } from "../../../../api/client";
import { useFullGraphEvidence } from "../../../../api/graphEvidenceQueries";
import type { GraphEvidenceSummary } from "../../../../contracts/graphEvidence";

type Props = { readonly evidence: GraphEvidenceSummary; readonly onClose: () => void };

const summaryDigests = [
  ["Report", "report_digest"], ["Document", "document_digest"], ["Source", "source_hash"],
  ["Configuration", "configuration_digest"], ["Execution plan", "execution_plan_digest"], ["Command batch", "command_batch_digest"],
] as const;

export function GraphEvidenceInspector({ evidence, onClose }: Props) {
  const full = useFullGraphEvidence(evidence.document_id, false);
  const isForbidden = full.error instanceof APIError && full.error.status === 403;
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" aria-labelledby="inspector-heading">
      <div className="flex items-start justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-widest text-indigo-600">Evidence summary</p><h2 id="inspector-heading" className="mt-1 text-lg font-semibold text-slate-950">Inspection</h2></div><button type="button" onClick={onClose} aria-label="Close evidence inspector" className="rounded-md p-2 text-slate-500 hover:bg-slate-100"><X size={18} aria-hidden="true" /></button></div>
      <dl className="mt-5 grid gap-4 sm:grid-cols-2">
        <div><dt className="text-xs text-slate-500">Document ID</dt><dd className="break-all font-mono text-xs">{evidence.document_id}</dd></div>
        <div><dt className="text-xs text-slate-500">Idempotent</dt><dd className="font-medium">{evidence.idempotent ? "Yes" : "No"}</dd></div>
        {summaryDigests.map(([label, key]) => <div key={key}><dt className="text-xs text-slate-500">{label} digest</dt><dd className="break-all font-mono text-xs">{evidence[key]}</dd></div>)}
      </dl>
      <div className="mt-6 border-t border-slate-200 pt-5">
        <button type="button" onClick={() => { void full.refetch(); }} disabled={full.isFetching} className="inline-flex items-center gap-2 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"><LockKeyhole size={16} aria-hidden="true" />{full.isFetching ? "Loading full evidence..." : "Inspect admin evidence"}</button>
        {isForbidden ? <p role="status" className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">Full evidence requires the console_admin role. The safe summary remains available.</p> : null}
        {full.isError && !isForbidden ? <p role="alert" className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-800">{full.error instanceof Error ? full.error.message : "Full evidence could not be loaded."}</p> : null}
        {full.data ? (
          <div className="mt-4 space-y-3"><h3 className="font-semibold">Validated full report payload</h3><pre className="max-h-96 overflow-auto rounded-lg bg-slate-950 p-4 text-xs text-slate-100">{JSON.stringify(full.data.data.report_payload, null, 2)}</pre></div>
        ) : null}
      </div>
    </section>
  );
}
