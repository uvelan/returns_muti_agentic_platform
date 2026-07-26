import { apiClient } from "./client";
import type {
  AIStudioProposal,
  AIStudioProposalDetail,
  FeedbackLearningRecord,
  GraphSyncRun,
  SchemaRegistry,
} from "../contracts/dataStudio";

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

export async function getSchemaRegistry(signal?: AbortSignal): Promise<SchemaRegistry> {
  return requireData((await apiClient<SchemaRegistry>("/data-console/v1/schema", { signal })).data);
}

export async function listAIStudioProposals(signal?: AbortSignal): Promise<readonly AIStudioProposal[]> {
  return requireData((await apiClient<AIStudioProposal[]>("/data-console/v1/ai-studio/proposals", { signal })).data);
}

export async function getAIStudioProposal(id: string, signal?: AbortSignal): Promise<AIStudioProposalDetail> {
  return requireData((await apiClient<AIStudioProposalDetail>(
    `/data-console/v1/ai-studio/proposals/${encodeURIComponent(id)}`,
    { signal },
  )).data);
}

export async function generateAIStudioProposal(payload: {
  assetIds: string[];
  recordsPerAsset: number;
  seed: number;
  mode: "DETERMINISTIC" | "AI_ASSISTED";
  scenarioName: string;
}): Promise<AIStudioProposal> {
  return requireData((await apiClient<AIStudioProposal>(
    "/data-console/v1/ai-studio/proposals",
    jsonInit("POST", payload),
  )).data);
}

export async function applyAIStudioProposal(proposal: AIStudioProposal): Promise<AIStudioProposal> {
  return requireData((await apiClient<AIStudioProposal>(
    `/data-console/v1/ai-studio/proposals/${encodeURIComponent(proposal.id)}/apply`,
    jsonInit("POST", { expectedDigest: proposal.digest }),
  )).data);
}

export async function listGraphSyncRuns(signal?: AbortSignal): Promise<readonly GraphSyncRun[]> {
  return requireData((await apiClient<GraphSyncRun[]>("/data-console/v1/graph-sync/runs", { signal })).data);
}

export async function executeGraphSync(payload: {
  mode: "FULL" | "SOURCE_MONGODB" | "SQLSERVER";
  maxRecordsPerAsset: number;
  applySchema: boolean;
}): Promise<GraphSyncRun> {
  return requireData((await apiClient<GraphSyncRun>(
    "/data-console/v1/graph-sync/runs",
    jsonInit("POST", payload),
  )).data);
}

export async function applyGraphSchema(): Promise<readonly string[]> {
  return requireData((await apiClient<string[]>(
    "/data-console/v1/graph-sync/schema/apply",
    jsonInit("POST"),
  )).data);
}

export async function listFeedbackLearning(signal?: AbortSignal): Promise<readonly FeedbackLearningRecord[]> {
  return requireData((await apiClient<FeedbackLearningRecord[]>(
    "/data-console/v1/feedback-learning",
    { signal },
  )).data);
}
