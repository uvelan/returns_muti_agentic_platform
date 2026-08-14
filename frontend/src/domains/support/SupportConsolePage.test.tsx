/**
 * UI-03 -- what the Support console can express, and what it refuses to.
 *
 * The load-bearing assertions are about *shape*: a case is `N RMAs -> N items`,
 * each RMA owning its own label, tracking and return location, and the console
 * used to be able to say none of that. A form that can only post one record
 * makes the multi-RMA half of contract C3 unreachable from the only screen that
 * issues RMAs, and no amount of correct rendering compensates.
 *
 * The rest guard the three ways this screen can lie: a duplicate send that the
 * workflow silently ignores while the operator watches it succeed, a shipment
 * verdict rendered as a failure when it is a correct outcome, and a bay absence
 * rendered as a fault when it is the normal state.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as CasesModule from "../../api/cases";
import type { CaseDetail, CaseFact, CaseReturnItem, CaseReturnRecord } from "../../api/cases";
import type * as ShipmentsModule from "../../api/returnShipments";
import { ShipmentGraphSyncFailed, type ShipmentUpdateResult } from "../../api/returnShipments";
import type { SupportMessage, SupportWorkItem } from "../../api/support";
import { SupportConsolePage } from "./SupportConsolePage";

/** The enclosing `<fieldset>` / `<article>` a query landed inside. */
function enclosing(element: HTMLElement, selector: string): HTMLElement {
  const found = element.closest(selector);
  if (!(found instanceof HTMLElement)) {
    throw new Error(`no enclosing ${selector} for ${element.textContent ?? ""}`);
  }
  return found;
}

const mocks = vi.hoisted(() => ({
  listWorkItems: vi.fn(),
  readWorkItem: vi.fn(),
  listMessages: vi.fn(),
  reply: vi.fn(),
  act: vi.fn(),
  submitReturnOutcome: vi.fn(),
  readCase: vi.fn(),
  recordUpdate: vi.fn(),
  can: vi.fn(),
}));

vi.mock("../../api/support", () => ({
  supportApi: {
    listWorkItems: mocks.listWorkItems,
    readWorkItem: mocks.readWorkItem,
    listMessages: mocks.listMessages,
    reply: mocks.reply,
    act: mocks.act,
    submitReturnOutcome: mocks.submitReturnOutcome,
  },
}));

// Only the transport is stubbed. `bayRecommendation` and `latestFacts` are the
// projection this screen's correctness depends on -- replacing them with a
// fixture would test the fixture.
vi.mock("../../api/cases", async (importOriginal) => ({
  ...(await importOriginal<typeof CasesModule>()),
  casesApi: { read: mocks.readCase, list: vi.fn() },
}));

vi.mock("../../api/returnShipments", async (importOriginal) => ({
  ...(await importOriginal<typeof ShipmentsModule>()),
  returnShipmentsApi: { recordUpdate: mocks.recordUpdate },
}));

vi.mock("../../hooks/capabilityContext", () => ({
  useCapabilities: () => ({ can: mocks.can }),
}));

function workItem(overrides: Partial<SupportWorkItem> = {}): SupportWorkItem {
  return {
    id: "wi-1",
    sessionId: null,
    caseId: "case-1",
    threadId: "th-1",
    status: "NEW",
    priority: "NORMAL",
    queue: "RETURNS",
    subject: "Return for CW273354",
    assignedTo: null,
    returnReference: null,
    shippingInstructionReference: null,
    slaDueAt: "2026-08-12T00:00:00Z",
    version: 3,
    createdAt: "2026-08-11T00:00:00Z",
    updatedAt: "2026-08-11T00:00:00Z",
    ...overrides,
  };
}

function item(overrides: Partial<CaseReturnItem> = {}): CaseReturnItem {
  return {
    returnItemId: "ri-1",
    orderLineReference: "LINE-1",
    productReference: "SKU-1",
    quantity: 1,
    reason: null,
    condition: null,
    packageReference: null,
    ...overrides,
  };
}

