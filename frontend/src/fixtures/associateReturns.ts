import type { AssociateConversation, AssociateSubmitResult } from "../contracts/associateReturns";

export const FIXTURE_ASSOCIATE_CONVERSATIONS: AssociateConversation[] = [
  {
    id: "conv-mock-1001",
    status: "CANDIDATE_FOUND",
    anchorType: "ORDER_NUMBER",
    anchorValueMasked: "ORD-10001",
    messages: [
      {
        id: "msg-1",
        role: "ASSOCIATE",
        content: "ORD-10001",
        createdAt: new Date().toISOString(),
      },
      {
        id: "msg-2",
        role: "AI_ASSISTANT",
        content: "I found order ORD-10001 for customer John Ferguson (Acme Corp). Please confirm the items you are returning.",
        createdAt: new Date().toISOString(),
      },
    ],
    candidates: [
      {
        customerReference: "CUST-9910",
        customerName: "John Ferguson (Acme Corp)",
        orderReference: "ORD-10001",
        orderStatus: "SHIPPED",
        sellWarehouseId: "WH-ATL-01",
        shipFromWarehouseId: "WH-ATL-01",
        shippingMethod: "GROUND",
        confidenceMillionths: 990000,
        evidenceSource: "NEO4J_PRIMARY_INDEX",
        lines: [
          {
            orderLineId: "LINE-1",
            productId: "PROD-5501",
            sku: "SKU-10001",
            productDescription: "Industrial Faucet Assembly Grade-A",
            productType: "PLUMBING",
            shippedQuantity: 2,
          },
        ],
      },
    ],
    discoveryLock: null,
    returnDetails: null,
    returnSessionId: null,
    nextQuestion: null,
    version: 1,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  },
];

export const FIXTURE_CONFIRMED_CONVERSATION: AssociateConversation = {
  ...FIXTURE_ASSOCIATE_CONVERSATIONS[0],
  status: "DISCOVERY_CONFIRMED",
  discoveryLock: {
    customerReference: "CUST-9910",
    orderReference: "ORD-10001",
    orderLineId: "LINE-1",
    productId: "PROD-5501",
    lockDigest: "sha256-abc123mockdigest999",
    confirmedBy: "associate-reference",
    confirmedAt: new Date().toISOString(),
  },
  version: 2,
};

export const FIXTURE_SUBMIT_RESULT: AssociateSubmitResult = {
  conversation: {
    ...FIXTURE_ASSOCIATE_CONVERSATIONS[0],
    status: "SUBMITTED",
    returnSessionId: "ret-sess-10001",
    version: 2,
  },
  returnSession: {
    id: "ret-sess-10001",
    correlationId: "corr-1001",
    workflowId: "wf-1001",
    customerReference: "CUST-9910",
    orderReference: "ORD-10001",
    itemReferences: ["LINE-1"],
    productReferences: ["PROD-5501"],
    processingWarehouseReference: "WH-ATL-01",
    productType: "PLUMBING",
    reasonCode: "DAMAGED",
    returnQuantity: 1,
    packageCount: 1,
    shippingPathExpectation: "PPL",
    notes: "Damaged on arrival",
    channel: "ASSOCIATE_COPILOT",
    status: "COMPLETED",
    currentStage: "STAGE_3_COMPLETED",
    progressPercentage: 100,
    eligibilityDecision: "APPROVE",
    returnReference: "RET-ORD-10001",
    supportTicketReference: null,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  },
};
