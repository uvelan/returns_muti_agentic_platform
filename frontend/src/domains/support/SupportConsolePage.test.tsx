/**
 * S3's outcome action -- the one control that closes Channel B back to A.
 *
 * These assert what the console *sends*, not how it looks. The mistake worth
 * catching is a form that posts a blank tracking number or that offers itself
 * on a work item with no case, where the request cannot land anywhere.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SupportMessage, SupportWorkItem } from "../../api/support";
import { SupportConsolePage } from "./SupportConsolePage";

const mocks = vi.hoisted(() => ({
  listWorkItems: vi.fn(),
  readWorkItem: vi.fn(),
  listMessages: vi.fn(),
  reply: vi.fn(),
  act: vi.fn(),
  submitReturnOutcome: vi.fn(),
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

describe("SupportConsolePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.can.mockReturnValue(true);
    mocks.listWorkItems.mockResolvedValue([workItem()]);
    mocks.readWorkItem.mockResolvedValue(workItem());
    mocks.listMessages.mockResolvedValue([AGENT_MESSAGE]);
    mocks.submitReturnOutcome.mockResolvedValue(undefined);
  });

  it("sends the RMA to the case, not to the work item", async () => {
    await openThread();
    fireEvent.click(screen.getByRole("button", { name: /issue rma/i }));
    fireEvent.change(screen.getByLabelText(/RMA number/i), { target: { value: "RMA-1001" } });
    fireEvent.change(screen.getByLabelText(/Tracking/i), { target: { value: "1Z999" } });
    fireEvent.click(screen.getByRole("button", { name: /send to the return/i }));

    await waitFor(() => { expect(mocks.submitReturnOutcome).toHaveBeenCalledTimes(1); });
    expect(mocks.submitReturnOutcome).toHaveBeenCalledWith("wi-1", {
      records: [{ returnReference: "RMA-1001", trackingReference: "1Z999" }],
    });
    // The reply composer is a different act on a different endpoint, and an
    // outcome that also posted a message would double-report to Support.
    expect(mocks.reply).not.toHaveBeenCalled();
    expect(mocks.act).not.toHaveBeenCalled();
  });

  it("omits a reference Support left blank rather than sending an empty one", async () => {
    await openThread();
    fireEvent.click(screen.getByRole("button", { name: /issue rma/i }));
    fireEvent.change(screen.getByLabelText(/RMA number/i), { target: { value: "RMA-2002" } });
    fireEvent.click(screen.getByRole("button", { name: /send to the return/i }));

    await waitFor(() => { expect(mocks.submitReturnOutcome).toHaveBeenCalledTimes(1); });
    const [, payload] = mocks.submitReturnOutcome.mock.calls[0] as [string, { records: object[] }];
    expect(payload.records[0]).toEqual({ returnReference: "RMA-2002" });
  });

  it("does not offer the action on a work item with no case", async () => {
    mocks.listWorkItems.mockResolvedValue([workItem({ caseId: null })]);
    mocks.readWorkItem.mockResolvedValue(workItem({ caseId: null }));

    await openThread();

    expect(screen.queryByRole("button", { name: /issue rma/i })).toBeNull();
    // The thread itself still works: Support can read and reply to a legacy
    // session-scoped item, they just cannot route an outcome through a case
    // that does not exist.
    expect(screen.getByLabelText(/Reply to the return request/i)).not.toBeNull();
  });

  it("surfaces a rejected outcome instead of reporting it sent", async () => {
    mocks.submitReturnOutcome.mockRejectedValue(new Error("This return is already closed."));

    await openThread();
    fireEvent.click(screen.getByRole("button", { name: /issue rma/i }));
    fireEvent.change(screen.getByLabelText(/RMA number/i), { target: { value: "RMA-3003" } });
    fireEvent.click(screen.getByRole("button", { name: /send to the return/i }));

    // Verbatim, and not collapsed into the collapsed state's success line:
    // "sent" for an RMA that was refused is the failure this guards.
    expect(await screen.findByRole("alert")).toHaveTextContent("This return is already closed.");
    expect(screen.queryByText(/The associate will see it/i)).toBeNull();
  });
});
