import { ChevronLeft, ChevronRight, Eye } from "lucide-react";
import type { GraphEvidenceSummary } from "../../../../contracts/graphEvidence";

type Props = {
  readonly items: readonly GraphEvidenceSummary[];
  readonly canPrevious: boolean;
  readonly canNext: boolean;
  readonly onPrevious: () => void;
  readonly onNext: () => void;
  readonly onInspect: (evidence: GraphEvidenceSummary) => void;
};

export function GraphEvidenceTable({ items, canPrevious, canNext, onPrevious, onNext, onInspect }: Props) {
  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm" aria-labelledby="history-heading">
      <div className="border-b border-slate-200 p-5">
        <h2 id="history-heading" className="text-lg font-semibold text-slate-950">Immutable evidence history</h2>
        <p className="mt-1 text-sm text-slate-500">Newest first — bounded seek pagination</p>
      </div>
      {items.length === 0 ? (
        <p className="p-8 text-center text-sm text-slate-500">No Customer graph evidence has been recorded.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Executed</th><th className="px-5 py-3">Sync run</th><th className="px-5 py-3">Counts</th><th className="px-5 py-3"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((item) => (
                <tr key={item.document_id} className="hover:bg-slate-50">
                  <td className="whitespace-nowrap px-5 py-4"><time dateTime={item.executed_at}>{new Date(item.executed_at).toLocaleString()}</time></td>
                  <td className="max-w-56 truncate px-5 py-4 font-mono text-xs" title={item.sync_run_id}>{item.sync_run_id}</td>
                  <td className="whitespace-nowrap px-5 py-4 text-slate-600">{item.expected_customer_count} / {item.expected_customer_account_count} / {item.expected_relationship_count}</td>
                  <td className="px-5 py-4 text-right"><button type="button" onClick={() => { onInspect(item); }} className="inline-flex items-center gap-1 rounded-md px-3 py-2 font-medium text-indigo-700 hover:bg-indigo-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"><Eye size={15} aria-hidden="true" />Inspect</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="flex justify-between border-t border-slate-200 p-4">
        <button type="button" disabled={!canPrevious} onClick={onPrevious} className="inline-flex items-center gap-1 rounded-md border border-slate-300 px-3 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-40"><ChevronLeft size={16} aria-hidden="true" />Previous</button>
        <button type="button" disabled={!canNext} onClick={onNext} className="inline-flex items-center gap-1 rounded-md border border-slate-300 px-3 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-40">Next<ChevronRight size={16} aria-hidden="true" /></button>
      </div>
    </section>
  );
}
