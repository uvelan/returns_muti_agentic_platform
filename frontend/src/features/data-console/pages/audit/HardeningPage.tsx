import { useHardeningSummary } from "../../../../api/consoleGovernanceQueries";
import { ErrorState } from "../../../../components/ErrorState";
import { LoadingState } from "../../../../components/LoadingState";
import { PageHeader } from "../../../../components/PageHeader";

const STATUS_CLASS: Record<string, string> = {
  PASS: "bg-green-100 text-green-800",
  WARN: "bg-amber-100 text-amber-800",
  FAIL: "bg-red-100 text-red-800",
  DEGRADED: "bg-amber-100 text-amber-800",
  NOT_VALIDATED: "bg-slate-100 text-slate-700",
};

export function HardeningPage() {
  const { data, isLoading, isError, error, refetch } = useHardeningSummary();
  if (isLoading) return <LoadingState message="Loading hardening evidence..." />;
  if (isError || !data) return <ErrorState title="Hardening evidence unavailable" message={error instanceof Error ? error.message : "No evidence returned"} />;

  return (
    <div className="p-6">
      <PageHeader title="Hardening Evidence" description="Runtime checks derived from catalog, configuration, resources, and worker heartbeats.">
        <button type="button" onClick={() => void refetch()} className="rounded border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50">Refresh</button>
      </PageHeader>
      <div className="mb-6 grid gap-4 md:grid-cols-3">
        <div className="rounded border border-gray-200 bg-white p-5"><p className="text-sm text-gray-500">Overall</p><span className={`mt-2 inline-flex rounded px-2 py-1 text-sm font-semibold ${STATUS_CLASS[data.status] ?? STATUS_CLASS.NOT_VALIDATED}`}>{data.status}</span></div>
        <div className="rounded border border-gray-200 bg-white p-5"><p className="text-sm text-gray-500">Evidence score</p><p className="mt-2 text-3xl font-semibold">{data.score === null ? "N/A" : `${String(data.score)}%`}</p></div>
        <div className="rounded border border-gray-200 bg-white p-5"><p className="text-sm text-gray-500">Vulnerabilities</p><p className="mt-2 text-3xl font-semibold">{data.vulnerabilities === null ? "Not validated" : data.vulnerabilities}</p></div>
      </div>
      <div className="space-y-3">
        {data.checks.map((check) => <article key={check.id} className="rounded border border-gray-200 bg-white p-4"><div className="flex items-center justify-between gap-3"><h2 className="font-semibold">{check.id}</h2><span className={`rounded px-2 py-1 text-xs font-semibold ${STATUS_CLASS[check.status]}`}>{check.status}</span></div><p className="mt-2 text-sm text-gray-700">{check.details}</p><code className="mt-2 block break-all rounded bg-gray-50 p-2 text-xs text-gray-600">{check.evidence}</code></article>)}
      </div>
      <p className="mt-4 text-xs text-gray-500">Evaluated {new Date(data.evaluatedAt).toLocaleString()}. Unvalidated checks remain explicit; no vulnerability count is fabricated.</p>
    </div>
  );
}
