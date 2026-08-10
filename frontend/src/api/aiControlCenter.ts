/**
 * `/api/ai` -- the AI Control Center (Phase 21).
 *
 * Five read endpoints, no mutations. The plan's interception actions (Claim,
 * Respond Manually, Generate Candidate, Replay, Release, Cancel) have no
 * backend route on this surface yet -- D2's operator API is still open -- so
 * none is called from here.
 *
 * Field names are camelCase because the backend contracts are
 * (`ai/gateway/models.py` sets `populate_by_name` on camelCase fields);
 * these mirror them exactly rather than renaming at the boundary.
 */

import { apiClient } from "./client";

export type CircuitState = "CLOSED" | "OPEN" | "HALF_OPEN";
export type ModelTier = string;
export type SafetyStatus = string;

export type AIRouteHealthView = {
  readonly routeId: string;
  readonly provider: string;
  readonly model: string;
  readonly credentialId: string;
  readonly tier: ModelTier;
  readonly configured: boolean;
  readonly circuitState: CircuitState;
  readonly activeRequests: number;
  readonly requestsThisMinute: number;
  readonly tokensThisMinute: number;
  readonly lastError: string | null;
  readonly lastSuccessAtEpochMs: number | null;
  readonly lastFailureAtEpochMs: number | null;
};

export type AITaskView = {
  readonly taskId: string;
  readonly tier: ModelTier;
  readonly promptVersion: string;
  readonly fallbackStrategy: string;
  readonly fallbackTemplate: string;
  readonly maximumOutputTokens: number;
  readonly maximumInputTokens: number;
  readonly allowTierEscalation: boolean;
  readonly allowedProviders: readonly string[];
  readonly allowedInputKeys: readonly string[];
};

export type AIUsageAttemptView = {
  readonly id: string;
  readonly traceId: string;
  readonly sessionId: string | null;
  readonly taskId: string;
  readonly configuredTier: ModelTier;
  readonly selectedTier: ModelTier | null;
  readonly provider: string | null;
  readonly model: string | null;
  readonly routeId: string | null;
  readonly attemptNumber: number;
  readonly selectionReason: string;
  readonly status: string;
  readonly fallbackUsed: boolean;
  readonly safetyStatus: SafetyStatus;
  readonly latencyMs: number;
  readonly rateLimitWaitMs: number;
  readonly inputTokens: number;
  readonly outputTokens: number;
  readonly totalTokens: number;
  readonly estimatedCostMicrousd: number;
  readonly errorCode: string | null;
  readonly requestDigest: string;
  readonly responseDigest: string | null;
  readonly createdAt: string;
};

export type AIUsageSummaryView = {
  readonly attempts: number;
  readonly successes: number;
  readonly failures: number;
  readonly fallbacks: number;
  readonly blockedBySafety: number;
  readonly inputTokens: number;
  readonly outputTokens: number;
  readonly totalTokens: number;
  readonly estimatedCostMicrousd: number;
  readonly byProvider: Readonly<Record<string, number>>;
  readonly byModel: Readonly<Record<string, number>>;
  readonly byTask: Readonly<Record<string, number>>;
  readonly byTier: Readonly<Record<string, number>>;
};

/**
 * The interceptions route is typed `list[dict[str, Any]]` on the backend, so
 * there is no contract to mirror. Only the fields the queue actually renders
 * are declared, all optional -- inventing a stricter shape than the backend
 * guarantees would break the screen the first time a field is absent.
 */
export type InterceptionRow = {
  readonly interception_id?: string;
  readonly status?: string;
  readonly task_id?: string;
  readonly agent_id?: string;
  readonly created_at?: string;
  readonly claimed_by?: string;
  readonly response_origin?: string;
};

async function unwrap<T>(path: string): Promise<T> {
  const response = await apiClient<T>(path);
  if (response.data === undefined || response.data === null) {
    throw new Error(`No data returned from ${path}.`);
  }
  return response.data;
}

export const aiControlCenterApi = {
  listRoutes: () => unwrap<AIRouteHealthView[]>("/api/ai/routes"),
  listTasks: () => unwrap<AITaskView[]>("/api/ai/tasks"),
  listAttempts: () => unwrap<AIUsageAttemptView[]>("/api/ai/metrics"),
  getSummary: () => unwrap<AIUsageSummaryView>("/api/ai/metrics/summary"),
  listInterceptions: () => unwrap<InterceptionRow[]>("/api/ai/interceptions"),
};
