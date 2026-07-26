import { useQuery } from "@tanstack/react-query";

import { listSimulationAIMetrics } from "../../api/dependencySimulator";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { LoadingState } from "../../components/LoadingState";
import { PageHeader } from "../../components/PageHeader";
import { formatDate, KeyValue, Panel, ToneBadge } from "../operations/shared";
import { simulatorQueryKey } from "./queryKeys";

export function AiMetricsPage() {
  const query = useQuery({
    queryKey: [...simulatorQueryKey, "ai-metrics"],
    queryFn: ({ signal }) => listSimulationAIMetrics(signal),
    refetchInterval: 5_000,
  });
  const metrics = query.data ?? [];
  return (
    <div>
      <PageHeader
        title="Simulator AI metrics"
        description="Provider, model, latency, token, fallback, and retry evidence."
      />
      {query.isLoading && <LoadingState message="Loading AI metrics..." />}
      {query.isError && <ErrorState message={query.error.message} />}
      {!query.isLoading && metrics.length === 0 && (
        <EmptyState
          title="No AI usage evidence"
          description="Run an AI-assisted simulation to populate usage metrics."
        />
      )}
      <div className="grid gap-4">
        {metrics.map((metric) => (
          <Panel key={metric.id}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="font-semibold text-slate-900">
                  {metric.dependency} · {metric.operation}
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  {metric.provider} / {metric.model}
                </p>
              </div>
              <ToneBadge value={metric.status} />
            </div>
            <dl className="mt-4">
              <KeyValue label="Tokens" value={metric.totalTokens} />
              <KeyValue label="Latency" value={`${String(metric.latencyMs)} ms`} />
              <KeyValue label="Fallback" value={metric.fallbackUsed ? "Yes" : "No"} />
              <KeyValue label="Created" value={formatDate(metric.createdAt)} />
            </dl>
          </Panel>
        ))}
      </div>
    </div>
  );
}
