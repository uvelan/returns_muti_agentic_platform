/**
 * Case projections to render panes against.
 *
 * **Builders, not constants.** Every block on `CaseProjection` is nullable and
 * *absent rather than defaulted*, and that distinction is the whole audit: a
 * fixture that filled in a plausible shipment for every case would hide exactly
 * the states these tests exist to pin -- an RMA with no package, a bay with no
 * placement, a settlement with no producer. So the defaults here are the empty
 * ones, and a test that wants a value says so.
 *
 * The values that do appear are the shapes the audit found in the live
 * database, `RMA-OPS01-CD4364` above all: an RMA with a label and a null
 * tracking reference. None of them may leak into a component -- the fabrication
 * guard in `ReturnCopilotFabrication.test.ts` reads the source of every
 * non-test file under `domains/returns` and fails on any of it.
 */

import type {
  ApprovedItemProjection,
  CaseProjection,
  ConfirmedOrderProjection,
  CustomerProjection,
  PolicyEvaluationProjection,
  ReturnArtifactProjection,
  ReturnRecordProjection,
  SelectedItemProjection,
  SettlementProjection,
  ShipmentProjection,
  SupportProjection,
  WarehouseProjection,
} from "../../../api/cases";
import type { OrderLineView } from "../../../api/orderLines";
import type { AgentTurnResult } from "../../../api/orderAgent";

export const SAMPLE_TURN_DISCOVERY: AgentTurnResult = {
  conversation_id: "disc-test-1",
  conversation_version: 1,
  client_turn_id: "turn-disc-1",
  graph_generation_id: "gen-1",
  model_provider: "MOCK",
  model_name: "scripted",
  query_evidence: [],
  response: {
    status: "OK",
    business_capability: "order-discovery",
    statements: [
      {
        statement_id: "stmt-1",
        statement_type: "CLARIFICATION_QUESTION",
        text: "Could you provide the order reference or customer account number?",
      },
    ],
  },
};

/** Rows as the graph search hands them over: opaque records, no schema. */
export const SAMPLE_CANDIDATE_ROWS: readonly Record<string, unknown>[] = [
  {
    sales_order_number: "SO-A1",
    customer_id: "CUST-9012",
    account_id: "ACC-441",
    order_date: "2026-07-28",
    status: "DELIVERED",
  },
  {
    sales_order_number: "SO-A2",
    customer_id: "CUST-9012",
    account_id: "ACC-441",
    order_date: "2026-06-15",
    status: "DELIVERED",
  },
];

export function customer(
  overrides: Partial<CustomerProjection> = {},
): CustomerProjection {
  return {
    customerReference: "CUST-9012",
    accountReference: "ACC-441",
    displayName: "Melgon Heating",
    branchReference: "BR-01",
    ...overrides,
  };
}

export function confirmedOrder(
  overrides: Partial<ConfirmedOrderProjection> = {},
): ConfirmedOrderProjection {
  return {
    orderReference: "SO-A1",
    orderSource: "ORDER_GRAPH",
    sourceWebOrderNumber: null,
    trilogieOrderNumber: null,
    confirmationKey: null,
    candidateSetId: null,
    candidateId: null,
    confirmedAt: "2026-08-14T10:00:00Z",
    ...overrides,
  };
}

export function selectedItem(
  overrides: Partial<SelectedItemProjection> = {},
): SelectedItemProjection {
  return {
    returnItemId: "sel-1",
    orderLineReference: "L1",
    productReference: "PART-A",
    quantity: 1,
    reason: "DEFECTIVE",
    condition: "OPEN_BOX",
    packageReference: null,
    ...overrides,
  };
}

/**
 * One line of the confirmed order, as `GET /order-lines` serves it.
 *
 * `unitPrice` is `null` by default and stays that way in every test: the
 * reference extract carries prices on some lines and not others, and nothing in
 * the copilot may render one -- the case has no order total to put it against.
 * The four availability terms default to a line of two, all of it still
 * returnable, because a line with nothing available is the interesting case and
 * a test that wants it says so.
 */
export function orderLine(
  overrides: Partial<OrderLineView> = {},
): OrderLineView {
  return {
    lineReference: "L1",
    sku: "PART-A",
    description: "Line one",
    // Absent by default, like `unitPrice`: the source extract carries a colour
    // on some lines and not others, so a fixture that always had one would let
    // a screen assume it. A test that wants a colour says so.
    colour: null,
    orderedQuantity: 2,
    unitPrice: null,
    productReference: "PART-A",
    returnableQuantity: 2,
    completedReturnQuantity: 0,
    openAuthorizedQuantity: 0,
    activeReservationQuantity: 0,
    selfReservedQuantity: 0,
    dataInconsistency: null,
    ...overrides,
  };
}

