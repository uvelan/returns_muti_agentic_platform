import { apiClient } from "./client";
import type {
  AnchorType,
  AssociateConversation,
  AssociateSubmitResult,
} from "../contracts/associateReturns";

function requireData<T>(value: T | null): T {
  if (value === null) throw new Error("The API returned no data.");
  return value;
}

function jsonInit(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export async function listAssociateConversations(signal?: AbortSignal): Promise<readonly AssociateConversation[]> {
  return requireData((await apiClient<AssociateConversation[]>(
    "/api/v1/associate-returns/conversations",
    { signal },
  )).data);
}

export async function startAssociateConversation(payload: {
  anchorType: AnchorType;
  anchorValue: string;
}): Promise<AssociateConversation> {
  return requireData((await apiClient<AssociateConversation>(
    "/api/v1/associate-returns/conversations",
    jsonInit(payload),
  )).data);
}

export async function confirmAssociateDiscovery(payload: {
  conversationId: string;
  candidateIndex: number;
  orderLineId: string;
  expectedVersion: number;
}): Promise<AssociateConversation> {
  return requireData((await apiClient<AssociateConversation>(
    `/api/v1/associate-returns/conversations/${encodeURIComponent(payload.conversationId)}/confirm`,
    jsonInit({
      candidateIndex: payload.candidateIndex,
      orderLineId: payload.orderLineId,
      expectedVersion: payload.expectedVersion,
    }),
  )).data);
}

export async function submitAssociateReturnDetails(payload: {
  conversationId: string;
  reasonCode: string;
  returnQuantity: number;
  packageCount: number;
  shippingPathExpectation: "PPL" | "BOL" | "CUSTOMER_SHIP" | "NO_LABEL" | "DIRECT_VENDOR" | "FIELD_SCRAP";
  notes?: string;
  expectedVersion: number;
}): Promise<AssociateSubmitResult> {
  return requireData((await apiClient<AssociateSubmitResult>(
    `/api/v1/associate-returns/conversations/${encodeURIComponent(payload.conversationId)}/details`,
    jsonInit({
      reasonCode: payload.reasonCode,
      returnQuantity: payload.returnQuantity,
      packageCount: payload.packageCount,
      shippingPathExpectation: payload.shippingPathExpectation,
      notes: payload.notes ?? null,
      expectedVersion: payload.expectedVersion,
    }),
  )).data);
}
