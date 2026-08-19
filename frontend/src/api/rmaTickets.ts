/**
 * The Returns Support RMA ticket surface.
 *
 * Mirrors `api/rma-tickets` exactly. The create carries the whole workflow
 * agent assessment in one request, so the associate never retypes what the
 * agent established.
 */

import { apiClient } from "./client";

export type TicketStatus =
  | "DRAFT"
  | "SUBMITTED"
  | "CLARIFICATION_REQUIRED"
  | "RETURN_CREATED"
  | "REJECTED"
  | "CANCELLED"
  | "FAILED";

/** `dbo.return_tracking.tracking_type`, from that table's CHECK constraint. */
export type TrackingType =
  | "PPL"
  | "BOL"
  | "CUSTOMER_SHIP"
  | "NO_LABEL"
  | "DIRECT_VENDOR"
  | "FIELD_SCRAP";

export type RmaTicketItem = {
  readonly orderLineId: string;
  readonly productId: string;
  readonly requestedQuantity: number;
  readonly shippedQuantity?: number | null;
  readonly reasonCode: string;
  readonly condition?: string | null;
};

export type CreateRmaTicketRequest = {
  readonly sessionId: string;
  readonly orderReference: string;
  readonly customerReference?: string | null;
  readonly correlationId?: string | null;
  readonly recommendedReturnMethod: string;
  readonly productPresence?: string | null;
  readonly branchId?: string | null;
  readonly associateId: string;
  readonly photoEvidenceRequired: boolean;
  readonly supportDraft: string;
  readonly missingFields: readonly string[];
  readonly items: readonly RmaTicketItem[];
  readonly idempotencyKey: string;
};

export type RecordTrackingRequest = {
  readonly trackingReference: string;
  readonly trackingType: TrackingType;
  readonly carrierCode?: string | null;
  readonly trackingStatus: string;
  readonly eventAt?: string | null;
  readonly shipmentDetails?: string | null;
  readonly orderReference?: string | null;
};

export type TicketItemView = {
  readonly returnItemId: string;
  readonly orderLineId: string;
  readonly productId: string;
  readonly quantity: number;
  readonly reasonCode: string;
  readonly itemStatus: string;
};

export type TrackingView = {
  readonly trackingId: string;
  readonly trackingReference: string;
  readonly trackingType: string;
  readonly carrierCode: string | null;
  readonly trackingStatus: string;
  readonly eventAt: string | null;
  readonly shipmentDetails: string | null;
};

/**
 * What the source-shipment write did.
 *
 * Reported rather than assumed: this is the one place the platform writes to a
 * source system, and an associate should be able to see whether it happened.
 */
export type SourceShipmentEcho = {
  readonly attempted: boolean;
  readonly matchedDocument: string | null;
  readonly outcome: "INSERTED" | "UPDATED" | "SKIPPED" | "FAILED";
  readonly detail: string | null;
};

export type RmaTicketView = {
  readonly ticketId: string;
  readonly sessionId: string;
  readonly status: TicketStatus;
  readonly returnReference: string | null;
  readonly externalReference: string | null;
  readonly orderReference: string | null;
  readonly customerReference: string | null;
  readonly associateId: string | null;
  readonly recommendedReturnMethod: string | null;
  readonly supportDraft: string | null;
  readonly missingFields: readonly string[];
  readonly photoEvidenceRequired: boolean;
  readonly items: readonly TicketItemView[];
  readonly tracking: readonly TrackingView[];
  readonly createdAt: string | null;
  readonly updatedAt: string | null;
};

export type CreateRmaTicketResult = {
  readonly ticket: RmaTicketView;
  readonly outcome: "CREATED" | "DUPLICATE";
};

export type RecordTrackingResult = {
  readonly ticket: RmaTicketView;
  readonly outcome: "INSERTED" | "UPDATED";
  readonly sourceShipment: SourceShipmentEcho;
};

const BASE = "/api/rma-tickets";

function json(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

function required<T>(value: T | null | undefined): T {
  if (value === null || value === undefined) {
    throw new Error("The RMA ticket service returned no data.");
  }
  return value;
}

export const rmaTicketsApi = {
  async list(limit = 50): Promise<readonly RmaTicketView[]> {
    const response = await apiClient<RmaTicketView[]>(`${BASE}?limit=${String(limit)}`);
    return response.data ?? [];
  },
  async get(sessionId: string): Promise<RmaTicketView> {
    const response = await apiClient<RmaTicketView>(`${BASE}/${encodeURIComponent(sessionId)}`);
    return required(response.data);
  },
  async create(input: CreateRmaTicketRequest): Promise<CreateRmaTicketResult> {
    const response = await apiClient<CreateRmaTicketResult>(BASE, json("POST", input));
    return required(response.data);
  },
  async recordTracking(
    sessionId: string,
    input: RecordTrackingRequest,
  ): Promise<RecordTrackingResult> {
    const response = await apiClient<RecordTrackingResult>(
      `${BASE}/${encodeURIComponent(sessionId)}/tracking`,
      json("POST", input),
    );
    return required(response.data);
  },
  async setStatus(
    sessionId: string,
    status: TicketStatus,
    clarification?: string,
  ): Promise<RmaTicketView> {
    const response = await apiClient<RmaTicketView>(
      `${BASE}/${encodeURIComponent(sessionId)}/status`,
      json("POST", { status, clarification }),
    );
    return required(response.data);
  },
};
