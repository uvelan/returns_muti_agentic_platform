import { useQuery } from "@tanstack/react-query";
import { Link } from "wouter";

import { listSimulationOperations } from "../../api/dependencySimulator";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { LoadingState } from "../../components/LoadingState";
import { PageHeader } from "../../components/PageHeader";
import type { DependencyKind } from "../../contracts/dependencySimulator";
import {
  formatDate,
  KeyValue,
  Panel,
  ToneBadge,
} from "../operations/shared";

import { simulatorQueryKey } from "./queryKeys";

export function DependencyOperationsPage({
  dependency,
  description,
}: {
  dependency: DependencyKind;
  description: string;
}) {
  const query = useQuery({
    queryKey: [...simulatorQueryKey, "operations", dependency],
    queryFn: ({ signal }) => listSimulationOperations(dependency, signal),
    refetchInterval: 5_000,
  });
  const operations = query.data ?? [];

  return (
    <div>
      <PageHeader title={`${dependency} simulator`} description={description} />
      {query.isLoading && <LoadingState message={`Loading ${dependency} operations...`} />}
      {query.isError && <ErrorState message={query.error.message} />}
      {!query.isLoading && operations.length === 0 && (
        <EmptyState
          title={`No ${dependency} operations`}
          description="Run a simulated return flow to populate deterministic operation evidence."
        />
      )}
      <div className="grid gap-4">
        {operations.map((operation) => (
          <Link
            key={operation.id}
            href={`/system/dependency-simulator/operations/${operation.id}`}
            className="block rounded-xl focus:outline-none focus:ring-2 focus:ring-slate-500"
          >
            <Panel className="transition hover:border-slate-400">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="font-semibold text-slate-900">{operation.operation}</h2>
                  <p className="mt-1 text-sm text-slate-500">{operation.sessionId}</p>
                </div>
                <ToneBadge value={operation.status} />
              </div>
              <dl className="mt-4">
                <KeyValue label="State" value={operation.simulatedState} />
                <KeyValue label="Reference" value={operation.externalReference} />
                <KeyValue label="Updated" value={formatDate(operation.updatedAt)} />
              </dl>
              <p className="mt-3 text-sm text-slate-600">{operation.narrative.summary}</p>
            </Panel>
          </Link>
        ))}
      </div>
    </div>
  );
}
