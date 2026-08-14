import { apiClient } from "./client";

/**
 * The case: one return, its RMAs, and which items each covers.
 *
 * Replaces the copilot's client-side join. S1 used to find a return by matching
 * `session.orderReference` against the top search candidate across two
 * unrelated collections -- so two open orders sharing a reference showed the
 * wrong one, and closing the tab lost the link entirely.
 *
 * Items arrive nested inside their record rather than as a flat list, which is
 * what makes "label LBL-1 belongs to RMA-2" unsayable by accident.
 */

export type CaseReturnItem = {
  returnItemId: string;
  orderLineReference: string;
  productReference: string | null;
  quantity: number;
  reason: string | null;
  condition: string | null;
  packageReference: string | null;
};

export type ReturnRecord = {
  returnRecordId: string;
  caseId: string;
  returnReference: string | null;
  status: string;
  returnLocation: string | null;
  trackingReference: string | null;
  labelReference: string | null;
  shippingInstructionReference: string | null;
  sourceSystem: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
};

export type CaseReturnRecord = {
  record: ReturnRecord;
  items: CaseReturnItem[];
};

export type CaseFact = {
  factId: string;
  caseId: string;
  factName: string;
  value: unknown;
  agentId: string;
  channel: string;
  acquisitionMethod: string;
  sourceSystem: string | null;
  sourcePath: string | null;
  observedAt: string;
  recordedAt: string;
};

export type CaseSummary = {
  caseId: string;
  status: string;
  confirmedOrderReference: string | null;
  channelAConversationId: string | null;
  returnRecordCount: number;
  updatedAt: string;
};

/**
 * `operations/models.py::CaseView`.
 *
 * The last six fields are what the case was *persisted and projected* under,
 * and they were omitted here while the only reader was the copilot, which shows
 * a conversation rather than a record. An operations reader needs them: a case
 * with no `workflowId` has no durable execution behind it, and a
 * `graphGenerationId` is the generation an answer about this case was read
 * from. Naming them beats inferring them from silence.
 */
export type CaseView = {
  caseId: string;
  tenantId: string;
  principalId: string;
  branchId: string | null;
  status: string;
  channelAConversationId: string | null;
  channelBWorkItemId: string | null;
  confirmedOrderReference: string | null;
  /** tenant | conversation | order | line-set -- the confirmation idempotency boundary. */
  confirmationKey: string | null;
  sessionId: string | null;
  /** Null means no durable execution was ever started for this case. */
  workflowId: string | null;
  configurationReleaseId: string | null;
  graphGenerationId: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
};

export type CaseDetail = {
  case: CaseView;
  returnRecords: CaseReturnRecord[];
  /** Lines the associate named that no RMA covers yet. Never folded into the first record. */
  unassignedItems: CaseReturnItem[];
  facts: CaseFact[];
};

/**
 * The newest fact for each name.
 *
 * `facts` is an append-only log -- Bay, Support, Fulfilment and Channel A all
 * write concurrently -- and the backend's own `latest_case_facts` projection
 * takes the newest per name. Doing the same here means a corrected bay
 * recommendation supersedes the first one on screen instead of both being
 * rendered as though the platform held two opinions.
 *
 * Ordered by `recordedAt`, with `observedAt` as the tiebreak: two facts written
 * in one activity share a millisecond often enough that ties are the norm, and
 * falling back to array order would make the projection depend on Mongo's
 * cursor.
 */
export function latestFacts(facts: readonly CaseFact[]): ReadonlyMap<string, CaseFact> {
  const latest = new Map<string, CaseFact>();
  for (const fact of facts) {
    const held = latest.get(fact.factName);
    if (
      held === undefined
      || fact.recordedAt > held.recordedAt
      || (fact.recordedAt === held.recordedAt && fact.observedAt >= held.observedAt)
    ) {
      latest.set(fact.factName, fact);
    }
  }
  return latest;
}

/**
 * The bay recommendation, as `ReturnCaseWorkflow` records it on the case.
 *
 * **Best-effort by declared policy.** A case with no bay is the normal state of
 * a case whose workflow has not reached placement, or one where placement is
 * not configured -- `bay_reason` says which. It is not an error and must not be
 * rendered as one.
 *
 * Confidence is stored in millionths because the fact log holds no floats;
 * `confidence` below is the fraction, or null when nothing computed one. A
 * constant confidence would violate C2, so an absent one is reported absent
 * rather than defaulted to something that looks computed.
 */
export type BayRecommendation = {
  readonly warehouseReference: string | null;
  readonly bayReference: string | null;
  readonly returnLocation: string | null;
  readonly confidence: number | null;
  readonly reason: string | null;
  readonly evidenceReference: string | null;
  readonly capacityEvidence: string | null;
};

function factString(latest: ReadonlyMap<string, CaseFact>, name: string): string | null {
  const value = latest.get(name)?.value;
  return typeof value === "string" && value !== "" ? value : null;
}

export function bayRecommendation(facts: readonly CaseFact[]): BayRecommendation {
  const latest = latestFacts(facts);
  const millionths = latest.get("bay_confidence_millionths")?.value;
  return {
    warehouseReference: factString(latest, "bay_warehouse_reference"),
    bayReference: factString(latest, "bay_reference"),
    returnLocation: factString(latest, "bay_return_location"),
    confidence: typeof millionths === "number" ? millionths / 1_000_000 : null,
    reason: factString(latest, "bay_reason"),
    evidenceReference: factString(latest, "bay_evidence_reference"),
    capacityEvidence: factString(latest, "bay_capacity_evidence"),
  };
}

/** True when the workflow asked for a bay but nothing came back with a location. */
export function hasBayResult(bay: BayRecommendation): boolean {
  return bay.bayReference !== null || bay.returnLocation !== null;
}

export const casesApi = {
  /**
   * The caller's own cases, newest first.
   *
   * `conversationId` narrows to the one that conversation raised. That is how
   * a resumed conversation gets its return back: the case id arrives on the
   * turn that confirmed, and a reopened conversation has no such turn.
   */
  async list(conversationId?: string): Promise<CaseSummary[]> {
    const query =
      conversationId === undefined ? "" : `?conversationId=${encodeURIComponent(conversationId)}`;
    const response = await apiClient<CaseSummary[]>(`/api/cases${query}`);
    return response.data ?? [];
  },

  async read(caseId: string): Promise<CaseDetail> {
    const response = await apiClient<CaseDetail>(`/api/cases/${encodeURIComponent(caseId)}`);
    if (!response.data) throw new Error("The case could not be read.");
    return response.data;
  },
};
