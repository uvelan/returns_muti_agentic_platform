import { useQuery } from "@tanstack/react-query";
import { useParams } from "wouter";

import { getSimulationOperation } from "../../api/dependencySimulator";
import { ErrorState } from "../../components/ErrorState";
import { LoadingState } from "../../components/LoadingState";
import { PageHeader } from "../../components/PageHeader";
import {
  formatDate,
  JsonBlock,
  KeyValue,
  Panel,
  ToneBadge,
} from "../operations/shared";
import { simulatorQueryKey } from "./queryKeys";

export function OperationDetailPage() {
  const { operationId } = useParams<{ operationId: string }>();
  const query = useQuery({
    queryKey: [...simulatorQueryKey, "operation", operationId],
    queryFn: ({ signal }) => getSimulationOperation(operationId, signal),
    enabled: operationId.length > 0,
  });
  if (query.isLoading) return <LoadingState message="Loading simulation operation..." />;
  if (query.isError || !query.data) {
    return <ErrorState message={query.error?.message ?? "Operation not found"} />;
  }
  const operation = query.data;
  return (
    <div>
      <PageHeader title={operation.operation} description={operation.narrative.message} />
      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Operation">
          <dl>
            <KeyValue label="Status" value={<ToneBadge value={operation.status} />} />
            <KeyValue label="Dependency" value={operation.dependency} />
            <KeyValue label="Session" value={operation.sessionId} />
            <KeyValue label="Reference" value={operation.externalReference} />
            <KeyValue label="Updated" value={formatDate(operation.updatedAt)} />
          </dl>
        </Panel>
        <Panel title="Deterministic response">
          <JsonBlock value={operation.responsePayload} />
        </Panel>
      </div>
    </div>
  );
}
