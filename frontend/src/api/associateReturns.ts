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

export async function getAssociateConversation(
  conversationId: string,
  signal?: AbortSignal,
): Promise<AssociateConversation> {
  return requireData((await apiClient<AssociateConversation>(
    `/api/v1/associate-returns/conversations/${encodeURIComponent(conversationId)}`,
    { signal },
  )).data);
}

export async function startAssociateChat(payload: {
  message: string;
}): Promise<AssociateConversation> {
  return requireData((await apiClient<AssociateConversation>(
    "/api/v1/associate-returns/chat",
    jsonInit(payload),
  )).data);
}

export async function continueAssociateChat(payload: {
  conversationId: string;
  message: string;
  expectedVersion: number;
}): Promise<AssociateConversation> {
  return requireData((await apiClient<AssociateConversation>(
    `/api/v1/associate-returns/conversations/${encodeURIComponent(payload.conversationId)}/chat`,
    jsonInit({
      message: payload.message,
      expectedVersion: payload.expectedVersion,
    }),
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
  candidateSetId?: string | null;
}): Promise<AssociateConversation> {
  return requireData((await apiClient<AssociateConversation>(
    `/api/v1/associate-returns/conversations/${encodeURIComponent(payload.conversationId)}/confirm`,
    jsonInit({
      candidateIndex: payload.candidateIndex,
      orderLineId: payload.orderLineId,
      expectedVersion: payload.expectedVersion,
      candidateSetId: payload.candidateSetId ?? null,
    }),
  )).data);
}

export async function continueAssociateConversation(payload: {
  conversationId: string;
  anchorType: AnchorType;
  anchorValue: string;
  expectedVersion: number;
}): Promise<AssociateConversation> {
  return requireData((await apiClient<AssociateConversation>(
    `/api/v1/associate-returns/conversations/${encodeURIComponent(payload.conversationId)}/messages`,
    jsonInit({
      anchorType: payload.anchorType,
      anchorValue: payload.anchorValue,
      expectedVersion: payload.expectedVersion,
    }),
  )).data);
}

export async function submitAssociateReturnDetails(payload: {
  conversationId: string;
  reasonCode: string;
  returnQuantity: number;
  packageCount: number;
  shippingPathExpectation: "PREPAID_PARCEL" | "BRANCH_UPS" | "BRANCH_LTL" | "OFFSITE_PARCEL" | "OFFSITE_LTL" | "DIRECT_VENDOR" | "FIELD_SCRAP" | "NO_PHYSICAL_RETURN" | "CUSTOMER_KEEP";
  branchReference?: string;
  attachmentIds?: readonly string[];
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
      branchReference: payload.branchReference ?? null,
      attachmentIds: payload.attachmentIds ?? [],
      notes: payload.notes ?? null,
      expectedVersion: payload.expectedVersion,
    }),
  )).data);
}
