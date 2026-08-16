/**
 * The case read contract: when to ask again, and which answer to believe.
 *
 * These are the two decisions Phase 8 owns, and both used to be made from the
 * wrong evidence. Polling stopped because an array had become non-empty, and a
 * response was applied because it was the most recent one to arrive rather than
 * the newest one written. Neither is a rendering question, so neither is tested
 * through the DOM: `ReturnCopilotReload.test.tsx` asserts the screen is wired to
 * these, and these assert what being wired to them means.
 */

import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CASE_POLL_INTERVAL_MS,
  caseLifecycle,
  caseRefetchInterval,
  caseRetry,
  keepNewerRevision,
  type CaseProjection,
  type ReturnRecordProjection,
} from "./cases";
import { APIError } from "./client";

function projection(overrides: Partial<CaseProjection> = {}): CaseProjection {
  return {
    caseId: "case-1",
    tenantId: "tenant-a",
    principalId: "tester",
    conversationId: "disc-1",
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
    stage: "AUTHORIZED_RMA",
    awaiting: ["TRACKING", "LABEL"],
    businessComplete: false,
    isTerminal: false,
    ...overrides,
  };
}

/**
 * The real record `4e372a39...`: an RMA, a prepaid label, and no package.
 *
 * The label has `shipmentId: null` because no shipment exists to attribute it
 * to -- that is the shape the contract was amended to express, and it is
 * unrepresentable in the nested one. `trackingNumber` is null on the shipment
 * the platform *has* not created, so there is no shipment here at all.
 */
function rmaWithoutTracking(): ReturnRecordProjection {
  return {
    returnRecordId: "4e372a39",
    returnReference: "RMA-OPS01-CD4364",
    status: "ISSUED",
    returnMethod: "PREPAID_PARCEL",
    returnLocation: null,
    approvedItems: null,
    shipments: null,
    artifacts: [
      {
        artifactId: "LBL-OPS01",
        artifactType: "SHIPPING_LABEL",
        shipmentId: null,
        fileName: null,
        mediaType: null,
        version: 1,
        active: true,
        supersededBy: null,
        expiresAt: null,
        createdAt: "2026-08-15T09:00:00Z",
      },
    ],
  };
}

describe("when to ask the platform again", () => {
  it("keeps asking while the case is not terminal", () => {
    expect(caseRefetchInterval(projection({ isTerminal: false }))).toBe(CASE_POLL_INTERVAL_MS);
  });

  it("stops once the case is terminal", () => {
    expect(
      caseRefetchInterval(projection({ status: "COMPLETED", stage: "COMPLETED", isTerminal: true })),
    ).toBe(false);
  });

  it("keeps asking about an RMA that has no tracking and no label", () => {
    // The audit's defect, stated as directly as it can be stated. The shipped
    // predicate was `returnRecords.length > 0`, so this exact case -- an RMA
    // issued, nothing tendered, no tracking number in existence -- stopped the
    // client dead the moment the RMA appeared. The associate watched a return
    // that was still being worked and saw nothing further arrive, ever.
    const stuck = projection({
      status: "PROCESSING_RETURN",
      stage: "AUTHORIZED_RMA",
      returnRecords: [
        { ...rmaWithoutTracking(), artifacts: null },
      ],
      awaiting: ["TRACKING", "LABEL"],
      isTerminal: false,
    });

    expect(stuck.returnRecords).toHaveLength(1);
    expect(caseRefetchInterval(stuck)).toBe(CASE_POLL_INTERVAL_MS);
  });

  it("keeps asking about an RMA whose label exists but whose package does not", () => {
    // One requirement satisfied is not completion. The label is real and
    // attributed to no shipment, tracking is still outstanding, and the case
    // must stay live until it is not.
    const halfDone = projection({
      returnRecords: [rmaWithoutTracking()],
      awaiting: ["TRACKING"],
      isTerminal: false,
    });

    expect(halfDone.returnRecords?.[0].artifacts?.[0].shipmentId).toBeNull();
    expect(caseRefetchInterval(halfDone)).toBe(CASE_POLL_INTERVAL_MS);
  });

  it("stops on a rejected case, which is finished and never business-complete", () => {
    // `businessComplete` is the wrong signal in the other direction:
    // POLICY_REJECTED can never be true, so a client keyed on it would poll a
    // dead case for as long as the tab stayed open.
    const rejected = projection({
      status: "POLICY_REJECTED",
      stage: "COMPLETED",
      awaiting: ["POLICY"],
      businessComplete: false,
      isTerminal: true,
    });

    expect(rejected.businessComplete).toBe(false);
    expect(caseRefetchInterval(rejected)).toBe(false);
  });

  it("stops on a cancelled case for the same reason", () => {
    expect(
      caseRefetchInterval(
        projection({ status: "CANCELLED", businessComplete: false, isTerminal: true }),
      ),
    ).toBe(false);
  });

  it("keeps asking about a case awaiting recovery", () => {
    // Deliberately non-terminal. Recovery restarts processing, and a client
    // that stopped here would go quiet across the exact interval the case was
    // being repaired in.
    const recovering = projection({
      status: "RECOVERY_REQUIRED",
      awaiting: ["RECOVERY"],
      businessComplete: false,
      isTerminal: false,
    });

    expect(caseRefetchInterval(recovering)).toBe(CASE_POLL_INTERVAL_MS);
  });

  it("keeps asking when the payload carries no lifecycle at all", () => {
    // The legacy `CaseDetail` body, which is what the route still serves. Not
    // knowing whether a case is finished is not the same as knowing it is, and
    // guessing "finished" is the audit's defect wearing the swap as a costume.
    const legacy = { case: { caseId: "case-1" }, returnRecords: [], unassignedItems: [], facts: [] };

    expect(caseRefetchInterval(legacy)).toBe(CASE_POLL_INTERVAL_MS);
    expect(caseRefetchInterval(undefined)).toBe(CASE_POLL_INTERVAL_MS);
  });
});