export function approvedItem(
  overrides: Partial<ApprovedItemProjection> = {},
): ApprovedItemProjection {
  return {
    returnItemId: "app-1",
    orderLineReference: "L1",
    productReference: "PART-A",
    quantityApproved: 1,
    disposition: null,
    itemStatus: null,
    ...overrides,
  };
}

export function artifact(
  overrides: Partial<ReturnArtifactProjection> = {},
): ReturnArtifactProjection {
  return {
    artifactId: "art-1",
    artifactType: "SHIPPING_LABEL",
    shipmentId: null,
    fileName: "label-1.pdf",
    mediaType: "application/pdf",
    version: 1,
    active: true,
    supersededBy: null,
    expiresAt: null,
    createdAt: "2026-08-15T09:00:00Z",
    ...overrides,
  };
}

export function shipment(
  overrides: Partial<ShipmentProjection> = {},
): ShipmentProjection {
  return {
    shipmentId: "SHP-1",
    shipmentStatus: "AWAITING_HANDOFF",
    carrier: null,
    serviceLevel: null,
    trackingNumber: null,
    estimatedDeliveryAt: null,
    createdAt: "2026-08-15T09:00:00Z",
    updatedAt: "2026-08-15T09:00:00Z",
    ...overrides,
  };
}

export function returnRecord(
  overrides: Partial<ReturnRecordProjection> = {},
): ReturnRecordProjection {
  return {
    returnRecordId: "rec-1",
    returnReference: "RMA-OPS01-CD4364",
    status: "ISSUED",
    returnMethod: null,
    returnLocation: null,
    approvedItems: null,
    shipments: null,
    artifacts: null,
    ...overrides,
  };
}

/** The real record `4e372a39…`: an RMA issued, a label printed, no package. */
export function rmaWithLabelAndNoTracking(): ReturnRecordProjection {
  return returnRecord({
    returnRecordId: "4e372a39",
    returnReference: "RMA-OPS01-CD4364",
    status: "ISSUED",
    artifacts: [artifact({ artifactId: "LBL-OPS01", fileName: "LBL-OPS01.pdf" })],
  });
}

export function policyEvaluation(
  overrides: Partial<PolicyEvaluationProjection> = {},
): PolicyEvaluationProjection {
  return {
    route: "STANDARD_RETURN",
    originalDecision: "APPROVE",
    effectiveDecision: "APPROVE",
    override: null,
    reasonCodes: null,
    conditions: null,
    appliedRules: null,
    policyId: "release-under-test",
    policyVersion: "v3",
    evaluatedAt: "2026-08-15T09:00:00Z",
    // Absent by default, like every other block: the rate exists only once a
    // policy release configures `restocking_fee.seller_schedule` and the
    // workflow records it as evaluated. Declared here because `Served<T>` makes
    // every key required, so a `Partial` spread over a missing key widens it to
    // `| undefined` and the fixture stops matching the contract.
    rateBasisPoints: null,
    rateSource: null,
    ...overrides,
  };
}

export function support(overrides: Partial<SupportProjection> = {}): SupportProjection {
  return {
    workItemId: "WI-1",
    threadId: null,
    queue: "RETURNS_SUPPORT",
    status: "IN_PROGRESS",
    subject: null,
    priority: null,
    assignedTo: null,
    slaDueAt: null,
    openedAt: null,
    resolvedAt: null,
    ...overrides,
  };
}

export function warehouse(
  overrides: Partial<WarehouseProjection> = {},
): WarehouseProjection {
  return {
    facilityId: null,
    facilityName: null,
    bayId: null,
    bayReason: null,
    receivedAt: null,
    receivedQuantity: null,
    inspectionStatus: null,
    condition: null,
    disposition: null,
    qaStatus: null,
    warehouseStatus: null,
    ...overrides,
  };
}

export function settlement(
  overrides: Partial<SettlementProjection> = {},
): SettlementProjection {
  return {
    status: "NOT_INTEGRATED",
    creditMemoReference: null,
    settledAmount: null,
    settledAt: null,
    ...overrides,
  };
}

/**
 * A case, with every optional block absent unless a test asks for it.
 *
 * The derived four are supplied rather than computed -- the backend computes
 * them and the console is forbidden from re-deriving them -- so a fixture that
 * names a stage is naming what the platform said, which is exactly what the
 * screen is supposed to obey.
 */
export function caseProjection(overrides: Partial<CaseProjection> = {}): CaseProjection {
  return {
    caseId: "case-7",
    tenantId: "tenant-a",
    principalId: "tester",
    conversationId: "disc-old",
    status: "PROCESSING_RETURN",
    revision: 4,
    updatedAt: "2026-08-15T09:00:00Z",
    customer: null,
    confirmedOrder: null,
    selectedItems: null,
    facts: null,
    policyEvaluation: null,
    support: null,
    returnRecords: null,
    pickup: null,
    warehouse: null,
    settlement: null,
    stage: "DISCOVERY",
    awaiting: [],
    businessComplete: false,
    isTerminal: false,
    ...overrides,
  };
}
