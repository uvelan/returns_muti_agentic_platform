/**
 * The Return Business Copilot's action surface, and the two panels D4 unblocked.
 *
 * What these hold that the happy path does not:
 *
 * **A refusal is shown verbatim.** The backend distinguishes "already
 * recorded" from "out of order" from "the workflow service is unavailable".
 * Flattening those into "something went wrong" throws away the only thing that
 * tells an operator what to do next, and it is the easiest thing to lose in a
 * later refactor.
 *
 * **The screen does not decide which events are legal.** All twenty are
 * offered. A test asserts the count, because the tempting "improvement" is to
 * filter the list by the return's stage -- which would be a second copy of
 * `_validate_transition` that drifts from the real one.
 *
 * **"No conversation" is not an error.** A SYSTEM-channel return has none, and
 * in a batch deployment that is most returns. Rendering an error there would
 * make the normal case look broken.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as ActualModuleNamespace from "../../api/returnsDomain";

type ActualModule = typeof ActualModuleNamespace;

import { ReturnCopilotPage } from "./ReturnCopilotPage";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  timeline: vi.fn(),
  support: vi.fn(),
  conversation: vi.fn(),
  recordEvent: vi.fn(),
  can: vi.fn(),
}));

vi.mock("../../api/returnsDomain", async (importOriginal) => {
  const actual = await importOriginal<ActualModule>();
  return {
    ...actual,
    returnsApi: {
      list: mocks.list,
      get: vi.fn(),
      timeline: mocks.timeline,
      artifacts: vi.fn(),
      evidence: vi.fn(),
      support: mocks.support,
      conversation: mocks.conversation,
      recordEvent: mocks.recordEvent,
    },
  };
});

vi.mock("../../hooks/capabilityContext", () => ({
  useCapabilities: () => ({ can: mocks.can }),
}));

const SESSION = {
  id: "ret-1",
  correlationId: "corr-1",
  customerReference: "CUST-1",
  orderReference: "ORD-1",
  itemReferences: ["LINE-1"],
  productReferences: [],
  processingWarehouseReference: null,
  reasonCode: "DAMAGED",
  returnQuantity: 1,
  packageCount: 1,
  shippingPathExpectation: "UNKNOWN",
  orderSource: "UNKNOWN",
  channel: "SYSTEM",
  status: "RUNNING" as const,
  currentStage: "INTAKE",
  progressPercentage: 10,
  returnReference: null,
  supportTicketReference: null,
  supportStatus: null,
  approvedReturnMethod: null,
  customerResolutionStatus: "PENDING",
  physicalReturnStatus: "IN_PROGRESS",
  warehouseStatus: "PENDING",
  vendorRecoveryStatus: "NOT_REQUIRED",
  caseClosureStatus: "OPEN",
  trackingReference: null,
  bayReference: null,
  aiRequestId: null,
  failureCode: null,
  failureMessage: null,
  notes: null,
  version: 0,
  createdAt: "2026-08-10T10:00:00Z",
  updatedAt: "2026-08-10T10:00:00Z",
};

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

async function openSession() {
  render(<ReturnCopilotPage />, { wrapper });
  fireEvent.click(await screen.findByRole("button", { name: /ORD-1/ }));
  await screen.findByText("Record an event");
}

describe("Return Business Copilot actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.can.mockReturnValue(true);
    mocks.list.mockResolvedValue([SESSION]);
    mocks.timeline.mockResolvedValue([]);
    mocks.support.mockResolvedValue({ case: null, workItem: null });
    mocks.conversation.mockResolvedValue(null);
    mocks.recordEvent.mockResolvedValue({
      stage: "PHYSICAL_RETURN",
      caseFullyClosed: false,
      cancelled: false,
    });
  });

  it("records an event with its evidence reference", async () => {
    await openSession();

    fireEvent.change(screen.getByLabelText("Event type"), {
      target: { value: "RECEIPT_CONFIRMED" },
    });
    fireEvent.change(screen.getByLabelText("Evidence reference"), {
      target: { value: "scan-42" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Record event" }));

    await waitFor(() => {
      expect(mocks.recordEvent).toHaveBeenCalledTimes(1);
    });
    const [sessionId, payload] = mocks.recordEvent.mock.calls[0] as [
      string,
      { eventType: string; evidenceReference: string; eventId: string },
    ];
    expect(sessionId).toBe("ret-1");
    expect(payload.eventType).toBe("RECEIPT_CONFIRMED");
    expect(payload.evidenceReference).toBe("scan-42");
    expect(payload.eventId).not.toBe("");
  });

  it("offers every event type rather than guessing which apply", async () => {
    // Filtering this list by stage would be a second copy of
    // `_validate_transition`. Twenty is the enum's size.
    await openSession();

    const options = screen.getByLabelText("Event type").querySelectorAll("option");
    expect(options).toHaveLength(20);
  });

  it("offers cancellation as an event, not a separate control", async () => {
    await openSession();

    const values = Array.from(
      screen.getByLabelText("Event type").querySelectorAll("option"),
      (option) => option.getAttribute("value"),
    );
    expect(values).toContain("CANCELLED");
    expect(screen.queryByRole("button", { name: /cancel return/i })).toBeNull();
  });

  it("will not submit without an evidence reference", async () => {
    // The evidence is the whole justification for the transition. The backend
    // requires it too; this only saves the round trip.
    await openSession();

    expect(screen.getByRole("button", { name: "Record event" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Evidence reference"), { target: { value: "ab" } });
    expect(screen.getByRole("button", { name: "Record event" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Evidence reference"), { target: { value: "abc" } });
    expect(screen.getByRole("button", { name: "Record event" })).toBeEnabled();
  });

  it("shows the backend's refusal verbatim", async () => {
    mocks.recordEvent.mockRejectedValue(
      new Error("RECEIPT_CONFIRMED is already recorded for this return."),
    );
    await openSession();

    fireEvent.change(screen.getByLabelText("Evidence reference"), { target: { value: "scan-42" } });
    fireEvent.click(screen.getByRole("button", { name: "Record event" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("already recorded");
    expect(mocks.recordEvent).toHaveBeenCalledTimes(1);
  });

  it("reports the stage the return moved to", async () => {
    await openSession();

    fireEvent.change(screen.getByLabelText("Evidence reference"), { target: { value: "scan-42" } });
    fireEvent.click(screen.getByRole("button", { name: "Record event" }));

    expect(await screen.findByText(/PHYSICAL_RETURN/)).toBeInTheDocument();
  });

  it("offers no action panel without the write capability", async () => {
    mocks.can.mockImplementation((capability: string) => capability !== "returns.session.write");
    render(<ReturnCopilotPage />, { wrapper });
    fireEvent.click(await screen.findByRole("button", { name: /ORD-1/ }));

    expect(await screen.findByText(/cannot record events/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Record event" })).toBeNull();
  });
});

describe("Return Business Copilot panels", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.can.mockReturnValue(true);
    mocks.list.mockResolvedValue([SESSION]);
    mocks.timeline.mockResolvedValue([]);
    mocks.support.mockResolvedValue({ case: null, workItem: null });
    mocks.conversation.mockResolvedValue(null);
  });

  it("says a return had no conversation rather than showing an error", async () => {
    await openSession();

    expect(
      await screen.findByText(/did not come from a discovery conversation/),
    ).toBeInTheDocument();
  });

  it("renders the conversation when there is one", async () => {
    mocks.conversation.mockResolvedValue({
      id: "conv-1",
      messages: [{ id: "m1", role: "associate", content: "customer says it arrived damaged" }],
    });
    await openSession();

    expect(await screen.findByText(/arrived damaged/)).toBeInTheDocument();
  });

  it("keeps the platform case and the human work item apart", async () => {
    // Collapsing them into one "support status" would lose which of the two an
    // operator is looking at -- and only one means somebody is already on it.
    mocks.support.mockResolvedValue({
      case: { caseType: "FLOW_FAILURE", status: "OPEN", priority: "HIGH", slaBreached: true },
      workItem: { subject: "Customer chasing refund", status: "IN_PROGRESS", queue: "SUPPORT" },
    });
    await openSession();

    expect(await screen.findByText("FLOW_FAILURE")).toBeInTheDocument();
    expect(screen.getByText("Customer chasing refund")).toBeInTheDocument();
    expect(screen.getByText("Breached")).toBeInTheDocument();
  });

  it("shows no support section when there is neither record", async () => {
    // By heading, not by text: "Support" is also a queue label in the left
    // column, and matching that would make this pass for the wrong reason.
    await openSession();

    expect(screen.queryByRole("heading", { name: "Support" })).toBeNull();
  });
});
