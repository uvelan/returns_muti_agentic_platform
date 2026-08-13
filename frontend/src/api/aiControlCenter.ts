/**
 * `/api/ai` -- the AI Control Center (Phase 21).
 *
 * Five read endpoints plus the two operator routes D2 landed: unsealing a held
 * request and answering it. Claim, Generate Candidate, Replay and Release still
 * have no backend route and are still not called from here.
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
  readonly cachedInputTokens: number | null;
  readonly outputTokens: number;
  readonly totalTokens: number;
  /** Null when the release holds no price for this provider/model -- not zero. */
  readonly estimatedCostMicros: number | null;
  readonly pricingCurrency: string | null;
  readonly pricingStatus: "PRICED" | "UNKNOWN";
  readonly pricingVersion: string | null;
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
  /** Summed over priced attempts only; see `unpricedAttempts` for the rest. */
  readonly estimatedCostMicros: number;
  readonly pricingCurrency: string | null;
  readonly unpricedAttempts: number;
  readonly byProvider: Readonly<Record<string, number>>;
  readonly byModel: Readonly<Record<string, number>>;
  readonly byTask: Readonly<Record<string, number>>;
  readonly byTier: Readonly<Record<string, number>>;
};

/**
 * The interceptions route is typed `list[dict[str, Any]]` on the backend, so
 * there is no generated contract to mirror -- these are transcribed from the
 * handler by hand.
 *
 * **They were transcribed in snake_case and the backend emits camelCase.** Every
 * field but `status` therefore rendered as "-", and nothing failed: the fields
 * were all optional, so a total mismatch looked exactly like an empty queue.
 * Optionality was chosen so an absent field could not break the screen, and it
 * hid the mismatch instead. They are required now, all six, so the next
 * divergence is a type error rather than a blank column.
 */
export type InterceptionRow = {
  readonly interceptionId: string;
  readonly taskId: string;
  readonly status: string;
  readonly createdAt: string;
  readonly expiresAt: string;
  readonly answeredBy: string | null;
};

/** The unsealed prompt. Shape belongs to whichever task raised the request. */
export type InterceptionRequest = Readonly<Record<string, unknown>>;

export type InterceptionAnswerResult = {
  readonly interceptionId: string;
  readonly status: string;
  readonly answeredBy: string | null;
  readonly answeredAt: string | null;
};

async function unwrap<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiClient<T>(path, init);
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

  /**
   * The held prompt, unsealed. A separate call from the queue on purpose: it is
   * sealed at rest because it can carry customer rows, and decrypting every
   * pending prompt to render a list would defeat that. Needs
   * `ai.interception.act`, not merely `.read`.
   */
  readInterceptionRequest: (interceptionId: string) =>
    unwrap<InterceptionRequest>(
      `/api/ai/interceptions/${encodeURIComponent(interceptionId)}/request`,
    ),

  /**
   * Abandon a held request instead of answering it.
   *
   * The counterpart to `answerInterception`, and it exists for the same reason:
   * without it the only ways out of PENDING are to answer or to wait for
   * `expiresAt`, so an operator who can see the prompt is wrong has to invent an
   * answer or leave a caller blocked. Refuses with 409 if an answer landed
   * first -- the backend reads the outcome back rather than trusting silence.
   */
  cancelInterception: (interceptionId: string) =>
    unwrap<InterceptionAnswerResult>(
      `/api/ai/interceptions/${encodeURIComponent(interceptionId)}/cancel`,
      { method: "POST" },
    ),

  answerInterception: (interceptionId: string, responseText: string) =>
    unwrap<InterceptionAnswerResult>(
      `/api/ai/interceptions/${encodeURIComponent(interceptionId)}/answer`,
      {
        method: "POST",
        // `createHeaders` sets Accept but not Content-Type, so every JSON POST
        // in this codebase declares its own. Omitting it makes FastAPI reject
        // the body as a missing field rather than as a bad content type.
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ responseText }),
      },
    ),

};

