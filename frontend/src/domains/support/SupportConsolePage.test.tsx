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
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as CasesModule from "../../api/cases";
import type {
  CaseFactProjection,
  CaseProjection,
  ReturnRecordProjection,
  SelectedItemProjection,
} from "../../api/cases";
import type * as ShipmentsModule from "../../api/returnShipments";
import { ShipmentGraphSyncFailed, type ShipmentUpdateResult } from "../../api/returnShipments";
import type * as SupportModule from "../../api/support";
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

// Only `supportApi` is replaced. `newSupportEventId` is kept real on purpose:
// the id it produces, and where the page calls it from, is exactly what the
// idempotency assertions below are about, and a stubbed generator would let a
// per-render or per-send mint pass.
vi.mock("../../api/support", async (importOriginal) => ({
  ...(await importOriginal<typeof SupportModule>()),
  supportApi: {
    listWorkItems: mocks.listWorkItems,
    readWorkItem: mocks.readWorkItem,
    listMessages: mocks.listMessages,
    reply: mocks.reply,
    act: mocks.act,
    submitReturnOutcome: mocks.submitReturnOutcome,
  },
}));

// Only the transport is stubbed. `bayRecommendation` and `projectedFactString`
// are the reads this screen's correctness depends on -- replacing them with a
// fixture would test the fixture.
vi.mock("../../api/cases", async (importOriginal) => ({
  ...(await importOriginal<typeof CasesModule>()),
  casesApi: { readProjection: mocks.readCase, list: vi.fn() },
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

function item(overrides: Partial<SelectedItemProjection> = {}): SelectedItemProjection {
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

/**
 * One fact off `CaseProjection.facts` -- the backend's latest-per-name
 * projection, so there is one entry per name and no superseded value beside it.
 */
function fact(
  name: string,
  value: string | number | boolean | null,
  overrides: Partial<CaseFactProjection> = {},
): CaseFactProjection {
  return {
    factId: `${name}-case-1`,
    factName: name,
    value,
    agentId: "bay-assignment-agent",
    channel: "SYSTEM",
    acquisitionMethod: "DERIVED",
    sourceSystem: "RETURN_CASE_WORKFLOW",
    observedAt: "2026-08-11T00:00:00Z",
    recordedAt: "2026-08-11T00:00:00Z",
    supersedesFactId: null,
    ...overrides,
  };
}

/**
 * One RMA with a label on its package.
 *
 * The label hangs off the record and names the package through `shipmentId`,
 * which is the amended contract: one home for the document, so it cannot be
 * attributed to a parcel it does not name.
 */
function returnRecord(overrides: Partial<ReturnRecordProjection> = {}): ReturnRecordProjection {
  return {
    returnRecordId: "rr-1",
    returnReference: "RMA-1",
    status: "ISSUED",
    returnMethod: "PREPAID_PARCEL",
    returnLocation: "DOCK-4",
    approvedItems: [
      {
        returnItemId: "ri-1",
        orderLineReference: "LINE-1",
        productReference: "SKU-1",
        quantityApproved: 1,
        disposition: null,
        itemStatus: null,
      },
    ],
    shipments: [
      {
        shipmentId: "rr-1",
        shipmentStatus: null,
        carrier: null,
        serviceLevel: null,
        trackingNumber: "1Z-A",
        estimatedDeliveryAt: null,
        createdAt: null,
        updatedAt: null,
      },
    ],
    artifacts: [
      {
        artifactId: "LBL-A",
        artifactType: "SHIPPING_LABEL",
        shipmentId: "rr-1",
        fileName: null,
        mediaType: null,
        version: 1,
        active: true,
        supersededBy: null,
        expiresAt: null,
        createdAt: null,
      },
    ],
    ...overrides,
  };
}

function caseDetail(overrides: Partial<CaseProjection> = {}): CaseProjection {
  return {
    caseId: "case-1",
    tenantId: "default",
    principalId: "dev-operator",
    conversationId: "disc-1",
    status: "AWAITING_SUPPORT",
    revision: 2,
    updatedAt: "2026-08-11T00:00:00Z",
    customer: null,
    confirmedOrder: {
      orderReference: "CW273354",
      orderSource: null,
      sourceWebOrderNumber: null,
      trilogieOrderNumber: null,
      confirmationKey: "default|disc-1|CW273354|1",
      candidateSetId: null,
      candidateId: null,
      confirmedAt: null,
    },
    selectedItems: [item(), item({ returnItemId: "ri-2", orderLineReference: "LINE-2" })],
    facts: null,
    policyEvaluation: null,
    support: {
      workItemId: "wi-1",
      threadId: "th-1",
      queue: "RETURNS",
      status: "NEW",
      subject: "Return for CW273354",
      priority: "NORMAL",
      assignedTo: null,
      slaDueAt: "2026-08-12T00:00:00Z",
      openedAt: "2026-08-11T00:00:00Z",
      resolvedAt: null,
    },
    returnRecords: null,
    pickup: null,
    warehouse: null,
    settlement: {
      status: "NOT_INTEGRATED",
      creditMemoReference: null,
      settledAmount: null,
      settledAt: null,
    },
    stage: "AWAITING_SUPPORT",
    awaiting: ["POLICY", "RETURN_METHOD"],
    businessComplete: false,
    isTerminal: false,
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
      const [target, sent] = mocks.submitReturnOutcome.mock.calls[0] as [
        string,
        { records: object[]; supportEventId: string },
      ];
      expect(target).toBe("wi-1");
      expect(sent.records).toEqual([
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
      ]);
      // Required by the endpoint, not optional: without it the write is refused
      // with 422 SUPPORT_EVENT_ID_REQUIRED and the RMAs are lost.
      expect(sent.supportEventId).toMatch(/^ui-wi-1-/);
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

    it("stops offering to cancel a send that has already started", async () => {
      /**
       * The button said "Cancel" the whole time, and pressing it only closed
       * the form. The POST carried on and `onSuccess` still fired, so an
       * operator who believed they had stopped the send had in fact issued the
       * RMAs -- and `support_response` takes the first notice, so there was no
       * correcting it afterwards.
       *
       * There is no cancel to offer instead. Aborting the request would not
       * un-write what the server had already committed, and calling that
       * "cancelled" would be the same claim one layer down. So the control is
       * withdrawn while the send is in flight, and says why.
       */
      let release: (() => void) | undefined;
      mocks.submitReturnOutcome.mockReturnValue(
        new Promise<void>((resolve) => { release = resolve; }),
      );
      await openOutcomeForm();

      fireEvent.change(screen.getByLabelText(/RMA 1 number/), { target: { value: "RMA-7" } });
      const cancel = screen.getByRole("button", { name: /^cancel$/i });
      expect(cancel).toBeEnabled();

      fireEvent.click(screen.getByRole("button", { name: /send 1 rma/i }));

      await waitFor(() => { expect(cancel).toBeDisabled(); });
      expect(cancel).toHaveAttribute("title", expect.stringMatching(/cannot be stopped/i));

      release?.();
      await waitFor(() => { expect(mocks.submitReturnOutcome).toHaveBeenCalledTimes(1); });
    });

    it("says how long the send has been running", async () => {
      // Two states -- idle and "Sending..." -- made a nine-second wait and a
      // nine-minute one look the same while they were happening.
      vi.useFakeTimers({ shouldAdvanceTime: true });
      try {
        mocks.submitReturnOutcome.mockReturnValue(new Promise<void>(() => { /* never */ }));
        await openOutcomeForm();

        fireEvent.change(screen.getByLabelText(/RMA 1 number/), { target: { value: "RMA-8" } });
        fireEvent.click(screen.getByRole("button", { name: /send 1 rma/i }));

        await waitFor(() => {
          expect(screen.getByRole("button", { name: /sending/i })).toBeTruthy();
        });
        await act(async () => {
          await vi.advanceTimersByTimeAsync(3_000);
        });
        expect(screen.getByRole("button", { name: /sending\.\.\. 3s/i })).toBeTruthy();
      } finally {
        vi.useRealTimers();
      }
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

    /**
     * The retry that must not become a second RMA.
     *
     * The endpoint is durable rather than synchronous: it commits the event and
     * an outbox command and returns, and the outbox delivers at least once
     * afterwards. Effectively-once processing is keyed on `supportEventId`
     * alone, so the id has to survive the one event that actually loses RMAs --
     * an operator whose response never arrived pressing send again. Minted per
     * send, or inside `submitReturnOutcome`, or on each render, that press is a
     * new identity and the backend has no way left to tell it from a new reply.
     */
    it("resends the same event id when the operator retries the same answer", async () => {
      mocks.submitReturnOutcome.mockRejectedValueOnce(new Error("The connection dropped."));
      await openOutcomeForm();

      fireEvent.change(screen.getByLabelText(/RMA 1 number/), { target: { value: "RMA-7007" } });
      fireEvent.click(screen.getByRole("button", { name: /send 1 rma/i }));
      expect(await screen.findByRole("alert")).toHaveTextContent("The connection dropped.");

      // The same drafts, the same button, no edit in between: one business act
      // the operator is repeating because they never learned its outcome.
      fireEvent.click(screen.getByRole("button", { name: /send 1 rma/i }));
      await waitFor(() => { expect(mocks.submitReturnOutcome).toHaveBeenCalledTimes(2); });

      const [first, second] = mocks.submitReturnOutcome.mock.calls as [
        [string, { supportEventId: string }],
        [string, { supportEventId: string }],
      ];
      expect(second[1].supportEventId).toBe(first[1].supportEventId);
    });

    it("mints a new event id for a deliberately new answer", async () => {
      mocks.submitReturnOutcome.mockRejectedValueOnce(new Error("The connection dropped."));
      await openOutcomeForm();

      fireEvent.change(screen.getByLabelText(/RMA 1 number/), { target: { value: "RMA-8008" } });
      fireEvent.click(screen.getByRole("button", { name: /send 1 rma/i }));
      expect(await screen.findByRole("alert")).toHaveTextContent("The connection dropped.");

      // Abandoning the draft and opening the form again is a second act, not a
      // repeat of the first. Carrying the id across would have the backend
      // refuse the new reply as an idempotency conflict with the old one.
      fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
      fireEvent.click(await screen.findByRole("button", { name: /issue rmas/i }));
      fireEvent.change(screen.getByLabelText(/RMA 1 number/), { target: { value: "RMA-9009" } });
      fireEvent.click(screen.getByRole("button", { name: /send 1 rma/i }));

      await waitFor(() => { expect(mocks.submitReturnOutcome).toHaveBeenCalledTimes(2); });
      const [first, second] = mocks.submitReturnOutcome.mock.calls as [
        [string, { supportEventId: string }],
        [string, { supportEventId: string }],
      ];
      expect(second[1].supportEventId).not.toBe(first[1].supportEventId);
    });

    /**
     * A re-render is not a new business act.
     *
     * `useState` rather than a value recomputed in the component body, because
     * this screen re-renders on every poll of the work-item and message queries
     * and a recomputed id would silently change underneath a form the operator
     * is still filling in.
     */
    it("keeps the event id stable across re-renders while the form is open", async () => {
      mocks.submitReturnOutcome.mockRejectedValueOnce(new Error("The connection dropped."));
      await openOutcomeForm();

      fireEvent.change(screen.getByLabelText(/RMA 1 number/), { target: { value: "RMA-5005" } });
      fireEvent.click(screen.getByRole("button", { name: /send 1 rma/i }));
      expect(await screen.findByRole("alert")).toHaveTextContent("The connection dropped.");

      // Every keystroke re-renders the whole form.
      fireEvent.change(screen.getByLabelText(/RMA 1 tracking/), { target: { value: "1Z777" } });
      fireEvent.click(screen.getByRole("button", { name: /add another rma/i }));
      fireEvent.click(screen.getByRole("button", { name: /remove rma 2/i }));
      fireEvent.click(screen.getByRole("button", { name: /send 1 rma/i }));

      await waitFor(() => { expect(mocks.submitReturnOutcome).toHaveBeenCalledTimes(2); });
      const [first, second] = mocks.submitReturnOutcome.mock.calls as [
        [string, { supportEventId: string }],
        [string, { supportEventId: string }],
      ];
      expect(second[1].supportEventId).toBe(first[1].supportEventId);
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
            returnRecord({
              returnRecordId: "rr-2",
              returnReference: "RMA-2",
              returnLocation: "DOCK-9",
              approvedItems: [
                {
                  returnItemId: "ri-2",
                  orderLineReference: "LINE-2",
                  productReference: "SKU-2",
                  quantityApproved: 1,
                  disposition: null,
                  itemStatus: null,
                },
              ],
              shipments: [
                {
                  shipmentId: "rr-2",
                  shipmentStatus: null,
                  carrier: null,
                  serviceLevel: null,
                  trackingNumber: "1Z-B",
                  estimatedDeliveryAt: null,
                  createdAt: null,
                  updatedAt: null,
                },
              ],
              artifacts: [
                {
                  artifactId: "LBL-B",
                  artifactType: "SHIPPING_LABEL",
                  shipmentId: "rr-2",
                  fileName: null,
                  mediaType: null,
                  version: 1,
                  active: true,
                  supersededBy: null,
                  expiresAt: null,
                  createdAt: null,
                },
              ],
            }),
          ],
          selectedItems: [],
        }),
      );
      await openThread();

      const first = enclosing(await screen.findByText("RMA-1"), "article");
      const second = enclosing(screen.getByText("RMA-2"), "article");

      // Each block carries its own three. The failure this guards is a case
      // header that shows "the" label -- which is wrong the moment RMA-2 exists
      // and is unsayable in SQL, where they are `return_record` columns.
      expect(within(first).getByText("LBL-A")).toBeTruthy();
      expect(within(first).getByText("1Z-A")).toBeTruthy();
      expect(within(first).getByText("DOCK-4")).toBeTruthy();
      expect(within(first).getByText("LINE-1")).toBeTruthy();
      expect(within(second).getByText("LBL-B")).toBeTruthy();
      expect(within(second).getByText("1Z-B")).toBeTruthy();
      expect(within(second).getByText("DOCK-9")).toBeTruthy();
      expect(within(second).getByText("LINE-2")).toBeTruthy();
      expect(within(first).queryByText("LBL-B")).toBeNull();
      expect(within(second).queryByText("LBL-A")).toBeNull();
    });

    it("shows an RMA with a label and no package as exactly that", async () => {
      // Record `4e372a39...`. The label is real, it belongs to no package, and
      // neither dropping it nor inventing a shipment to carry it is acceptable.
      mocks.readCase.mockResolvedValue(
        caseDetail({
          returnRecords: [
            returnRecord({
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
                  createdAt: null,
                },
              ],
            }),
          ],
          selectedItems: [],
          awaiting: ["LABEL", "TRACKING"],
        }),
      );
      await openThread();

      const block = enclosing(await screen.findByText("RMA-1"), "article");
      expect(within(block).getByText("LBL-OPS01")).toBeTruthy();
      expect(within(block).getByText(/label and no package yet/i)).toBeTruthy();
      // And the case says the same thing from the other end.
      expect(screen.getByText(/Waiting on LABEL, TRACKING/i)).toBeTruthy();
    });

    it("ignores a superseded label rather than taking the first artifact", async () => {
      mocks.readCase.mockResolvedValue(
        caseDetail({
          returnRecords: [
            returnRecord({
              artifacts: [
                {
                  artifactId: "LBL-REPLACED",
                  artifactType: "SHIPPING_LABEL",
                  shipmentId: "rr-1",
                  fileName: null,
                  mediaType: null,
                  version: 1,
                  active: false,
                  supersededBy: "LBL-A",
                  expiresAt: null,
                  createdAt: null,
                },
                {
                  artifactId: "LBL-A",
                  artifactType: "SHIPPING_LABEL",
                  shipmentId: "rr-1",
                  fileName: null,
                  mediaType: null,
                  version: 2,
                  active: true,
                  supersededBy: null,
                  expiresAt: null,
                  createdAt: null,
                },
              ],
            }),
          ],
          selectedItems: [],
        }),
      );
      await openThread();

      const block = enclosing(await screen.findByText("RMA-1"), "article");
      expect(within(block).getByText("LBL-A")).toBeTruthy();
      expect(within(block).queryByText("LBL-REPLACED")).toBeNull();
    });

    it("says what a case is waiting on rather than inferring it from a missing field", async () => {
      // This replaces the missing-`workflowId` reading. `CaseProjection` carries
      // no workflow id; it carries `awaiting`, computed by the backend from the
      // release's requirement table, which is the answer that reading wanted.
      mocks.readCase.mockResolvedValue(caseDetail({ awaiting: ["LABEL", "TRACKING"] }));
      await openThread();

      expect(await screen.findByText(/Waiting on LABEL, TRACKING/i)).toBeTruthy();
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

    it("reads the newest fact because the backend already projected it", async () => {
      // `CaseProjection.facts` *is* `latest_case_facts`: one entry per name,
      // already the newest, with the fact it supersedes named. The console used
      // to reduce the whole log itself; that duplicate is deleted, and this
      // asserts the screen shows the surviving value and not the superseded id.
      mocks.readCase.mockResolvedValue(
        caseDetail({
          facts: [
            fact("bay_reference", "BAY-NEW", {
              factId: "bay_reference-2",
              recordedAt: "2026-08-12T00:00:00Z",
              supersedesFactId: "bay_reference-case-1",
            }),
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
        caseDetail({ returnRecords: [returnRecord()], selectedItems: [] }),
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
        caseDetail({ returnRecords: [returnRecord()], selectedItems: [] }),
      );
      await openThread();

      expect(await screen.findByText(/requires returns\.logistics\.act/i)).toBeTruthy();
      expect(screen.queryByRole("button", { name: /record or correct a shipment/i })).toBeNull();
    });

    it("offers no shipment editor for an RMA with no number to send", async () => {
      mocks.readCase.mockResolvedValue(
        caseDetail({
          returnRecords: [returnRecord({ returnReference: null })],
          selectedItems: [],
        }),
      );
      await openThread();

      expect(await screen.findByText(/needs an RMA number/i)).toBeTruthy();
      expect(screen.queryByRole("button", { name: /record or correct a shipment/i })).toBeNull();
    });
  });

  describe("the opening request is a document, not a sentence", () => {
    const HANDOFF = [
      "RETURN SUPPORT REQUEST",
      "",
      "Case:",
      "- Case ID: case-1",
      "",
      "Order:",
      "- Order Number: CW273354",
      "- Line/Order-Line Number: 1",
      "  - Product Name: 6X12 CEIL ALUM 4-WAY REG SAND",
      "  - Colour: Sandtone",
      "",
      "Bay Assignment:",
      "- Recommended Bay: 686-BAY-01",
    ].join("\n");

    it("keeps every line of a sectioned request", async () => {
      mocks.listMessages.mockResolvedValue([{ ...AGENT_MESSAGE, messageText: HANDOFF }]);
      renderPage();
      fireEvent.click(await screen.findByText("Return for CW273354"));

      // `findByText` with an exact string is the assertion: HTML collapses
      // whitespace, so before the fix these lines arrived concatenated into one
      // paragraph and none of them could be found on its own.
      const heading = await screen.findByText("RETURN SUPPORT REQUEST", { exact: false });
      const bubble = enclosing(heading, "div");

      expect(bubble.textContent).toContain("- Colour: Sandtone");
      expect(bubble.textContent).toContain("- Recommended Bay: 686-BAY-01");
      // Every section survives as its own line, indentation included.
      for (const line of HANDOFF.split("\n")) {
        expect(bubble.textContent?.split("\n")).toContain(line);
      }
      // Wide content scrolls inside the bubble rather than wrapping a value
      // away from the label that introduces it.
      expect(bubble.className).toContain("whitespace-pre");
      expect(bubble.className).toContain("overflow-x-auto");
    });

    it("leaves an ordinary one-line reply as an ordinary bubble", async () => {
      mocks.listMessages.mockResolvedValue([AGENT_MESSAGE]);
      renderPage();
      fireEvent.click(await screen.findByText("Return for CW273354"));

      const text = await screen.findByText("Could you raise the RMA?");
      const bubble = enclosing(text, "div");

      expect(bubble.className).toContain("whitespace-pre-wrap");
      expect(bubble.className).toContain("max-w-[80%]");
      expect(bubble.className).not.toContain("overflow-x-auto");
    });
  });
});
