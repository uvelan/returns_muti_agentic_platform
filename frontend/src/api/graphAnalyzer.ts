import { apiClient } from "./client";
import type {
  AgentContext,
  AgentReply,
  AgentRecommendation,
  AnalysisRun,
  AnalyzerBootstrap,
  AnalyzerSource,
  GraphEntity,
  GraphRelationship,
  GraphSchema,
  PreviewPage,
  SchemaValidation,
  SourceInput,
  SyncMode,
  SyncRun,
} from "../contracts/graphAnalyzer";

function requireData<T>(value: T | null): T {
  if (value === null) throw new Error("The Graph Schema Analyzer API returned no data.");
  return value;
}

function json(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

export async function getAnalyzerBootstrap(signal?: AbortSignal): Promise<AnalyzerBootstrap> {
  return requireData((await apiClient<AnalyzerBootstrap>("/graph-analyzer/v1/bootstrap", { signal })).data);
}

export async function saveSource(input: SourceInput, sourceId?: string): Promise<AnalyzerSource> {
  const path = sourceId === undefined ? "/graph-analyzer/v1/sources" : `/graph-analyzer/v1/sources/${encodeURIComponent(sourceId)}`;
  return requireData((await apiClient<AnalyzerSource>(path, json(sourceId === undefined ? "POST" : "PUT", input))).data);
}

export async function testSource(input: SourceInput, sourceId?: string): Promise<{ readonly status: string; readonly message: string }> {
  const suffix = sourceId === undefined ? "" : `?sourceId=${encodeURIComponent(sourceId)}`;
  return requireData((await apiClient<{ readonly status: string; readonly message: string }>(`/graph-analyzer/v1/sources/test${suffix}`, json("POST", input))).data);
}

export async function validateSource(sourceId: string): Promise<AnalyzerSource> {
  return requireData((await apiClient<AnalyzerSource>(`/graph-analyzer/v1/sources/${encodeURIComponent(sourceId)}/validate`, json("POST"))).data);
}

export async function refreshSource(sourceId: string): Promise<AnalyzerSource> {
  return requireData((await apiClient<AnalyzerSource>(`/graph-analyzer/v1/sources/${encodeURIComponent(sourceId)}/metadata`, json("POST"))).data);
}

export async function removeSource(sourceId: string): Promise<void> {
  await apiClient<null>(`/graph-analyzer/v1/sources/${encodeURIComponent(sourceId)}`, { method: "DELETE" });
}

export async function previewSourceObject(sourceId: string, objectId: string, page: number, signal?: AbortSignal): Promise<PreviewPage> {
  const params = new URLSearchParams({ objectId, page: String(page), pageSize: "25" });
  return requireData((await apiClient<PreviewPage>(`/graph-analyzer/v1/sources/${encodeURIComponent(sourceId)}/preview?${params.toString()}`, { signal })).data);
}

export async function startAnalysis(selectedObjectIds: readonly string[], context: string): Promise<AnalysisRun> {
  return requireData((await apiClient<AnalysisRun>("/graph-analyzer/v1/analyses", json("POST", { selectedObjectIds, context }))).data);
}

export async function getAnalysis(runId: string, signal?: AbortSignal): Promise<AnalysisRun> {
  return requireData((await apiClient<AnalysisRun>(`/graph-analyzer/v1/analyses/${encodeURIComponent(runId)}`, { signal })).data);
}

export async function getSchemas(signal?: AbortSignal): Promise<{ readonly existing: GraphSchema | null; readonly proposed: GraphSchema | null }> {
  return requireData((await apiClient<{ readonly existing: GraphSchema | null; readonly proposed: GraphSchema | null }>("/graph-analyzer/v1/schemas", { signal })).data);
}

export async function updateEntity(entity: GraphEntity): Promise<GraphSchema> {
  return requireData((await apiClient<GraphSchema>(`/graph-analyzer/v1/schemas/proposed/entities/${encodeURIComponent(entity.id)}`, json("PUT", entity))).data);
}

export async function updateRelationship(relationship: GraphRelationship): Promise<GraphSchema> {
  return requireData((await apiClient<GraphSchema>(`/graph-analyzer/v1/schemas/proposed/relationships/${encodeURIComponent(relationship.id)}`, json("PUT", relationship))).data);
}

export async function validateSchema(): Promise<SchemaValidation> {
  return requireData((await apiClient<SchemaValidation>("/graph-analyzer/v1/schemas/proposed/validate", json("POST"))).data);
}

export async function finalizeSchema(): Promise<GraphSchema> {
  return requireData((await apiClient<GraphSchema>("/graph-analyzer/v1/schemas/proposed/finalize", json("POST"))).data);
}

export async function askAnalyzer(message: string, context: AgentContext): Promise<AgentReply> {
  return requireData((await apiClient<AgentReply>("/graph-analyzer/v1/agent/messages", json("POST", { message, context }))).data);
}

export async function reviewRecommendation(recommendationId: string, decision: "APPLY" | "REJECT"): Promise<{ readonly recommendation: AgentRecommendation; readonly proposedSchema: GraphSchema | null }> {
  return requireData((await apiClient<{ readonly recommendation: AgentRecommendation; readonly proposedSchema: GraphSchema | null }>(`/graph-analyzer/v1/agent/recommendations/${encodeURIComponent(recommendationId)}`, json("POST", { decision }))).data);
}

export async function startSync(mode: SyncMode, scope: readonly string[]): Promise<SyncRun> {
  return requireData((await apiClient<SyncRun>("/graph-analyzer/v1/sync/runs", json("POST", { mode, scope }))).data);
}

export async function getSyncRun(runId: string, signal?: AbortSignal): Promise<SyncRun> {
  return requireData((await apiClient<SyncRun>(`/graph-analyzer/v1/sync/runs/${encodeURIComponent(runId)}`, { signal })).data);
}
