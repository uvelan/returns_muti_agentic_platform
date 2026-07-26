export type ReturnStatus =
  | "QUEUED"
  | "RUNNING"
  | "INTERCEPTION_PENDING"
  | "REVIEW_REQUIRED"
  | "APPROVED"
  | "REJECTED"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type AIDecision = "APPROVE" | "REJECT" | "REVIEW_REQUIRED";

export type ReturnSession = {
  readonly id: string;
  readonly correlationId: string;
  readonly workflowId: string | null;
  readonly customerReference: string;
  readonly orderReference: string;
  readonly itemReferences: readonly string[];
  readonly productReferences: readonly string[];
  readonly processingWarehouseReference: string | null;
  readonly productType: string | null;
  readonly reasonCode: string;
  readonly returnQuantity: number;
  readonly packageCount: number;
  readonly shippingPathExpectation: string;
  readonly notes: string | null;
  readonly channel: string;
  readonly status: ReturnStatus;
  readonly currentStage: string;
  readonly progressPercentage: number;
  readonly eligibilityDecision: AIDecision | null;
  readonly returnReference: string | null;
  readonly supportTicketReference: string | null;
  readonly trackingReference: string | null;
  readonly bayReference: string | null;
  readonly feedbackReference: string | null;
  readonly supportCaseId: string | null;
  readonly aiRequestId: string | null;
  readonly failureCode: string | null;
  readonly failureMessage: string | null;
  readonly version: number;
  readonly createdAt: string;
  readonly updatedAt: string;
};

export type TimelineEvent = {
  readonly id: string;
  readonly streamId: string;
  readonly sequence: number;
  readonly eventType: string;
  readonly actorType: string;
  readonly actorId: string;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly occurredAt: string;
  readonly publishedAt: string | null;
};

export type SupportCase = {
  readonly id: string;
  readonly sessionId: string;
  readonly caseType: string;
  readonly status: "OPEN" | "ASSIGNED" | "RESOLVED" | "CANCELLED";
  readonly priority: string;
  readonly reason: string;
  readonly assignedTo: string | null;
  readonly resolution: string | null;
  readonly decision: AIDecision | null;
  readonly slaDueAt: string;
  readonly slaBreached: boolean;
  readonly version: number;
  readonly createdAt: string;
  readonly updatedAt: string;
};

export type AIRequestStatus =
  | "CREATED"
  | "REDACTED"
  | "POLICY_CHECKED"
  | "INTERCEPTION_PENDING"
  | "DISPATCHED"
  | "RESPONSE_RECEIVED"
  | "RESPONSE_VALIDATED"
  | "DECISION_PERSISTED"
  | "REDACTION_FAILED"
  | "POLICY_BLOCKED"
  | "AUTH_FAILED"
  | "RATE_LIMITED"
  | "TIMEOUT"
  | "PROVIDER_UNAVAILABLE"
  | "RESPONSE_INVALID"
  | "CANCELLED"
  | "MANUAL_OVERRIDE";

export type AITrace = {
  readonly id: string;
  readonly sessionId: string | null;
  readonly status: AIRequestStatus;
  readonly provider: string | null;
  readonly model: string | null;
  readonly promptVersion: string;
  readonly redactedInput: Readonly<Record<string, unknown>>;
  readonly systemPrompt: string;
  readonly requestDigest: string;
  readonly responseText: string | null;
  readonly decision: AIDecision | null;
  readonly explanation: string | null;
  readonly confidenceMillionths: number | null;
  readonly latencyMs: number | null;
  readonly inputTokens: number | null;
  readonly outputTokens: number | null;
  readonly totalTokens: number | null;
  readonly responseDigest: string | null;
  readonly attempts: number;
  readonly errorCode: string | null;
  readonly interceptedBy: string | null;
  readonly interceptionReason: string | null;
  readonly originalRequestDigest: string | null;
  readonly version: number;
  readonly createdAt: string;
  readonly updatedAt: string;
};

export type AIGatewaySettings = {
  readonly interceptMode: boolean;
  readonly providerOrder: readonly string[];
  readonly version: number;
  readonly updatedAt: string;
  readonly updatedBy: string;
};

export type SeedStatus = {
  readonly version: string;
  readonly digest: string;
  readonly appliedAt: string | null;
  readonly appliedBy: string | null;
  readonly ready: boolean;
  readonly counts: Readonly<Record<string, number>>;
  readonly scenarioCounts: Readonly<Record<string, number>>;
  readonly validationErrors: readonly string[];
};

export type OperationalDependency = {
  readonly id: string;
  readonly name: string;
  readonly category: string;
  readonly status: "HEALTHY" | "DEGRADED" | "UNAVAILABLE" | "STARTING" | "UNKNOWN";
  readonly message: string;
  readonly checkedAt: string;
  readonly details: Readonly<Record<string, unknown>>;
};
