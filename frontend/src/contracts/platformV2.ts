export type ModuleStatus =
  | "DRAFT"
  | "VALIDATED"
  | "APPROVED"
  | "RELEASED"
  | "SUPERSEDED"
  | "ARCHIVED"
  | "QUARANTINED";

export type ConfigurationModule = {
  moduleId: string;
  moduleType: string;
  schemaVersion: string;
  configurationVersion: string;
  owner: string;
  status: ModuleStatus;
  dependencies: { moduleId: string; versionConstraint: string }[];
  payload: Record<string, unknown>;
  checksum: string;
  createdAt: string;
  createdBy: string;
  revision: number;
};

export type ValidationIssue = {
  code: string;
  path: (string | number)[];
  message: string;
  severity: "ERROR" | "WARNING";
  suggestedResolution: string | null;
};

export type ValidationResult = {
  valid: boolean;
  issues: ValidationIssue[];
  checksum: string | null;
};

export type ReleaseStatus =
  | "DRAFT"
  | "DEPENDENCIES_RESOLVED"
  | "VALIDATED"
  | "APPROVED"
  | "MIGRATION_READY"
  | "ACTIVE"
  | "SUPERSEDED"
  | "ARCHIVED";

export type ReleaseManifest = {
  releaseId: string;
  status: ReleaseStatus;
  modules: { moduleId: string; version: string; checksum: string }[];
  dependencyLockDigest: string;
  createdAt: string;
  createdBy: string;
  activatedAt: string | null;
};

export type SchemaQuestion = {
  questionId: string;
  fieldPath: string;
  prompt: string;
  reason: string;
  requiredOwner: string;
  evidence: string[];
  options: string[];
};

export type ProposalCommand = {
  moduleId: string;
  path: (string | number)[];
  operation: "SET" | "REMOVE" | "APPEND";
  currentValue: unknown;
  proposedValue: unknown;
  evidence: string[];
  reason: string;
  changeClassification: string;
  requiredOwner: string;
};

export type SchemaDesignContext = {
  requestId: string;
  contextVersion: number;
  selectedModules: string[];
  requestedCapabilities: string[];
  sourceStructures: unknown[];
  existingSchema: Record<string, unknown> | null;
  answers: Record<string, unknown>;
  commands: ProposalCommand[];
  currentQuestion: SchemaQuestion | null;
  status: "ANALYZING" | "WAITING_FOR_ANSWER" | "REVIEW_READY" | "INVALID";
  issues: ValidationIssue[];
  createdBy: string;
  updatedAt: string;
};

export type SyncResult = {
  requestId: string;
  syncType: "PARTIAL_ORDER_SYNC" | "FULL_ORDER_SYNC";
  status: "RESOLVED" | "NARROWING_REQUIRED" | "COMPLETED" | "NOT_FOUND" | "REJECTED" | "FAILED";
  releaseId: string;
  fullOrderIds: string[];
  recordsRead: number;
  graphWrites: number;
  message: string;
  digest: string;
  createdAt: string;
};

export type ImportRecord = {
  importId: string;
  status: "QUARANTINED" | "VALIDATED" | "REJECTED" | "DRAFTS_CREATED";
  modules: ConfigurationModule[];
  issues: ValidationIssue[];
  createdAt: string;
  createdBy: string;
};
