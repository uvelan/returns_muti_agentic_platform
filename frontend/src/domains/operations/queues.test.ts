import { describe, expect, it } from "vitest";

import type { ReturnSessionView, ReturnStatus } from "../../api/returnsDomain";
import { QUEUES, isClosed } from "./queues";

function session(overrides: Partial<ReturnSessionView> = {}): ReturnSessionView {
  return {
    id: "s1",
    correlationId: "c1",
    customerReference: "CUST-1",
    orderReference: "ORD-1",
    itemReferences: [],
    productReferences: [],
    processingWarehouseReference: null,
    reasonCode: "DAMAGED",
    returnQuantity: 1,
    packageCount: 1,
    shippingPathExpectation: "CARRIER",
    orderSource: "WEB",
    channel: "WEB",
    status: "RUNNING",
    currentStage: "INTAKE",
    progressPercentage: 10,
    returnReference: null,
    supportTicketReference: null,
    supportStatus: null,
    approvedReturnMethod: null,
    customerResolutionStatus: "PENDING",
    physicalReturnStatus: "NOT_STARTED",
    warehouseStatus: "NOT_REQUIRED_OR_PENDING",
    vendorRecoveryStatus: "NOT_REQUIRED_OR_PENDING",
    caseClosureStatus: "OPEN",
    trackingReference: null,
    bayReference: null,
    aiRequestId: null,
    failureCode: null,
    failureMessage: null,
    notes: null,
    version: 1,
    createdAt: "2026-08-10T00:00:00Z",
    updatedAt: "2026-08-10T00:00:00Z",
    ...overrides,
  };
}

function queue(id: string) {
  const found = QUEUES.find((q) => q.id === id);
  if (!found) throw new Error(`No such queue: ${id}`);
  return found;
}

describe("closed detection", () => {
  it.each<ReturnStatus>(["COMPLETED", "REJECTED", "CANCELLED", "FAILED"])(
    "treats %s as closed",
    (status) => {
      expect(isClosed(session({ status }))).toBe(true);
    },
  );

  it("treats an explicitly closed case as closed even while running", () => {
    // Terminal status and case closure are separate facts; either one closes
    // the return for queueing purposes.
    expect(isClosed(session({ status: "RUNNING", caseClosureStatus: "CLOSED" }))).toBe(true);
  });

  it("leaves an in-flight return open", () => {
    expect(isClosed(session())).toBe(false);
  });
});

describe("queue membership", () => {
  it("puts an in-flight return in My Returns and not in Closed", () => {
    const s = session();
    expect(queue("mine").match(s)).toBe(true);
    expect(queue("closed").match(s)).toBe(false);
  });

  it("routes a support-blocked return to Support", () => {
    expect(queue("support").match(session({ status: "WAITING_SUPPORT" }))).toBe(true);
    expect(queue("support").match(session({ supportTicketReference: "TCK-1" }))).toBe(true);
    expect(queue("support").match(session({ status: "REVIEW_REQUIRED" }))).toBe(true);
  });

  it("routes a warehouse-involved return to Warehouse", () => {
    expect(queue("warehouse").match(session({ warehouseStatus: "RECEIVING" }))).toBe(true);
    expect(queue("warehouse").match(session({ bayReference: "BAY-7" }))).toBe(true);
  });

  it("never shows a closed return in an active queue", () => {
    // A completed return with a support ticket must not linger in Support --
    // otherwise the queue grows without bound as returns finish.
    const closed = session({
      status: "COMPLETED",
      supportTicketReference: "TCK-1",
      warehouseStatus: "RECEIVING",
    });
    expect(queue("mine").match(closed)).toBe(false);
    expect(queue("support").match(closed)).toBe(false);
    expect(queue("warehouse").match(closed)).toBe(false);
    expect(queue("closed").match(closed)).toBe(true);
  });

  it("puts every session in at least one queue", () => {
    const cases = [
      session(),
      session({ status: "WAITING_SUPPORT" }),
      session({ warehouseStatus: "RECEIVING" }),
      session({ status: "COMPLETED" }),
    ];
    for (const s of cases) {
      expect(QUEUES.some((q) => q.match(s))).toBe(true);
    }
  });
});
