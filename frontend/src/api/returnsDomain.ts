/**
 * `/api/returns` -- the canonical Return Business Copilot surface (Phase 16).
 *
 * Seven reads and two writes: the whole return aggregate on one surface. Field
 * names are camelCase to match the backend contract.
 *
 * **There is no `cancel`, and that is not an omission.** Cancellation is
 * `recordEvent(id, {eventType: "CANCELLED"})`. The legacy pair disagreed --
 * `POST /cancel` wrote the session document and released the discovery lock
 * without telling the workflow, while the workflow's CANCELLED event left the
 * lock held -- and the backend resolved it by making the event do both. A
 * `cancel()` helper here would re-introduce the idea that they are separate
 * things.
 */

import { apiClient } from "./client";

export type ReturnStatus =
  | "QUEUED"
  | "RUNNING"
  | "INTERCEPTION_PENDING"
  | "REVIEW_REQUIRED"
  | "WAITING_SUPPORT"
  | "WAITING_EXTERNAL"
  | "APPROVED"
  | "REJECTED"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type ReturnSessionView = {
  readonly id: string;
  readonly correlationId: string;
  readonly customerReference: string;
  readonly orderReference: string;
  readonly itemReferences: readonly string[];
  readonly productReferences: readonly string[];
  readonly processingWarehouseReference: string | null;
  readonly reasonCode: string;
  readonly returnQuantity: number;
  readonly packageCount: number;
  readonly shippingPathExpectation: string;
  readonly orderSource: string;
  readonly channel: string;
  readonly status: ReturnStatus;
  readonly currentStage: string;
  readonly progressPercentage: number;
  readonly returnReference: string | null;
  readonly supportTicketReference: string | null;
  readonly supportStatus: string | null;
  readonly approvedReturnMethod: string | null;
  readonly customerResolutionStatus: string;
  readonly physicalReturnStatus: string;
  readonly warehouseStatus: string;
  readonly vendorRecoveryStatus: string;
  readonly caseClosureStatus: string;
  readonly trackingReference: string | null;
  readonly bayReference: string | null;
  readonly aiRequestId: string | null;
  readonly failureCode: string | null;
  readonly failureMessage: string | null;
  readonly notes: string | null;
  readonly version: number;
  readonly createdAt: string;
  readonly updatedAt: string;
};

export type TimelineEvent = {
  readonly id: string;
  readonly streamId: string;
  readonly sequence: number;
  readonly eventType: string;
  readonly actorType: string;
  readonly actorId: string;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly occurredAt: string;
  readonly publishedAt: string | null;
};

/**
 * The evidence-carrying commands. Mirrors `ProductionReturnEventType`.
 *
 * Enumerated rather than typed as `string` so a misspelling is a compile error
 * instead of a 422 discovered by a user. The list is deliberately flat and
 * complete: which of these is *legal right now* depends on preconditions the
 * state machine owns, and reproducing that here would be a second copy of
 * `_validate_transition` that drifts. The backend answers 409 with the reason;
 * the screen shows it.
 */
export const RETURN_EVENT_TYPES = [
  "DISCOVERY_CONFIRMED",
  "RETURN_DETAILS_CONFIRMED",
  "SUPPORT_REQUEST_CREATED",
  "SUPPORT_ACKNOWLEDGED",
  "OMC_RETURN_CREATED",
  "SHIPPING_INSTRUCTIONS_ISSUED",
  "BOL_TENDERED",
  "CARRIER_BOOKING_CONFIRMED",
  "PHYSICAL_HANDOFF_CONFIRMED",
  "PHYSICAL_RETURN_NOT_REQUIRED",
  "RECEIPT_CONFIRMED",
  "LICENSE_PLATE_NOT_REQUIRED",
  "WAREHOUSE_PROCESSING_NOT_REQUIRED",
  "LICENSE_PLATE_ASSIGNED",
  "CUSTOMER_RESOLUTION_COMPLETED",
  "PRODUCT_DISPOSITION_COMPLETED",
  "WAREHOUSE_PROCESSING_COMPLETED",
  "VENDOR_RECOVERY_REQUIRED",
  "VENDOR_RECOVERY_COMPLETED",
  "CANCELLED",
] as const;

