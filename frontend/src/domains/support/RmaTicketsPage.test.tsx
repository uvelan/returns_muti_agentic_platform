/**
 * The RMA ticket workbench.
 *
 * Two things carry weight here. First, the create must take the workflow
 * agent's payload whole -- the screen exists so an associate confirms what the
 * agent established rather than retyping it, and a paste that fills nothing is
 * the failure that would make the screen pointless.
 *
 * Second, the source-shipment outcome must be *shown*. That write is the one
 * place this platform touches a collection everything else treats as read-only,
 * and an associate who cannot see whether it landed has no way to know the
 * source and the platform disagree.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as RmaModule from "../../api/rmaTickets";
import type { RmaTicketView } from "../../api/rmaTickets";
import { RmaTicketsPage } from "./RmaTicketsPage";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  create: vi.fn(),
  recordTracking: vi.fn(),
  setStatus: vi.fn(),
}));

vi.mock("../../api/rmaTickets", async (importOriginal) => ({
  ...(await importOriginal<typeof RmaModule>()),
  rmaTicketsApi: {
    list: mocks.list,
    get: mocks.get,
    create: mocks.create,
    recordTracking: mocks.recordTracking,
    setStatus: mocks.setStatus,
  },
}));

function ticket(overrides: Partial<RmaTicketView> = {}): RmaTicketView {
  return {
    ticketId: "TCK-1",
    sessionId: "session-1",
    status: "SUBMITTED",
    returnReference: "RMA-SESSION1",
    externalReference: null,
    orderReference: "ORD-1",
    customerReference: "CUS-1",
    associateId: "associate-1",
    recommendedReturnMethod: "BRANCH_UPS",
    supportDraft: "Customer reports a damaged faucet.",
    missingFields: [],
    photoEvidenceRequired: false,
    items: [
      {
        returnItemId: "ITEM-1",
        orderLineId: "LINE-1",
        productId: "SKU-1",
        quantity: 1,
        reasonCode: "DAMAGED",
        itemStatus: "CREATED",
      },
    ],
    tracking: [],
    createdAt: null,
    updatedAt: null,
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<RmaTicketsPage />, { wrapper });
}

describe("the RMA ticket workbench", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue([ticket()]);
    mocks.get.mockResolvedValue(ticket());
    mocks.create.mockResolvedValue({ ticket: ticket(), outcome: "CREATED" });
    mocks.recordTracking.mockResolvedValue({
      ticket: ticket(),
      outcome: "INSERTED",
      sourceShipment: {
        attempted: true,
        matchedDocument: "1Z9900000000000001",
        outcome: "UPDATED",
        detail: "Return tracking replaced on the source shipment document.",
      },
    });
    mocks.setStatus.mockResolvedValue(ticket({ status: "RETURN_CREATED" }));
  });

  it("lists the queue and opens a ticket", async () => {
    renderPage();

    fireEvent.click(await screen.findByText("RMA-SESSION1"));

    expect(await screen.findByText("LINE-1")).toBeTruthy();
    expect(screen.getByText("Customer reports a damaged faucet.")).toBeTruthy();
  });

  it("fills the create form from the workflow agent payload", async () => {
    // The whole point of the screen: the agent already established these
    // facts, so pasting what it posted must populate the form rather than
    // leaving an associate to retype them.
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "New RMA ticket" }));

    const payload = screen.getByLabelText(/Workflow agent payload/);
    fireEvent.change(payload, {
      target: {
        value: JSON.stringify({
          sessionId: "session-9",
          orderReference: "ORD-9",
          associateId: "associate-9",
          recommendedReturnMethod: "OFFSITE_LTL",
          supportDraft: "Agent narrative.",
          items: [
            {
              orderLineId: "L9",
              productId: "P9",
              requestedQuantity: 3,
              reasonCode: "WRONG_ITEM",
            },
          ],
        }),
      },
    });
    fireEvent.blur(payload);

    await waitFor(() => {
      expect(screen.getByLabelText<HTMLInputElement>("Session id").value).toBe("session-9");
    });
    expect(screen.getByLabelText<HTMLInputElement>("Order reference").value).toBe("ORD-9");
    expect(screen.getByLabelText<HTMLInputElement>("Recommended return method").value).toBe(
      "OFFSITE_LTL",
    );
    expect(screen.getByLabelText<HTMLInputElement>("Order line").value).toBe("L9");
    expect(screen.getByLabelText<HTMLInputElement>("Quantity").value).toBe("3");
  });

  it("says so when the pasted payload is not an agent payload", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "New RMA ticket" }));

    const payload = screen.getByLabelText(/Workflow agent payload/);
    fireEvent.change(payload, { target: { value: "not json" } });
    fireEvent.blur(payload);

    expect(await screen.findByText(/not a workflow agent payload/i)).toBeTruthy();
  });

  it("reports the source shipment outcome after saving tracking", async () => {
    // The assertion this file exists for. A source write that happened
    // silently, or one that failed silently, are the same thing to an
    // associate -- so the outcome is rendered either way.
    renderPage();
    fireEvent.click(await screen.findByText("RMA-SESSION1"));

    fireEvent.change(await screen.findByLabelText("Tracking reference"), {
      target: { value: "1Z-RETURN-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save tracking" }));

    expect(await screen.findByText(/source shipment: updated/i)).toBeTruthy();
    expect(screen.getByText(/1Z9900000000000001/)).toBeTruthy();
  });

  it("shows a failed source write without claiming the tracking was lost", async () => {
    mocks.recordTracking.mockResolvedValue({
      ticket: ticket(),
      outcome: "INSERTED",
      sourceShipment: {
        attempted: true,
        matchedDocument: null,
        outcome: "FAILED",
        detail: "The platform tracking record was written and is authoritative.",
      },
    });
    renderPage();
    fireEvent.click(await screen.findByText("RMA-SESSION1"));

    fireEvent.change(await screen.findByLabelText("Tracking reference"), {
      target: { value: "1Z-RETURN-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save tracking" }));

    expect(await screen.findByText(/source shipment: failed/i)).toBeTruthy();
    expect(screen.getByText(/authoritative/i)).toBeTruthy();
  });

  it("surfaces what the agent could not establish", async () => {
    mocks.get.mockResolvedValue(
      ticket({ status: "CLARIFICATION_REQUIRED", missingFields: ["branch_id"] }),
    );
    renderPage();

    fireEvent.click(await screen.findByText("RMA-SESSION1"));

    expect(await screen.findByText(/could not establish: branch_id/i)).toBeTruthy();
  });

  it("reports a duplicate submit rather than showing a second ticket", async () => {
    mocks.create.mockResolvedValue({ ticket: ticket(), outcome: "DUPLICATE" });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "New RMA ticket" }));

    const payload = screen.getByLabelText(/Workflow agent payload/);
    fireEvent.change(payload, {
      target: {
        value: JSON.stringify({
          sessionId: "session-1",
          orderReference: "ORD-1",
          associateId: "a1",
          recommendedReturnMethod: "BRANCH_UPS",
          supportDraft: "Narrative.",
          items: [
            { orderLineId: "L1", productId: "P1", requestedQuantity: 1, reasonCode: "DAMAGED" },
          ],
        }),
      },
    });
    fireEvent.blur(payload);
    await waitFor(() => {
      expect(screen.getByLabelText<HTMLInputElement>("Session id").value).toBe("session-1");
    });

    fireEvent.click(screen.getByRole("button", { name: "Create RMA ticket" }));

    expect(await screen.findByText(/already existed for that session/i)).toBeTruthy();
  });

  it("reports a queue that could not be loaded rather than showing it as empty", async () => {
    mocks.list.mockRejectedValue(new Error("SQL Server unavailable"));
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be loaded/i);
  });
});
