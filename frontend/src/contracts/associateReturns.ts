import type { ReturnSession } from "./operations";

export type AnchorType =
  | "ORDER_NUMBER"
  | "CUSTOMER_ID"
  | "PHONE"
  | "EMAIL"
  | "TRACKING_NUMBER"
  | "SKU"
  | "CUSTOMER_NAME"
  | "PRODUCT_DESCRIPTION";

export type ConversationMessage = {
  readonly id: string;
  readonly role: string;
  readonly content: string;
  readonly createdAt: string;
};

export type OrderLineCandidate = {
  readonly orderLineId: string;
  readonly productId: string;
  readonly sku: string | null;
  readonly productDescription: string | null;
  readonly productType: string | null;
  readonly shippedQuantity: number | null;
};

export type OrderCandidate = {
  readonly customerReference: string;
  readonly customerName: string | null;
  readonly orderReference: string;
  readonly orderStatus: string | null;
  readonly sellWarehouseId: string | null;
  readonly shipFromWarehouseId: string | null;
  readonly shippingMethod: string | null;
  readonly billingCity: string | null;
  readonly postalCode: string | null;
  readonly accountType: string | null;
  readonly retrievalScore: number | null;
  readonly confidenceMillionths: number;
  readonly evidenceSource: string;
  readonly lines: readonly OrderLineCandidate[];
};

export type ClarificationPrompt = {
  readonly slot: string;
  readonly question: string;
  readonly options: readonly {
    readonly value: string;
    readonly label: string;
    readonly candidateCount: number;
  }[];
};

export type DiscoveryLock = {
  readonly customerReference: string;
  readonly orderReference: string;
  readonly orderLineId: string;
  readonly productId: string;
  readonly lockDigest: string;
  readonly confirmedBy: string;
  readonly confirmedAt: string;
};

export type AssociateConversation = {
  readonly id: string;
  readonly status: string;
  readonly anchorType: AnchorType;
  readonly anchorValueMasked: string;
  readonly messages: readonly ConversationMessage[];
  readonly candidates: readonly OrderCandidate[];
  readonly discoveryLock: DiscoveryLock | null;
  readonly returnDetails: Readonly<Record<string, unknown>> | null;
  readonly returnSessionId: string | null;
  readonly nextQuestion: string | null;
  readonly activeDialogueState: string;
  readonly activeRequestedSlots: readonly string[];
  readonly clarificationPrompt: ClarificationPrompt | null;
  readonly candidateSetId: string | null;
  readonly candidateSetExpiresAt: string | null;
  readonly configurationReleaseId: string | null;
  readonly configurationChecksum: string | null;
  readonly configurationSource: string;
  readonly version: number;
  readonly createdAt: string;
  readonly updatedAt: string;
};

export type AssociateSubmitResult = {
  readonly conversation: AssociateConversation;
  readonly returnSession: ReturnSession;
};