function fact(name: string, value: unknown): CaseFact {
  return {
    factId: `${name}-case-1`,
    caseId: "case-1",
    factName: name,
    value,
    agentId: "bay-assignment-agent",
    channel: "SYSTEM",
    acquisitionMethod: "DERIVED",
    sourceSystem: null,
    sourcePath: "RETURN_CASE_WORKFLOW",
    observedAt: "2026-08-11T00:00:00Z",
    recordedAt: "2026-08-11T00:00:00Z",
  };
}

function returnRecord(overrides: Partial<CaseReturnRecord["record"]> = {}): CaseReturnRecord {
  return {
    record: {
      returnRecordId: "rr-1",
      caseId: "case-1",
      returnReference: "RMA-1",
      status: "ISSUED",
      returnLocation: "DOCK-4",
      trackingReference: "1Z-A",
      labelReference: "LBL-A",
      shippingInstructionReference: null,
      sourceSystem: "SUPPORT",
      version: 1,
      createdAt: "2026-08-11T00:00:00Z",
      updatedAt: "2026-08-11T00:00:00Z",
      ...overrides,
    },
    items: [item()],
  };
}

function caseDetail(overrides: Partial<CaseDetail> = {}): CaseDetail {
  return {
    case: {
      caseId: "case-1",
      tenantId: "default",
      principalId: "dev-operator",
      branchId: null,
      status: "AWAITING_SUPPORT",
      channelAConversationId: "disc-1",
      channelBWorkItemId: "wi-1",
      confirmedOrderReference: "CW273354",
      confirmationKey: "default|disc-1|CW273354|1",
      sessionId: null,
      workflowId: "return-case-case-1",
      configurationReleaseId: "release-1",
      graphGenerationId: "gen-7",
      version: 2,
      createdAt: "2026-08-11T00:00:00Z",
      updatedAt: "2026-08-11T00:00:00Z",
    },
    returnRecords: [],
    unassignedItems: [item(), item({ returnItemId: "ri-2", orderLineReference: "LINE-2" })],
    facts: [],
    ...overrides,
  };
}

const AGENT_MESSAGE: SupportMessage = {
  id: "m-1",
  threadId: "th-1",
  sequence: 1,
  senderRole: "AGENT",
  senderId: "order-discovery-agent",
  messageType: "REQUEST",
  messageText: "Could you raise the RMA?",
  businessPayload: {},
  createdAt: "2026-08-11T00:00:00Z",
};

function shipmentResult(overrides: Partial<ShipmentUpdateResult> = {}): ShipmentUpdateResult {
  return {
    outcome: "APPLIED",
    returnReference: "RMA-1",
    trackingReference: "1Z-A",
    currentStatus: "IN_TRANSIT",
    currentStatusAt: "2026-08-12T09:00:00Z",
    rowVersion: 4,
    graphGenerationId: "gen-7",
    reading: {
      caseId: "case-1",
      fulfillmentStatus: "IN_TRANSIT",
      evidence: "OBSERVED",
      evidenceReference: "SHIPMENT_OBSERVED:RMA-1",
      graphGenerationId: "gen-7",
      observedStatus: "IN_TRANSIT",
    },
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<SupportConsolePage />, { wrapper });
}

async function openThread() {
  renderPage();
  fireEvent.click(await screen.findByText("Return for CW273354"));
  await screen.findByText("Could you raise the RMA?");
}

async function openOutcomeForm() {
  await openThread();
  fireEvent.click(await screen.findByRole("button", { name: /issue rmas/i }));
}

