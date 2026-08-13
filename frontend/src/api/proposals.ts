import { apiClient } from "./client";

/**
 * `/api/proposals` -- the one governance inbox.
 *
 * Every change a human has to sign off arrives here: a validated graph schema,
 * an agent configuration edit, a feedback improvement. One surface because they
 * are one decision queue, and a platform with three approval screens has three
 * places for a change to sit unnoticed.
 *
 * **`ProposalKernel` owns the lifecycle; this module only asks it things.**
 * There is no client-side transition table that *decides* anything: the screen
 * mirrors the kernel's table to avoid offering a button the backend will refuse,
 * and every refusal comes back from the kernel and is shown verbatim.
 *
 * Field names mirror `api/proposals.py` exactly. The view models are camelCase
 * by hand, except `history` and `diff`, which the router serializes straight
 * from the Pydantic record -- so their keys are the record's own snake_case
 * ones. Guessing camelCase there would have produced a silently empty column.
 */

export type ProposalType = "GRAPH_SCHEMA" | "IMPROVEMENT" | "CONFIGURATION";

/** `platform/governance/proposal.py::ProposalStatus`. */
export type ProposalStatus =
  | "DRAFT"
  | "VALIDATED"
  | "REVIEW_PENDING"
  | "APPROVED"
  | "REJECTED"
  | "ACTIVATED"
  | "SUPERSEDED";

export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";

export type ChangeKind = "ADDED" | "REMOVED" | "CHANGED";

/** What a queue row carries. Deliberately without `before`/`after`. */
export type ProposalSummary = {
  readonly proposalId: string;
  readonly proposalType: ProposalType;
  readonly subjectId: string;
  readonly title: string;
  readonly status: ProposalStatus;
  /** `RiskLevel`, widened because the router serializes it as a plain string. */
  readonly risk: string;
  readonly affectedKeys: readonly string[];
  readonly proposedBy: string;
  readonly decidedBy: string | null;
  readonly createdAt: string;
  readonly updatedAt: string;
};

/** `ProposalDiffEntry`, serialized from the record, so snake-free by luck not design. */
export type ProposalDiffEntry = {
  readonly key: string;
  readonly change: ChangeKind;
  readonly before?: unknown;
  readonly after?: unknown;
};

/** `ProposalTransition`, serialized from the record -- hence `occurred_at`. */
export type ProposalTransition = {
  readonly status: ProposalStatus;
  readonly actor: string;
  readonly occurred_at: string;
  readonly note: string | null;
};

export type ProposalDetail = ProposalSummary & {
  readonly before: Readonly<Record<string, unknown>>;
  readonly after: Readonly<Record<string, unknown>>;
  readonly diff: readonly ProposalDiffEntry[];
  /** References, never payloads: a session id, a validation result id, a sync run id. */
  readonly evidence: readonly string[];
  readonly evidenceDigest: string;
  readonly validationReceipt: string | null;
  readonly decisionNote: string | null;
  readonly activationReference: string | null;
  readonly history: readonly ProposalTransition[];
};

export type ProposalQuery = {
  readonly type?: ProposalType;
  readonly status?: ProposalStatus;
  readonly subjectId?: string;
  readonly limit?: number;
};

/**
 * The decisions reachable from each status, mirroring `PROPOSAL_TRANSITIONS`.
 *
 * Single-sourced from the backend table rather than restated per button, in the
 * same idiom as `ALLOWED_PROMOTIONS` on the configuration surface. This exists
 * so an operator is not offered an action that will come back 409; it decides
 * nothing. `REJECTED` and `SUPERSEDED` are terminal and therefore absent, and a
 * status this map does not know offers no actions at all -- which is the safe
 * direction to be wrong in.
 */
export const DECISIONS_BY_STATUS: Readonly<
  Record<string, readonly ("APPROVE" | "REJECT" | "ACTIVATE")[]>
> = {
  REVIEW_PENDING: ["APPROVE", "REJECT"],
  APPROVED: ["ACTIVATE"],
};

function unwrapDetail(data: ProposalDetail | null, path: string): ProposalDetail {
  if (data === null) {
    throw new Error(`No proposal returned from ${path}.`);
  }
  return data;
}

async function decide(
  proposalId: string,
  action: "approve" | "reject",
  note: string | null,
): Promise<ProposalDetail> {
  const path = `/api/proposals/${encodeURIComponent(proposalId)}/${action}`;
  const response = await apiClient<ProposalDetail>(path, {
    method: "POST",
    // `createHeaders` sets Accept but not Content-Type.
    headers: { "Content-Type": "application/json" },
    // Sent as `null` rather than omitted when empty: `DecisionRequest.note` is
    // `str | None`, and an absent reason is a real answer to "why", not a field
    // the caller forgot.
    body: JSON.stringify({ note }),
  });
  return unwrapDetail(response.data, path);
}

export const proposalsApi = {
  async list(query: ProposalQuery = {}): Promise<ProposalSummary[]> {
    const params = new URLSearchParams();
    if (query.type !== undefined) params.set("type", query.type);
    if (query.status !== undefined) params.set("status", query.status);
    if (query.subjectId !== undefined && query.subjectId !== "") {
      params.set("subjectId", query.subjectId);
    }
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    const search = params.toString();
    const response = await apiClient<ProposalSummary[]>(
      `/api/proposals${search === "" ? "" : `?${search}`}`,
    );
    return response.data ?? [];
  },

  async get(proposalId: string): Promise<ProposalDetail> {
    const path = `/api/proposals/${encodeURIComponent(proposalId)}`;
    const response = await apiClient<ProposalDetail>(path);
    return unwrapDetail(response.data, path);
  },

  approve: (proposalId: string, note: string | null) => decide(proposalId, "approve", note),

  reject: (proposalId: string, note: string | null) => decide(proposalId, "reject", note),

  /**
   * Carry an approved change into the runtime.
   *
   * Separately granted from approving, because on this platform activating
   * publishes a configuration release -- not a decision the reviewer of a
   * wording change should make by side effect. `parameters` is passed through
   * to the type's own activator; the screen sends none, so each activator
   * applies its own defaults rather than having the browser guess them.
   */
  async activate(
    proposalId: string,
    parameters: Readonly<Record<string, unknown>> = {},
  ): Promise<ProposalDetail> {
    const path = `/api/proposals/${encodeURIComponent(proposalId)}/activate`;
    const response = await apiClient<ProposalDetail>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parameters }),
    });
    return unwrapDetail(response.data, path);
  },
};
