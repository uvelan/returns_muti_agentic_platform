import { apiClient } from "./client";

/**
 * Channel B: where the platform talks to Returns Support.
 *
 * The backend for this has existed since Wave C -- threads, messages,
 * idempotency, transactions -- with no operator surface at all, so no human
 * could play the Support role and no return could be driven end to end.
 *
 * Teams is not connected. When it is, this screen becomes one transport among
 * two rather than dead code: the thread is the same thread either way.
 */

export type SupportWorkItem = {
  id: string;
  sessionId: string | null;
  caseId: string | null;
  threadId: string;
  status: string;
  priority: string;
  queue: string;
  subject: string;
  assignedTo: string | null;
  returnReference: string | null;
  shippingInstructionReference: string | null;
  slaDueAt: string;
  version: number;
  createdAt: string;
  updatedAt: string;
};

export type SupportMessage = {
  id: string;
  threadId: string;
  sequence: number;
  senderRole: string;
  senderId: string;
  messageType: string;
  messageText: string;
  businessPayload: Record<string, unknown>;
  createdAt: string;
};

export type SupportActionInput = {
  action: string;
  expectedVersion: number;
  reason: string;
  returnReference?: string;
  shippingInstructionReference?: string;
  shippingInstructionType?: string;
  carrier?: string;
  trackingNumbers?: string[];
};

export const supportApi = {
  async listWorkItems(status?: string): Promise<SupportWorkItem[]> {
    const query = status === undefined || status === "" ? "" : `?status=${encodeURIComponent(status)}`;
    const response = await apiClient<SupportWorkItem[]>(`/api/v1/return-support/work-items${query}`);
    return response.data ?? [];
  },

  async readWorkItem(workItemId: string): Promise<SupportWorkItem> {
    const response = await apiClient<SupportWorkItem>(
      `/api/v1/return-support/work-items/${encodeURIComponent(workItemId)}`,
    );
    if (!response.data) throw new Error("The support work item could not be read.");
    return response.data;
  },

  async listMessages(workItemId: string): Promise<SupportMessage[]> {
    const response = await apiClient<SupportMessage[]>(
      `/api/v1/return-support/work-items/${encodeURIComponent(workItemId)}/messages`,
    );
    return response.data ?? [];
  },

  async reply(
    workItemId: string,
    input: { messageText: string; expectedVersion: number; messageType?: string },
  ): Promise<void> {
    await apiClient(`/api/v1/return-support/work-items/${encodeURIComponent(workItemId)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messageType: input.messageType ?? "COMMENT",
        messageText: input.messageText,
        // The backend rejects a write built on a stale view rather than
        // silently clobbering, so the version the reader saw must travel back.
        expectedVersion: input.expectedVersion,
      }),
    });
  },

  async act(workItemId: string, input: SupportActionInput): Promise<void> {
    await apiClient(`/api/v1/return-support/work-items/${encodeURIComponent(workItemId)}/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
  },
};