describe("SupportConsolePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.can.mockReturnValue(true);
    mocks.listWorkItems.mockResolvedValue([workItem()]);
    mocks.readWorkItem.mockResolvedValue(workItem());
    mocks.listMessages.mockResolvedValue([AGENT_MESSAGE]);
    mocks.submitReturnOutcome.mockResolvedValue(undefined);
    mocks.readCase.mockResolvedValue(caseDetail());
    mocks.recordUpdate.mockResolvedValue(shipmentResult());
  });

  describe("issuing RMAs", () => {
    it("sends N records with the lines each one covers", async () => {
      await openOutcomeForm();

      fireEvent.change(screen.getByLabelText(/RMA 1 number/), { target: { value: "RMA-1001" } });
      fireEvent.change(screen.getByLabelText(/RMA 1 tracking/), { target: { value: "1Z999" } });
      fireEvent.change(screen.getByLabelText(/RMA 1 return to/), { target: { value: "DOCK-4" } });
      fireEvent.click(within(enclosing(screen.getByText("RMA 1"), "fieldset")).getByLabelText(/LINE-1/));

      fireEvent.click(screen.getByRole("button", { name: /add another rma/i }));
      fireEvent.change(screen.getByLabelText(/RMA 2 number/), { target: { value: "RMA-1002" } });
      fireEvent.change(screen.getByLabelText(/RMA 2 label/), { target: { value: "LBL-B" } });
      fireEvent.click(within(enclosing(screen.getByText("RMA 2"), "fieldset")).getByLabelText(/LINE-2/));

      fireEvent.click(screen.getByRole("button", { name: /send 2 rmas/i }));

      await waitFor(() => { expect(mocks.submitReturnOutcome).toHaveBeenCalledTimes(1); });
      expect(mocks.submitReturnOutcome).toHaveBeenCalledWith("wi-1", {
        records: [
          {
            returnReference: "RMA-1001",
            trackingReference: "1Z999",
            returnLocation: "DOCK-4",
            orderLineReferences: ["LINE-1"],
          },
          {
            returnReference: "RMA-1002",
            labelReference: "LBL-B",
            orderLineReferences: ["LINE-2"],
          },
        ],
      });
      // The reply composer is a different act on a different endpoint, and an
      // outcome that also posted a message would double-report to Support.
      expect(mocks.reply).not.toHaveBeenCalled();
      expect(mocks.act).not.toHaveBeenCalled();
    });

    it("will not let two RMAs claim the same line", async () => {
      await openOutcomeForm();

      fireEvent.click(within(enclosing(screen.getByText("RMA 1"), "fieldset")).getByLabelText(/LINE-1/));
      fireEvent.click(screen.getByRole("button", { name: /add another rma/i }));

      const second = within(enclosing(screen.getByText("RMA 2"), "fieldset"));
      expect(second.getByLabelText(/LINE-1/)).toBeDisabled();
      expect(second.getByLabelText(/LINE-2/)).toBeEnabled();
    });

    it("refuses to send two blocks carrying the same RMA number", async () => {
      await openOutcomeForm();

      fireEvent.change(screen.getByLabelText(/RMA 1 number/), { target: { value: "RMA-1001" } });
      fireEvent.click(screen.getByRole("button", { name: /add another rma/i }));
      fireEvent.change(screen.getByLabelText(/RMA 2 number/), { target: { value: "RMA-1001" } });

      expect(screen.getByRole("alert")).toHaveTextContent(/same RMA number/i);
      expect(screen.getByRole("button", { name: /send 2 rmas/i })).toBeDisabled();
      expect(mocks.submitReturnOutcome).not.toHaveBeenCalled();
    });

    it("removes a block without carrying its values into the next one", async () => {
      await openOutcomeForm();

      fireEvent.change(screen.getByLabelText(/RMA 1 number/), { target: { value: "FIRST" } });
      fireEvent.click(screen.getByRole("button", { name: /add another rma/i }));
      fireEvent.change(screen.getByLabelText(/RMA 2 number/), { target: { value: "SECOND" } });
      fireEvent.click(screen.getByRole("button", { name: /remove rma 1/i }));

      // The surviving block renumbers to 1 and keeps *its own* value. Keying on
      // array position would have handed it the removed block's state.
      expect(screen.getByLabelText(/RMA 1 number/)).toHaveValue("SECOND");
      expect(screen.queryByLabelText(/RMA 2 number/)).toBeNull();
    });

    it("omits a reference Support left blank rather than sending an empty one", async () => {
      await openOutcomeForm();

      fireEvent.change(screen.getByLabelText(/RMA 1 number/), { target: { value: "RMA-2002" } });
      fireEvent.click(screen.getByRole("button", { name: /send 1 rma/i }));

      await waitFor(() => { expect(mocks.submitReturnOutcome).toHaveBeenCalledTimes(1); });
      const [, payload] = mocks.submitReturnOutcome.mock.calls[0] as [
        string,
        { records: object[] },
      ];
      expect(payload.records[0]).toEqual({ returnReference: "RMA-2002" });
    });

    /**
     * The duplicate that matters.
     *
     * `ReturnCaseWorkflow.support_response` takes the first notice and ignores
     * every later one. A second send is therefore not "harmless", it is
     * silently nothing -- and an operator who fixed a typo and pressed send
     * again would watch it succeed and change no RMA.
     */
    it("refuses a second send and says the first response is the one that counts", async () => {
      await openOutcomeForm();

      fireEvent.change(screen.getByLabelText(/RMA 1 number/), { target: { value: "RMA-1" } });
      fireEvent.click(screen.getByRole("button", { name: /send 1 rma/i }));

      await waitFor(() => { expect(mocks.submitReturnOutcome).toHaveBeenCalledTimes(1); });
      expect(await screen.findByText(/Answer sent to the case/i)).toBeTruthy();
      expect(screen.getByText(/ignores any later one/i)).toBeTruthy();
      expect(screen.queryByRole("button", { name: /issue rmas/i })).toBeNull();
      expect(screen.queryByRole("button", { name: /send 1 rma/i })).toBeNull();
      expect(mocks.submitReturnOutcome).toHaveBeenCalledTimes(1);
    });

    it("does not fire twice on a double click", async () => {
      let release: (() => void) | undefined;
      mocks.submitReturnOutcome.mockReturnValue(
        new Promise<void>((resolve) => { release = resolve; }),
      );
      await openOutcomeForm();

      fireEvent.change(screen.getByLabelText(/RMA 1 number/), { target: { value: "RMA-9" } });
      fireEvent.click(screen.getByRole("button", { name: /send 1 rma/i }));
      // The second click lands on the same control once React has flushed the
      // pending state -- which is the real race a hurried operator creates.
      fireEvent.click(await screen.findByRole("button", { name: /sending/i }));

      expect(mocks.submitReturnOutcome).toHaveBeenCalledTimes(1);
      release?.();
    });

    it("does not offer the action on a work item with no case", async () => {
      mocks.listWorkItems.mockResolvedValue([workItem({ caseId: null })]);
      mocks.readWorkItem.mockResolvedValue(workItem({ caseId: null }));

      await openThread();

      expect(screen.queryByRole("button", { name: /issue rmas/i })).toBeNull();
      // Named, not blank: a session-backed item is a different shape, and an
      // empty RMA list would read as "this case has no RMAs".
      expect(await screen.findByText(/belongs to a return session/i)).toBeTruthy();
      expect(screen.getByLabelText(/Reply to the return request/i)).not.toBeNull();
      expect(mocks.readCase).not.toHaveBeenCalled();
    });

    it("surfaces a rejected outcome instead of reporting it sent", async () => {
      mocks.submitReturnOutcome.mockRejectedValue(new Error("This return is already closed."));

      await openOutcomeForm();
      fireEvent.change(screen.getByLabelText(/RMA 1 number/), { target: { value: "RMA-3003" } });
      fireEvent.click(screen.getByRole("button", { name: /send 1 rma/i }));

      expect(await screen.findByRole("alert")).toHaveTextContent("This return is already closed.");
      expect(screen.queryByText(/Answer sent to the case/i)).toBeNull();
    });

    it("does not offer to issue an RMA without returns.support.act", async () => {
      mocks.can.mockImplementation((capability: string) => capability !== "returns.support.act");
      await openThread();

      expect(await screen.findByText(/requires returns\.support\.act/i)).toBeTruthy();
      expect(screen.queryByRole("button", { name: /issue rmas/i })).toBeNull();
    });
  });

  describe("the case's RMAs", () => {
    it("keeps label, tracking and location inside the RMA that owns them", async () => {
      mocks.readCase.mockResolvedValue(
        caseDetail({
          returnRecords: [
            returnRecord(),
            {
              record: {
                ...returnRecord().record,
                returnRecordId: "rr-2",
                returnReference: "RMA-2",
                labelReference: "LBL-B",
                trackingReference: "1Z-B",
                returnLocation: "DOCK-9",
              },
              items: [item({ returnItemId: "ri-2", orderLineReference: "LINE-2" })],
            },
          ],
          unassignedItems: [],
        }),
      );
      await openThread();

      const first = enclosing(await screen.findByText("RMA-1"), "article");
      const second = enclosing(screen.getByText("RMA-2"), "article");

      // Each block carries its own three. The failure this guards is a case
      // header that shows "the" label -- which is wrong the moment RMA-2 exists
      // and is unsayable in SQL, where they are `return_record` columns.
      expect(within(first).getByText("LBL-A")).toBeTruthy();
      expect(within(first).getByText("DOCK-4")).toBeTruthy();
      expect(within(first).getByText("LINE-1")).toBeTruthy();
      expect(within(second).getByText("LBL-B")).toBeTruthy();
      expect(within(second).getByText("DOCK-9")).toBeTruthy();
      expect(within(second).getByText("LINE-2")).toBeTruthy();
      expect(within(first).queryByText("LBL-B")).toBeNull();
      expect(within(second).queryByText("LBL-A")).toBeNull();
    });

    it("says a case with no durable workflow has nothing waiting on the answer", async () => {
      mocks.readCase.mockResolvedValue(
        caseDetail({ case: { ...caseDetail().case, workflowId: null } }),
      );
      await openThread();

      expect(await screen.findByText(/No durable workflow is recorded/i)).toBeTruthy();
    });
  });

  describe("the bay recommendation", () => {
    it("shows the computed recommendation, not a constant", async () => {
      mocks.readCase.mockResolvedValue(
        caseDetail({
          facts: [
            fact("bay_warehouse_reference", "WH-1"),
            fact("bay_reference", "BAY-12"),
            fact("bay_return_location", "DOCK-4"),
            fact("bay_confidence_millionths", 812_500),
            fact("bay_reason", "CAPACITY_AND_AFFINITY"),
            fact("bay_evidence_reference", "BAY_EVIDENCE:case-1"),
          ],
        }),
      );
      await openThread();

      expect(await screen.findByText("BAY-12")).toBeTruthy();
      expect(screen.getByText("81.3%")).toBeTruthy();
      expect(screen.getByText("CAPACITY_AND_AFFINITY")).toBeTruthy();
    });

    it("treats an absent bay as normal rather than as a fault", async () => {
      mocks.readCase.mockResolvedValue(
        caseDetail({ facts: [fact("bay_reason", "BAY_PLACEMENT_NOT_CONFIGURED")] }),
      );
      await openThread();

      expect(await screen.findByText(/No bay recommended/i)).toBeTruthy();
      expect(screen.getByText(/best-effort/i)).toBeTruthy();
      // No alert anywhere on the case pane: an error tone here sends an
      // operator looking for a fault that does not exist.
      expect(screen.queryByRole("alert")).toBeNull();
    });

    it("takes the newest fact when the log holds a superseded one", async () => {
      mocks.readCase.mockResolvedValue(
        caseDetail({
          facts: [
            fact("bay_reference", "BAY-OLD"),
            { ...fact("bay_reference", "BAY-NEW"), recordedAt: "2026-08-12T00:00:00Z" },
          ],
        }),
      );
      await openThread();

      expect(await screen.findByText("BAY-NEW")).toBeTruthy();
      expect(screen.queryByText("BAY-OLD")).toBeNull();
    });
  });

  describe("recording a shipment", () => {
    async function openShipmentEditor() {
      mocks.readCase.mockResolvedValue(
        caseDetail({ returnRecords: [returnRecord()], unassignedItems: [] }),
      );
      await openThread();
      fireEvent.click(await screen.findByRole("button", { name: /record or correct a shipment/i }));
      fireEvent.change(screen.getByLabelText(/Tracking number/), { target: { value: "1Z-A" } });
      fireEvent.change(screen.getByLabelText(/Carrier status/), {
        target: { value: "IN_TRANSIT" },
      });
    }

    it("sends the RMA reference, the type, and a zoned timestamp", async () => {
      await openShipmentEditor();
      fireEvent.click(screen.getByRole("button", { name: /record shipment/i }));

      await waitFor(() => { expect(mocks.recordUpdate).toHaveBeenCalledTimes(1); });
      const [reference, payload] = mocks.recordUpdate.mock.calls[0] as [
        string,
        { statusAt: string; trackingType: string },
      ];
      expect(reference).toBe("RMA-1");
      expect(payload.trackingType).toBe("PPL");
      // The ordering authority for the whole contract. An unzoned value is
      // refused by the backend, so sending one would be a guaranteed 422.
      expect(payload.statusAt).toMatch(/[+-]\d{2}:\d{2}$/);
    });

    it.each([
      { outcome: "DUPLICATE" as const, says: /Already recorded/i },
      { outcome: "STALE" as const, says: /Refused as stale/i },
    ])("renders $outcome as an outcome, not as a failure", async ({ outcome, says }) => {
      mocks.recordUpdate.mockResolvedValue(shipmentResult({ outcome, reading: null }));
      await openShipmentEditor();
      fireEvent.click(screen.getByRole("button", { name: /record shipment/i }));

      const status = await screen.findByRole("status");
      expect(status).toHaveTextContent(says);
      // The whole point: a correct verdict must not be announced as a refusal,
      // or an operator learns to re-enter it with a fresh timestamp.
      expect(screen.queryByRole("alert")).toBeNull();
    });

    // The 502 -> `ShipmentGraphSyncFailed` mapping itself is proven against a
    // real response in `api/returnShipments.test.ts`; this asserts what the
    // screen does once it holds one.
    it("says a graph sync failure is committed and safe to retry", async () => {
      mocks.recordUpdate.mockRejectedValue(
        new ShipmentGraphSyncFailed(
          "The shipment update was committed to the authoritative store and could not be "
          + "projected into the graph.",
        ),
      );
      await openShipmentEditor();
      fireEvent.click(screen.getByRole("button", { name: /record shipment/i }));

      const status = await screen.findByRole("status");
      expect(status).toHaveTextContent(/committed to the authoritative store/i);
      expect(status).toHaveTextContent(/will answer DUPLICATE/i);
      expect(status).toHaveTextContent(/Do not re-enter it with a new timestamp/i);
    });

    it("surfaces an ordinary refusal as a refusal", async () => {
      mocks.recordUpdate.mockRejectedValue(new Error("Insufficient permissions"));
      await openShipmentEditor();
      fireEvent.click(screen.getByRole("button", { name: /record shipment/i }));

      expect(await screen.findByRole("alert")).toHaveTextContent("Insufficient permissions");
    });

    it("does not offer the editor without returns.logistics.act", async () => {
      mocks.can.mockImplementation((capability: string) => capability !== "returns.logistics.act");
      mocks.readCase.mockResolvedValue(
        caseDetail({ returnRecords: [returnRecord()], unassignedItems: [] }),
      );
      await openThread();

      expect(await screen.findByText(/requires returns\.logistics\.act/i)).toBeTruthy();
      expect(screen.queryByRole("button", { name: /record or correct a shipment/i })).toBeNull();
    });

    it("offers no shipment editor for an RMA with no number to send", async () => {
      mocks.readCase.mockResolvedValue(
        caseDetail({
          returnRecords: [returnRecord({ returnReference: null })],
          unassignedItems: [],
        }),
      );
      await openThread();

      expect(await screen.findByText(/needs an RMA number/i)).toBeTruthy();
      expect(screen.queryByRole("button", { name: /record or correct a shipment/i })).toBeNull();
    });
  });
});
