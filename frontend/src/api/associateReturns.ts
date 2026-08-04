import { APIError, apiClient } from "./client";
import type {
  AnchorType,
  AssociateConversation,
  AssociateSubmitResult,
} from "../contracts/associateReturns";

export const ASSOCIATE_RETURNS_V1_BASE = "/api/v1/associate-returns";

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

export async function listAssociateConversations(
  signal?: AbortSignal,
  basePath = ASSOCIATE_RETURNS_V1_BASE,
): Promise<readonly AssociateConversation[]> {
  return requireData((await apiClient<AssociateConversation[]>(
    `${basePath}/conversations`,
    { signal },
  )).data);
}

export async function getAssociateConversation(
  conversationId: string,
  signal?: AbortSignal,
  basePath = ASSOCIATE_RETURNS_V1_BASE,
): Promise<AssociateConversation> {
  return requireData((await apiClient<AssociateConversation>(
    `${basePath}/conversations/${encodeURIComponent(conversationId)}`,
    { signal },
  )).data);
}

export async function startAssociateChat(payload: {
  message: string;
}, basePath = ASSOCIATE_RETURNS_V1_BASE): Promise<AssociateConversation> {
  return requireData((await apiClient<AssociateConversation>(
    `${basePath}/chat`,
    jsonInit(payload),
  )).data);
}

export async function continueAssociateChat(payload: {
  conversationId: string;
  message: string;
  expectedVersion: number;
}, basePath = ASSOCIATE_RETURNS_V1_BASE): Promise<AssociateConversation> {
  return requireData((await apiClient<AssociateConversation>(
    `${basePath}/conversations/${encodeURIComponent(payload.conversationId)}/chat`,
    jsonInit({
      message: payload.message,
      expectedVersion: payload.expectedVersion,
    }),
  )).data);
}

export async function startAssociateConversation(payload: {
  anchorType: AnchorType;
  anchorValue: string;
}, basePath = ASSOCIATE_RETURNS_V1_BASE): Promise<AssociateConversation> {
  return requireData((await apiClient<AssociateConversation>(
    `${basePath}/conversations`,
    jsonInit(payload),
  )).data);
}

export async function confirmAssociateDiscovery(payload: {
  conversationId: string;
  candidateIndex: number;
  orderLineId: string;
  expectedVersion: number;
  candidateSetId?: string | null;
}, basePath = ASSOCIATE_RETURNS_V1_BASE): Promise<AssociateConversation> {
  const submit = async (
    conversation: Pick<AssociateConversation, "version" | "candidateSetId">,
    candidateIndex: number,
  ): Promise<AssociateConversation> => requireData((
    await apiClient<AssociateConversation>(
      `${basePath}/conversations/${encodeURIComponent(payload.conversationId)}/confirm`,
      jsonInit({
        candidateIndex,
        orderLineId: payload.orderLineId,
        expectedVersion: conversation.version,
        candidateSetId: conversation.candidateSetId ?? null,
      }),
    )
  ).data);

  try {
    return await submit({
      version: payload.expectedVersion,
      candidateSetId: payload.candidateSetId ?? null,
    }, payload.candidateIndex);
  } catch (error) {
    if (
      !(error instanceof APIError)
      || error.status !== 409
      || error.message !== "Conversation version conflict"
    ) {
      throw error;
    }

    const latest = await getAssociateConversation(payload.conversationId, undefined, basePath);
    if (latest.discoveryLock !== null) {
      return latest;
    }
    if (latest.candidateSetId !== (payload.candidateSetId ?? null)) {
      throw error;
    }

    const latestCandidateIndex = latest.candidates.findIndex((candidate) => (
      candidate.lines.some((line) => line.orderLineId === payload.orderLineId)
    ));
    if (latestCandidateIndex < 0) {
      throw error;
    }

    return submit(latest, latestCandidateIndex);
  }
}

export async function continueAssociateConversation(payload: {
  conversationId: string;
  anchorType: AnchorType;
  anchorValue: string;
  expectedVersion: number;
}, basePath = ASSOCIATE_RETURNS_V1_BASE): Promise<AssociateConversation> {
  return requireData((await apiClient<AssociateConversation>(
    `${basePath}/conversations/${encodeURIComponent(payload.conversationId)}/messages`,
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
}, basePath = ASSOCIATE_RETURNS_V1_BASE): Promise<AssociateSubmitResult> {
  return requireData((await apiClient<AssociateSubmitResult>(
    `${basePath}/conversations/${encodeURIComponent(payload.conversationId)}/details`,
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
