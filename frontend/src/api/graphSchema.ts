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

/**
 * `graph_schema_analyzer/domain/analysis_session.py::SessionStatus`, verbatim.
 *
 * This list was wrong, and wrong in the way that hurts: it named three statuses
 * the backend has never emitted -- `AWAITING_CLARIFICATION`, `PROPOSING`,
 * `AWAITING_REVIEW` -- and omitted `DRAFT`, which is the *only* status a
 * session is ever created with. So the one status every new analysis actually
 * carries was untypeable, and three that cannot occur were.
 *
 * The generated mirror at `generated/return-platform.d.ts:6688` has always had
 * the real values, so the app carried two client mirrors that disagreed. This
 * one is now the same nine names in the same order.
 *
 * `READY_FOR_APPROVAL` and `APPROVED` are unreachable on today's transition
 * table -- nothing drives a session into them -- but they are published enum
 * members and removing them is a contract change, not a mirror correction.
 */
export type SessionStatus =
  | "DRAFT"
  | "DISCOVERING"
  | "ANALYZING"
  | "NEEDS_CLARIFICATION"
  | "NEEDS_HUMAN_REVIEW"
  | "READY_FOR_APPROVAL"
  | "APPROVED"
  | "ABANDONED"
  | "FAILED";

/**
 * `graph_schema_analyzer/domain/schema_draft.py::DraftStatus`, verbatim.
 *
 * `SUPERSEDED` was invented here. A draft is never superseded in the analyzer's
 * lifecycle -- a mutation sends a `VALIDATED` draft back to `DRAFT` and the same
 * draft id carries on -- and the name was borrowed from the *release* lifecycle
 * in `configuration.ts`, where it is real.
 */
export type DraftStatus = "DRAFT" | "VALIDATED" | "APPROVED";

/**
 * The three statuses a session never leaves, from `_TERMINAL` in
 * `analysis_session.py`. Declared once here rather than spelled out at each
 * reader, so a lifecycle change is one edit.
 */
export const TERMINAL_SESSION_STATUSES: readonly SessionStatus[] = [
  "APPROVED",
  "ABANDONED",
  "FAILED",
];

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

/**
 * The draft shape, typed at the API boundary on both sides.
 *
 * The backend's view models set `extra="forbid"`, so a mutation command that
 * started writing a differently-named key fails loudly there rather than
 * arriving here as a quietly missing field. These mirror those models exactly;
 * a mismatch is a 500 on the server, not a blank column on the screen.
 */
export type PropertyShapeView = {
  readonly type: string;
  readonly source_field: string;
  readonly transformation: string;
};

export type EntityShapeView = {
  readonly label: string;
  readonly source_dataset: string;
  readonly properties: Readonly<Record<string, PropertyShapeView>>;
  readonly identifier_properties: readonly string[];
  readonly ownership: string;
  readonly sync_mode: string;
};

export type RelationshipShapeView = {
  readonly relationship_type: string;
  readonly from_label: string;
  readonly to_label: string;
  readonly cardinality: string;
};

export type GraphIndexShapeView = {
  readonly label: string;
  readonly properties: readonly string[];
};

export type GraphConstraintShapeView = {
  readonly label: string;
  readonly property_name: string;
  readonly unique: boolean;
  readonly required: boolean;
};

