import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useGovernanceSummary } from "../../../../api/consoleGovernanceQueries";
import { ErrorState } from "../../../../components/ErrorState";
import { LoadingState } from "../../../../components/LoadingState";
import { PageHeader } from "../../../../components/PageHeader";

export function GovernancePage() {
  const { data, isLoading, isError, error, refetch } = useGovernanceSummary();
  if (isLoading) return <LoadingState message="Evaluating governance catalog..." />;
  if (isError || !data) return <ErrorState title="Governance evaluation failed" message={error instanceof Error ? error.message : "No evidence returned"} />;

  const valid = data.status === "VALIDATED";
  return (
    <div className="p-6">
      <PageHeader title="Governance" description="Evidence-backed ownership and operation-boundary validation.">
        <button type="button" onClick={() => void refetch()} className="rounded border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50">Re-evaluate</button>
      </PageHeader>
      <div className={`mb-6 flex items-start gap-3 rounded border p-4 ${valid ? "border-green-200 bg-green-50 text-green-900" : "border-amber-200 bg-amber-50 text-amber-900"}`}>
        {valid ? <CheckCircle2 /> : <AlertTriangle />}
        <div><p className="font-semibold">{data.status}</p><p className="text-sm">Evaluated {new Date(data.evaluatedAt).toLocaleString()}</p></div>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        {[
          ["Governed assets", data.assetCount],
          ["Authoritative", data.authoritativeAssetCount],
          ["Sampling enabled", data.sampledAssetCount],
        ].map(([label, value]) => <div key={String(label)} className="rounded border border-gray-200 bg-white p-5"><p className="text-sm text-gray-500">{label}</p><p className="mt-2 text-3xl font-semibold">{value}</p></div>)}
      </div>
      <section className="mt-6 rounded border border-gray-200 bg-white p-5">
        <h2 className="font-semibold">Catalog evidence</h2>
        <dl className="mt-4 grid gap-3 text-sm md:grid-cols-2">
          <div><dt className="text-gray-500">Version</dt><dd className="font-mono">{data.catalogVersion}</dd></div>
          <div><dt className="text-gray-500">SHA-256</dt><dd className="break-all font-mono text-xs">{data.catalogDigest}</dd></div>
          <div className="md:col-span-2"><dt className="text-gray-500">Path</dt><dd className="font-mono text-xs">{data.catalogPath}</dd></div>
        </dl>
      </section>
      <section className="mt-6 rounded border border-gray-200 bg-white p-5">
        <h2 className="font-semibold">Ownership distribution</h2>
        <div className="mt-3 flex flex-wrap gap-2">{Object.entries(data.ownershipCounts).map(([ownership, count]) => <span key={ownership} className="rounded-full bg-slate-100 px-3 py-1 text-sm">{ownership}: {count}</span>)}</div>
      </section>
      {data.violations.length > 0 && <section className="mt-6 rounded border border-red-200 bg-red-50 p-5 text-red-900"><h2 className="font-semibold">Violations</h2><ul className="mt-2 list-disc pl-5 text-sm">{data.violations.map((violation) => <li key={violation}>{violation}</li>)}</ul></section>}
    </div>
  );
}
