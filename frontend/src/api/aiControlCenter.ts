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
  /**
   * Which piece of business work the call served (W4.12). Platform ids only --
   * the backend record deliberately carries no customer-identifying field, so
   * there is nothing here to redact before rendering.
   */
  readonly correlationId: string | null;
  readonly caseId: string | null;
  readonly conversationId: string | null;
  readonly agentId: string | null;
  readonly promptVersion: string | null;
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
  readonly fallbackReason: string | null;
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
  /**
   * Which of the two hold points this is.
   *
   * `REQUEST` -- the provider has not been called; a human may answer in the
   * model's place. `RESPONSE` -- the provider has answered and a human may
   * accept, edit or reject the reply. One queue carries both, so this is what
   * tells an operator which job a row is.
   */
  readonly point: InterceptionPoint;
  readonly createdAt: string;
  readonly expiresAt: string;
  readonly answeredBy: string | null;
};

export type InterceptionPoint = "REQUEST" | "RESPONSE";

/**
 * The unsealed payload. Shape belongs to whichever task raised the hold.
 *
 * At `RESPONSE` it also carries `modelResponse` -- the reply the operator is
 * reviewing, with the provider and model that produced it. Typed loosely
 * because the request half genuinely varies by task; `modelResponse` is read
 * defensively at the point of use rather than asserted here.
 */
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

  /**
   * Take the held item unchanged.
   *
   * At `REQUEST` the model is allowed to proceed after all; at `RESPONSE` the
   * model's reply stands and reaches the caller byte-identical. Both are
   * reported as the model, which is exactly why this is a different call from
   * `answerInterception` -- approving is a statement that no human text is
   * involved, and routing it through "answer" would record an empty human
   * answer instead.
   *
   * The route has existed since AI-01 and nothing called it, so an operator's
   * only options here were to hand-write an answer or kill the request.
   */
  allowInterception: (interceptionId: string) =>
    unwrap<InterceptionAnswerResult>(
      `/api/ai/interceptions/${encodeURIComponent(interceptionId)}/allow`,
      { method: "POST" },
    ),

  /**
   * Supply the text the caller will receive: an answer at `REQUEST`, an edit of
   * the model's reply at `RESPONSE`. One route for both, because the act is the
   * same act; the backend attributes it from the record's point.
   */
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

  /**
   * Re-run a recorded request, optionally forcing a different provider.
   *
   * The route has existed and been unreachable: mounted on the backend, never
   * called from here, so the "was this a model problem or a prompt problem"
   * question had no answer short of a database query. The reply is a *new*
   * trace, not an edit of the old one -- the original is evidence and stays
   * exactly as recorded.
   */
  replayRequest: (traceId: string, provider?: string) =>
    unwrap<AITraceView>(`/api/ai/requests/${encodeURIComponent(traceId)}/replay`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(provider ? { provider } : {}),
    }),

  /** The same request against two to six providers at once, for comparison. */
  compareRequest: (traceId: string, providers: readonly string[]) =>
    unwrap<AITraceView[]>(`/api/ai/requests/${encodeURIComponent(traceId)}/compare`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ providers }),
    }),
};

/**
 * One recorded AI request. Transcribed from `operations/models.py::AITraceView`,
 * narrowed to the fields S8 renders -- the full model carries the prompt and the
 * response text, which this screen deliberately does not put on a list.
 */
export type AITraceView = {
  readonly id: string;
  readonly status: string;
  readonly taskId: string;
  readonly provider: string | null;
  readonly model: string | null;
  readonly promptVersion: string;
  readonly decision: string | null;
  readonly explanation: string | null;
  readonly latencyMs: number | null;
  readonly inputTokens: number | null;
  readonly outputTokens: number | null;
  readonly estimatedCostMicros: number | null;
  readonly pricingCurrency: string | null;
  readonly pricingStatus: string;
  readonly pricingVersion: string | null;
  readonly errorCode: string | null;
  readonly createdAt: string;
};
