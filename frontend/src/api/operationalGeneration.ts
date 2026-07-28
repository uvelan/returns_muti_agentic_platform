import { apiClient } from "./client";

export type GuardFinding = {
  code: string;
  severity: "ERROR" | "WARNING" | "DENIAL";
  asset_id: string;
  record_index?: number;
  field_path?: string;
  message: string;
}

export type ValidationResult = {
  state: "VALID" | "INVALID_RECORD" | "INVALID_PROPOSAL" | "POLICY_DENIED";
  findings: GuardFinding[];
}

export type GeneratedRecord = {
  asset_id: string;
  temporary_record_key: string;
  values: Record<string, unknown>;
  dependency_keys: string[];
  generation_index: number;
}

export type GenerationProvenance = {
  timestamp: string;
  generator_version: string;
}

export type OperationalGenerationProposal = {
  proposal_id: string;
  schema_release_id: string;
  schema_checksum: string;
  deterministic_seed: number;
  generation_mode: string;
  records: GeneratedRecord[];
  provenance: GenerationProvenance;
  proposal_checksum: string;
}

export type OperationalWritePlan = {
  plan_id: string;
  proposal_checksum: string;
  schema_release_id: string;
  schema_checksum: string;
  idempotency_key: string;
  saga_steps: unknown[];
  impact: unknown;
  plan_checksum: string;
}

export type ApprovalRecord = {
  approval_id: string;
  plan_id: string;
  proposal_checksum: string;
  plan_checksum: string;
  schema_release_id: string;
  approved_at: string;
  expires_at: string;
  approved_by: string;
  target_environment: string;
}

export type ExecutionRun = {
  run_id: string;
  plan_id: string;
  state: "DRAFT" | "GENERATED" | "VALIDATING" | "VALIDATION_FAILED" |
    "VALIDATED" | "PENDING_APPROVAL" | "APPROVED" | "APPLYING" | "APPLIED" |
    "PARTIALLY_FAILED" | "COMPENSATING" | "COMPENSATED" | "ROLLING_BACK" |
    "ROLLED_BACK" | "ROLLBACK_BLOCKED" | "ROLLBACK_FAILED" | "EXPIRED";
  started_at: string;
  updated_at: string;
  error?: string;
}

function requireData<T>(value: T | null): T {
  if (value === null) throw new Error("The API returned no data.");
  return value;
}

function jsonInit(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

const operationalBase = "/api/v1/data-console/ai-studio/operational";

export type CreateOperationalProposalRequest = {
  assetIds: string[];
  recordsPerAsset: number;
  seed: number;
  mode: "DETERMINISTIC" | "AI_ASSISTED";
  scenarioName: string;
}

export async function createProposal(
  config: CreateOperationalProposalRequest,
): Promise<OperationalGenerationProposal> {
  return requireData(
    (await apiClient<OperationalGenerationProposal>(
      `${operationalBase}/proposals`,
      jsonInit("POST", config)
    )).data
  );
}

export async function validateProposal(proposalId: string): Promise<ValidationResult> {
  return requireData(
    (await apiClient<ValidationResult>(
      `${operationalBase}/proposals/${encodeURIComponent(proposalId)}/validate`,
      jsonInit("POST")
    )).data
  );
}

export async function planProposal(proposalId: string, planSalt: string): Promise<OperationalWritePlan> {
  return requireData(
    (await apiClient<OperationalWritePlan>(
      `${operationalBase}/proposals/${encodeURIComponent(proposalId)}/plan?plan_salt=${encodeURIComponent(planSalt)}`,
      jsonInit("POST")
    )).data
  );
}

export async function approvePlan(
  proposalChecksum: string,
  planId: string,
  targetEnvironment: string,
): Promise<ApprovalRecord> {
  return requireData(
    (await apiClient<ApprovalRecord>(
      `${operationalBase}/proposals/${encodeURIComponent(proposalChecksum)}/approve?plan_id=${encodeURIComponent(planId)}&target_environment=${encodeURIComponent(targetEnvironment)}`,
      jsonInit("POST")
    )).data
  );
}

export async function applyPlan(
  proposalChecksum: string,
  planId: string,
  approvalId: string,
  targetEnvironment: string,
): Promise<ExecutionRun> {
  return requireData(
    (await apiClient<ExecutionRun>(
      `${operationalBase}/proposals/${encodeURIComponent(proposalChecksum)}/apply?plan_id=${encodeURIComponent(planId)}&approval_id=${encodeURIComponent(approvalId)}&target_environment=${encodeURIComponent(targetEnvironment)}`,
      jsonInit("POST")
    )).data
  );
}

export async function rollbackRun(runId: string, planId: string): Promise<ExecutionRun> {
  return requireData(
    (await apiClient<ExecutionRun>(
      `${operationalBase}/runs/${encodeURIComponent(runId)}/rollback?plan_id=${encodeURIComponent(planId)}`,
      jsonInit("POST")
    )).data
  );
}

export async function getRun(runId: string): Promise<ExecutionRun> {
  return requireData(
    (await apiClient<ExecutionRun>(
      `${operationalBase}/runs/${encodeURIComponent(runId)}`,
      { method: "GET" }
    )).data
  );
}
