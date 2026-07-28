import { afterEach, describe, expect, it, vi } from "vitest";

import type { AssociateConversation } from "../contracts/associateReturns";
import { confirmAssociateDiscovery } from "./associateReturns";

function envelope(data: AssociateConversation): Response {
  return new Response(JSON.stringify({
    data,
    meta: {
      schema_version: "1.0",
      request_id: "request-1",
      generated_at: "2026-07-28T11:00:00Z",
      freshness: "LIVE",
      partial: false,
      warnings: [],
    },
  }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function conversation(overrides: Partial<AssociateConversation> = {}): AssociateConversation {
  return {
    id: "conversation-1",
    status: "DISCOVERY_READY",
    anchorType: "CUSTOMER_NAME",
    anchorValueMasked: "Maya",
    messages: [],
    candidates: [{
      customerReference: "CUST-1",
      customerName: "Maya Foster",
      orderReference: "ORD-1",
      orderStatus: "DELIVERED",
      sellWarehouseId: null,
      shipFromWarehouseId: null,
      shippingMethod: null,
      billingCity: null,
      postalCode: null,
      accountType: null,
      retrievalScore: null,
      confidenceMillionths: 900_000,
      evidenceSource: "TEST",
      lines: [{
        orderLineId: "ORD-1:LINE:1",
        productId: "PRODUCT-1",
        sku: "SKU-1",
        productDescription: "Safety Sensor",
        productType: null,
        shippedQuantity: 1,
      }],
    }],
    discoveryLock: null,
    returnDetails: null,
    returnSessionId: null,
    activeDialogueState: "ORDER_DISCOVERY",
    activeRequestedSlots: [],
    clarificationPrompt: null,
    candidateSetId: "candidate-set-1",
    candidateSetExpiresAt: "2026-07-28T12:00:00Z",
    configurationReleaseId: null,
    configurationChecksum: null,
    configurationSource: "VERSION_CONTROLLED_BASELINE",
    nextQuestion: null,
    version: 5,
    createdAt: "2026-07-28T10:00:00Z",
    updatedAt: "2026-07-28T10:30:00Z",
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("confirmAssociateDiscovery", () => {
  it("returns the latest state when another request already confirmed the conversation", async () => {
    const confirmed = conversation({
      status: "DETAILS_REQUIRED",
      version: 6,
      discoveryLock: {
        customerReference: "CUST-1",
        orderReference: "ORD-1",
        orderLineId: "ORD-1:LINE:1",
        productId: "PRODUCT-1",
        lockDigest: "0".repeat(64),
        confirmedBy: "associate-1",
        confirmedAt: "2026-07-28T10:31:00Z",
      },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: "Conversation version conflict" }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ))
      .mockResolvedValueOnce(envelope(confirmed));
    vi.stubGlobal("fetch", fetchMock);

    const result = await confirmAssociateDiscovery({
      conversationId: "conversation-1",
      candidateIndex: 0,
      orderLineId: "ORD-1:LINE:1",
      expectedVersion: 5,
      candidateSetId: "candidate-set-1",
    });

    expect(result).toEqual(confirmed);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("retries once when the selected line remains in the current candidate set", async () => {
    const refreshed = conversation({ version: 6 });
    const confirmed = conversation({ status: "DETAILS_REQUIRED", version: 7 });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: "Conversation version conflict" }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ))
      .mockResolvedValueOnce(envelope(refreshed))
      .mockResolvedValueOnce(envelope(confirmed));
    vi.stubGlobal("fetch", fetchMock);

    const result = await confirmAssociateDiscovery({
      conversationId: "conversation-1",
      candidateIndex: 0,
      orderLineId: "ORD-1:LINE:1",
      expectedVersion: 5,
      candidateSetId: "candidate-set-1",
    });

    expect(result).toEqual(confirmed);
    const body = (fetchMock.mock.calls[2]?.[1] as RequestInit | undefined)?.body;
    expect(typeof body).toBe("string");
    const retryBody = JSON.parse(body as string) as { expectedVersion: number };
    expect(retryBody.expectedVersion).toBe(6);
  });
});
