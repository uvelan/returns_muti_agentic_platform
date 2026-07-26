import type {
  DependencyKind,
  DependencySimulationSummary,
  SimulationAIUsageMetric,
  SimulationOperation,
} from "../contracts/dependencySimulator";
import { apiClient } from "./client";

function requireData<T>(value: T | null): T {
  if (value === null) throw new Error("The dependency simulator returned no data.");
  return value;
}

export async function getDependencySimulationSummary(
  signal?: AbortSignal,
): Promise<DependencySimulationSummary> {
  const response = await apiClient<DependencySimulationSummary>(
    "/api/v1/dependency-simulator/summary",
    { signal },
  );
  return requireData(response.data);
}
export async function listSimulationOperations(
  dependency?: DependencyKind,
  signal?: AbortSignal,
): Promise<SimulationOperation[]> {
  const query = dependency
    ? `?dependency=${encodeURIComponent(dependency)}`
    : "";
  const response = await apiClient<SimulationOperation[]>(
    `/api/v1/dependency-simulator/operations${query}`,
    { signal },
  );
  return requireData(response.data);
}

export async function getSimulationOperation(
  operationId: string,
  signal?: AbortSignal,
): Promise<SimulationOperation> {
  const response = await apiClient<SimulationOperation>(
    `/api/v1/dependency-simulator/operations/${encodeURIComponent(operationId)}`,
    { signal },
  );
  return requireData(response.data);
}

export async function listSimulationAIMetrics(
  signal?: AbortSignal,
): Promise<SimulationAIUsageMetric[]> {
  const response = await apiClient<SimulationAIUsageMetric[]>(
    "/api/v1/dependency-simulator/ai-metrics",
    { signal },
  );
  return requireData(response.data);
}
