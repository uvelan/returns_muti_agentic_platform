import { CheckCircle2, Database } from "lucide-react";
import type { GraphEvidenceSummary } from "../../../../contracts/graphEvidence";

type Props = { readonly evidence: GraphEvidenceSummary };

export function GraphEvidenceStatusCard({ evidence }: Props) {
  const counts = [
    ["Customers", evidence.expected_customer_count],
    ["Accounts", evidence.expected_customer_account_count],
    ["HAS_ACCOUNT", evidence.expected_relationship_count],
  ] as const;

  return (
    <section className="overflow-hidden rounded-2xl border border-emerald-200 bg-white shadow-sm" aria-labelledby="validation-heading">
      <div className="flex flex-col gap-4 bg-emerald-50 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <span className="rounded-xl bg-emerald-600 p-2 text-white"><CheckCircle2 aria-hidden="true" size={22} /></span>
          <div>
            <p className="text-xs font-semibold uppercase tracking-widest text-emerald-700">Latest validation</p>
            <h2 id="validation-heading" className="text-xl font-bold text-slate-950">{evidence.evidence_classification}</h2>
          </div>
        </div>
        <time className="text-sm text-slate-600" dateTime={evidence.executed_at}>
          {new Date(evidence.executed_at).toLocaleString()}
        </time>
      </div>
      <div className="grid gap-px bg-slate-200 sm:grid-cols-3">
        {counts.map(([label, value]) => (
          <div key={label} className="bg-white p-5">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
            <p className="mt-1 text-3xl font-semibold text-slate-950">{value}</p>
          </div>
        ))}
      </div>
      <dl className="grid gap-3 border-t border-slate-200 p-5 text-sm sm:grid-cols-2">
        <div><dt className="text-slate-500">Sync run ID</dt><dd className="break-all font-mono text-xs text-slate-800">{evidence.sync_run_id}</dd></div>
        <div><dt className="text-slate-500">Source document</dt><dd className="flex items-center gap-2 font-medium text-slate-800"><Database size={14} aria-hidden="true" />{evidence.source_document_id}</dd></div>
      </dl>
    </section>
  );
}