export type DraftShapeView = {
  readonly entities: Readonly<Record<string, EntityShapeView>>;
  readonly relationships: readonly RelationshipShapeView[];
  readonly graph_indexes: readonly GraphIndexShapeView[];
  readonly graph_constraints: readonly GraphConstraintShapeView[];
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

/**
 * One typed mutation command, carried opaquely.
 *
 * **Deliberately not the seventeen-member union spelled out again in
 * TypeScript.** The command set is closed and enforced by the backend's
 * discriminated union with `extra="forbid"`; a second copy here would be a
 * second vocabulary to keep in step, and the screen never constructs a command
 * -- it displays `kind` and posts back exactly what it was handed. A field this
 * file does not know about survives the round trip untouched, which is the
 * property that matters.
 */
export type MutationCommandView = Readonly<Record<string, unknown>> & { readonly kind: string };

export type ChangeType = "ADDED" | "REMOVED" | "MODIFIED";

export type SchemaDiffEntryView = {
  readonly change_type: ChangeType;
  readonly element: string;
  readonly detail: string;
};

export type SchemaDiffView = {
  readonly from_sequence: number;
  readonly to_sequence: number;
  readonly entries: readonly SchemaDiffEntryView[];
};

export type DriftKind =
  | "DATASET_ADDED"
  | "DATASET_REMOVED"
  | "FIELD_ADDED"
  | "FIELD_REMOVED"
  | "FIELD_TYPE_CHANGED";

/**
 * One thing that drifted, and the commands that would answer it.
 *
 * An empty `mutations` is a first-class outcome, not an error: it means no
 * command can express the fix without guessing -- a dropped identifier field, a
 * collection whose name is not a legal label -- and the analyst has to decide.
 */
export type ProposedChangeView = {
  readonly drift: DriftKind;
  readonly dataset: string;
  readonly element: string;
  readonly detail: string;
  readonly mutations: readonly MutationCommandView[];
};

/**
 * A dataset whose shape is unchanged and whose location is not.
 *
 * Carries no mutation because nothing in the schema says where a dataset lives.
 * The act it asks for is a rebinding on the Sources tab.
 */
export type ProposedRebindingView = {
  readonly dataset: string;
  readonly from_source_id: string;
  readonly to_source_id: string;
  readonly to_dataset: string;
  readonly detail: string;
};

export type ReanalysisProposalView = {
  readonly draft_id: string;
  readonly from_content_hash: string;
  readonly to_content_hash: string;
  readonly changes: readonly ProposedChangeView[];
  readonly rebindings: readonly ProposedRebindingView[];
  readonly diff: SchemaDiffView;
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
        const responseDetail: unknown = body.detail;
        if (typeof responseDetail === "string") {
          detail = responseDetail;
        } else if (
          typeof responseDetail === "object"
          && responseDetail !== null
          && "message" in responseDetail
          && typeof responseDetail.message === "string"
        ) {
          detail = responseDetail.message;
        }
      }
    } catch {
      // A non-JSON error body is not itself an error worth surfacing; the
      // status code is the useful part.
    }
    throw new APIError(detail, response.status);
  }

  return (await response.json()) as T;
}

/**
 * The outcome of a publish.
 *
 * `accepted: false` is an ordinary answer, not an error: a shape can be
 * approved and still fail to compile -- an entity with no identifier, a
 * relationship with no resolvable join -- and `detail` names the element so
 * the analyst can fix it.
 */
export type PublishedReleaseView = {
  readonly configurationReleaseId: string;
  readonly accepted: boolean;
  readonly detail: string | null;
};

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

  /**
   * The entities and relationships a canvas draws.
   *
   * A separate call from `getDraft` on purpose, mirroring the backend split:
   * counts are O(1) and are what a draft *listing* needs, while a real source's
   * schema is unbounded. Fetching this only when the canvas is on screen is the
   * whole point of it not being inlined.
   */
  getDraftShape: (draftId: string) => request<DraftShapeView>(`/drafts/${draftId}/shape`),
  listRevisions: (draftId: string) => request<RevisionView[]>(`/drafts/${draftId}/revisions`),

  /**
   * Re-read the sources and ask what the draft would have to change.
   *
   * Returns a proposal and writes nothing to the draft. It does refresh the
   * *evidence*: the analysis re-grounds on the new snapshot, so validation
   * starts answering against what the source looks like today rather than
   * against the reading the draft was designed on.
   */
  reanalyzeDraft: (draftId: string) =>
    request<ReanalysisProposalView>(`/drafts/${draftId}/reanalysis`, { method: "POST" }),

  /**
   * Apply a batch of typed commands as one revision.
   *
   * The same call a hand-written edit makes, and the *only* way a proposal is
   * accepted. A separate "accept re-analysis" endpoint would be a second write
   * path into a draft, and the revision history would stop being one story.
   */
  applyMutations: (draftId: string, mutations: readonly MutationCommandView[]) =>
    request<DraftView>(`/drafts/${draftId}/mutations`, {
      method: "POST",
      body: JSON.stringify({ mutations }),
    }),
  validateDraft: (draftId: string) =>
    request<ValidationResultView>(`/drafts/${draftId}/validate`, { method: "POST" }),
  approveDraft: (draftId: string, note?: string) =>
    request<DraftView>(`/drafts/${draftId}/approve`, {
      method: "POST",
      body: JSON.stringify({ note: note ?? null }),
    }),

  /**
   * Turn an approved draft into the schema the platform runs.
   *
   * The step that closes the analyzer's loop: until this existed, approving a
   * draft changed a document and the runtime went on reading a file from the
   * repository. `activate` decides whether it goes live now or is only cut.
   */
  publishDraft: (draftId: string, activate: boolean) =>
    request<PublishedReleaseView>(`/drafts/${draftId}/publish`, {
      method: "POST",
      body: JSON.stringify({ activate }),
    }),
};
