/* eslint-disable @typescript-eslint/prefer-nullish-coalescing, @typescript-eslint/restrict-template-expressions */
import { apiClient } from "./client";
import type {
  AIGatewaySettings,
  AIDecision,
  AITrace,
  OperationalDependency,
  ReturnSession,
  SeedStatus,
  SupportCase,
  TimelineEvent,
} from "../contracts/operations";

function requireData<T>(value: T | null): T {
  if (value === null) throw new Error("The API returned no data.");
  return value;
}

export type OperationalRecord = Record<string, unknown>;

function jsonInit(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

export async function listReturns(status?: string, signal?: AbortSignal): Promise<readonly ReturnSession[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return requireData((await apiClient<ReturnSession[]>(`/api/v1/returns${query}`, { signal })).data);
}

export async function getReturn(sessionId: string, signal?: AbortSignal): Promise<ReturnSession> {
  return requireData((await apiClient<ReturnSession>(`/api/v1/returns/${encodeURIComponent(sessionId)}`, { signal })).data);
}

export async function cancelReturn(session: ReturnSession): Promise<ReturnSession> {
  return requireData((await apiClient<ReturnSession>(
    `/api/v1/returns/${encodeURIComponent(session.id)}/cancel?expectedVersion=${session.version}`,
    jsonInit("POST"),
  )).data);
}

export async function listEvents(sessionId: string, signal?: AbortSignal): Promise<readonly TimelineEvent[]> {
  return requireData((await apiClient<TimelineEvent[]>(
    `/api/v1/returns/${encodeURIComponent(sessionId)}/events`,
    { signal },
  )).data);
}

export async function listSupportCases(status?: string, signal?: AbortSignal): Promise<readonly SupportCase[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return requireData((await apiClient<SupportCase[]>(`/api/v1/support/cases${query}`, { signal })).data);
}

export async function getSupportCase(caseId: string, signal?: AbortSignal): Promise<SupportCase> {
  return requireData((await apiClient<SupportCase>(
    `/api/v1/support/cases/${encodeURIComponent(caseId)}`,
    { signal },
  )).data);
}

export async function operateSupportCase(payload: {
  caseId: string;
  operation: "ASSIGN" | "APPROVE" | "REJECT" | "RETRY" | "CANCEL" | "RESUME";
  reason: string;
  expectedVersion: number;
  assignee?: string;
}): Promise<SupportCase> {
  return requireData((await apiClient<SupportCase>(
    `/api/v1/support/cases/${encodeURIComponent(payload.caseId)}/operations`,
    jsonInit("POST", {
      operation: payload.operation,
      reason: payload.reason,
      expectedVersion: payload.expectedVersion,
      assignee: payload.assignee || null,
    }),
  )).data);
}

export async function listAITraces(status?: string, signal?: AbortSignal): Promise<readonly AITrace[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return requireData((await apiClient<AITrace[]>(`/api/v1/ai-gateway/requests${query}`, { signal })).data);
}

export async function getAITrace(traceId: string, signal?: AbortSignal): Promise<AITrace> {
  return requireData((await apiClient<AITrace>(
    `/api/v1/ai-gateway/requests/${encodeURIComponent(traceId)}`,
    { signal },
  )).data);
}

export async function simulateAI(payload: {
  customerReference: string;
  orderReferences: string[];
  reasonCode: string;
  requestedDecision: AIDecision | null;
}): Promise<AITrace> {
  return requireData((await apiClient<AITrace>("/api/v1/ai-gateway/simulator", jsonInit("POST", {
    ...payload,
    persist: true,
  }))).data);
}

export async function replayAI(payload: {
  traceId: string;
  provider?: string;
  editedSystemPrompt?: string;
}): Promise<AITrace> {
  return requireData((await apiClient<AITrace>(
    `/api/v1/ai-gateway/requests/${encodeURIComponent(payload.traceId)}/replay`,
    jsonInit("POST", {
      provider: payload.provider || null,
      editedSystemPrompt: payload.editedSystemPrompt || null,
    }),
  )).data);
}

export async function compareAI(payload: { traceId: string; providers: string[] }): Promise<readonly AITrace[]> {
  return requireData((await apiClient<AITrace[]>(
    `/api/v1/ai-gateway/requests/${encodeURIComponent(payload.traceId)}/compare`,
    jsonInit("POST", { providers: payload.providers }),
  )).data);
}

export async function interceptAI(payload: {
  traceId: string;
  action: "APPROVE" | "REJECT" | "REVIEW_REQUIRED" | "EDIT_AND_DISPATCH" | "CANCEL";
  reason: string;
  expectedVersion: number;
  editedSystemPrompt?: string;
}): Promise<AITrace> {
  return requireData((await apiClient<AITrace>(
    `/api/v1/ai-gateway/requests/${encodeURIComponent(payload.traceId)}/intercept`,
    jsonInit("POST", {
      action: payload.action,
      reason: payload.reason,
      expectedVersion: payload.expectedVersion,
      editedSystemPrompt: payload.editedSystemPrompt || null,
    }),
  )).data);
}

export async function getAISettings(signal?: AbortSignal): Promise<AIGatewaySettings> {
  return requireData((await apiClient<AIGatewaySettings>("/api/v1/ai-gateway/settings", { signal })).data);
}

export async function updateAISettings(payload: {
  interceptMode: boolean;
  providerOrder: string[];
  expectedVersion: number;
}): Promise<AIGatewaySettings> {
  return requireData((await apiClient<AIGatewaySettings>(
    "/api/v1/ai-gateway/settings",
    jsonInit("PUT", payload),
  )).data);
}

export async function getSeedStatus(signal?: AbortSignal): Promise<SeedStatus> {
  return requireData((await apiClient<SeedStatus>("/api/v1/seed-data", { signal })).data);
}

export async function applySeed(reset = false): Promise<SeedStatus> {
  return requireData((await apiClient<SeedStatus>(
    reset ? "/api/v1/seed-data/reset" : "/api/v1/seed-data/apply",
    jsonInit("POST"),
  )).data);
}

export async function listOperationalDependencies(signal?: AbortSignal): Promise<readonly OperationalDependency[]> {
  return requireData((await apiClient<OperationalDependency[]>("/api/v1/system/dependencies", { signal })).data);
}

export async function getOperationalDependency(id: string, signal?: AbortSignal): Promise<OperationalDependency> {
  return requireData((await apiClient<OperationalDependency>(
    `/api/v1/system/dependencies/${encodeURIComponent(id)}`,
    { signal },
  )).data);
}

export async function getProductionArtifacts(
  sessionId: string,
  signal?: AbortSignal,
): Promise<OperationalRecord> {
  return requireData((await apiClient<OperationalRecord>(
    `/api/v1/returns/${encodeURIComponent(sessionId)}/production-artifacts`,
    { signal },
  )).data);
}

export async function getReturnAgentConfiguration(signal?: AbortSignal): Promise<OperationalRecord> {
  return requireData((await apiClient<OperationalRecord>(
    "/api/v1/return-agents/configuration",
    { signal },
  )).data);
}

export async function listReturnSupportWorkItems(
  status?: string,
  signal?: AbortSignal,
): Promise<readonly OperationalRecord[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return requireData((await apiClient<OperationalRecord[]>(
    `/api/v1/return-support/work-items${query}`,
    { signal },
  )).data);
}

export async function listIntegrationOutbox(
  status?: string,
  signal?: AbortSignal,
): Promise<readonly OperationalRecord[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return requireData((await apiClient<OperationalRecord[]>(
    `/api/v1/integration-outbox${query}`,
    { signal },
  )).data);
}

export async function listAIRoutes(signal?: AbortSignal): Promise<readonly OperationalRecord[]> {
  return requireData((await apiClient<OperationalRecord[]>(
    "/api/v1/ai-gateway/routes",
    { signal },
  )).data);
}

export async function listAITasks(signal?: AbortSignal): Promise<readonly OperationalRecord[]> {
  return requireData((await apiClient<OperationalRecord[]>(
    "/api/v1/ai-gateway/tasks",
    { signal },
  )).data);
}

export async function listAIMetrics(signal?: AbortSignal): Promise<readonly OperationalRecord[]> {
  return requireData((await apiClient<OperationalRecord[]>(
    "/api/v1/ai-gateway/metrics",
    { signal },
  )).data);
}

export async function getAIMetricsSummary(signal?: AbortSignal): Promise<OperationalRecord> {
  return requireData((await apiClient<OperationalRecord>(
    "/api/v1/ai-gateway/metrics/summary",
    { signal },
  )).data);
}

export async function testAISafety(payload: {
  taskId: string;
  payload: OperationalRecord;
}): Promise<OperationalRecord> {
  return requireData((await apiClient<OperationalRecord>(
    "/api/v1/ai-gateway/safety-test",
    jsonInit("POST", payload),
  )).data);
}