export type ReturnEventType = (typeof RETURN_EVENT_TYPES)[number];

export type ReturnEventRequest = {
  readonly eventId: string;
  readonly eventType: ReturnEventType;
  readonly evidenceReference: string;
  readonly businessPayload?: Readonly<Record<string, unknown>>;
};

export type ReturnEventResult = {
  readonly stage: string;
  readonly caseFullyClosed: boolean;
  readonly cancelled: boolean;
};

export type ReturnEvidence = {
  readonly returnItems: readonly Record<string, unknown>[];
  readonly handlingUnits: readonly Record<string, unknown>[];
  readonly pickup: Readonly<Record<string, unknown>> | null;
  readonly branchStaging: readonly Record<string, unknown>[];
  readonly documentArtifacts: readonly Record<string, unknown>[];
  readonly shippingInstructions: readonly Record<string, unknown>[];
  readonly shipmentEvents: readonly Record<string, unknown>[];
  readonly omcCommands: readonly Record<string, unknown>[];
  readonly integrationCommands: readonly Record<string, unknown>[];
  readonly vendorReturnLinks: readonly Record<string, unknown>[];
  readonly agentDecisions: readonly Record<string, unknown>[];
};

/**
 * Two records, not one, and both nullable.
 *
 * `case` is raised by the platform when a flow fails; `workItem` is opened by a
 * person in the support workbench. Merging them in the view would lose which of
 * the two an operator is looking at, and only one of them means somebody is
 * already working it.
 */
export type ReturnSupport = {
  readonly case: Readonly<Record<string, unknown>> | null;
  readonly workItem: Readonly<Record<string, unknown>> | null;
};

async function unwrap<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiClient<T>(path, init);
  if (response.data === undefined || response.data === null) {
    throw new Error(`No data returned from ${path}.`);
  }
  return response.data;
}

/** `null` is a real answer on `/conversation`, so it cannot go through `unwrap`. */
async function unwrapNullable<T>(path: string): Promise<T | null> {
  const response = await apiClient<T | null>(path);
  return response.data ?? null;
}

export const returnsApi = {
  list: () => unwrap<ReturnSessionView[]>("/api/returns"),
  get: (sessionId: string) => unwrap<ReturnSessionView>(`/api/returns/${sessionId}`),
  timeline: (sessionId: string) => unwrap<TimelineEvent[]>(`/api/returns/${sessionId}/timeline`),
  artifacts: (sessionId: string) =>
    unwrap<Record<string, unknown>[]>(`/api/returns/${sessionId}/artifacts`),
  evidence: (sessionId: string) => unwrap<ReturnEvidence>(`/api/returns/${sessionId}/evidence`),
  support: (sessionId: string) => unwrap<ReturnSupport>(`/api/returns/${sessionId}/support`),

  /**
   * `null` when the return did not come from a discovery conversation -- a
   * SYSTEM-channel return never will. Distinct from a 404, which means no such
   * return.
   */
  conversation: (sessionId: string) =>
    unwrapNullable<Record<string, unknown>>(`/api/returns/${sessionId}/conversation`),

  /**
   * The only way to move a return, including cancelling it.
   *
   * `eventId` is the idempotency key: re-sending the same one is a no-op rather
   * than a second application, which is what makes a retry after a timeout safe.
   */
  recordEvent: (sessionId: string, payload: ReturnEventRequest) =>
    unwrap<ReturnEventResult>(`/api/returns/${sessionId}/events`, {
      method: "POST",
      // `createHeaders` sets Accept but not Content-Type, so every JSON POST in
      // this codebase declares its own. Omitting it makes FastAPI reject the
      // body as a missing field rather than as a bad content type.
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
};
