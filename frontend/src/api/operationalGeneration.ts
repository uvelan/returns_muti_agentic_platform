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
  idempotency_key: string;
  target_environment: string;
  steps: unknown[];
  plan_impact: unknown;
}

export type ApprovalRecord = {
  approval_id: string;
  plan_id: string;
  proposal_checksum: string;
  approved_at: string;
  approved_by: string;
  target_environment: string;
}

export type ExecutionRun = {
  run_id: string;
  plan_id: string;
  target_environment: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "ROLLED_BACK";
  started_at: string;
  completed_at?: string;
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

export async function createProposal(config: Record<string, unknown>): Promise<OperationalGenerationProposal> {
  return requireData(
    (await apiClient<OperationalGenerationProposal>(
      "/data-console/v1/operational-generation/proposals",
      jsonInit("POST", config)
    )).data
  );
}

export async function validateProposal(proposalId: string): Promise<ValidationResult> {
  return requireData(
    (await apiClient<ValidationResult>(
      `/data-console/v1/operational-generation/proposals/${proposalId}/validate`,
      jsonInit("POST")
    )).data
  );
}

export async function planProposal(proposalId: string, planSalt: string): Promise<OperationalWritePlan> {
  return requireData(
    (await apiClient<OperationalWritePlan>(
      `/data-console/v1/operational-generation/proposals/${proposalId}/plan?plan_salt=${encodeURIComponent(planSalt)}`,
      jsonInit("POST")
    )).data
  );
}

export async function approvePlan(planId: string, targetEnvironment: string): Promise<ApprovalRecord> {
  return requireData(
    (await apiClient<ApprovalRecord>(
      `/data-console/v1/operational-generation/plans/${planId}/approve?target_environment=${encodeURIComponent(targetEnvironment)}`,
      jsonInit("POST")
    )).data
  );
}

export async function applyPlan(planId: string, approvalId: string, targetEnvironment: string): Promise<ExecutionRun> {
  return requireData(
    (await apiClient<ExecutionRun>(
      `/data-console/v1/operational-generation/plans/${planId}/apply`,
      jsonInit("POST", { approval_id: approvalId, target_environment: targetEnvironment })
    )).data
  );
}

export async function rollbackRun(runId: string, planId: string): Promise<ExecutionRun> {
  return requireData(
    (await apiClient<ExecutionRun>(
      `/data-console/v1/operational-generation/runs/${runId}/rollback`,
      jsonInit("POST", { plan_id: planId })
    )).data
  );
}

export async function getRun(runId: string): Promise<ExecutionRun> {
  return requireData(
    (await apiClient<ExecutionRun>(
      `/data-console/v1/operational-generation/runs/${runId}`,
      { method: "GET" }
    )).data
  );
}
