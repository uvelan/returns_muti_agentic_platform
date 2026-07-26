export type DependencyKind = "OMC" | "PARCEL" | "FREIGHT" | "LSI";

export type SimulationOperationStatus =
  | "RECEIVED"
  | "VALIDATING"
  | "CONFIRMED"
  | "RETRYABLE_FAILURE"
  | "TERMINAL_FAILURE"
  | "MANUAL_REVIEW_REQUIRED"
  | "CANCELLED";

export type SimulationNarrative = {
  source: string;
  message: string;
  summary: string;
  nextAction: string;
  templateVersion: string;
  aiMetricId: string | null;
};

export type SimulationOperation = {
  id: string;
  dependency: DependencyKind;
  operation: string;
  sessionId: string;
  idempotencyKey: string;
  scenario: string;
  status: SimulationOperationStatus;
  externalReference: string | null;
  simulatedState: string | null;
  requestPayload: Record<string, unknown>;
  responsePayload: Record<string, unknown>;
  narrative: SimulationNarrative;
  errorCode: string | null;
  workflowEventType: string | null;
  workflowSignalStatus: string | null;
  createdAt: string;
  updatedAt: string;
};

export type SimulationAISummary = {
  requestCount: number;
  successCount: number;
  failureCount: number;
  fallbackCount: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  totalTokens: number;
  estimatedCostMicrousd: number;
  byProvider: Record<string, Record<string, number>>;
  byModel: Record<string, Record<string, number>>;
  byDependency: Record<string, Record<string, number>>;
  byOperation: Record<string, Record<string, number>>;
};

export type DependencySimulationSummary = {
  enabled: boolean;
  banner: string;
  environment: string;
  modes: Record<string, string>;
  operationCounts: Record<string, number>;
  ai: SimulationAISummary;
  configurationSha256: string;
};

export type SimulationAIUsageMetric = {
  id: string;
  operationId: string;
  sessionId: string;
  dependency: DependencyKind;
  operation: string;
  provider: string;
  model: string;
  credentialId: string | null;
  routeId: string | null;
  modelTier: string;
  selectionReason: string | null;
  status: string;
  fallbackUsed: boolean;
  attempt: number;
  latencyMs: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  estimatedCostMicrousd: number;
  errorCode: string | null;
  createdAt: string;
};
