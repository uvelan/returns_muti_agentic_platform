/**
 * `/api/graph-schema` -- the Graph Schema Analyzer (Phase 20).
 *
 * These routes return their view models **directly**, not wrapped in the
 * `APIResponse` envelope the canonical `/api/returns`, `/api/config` and
 * `/api/principal` surfaces use. That is the analyzer's existing shape, not an
 * oversight here, so this module unwraps nothing and `apiClient` is not used.
 *
 * Every type below mirrors a real backend model in
 * `graph_schema_analyzer/api/{schemas,drafts}.py`. Nothing is invented: an
 * endpoint absent from that surface is absent from this file, and the screen
 * shows it as unavailable rather than calling something that does not exist.
 */

import { APIError } from "./client";

export type SessionStatus =
  | "DISCOVERING"
  | "AWAITING_CLARIFICATION"
  | "PROPOSING"
  | "AWAITING_REVIEW"
  | "APPROVED"
  | "ABANDONED"
  | "FAILED";

export type DraftStatus = "DRAFT" | "VALIDATED" | "APPROVED" | "SUPERSEDED";

export type Severity = "ERROR" | "WARNING" | "INFO";

export type AnalysisSessionView = {
  readonly analysis_id: string;
  readonly status: SessionStatus;
  readonly source_refs: readonly string[];
  readonly created_by: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly version: number;
  readonly snapshot_id: string | null;
  readonly draft_id: string | null;
  readonly failure_reason: string | null;
};

export type SnapshotView = {
  readonly snapshot_id: string;
  readonly analysis_id: string;
  readonly content_hash: string;
  readonly sample_classification: string;
  readonly sample_expires_at: string | null;
  readonly captured_at: string;
  readonly dataset_count: number;
};

export type ClarificationView = {
  readonly clarification_id: string;
  readonly analysis_id: string;
  readonly question: string;
  readonly status: string;
  readonly answer: string | null;
  readonly asked_at: string;
  readonly answered_at: string | null;
  readonly answered_by: string | null;
};

export type DraftView = {
  readonly draft_id: string;
  readonly analysis_id: string;
  readonly status: DraftStatus;
  readonly current_revision: number;
  readonly version: number;
  readonly validation_result_id: string | null;
  readonly entity_count: number;
  readonly relationship_count: number;
};

export type RevisionView = {
  readonly revision_id: string;
  readonly sequence: number;
  readonly author: string;
  readonly authored_by_model: boolean;
  readonly mutation_count: number;
  readonly created_at: string;
};

export type ValidationFindingView = {
  readonly check: string;
  readonly severity: Severity;
  readonly element: string;
  readonly message: string;
};

export type ValidationResultView = {
  readonly result_id: string;
  readonly draft_id: string;
  readonly revision_id: string;
  readonly passed: boolean;
  readonly findings: readonly ValidationFindingView[];
  readonly checks_run: readonly string[];
  readonly missing_checks: readonly string[];
};

const BASE = "/api/graph-schema";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${BASE}${path}`, { ...init, headers });

  if (!response.ok) {
    // The analyzer returns FastAPI's `{detail}` shape rather than the
    // envelope's error body, so the message is read from there.
    let detail = response.statusText;
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null && "detail" in body) {
        detail = String(body.detail);
      }
    } catch {
      // A non-JSON error body is not itself an error worth surfacing; the
      // status code is the useful part.
    }
    throw new APIError(detail, response.status);
  }

  return (await response.json()) as T;
}

export const graphSchemaApi = {
  listAnalyses: () => request<AnalysisSessionView[]>("/analyses"),
  getAnalysis: (id: string) => request<AnalysisSessionView>(`/analyses/${id}`),
  createAnalysis: (sourceRefs: readonly string[]) =>
    request<AnalysisSessionView>("/analyses", {
      method: "POST",
      body: JSON.stringify({ source_refs: sourceRefs }),
    }),
  abandonAnalysis: (id: string) =>
    request<AnalysisSessionView>(`/analyses/${id}/abandon`, { method: "POST" }),
  getSnapshot: (id: string) => request<SnapshotView>(`/analyses/${id}/snapshot`),
  listClarifications: (id: string) =>
    request<ClarificationView[]>(`/analyses/${id}/clarifications`),
  answerClarification: (analysisId: string, clarificationId: string, answer: string) =>
    request<ClarificationView>(
      `/analyses/${analysisId}/clarifications/${clarificationId}/answer`,
      { method: "POST", body: JSON.stringify({ answer }) },
    ),
  getDraft: (draftId: string) => request<DraftView>(`/drafts/${draftId}`),
  listRevisions: (draftId: string) => request<RevisionView[]>(`/drafts/${draftId}/revisions`),
  validateDraft: (draftId: string) =>
    request<ValidationResultView>(`/drafts/${draftId}/validate`, { method: "POST" }),
  approveDraft: (draftId: string, note?: string) =>
    request<DraftView>(`/drafts/${draftId}/approve`, {
      method: "POST",
      body: JSON.stringify({ note: note ?? null }),
    }),
};