describe("a case the caller may not read", () => {
  // The case id is in the URL now, which makes it shareable -- and a shareable
  // id is one that arrives stale, from another tenant, or after the case was
  // archived. 403 and 404 will be the same answer in ten seconds.
  it("stops asking after a refusal", () => {
    expect(caseRefetchInterval(undefined, new APIError("No such case.", 404))).toBe(false);
    expect(caseRefetchInterval(undefined, new APIError("Not yours.", 403))).toBe(false);
  });

  it("keeps asking after a server-side failure, which may pass", () => {
    expect(caseRefetchInterval(undefined, new APIError("Upstream is down.", 502))).toBe(
      CASE_POLL_INTERVAL_MS,
    );
  });

  it("does not retry a refusal and bounds everything else", () => {
    expect(caseRetry(0, new APIError("No such case.", 404))).toBe(false);
    expect(caseRetry(0, new APIError("Upstream is down.", 502))).toBe(true);
    expect(caseRetry(2, new Error("socket hang up"))).toBe(true);
    expect(caseRetry(3, new Error("socket hang up"))).toBe(false);
  });
});

describe("reading the lifecycle envelope", () => {
  it("reads it off a projection", () => {
    expect(caseLifecycle(projection())).toEqual({
      revision: 4,
      updatedAt: "2026-08-15T09:00:00Z",
      stage: "AUTHORIZED_RMA",
      awaiting: ["TRACKING", "LABEL"],
      businessComplete: false,
      isTerminal: false,
    });
  });

  it("refuses a payload that has no envelope", () => {
    expect(caseLifecycle({ case: { caseId: "case-1" } })).toBeNull();
    expect(caseLifecycle(null)).toBeNull();
    expect(caseLifecycle([])).toBeNull();
  });

  it("refuses a stage or dimension this client does not know", () => {
    // Both enums are frozen by plan sect. 6.2. A member the console has never
    // heard of means the contract moved without the client, and rendering a
    // half-understood lifecycle is worse than reporting none.
    expect(caseLifecycle({ ...projection(), stage: "SHIPPED" })).toBeNull();
    expect(caseLifecycle({ ...projection(), awaiting: ["SETTLEMENT"] })).toBeNull();
  });
});

