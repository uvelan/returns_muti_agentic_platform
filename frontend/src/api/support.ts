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

/**
 * One RMA as Support is issuing it -- `return_support.py::ReturnOutcomeRecord`.
 *
 * Every optional field here is a property of *this RMA*, not of the case, which
 * is contract C3 and is why they are on the record: one reply can issue several
 * RMAs with different labels going to different places.
 */
export type ReturnOutcomeRecordInput = {
  returnReference: string;
  trackingReference?: string;
  labelReference?: string;
  returnLocation?: string;
  shippingInstructionReference?: string;
  /**
   * The order lines this RMA covers. The other half of "N RMAs, N items".
   *
   * Required, matching the API. It was optional here and defaulted to `()`
   * server-side, so an RMA could be issued covering nothing -- and the record it
   * produced had no items, which is how five return records came to read
   * `ISSUED` over an empty item list.
   */
  orderLineReferences: string[];
};

export type ReturnOutcomeInput = {
  records: ReturnOutcomeRecordInput[];
  rejected?: boolean;
  reason?: string;
  /**
   * The identity of *this* Support answer. Required, never optional.
   *
   * The backend refuses the write without it (`422 SUPPORT_EVENT_ID_REQUIRED`)
   * and will not mint one itself, because a server-minted id is a fresh id on
   * every retry and therefore no idempotency at all. Typed as required here so
   * the compiler, not a 422 in production, is what catches a caller that forgot.
   */
  supportEventId: string;
};

/**
 * A stable identity for one Support answer.
 *
 * **Call this at the user-action boundary, never inside a request function or a
 * render.** Everything the id buys depends on where it is minted: generated per
 * send, a resend after a lost response is a second RMA; generated per render,
 * so is a re-render. Minted once when the operator opens the form and carried
 * through the mutation's variables, a React Query retry, a double click and a
 * manual resend after a timeout all reuse it and collapse into one event, while
 * a deliberately new answer -- a new form, a new business act -- is a new id.
 *
 * Same shape as `orderAgent.ts::sendTurn`'s turn id, and for the same reason.
 */
export function newSupportEventId(workItemId: string): string {
  return `ui-${workItemId}-${crypto.randomUUID()}`;
}

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

  /**
   * Send Support's answer back to the case.
   *
   * Distinct from `act` on purpose: `act` records an outcome on the work item
   * itself, this one signals the case's workflow, which is what carries the RMA
   * into the associate's original conversation. A list of records, because one
   * reply can issue several RMAs.
   *
   * The call is durable rather than synchronous: it commits a Support event and
   * an outbox command in one transaction and returns, and the outbox delivers
   * to the workflow at least once afterwards. `input.supportEventId` is what
   * makes that redelivery -- and any resend from here -- exactly one business
   * mutation, so it is a caller's value and is deliberately not defaulted here.
   */
  async submitReturnOutcome(workItemId: string, input: ReturnOutcomeInput): Promise<void> {
    await apiClient(
      `/api/v1/return-support/work-items/${encodeURIComponent(workItemId)}/return-outcome`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    );
  },

  async act(workItemId: string, input: SupportActionInput): Promise<void> {
    await apiClient(`/api/v1/return-support/work-items/${encodeURIComponent(workItemId)}/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
  },
};
