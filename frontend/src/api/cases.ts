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

export type CaseDetail = {
  case: {
    caseId: string;
    tenantId: string;
    principalId: string;
    branchId: string | null;
    status: string;
    channelAConversationId: string | null;
    channelBWorkItemId: string | null;
    confirmedOrderReference: string | null;
    createdAt: string;
    updatedAt: string;
  };
  returnRecords: CaseReturnRecord[];
  /** Lines the associate named that no RMA covers yet. Never folded into the first record. */
  unassignedItems: CaseReturnItem[];
  facts: CaseFact[];
};

export const casesApi = {
  async list(): Promise<CaseSummary[]> {
    const response = await apiClient<CaseSummary[]>("/api/cases");
    return response.data ?? [];
  },

  async read(caseId: string): Promise<CaseDetail> {
    const response = await apiClient<CaseDetail>(`/api/cases/${encodeURIComponent(caseId)}`);
    if (!response.data) throw new Error("The case could not be read.");
    return response.data;
  },
};
