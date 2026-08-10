/**
 * `/api/returns` -- the canonical Return Business Copilot surface (Phase 16).
 *
 * Three read routes and no writes. The write surface is deliberately held
 * back until the nine legacy return routers are reconciled, so nothing here
 * mutates a session. Field names are camelCase to match the backend contract.
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

async function unwrap<T>(path: string): Promise<T> {
  const response = await apiClient<T>(path);
  if (response.data === undefined || response.data === null) {
    throw new Error(`No data returned from ${path}.`);
  }
  return response.data;
}

export const returnsApi = {
  list: () => unwrap<ReturnSessionView[]>("/api/returns"),
  get: (sessionId: string) => unwrap<ReturnSessionView>(`/api/returns/${sessionId}`),
  timeline: (sessionId: string) =>
    unwrap<TimelineEvent[]>(`/api/returns/${sessionId}/timeline`),
};
