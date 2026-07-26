import { useQuery } from "@tanstack/react-query";
import { Link } from "wouter";

import { getDependencySimulationSummary } from "../../api/dependencySimulator";
import { ErrorState } from "../../components/ErrorState";
import { LoadingState } from "../../components/LoadingState";
import { PageHeader } from "../../components/PageHeader";
import { Metric, Panel, ToneBadge } from "../operations/shared";
import { simulatorQueryKey } from "./queryKeys";

export function OverviewPage() {
  const query = useQuery({
    queryKey: [...simulatorQueryKey, "summary"],
    queryFn: ({ signal }) => getDependencySimulationSummary(signal),
    refetchInterval: 5_000,
  });
  if (query.isLoading) return <LoadingState message="Loading simulator status..." />;
  if (query.isError || !query.data) {
    return <ErrorState message={query.error?.message ?? "Simulator status unavailable"} />;
  }
  const summary = query.data;
  return (
    <div>
      <PageHeader title="Dependency Simulator" description={summary.banner} />
      <div className="mb-6 grid gap-4 sm:grid-cols-4">
        <Metric label="Status" value={<ToneBadge value={summary.enabled ? "ENABLED" : "DISABLED"} />} />
        <Metric label="Environment" value={summary.environment} />
        <Metric label="AI requests" value={summary.ai.requestCount} />
        <Metric label="Fallbacks" value={summary.ai.fallbackCount} />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {Object.entries(summary.modes).map(([dependency, mode]) => (
          <Link
            key={dependency}
            href={`/system/dependency-simulator/${dependency.toLowerCase()}`}
          >
            <Panel className="transition hover:border-slate-400">
              <h2 className="font-semibold text-slate-900">{dependency}</h2>
              <p className="mt-2 text-sm text-slate-600">Mode: {mode}</p>
              <p className="mt-1 text-sm text-slate-500">
                Operations: {summary.operationCounts[dependency] ?? 0}
              </p>
            </Panel>
          </Link>
        ))}
      </div>
    </div>
  );
}