describe("which answer to believe", () => {
  it("discards a response carrying a lower revision", () => {
    const held = projection({ revision: 18, stage: "AUTHORIZED_RMA" });
    const late = projection({ revision: 17, stage: "ITEM_SELECTION" });

    expect(keepNewerRevision(held, late)).toBe(held);
  });

  it("does not treat an equal revision as stale", () => {
    // The revision invariant of plan sect. 6.5 is only partly in place: not
    // every child writer bumps the case yet, and the assembler reads the case
    // document first so the reported revision is never *newer* than the data.
    // A same-revision response is therefore a legitimate carrier of new
    // children -- the label that arrived without a bump -- and discarding it
    // would freeze the screen from the opposite end.
    const held = projection({ revision: 18, stage: "AUTHORIZED_RMA" });
    const sameRevision = projection({ revision: 18, stage: "CARRIER_TRANSIT" });

    expect(keepNewerRevision(held, sameRevision)).toEqual(sameRevision);
  });

  it("accepts a higher revision", () => {
    const held = projection({ revision: 18 });
    const newer = projection({ revision: 19, stage: "WAREHOUSE_RECEIVING" });

    expect(keepNewerRevision(held, newer)).toEqual(newer);
  });

  it("accepts anything when there is nothing held yet", () => {
    const first = projection({ revision: 3 });
    expect(keepNewerRevision(undefined, first)).toEqual(first);
  });

  it("accepts a body with no revision on either side", () => {
    // The legacy shape again. Refusing it would leave the screen empty until
    // the projection route lands.
    const legacy = { case: { caseId: "case-1" }, returnRecords: [], unassignedItems: [], facts: [] };
    expect(keepNewerRevision(undefined, legacy)).toEqual(legacy);
  });
});

/**
 * The same two rules, wired the way the Copilot wires them.
 *
 * A predicate that is right in isolation and attached to the wrong option is
 * still a case that never refreshes, so these drive a real `QueryObserver`
 * through a real clock.
 */
describe("a case query built from the contract", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  function observe(queryFn: () => Promise<unknown>) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const observer = new QueryObserver(client, {
      queryKey: ["cases", "case-1"],
      queryFn,
      refetchInterval: (query) => caseRefetchInterval(query.state.data),
      structuralSharing: keepNewerRevision,
    });
    return { observer, unsubscribe: observer.subscribe(() => undefined) };
  }

  it("re-reads an unfinished case every ten seconds", async () => {
    vi.useFakeTimers();
    const read = vi.fn().mockResolvedValue(projection({ isTerminal: false }));
    const { unsubscribe } = observe(read);

    await vi.advanceTimersByTimeAsync(0);
    expect(read).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(CASE_POLL_INTERVAL_MS);
    expect(read).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(CASE_POLL_INTERVAL_MS);
    expect(read).toHaveBeenCalledTimes(3);

    unsubscribe();
  });

  it("stops re-reading once the case reports itself terminal", async () => {
    vi.useFakeTimers();
    const read = vi
      .fn()
      .mockResolvedValueOnce(projection({ revision: 1, isTerminal: false }))
      .mockResolvedValue(
        projection({ revision: 2, status: "COMPLETED", stage: "COMPLETED", isTerminal: true }),
      );
    const { unsubscribe } = observe(read);

    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(CASE_POLL_INTERVAL_MS);
    expect(read).toHaveBeenCalledTimes(2);

    // The terminal answer is now held, so the interval is gone rather than
    // merely long.
    await vi.advanceTimersByTimeAsync(CASE_POLL_INTERVAL_MS * 5);
    expect(read).toHaveBeenCalledTimes(2);

    unsubscribe();
  });

  it("never surfaces a late response that is older than the one on screen", async () => {
    vi.useFakeTimers();
    const read = vi
      .fn()
      .mockResolvedValueOnce(projection({ revision: 18, stage: "AUTHORIZED_RMA" }))
      .mockResolvedValueOnce(projection({ revision: 17, stage: "ITEM_SELECTION" }));
    const { observer, unsubscribe } = observe(read);

    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(CASE_POLL_INTERVAL_MS);
    expect(read).toHaveBeenCalledTimes(2);

    // Not merely "the stage did not change" -- the stale body never became
    // query data, so nothing downstream of the query can have seen it.
    expect(caseLifecycle(observer.getCurrentResult().data)).toMatchObject({
      revision: 18,
      stage: "AUTHORIZED_RMA",
    });

    unsubscribe();
  });

  it("applies a same-revision response that carries new children", async () => {
    vi.useFakeTimers();
    const read = vi
      .fn()
      .mockResolvedValueOnce(projection({ revision: 18, returnRecords: null }))
      .mockResolvedValueOnce(projection({ revision: 18, returnRecords: [rmaWithoutTracking()] }));
    const { observer, unsubscribe } = observe(read);

    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(CASE_POLL_INTERVAL_MS);

    const held = observer.getCurrentResult().data as CaseProjection;
    expect(held.returnRecords).toHaveLength(1);
    expect(held.returnRecords?.[0].returnReference).toBe("RMA-OPS01-CD4364");

    unsubscribe();
  });
});
