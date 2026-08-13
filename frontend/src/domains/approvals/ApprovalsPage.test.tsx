/**
 * UI-01 -- what the approvals screen shows, and what it sends.
 *
 * These assert what a reviewer depends on, not layout. The ones worth having:
 * an empty queue that is really a failed request (a reviewer who believes
 * nothing is waiting on them leaves), a decision sent without the reason they
 * typed, an Approve button offered to someone the kernel will refuse, and a
 * refusal reported as a success.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as ActualModuleNamespace from "../../api/proposals";
import type { ProposalDetail, ProposalSummary } from "../../api/proposals";
import { ApprovalsPage } from "./ApprovalsPage";

type ActualModule = typeof ActualModuleNamespace;

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
  activate: vi.fn(),
  can: vi.fn(),
}));

vi.mock("../../api/proposals", async (importOriginal) => {
  // `DECISIONS_BY_STATUS` mirrors the kernel's transition table and is the
  // thing under test whenever a button's presence is asserted, so the real one
  // is kept rather than restated here.
  const actual = await importOriginal<ActualModule>();
  return {
    ...actual,
    proposalsApi: {
      list: mocks.list,
      get: mocks.get,
      approve: mocks.approve,
      reject: mocks.reject,
      activate: mocks.activate,
    },
  };
});

vi.mock("../../hooks/capabilityContext", () => ({
  useCapabilities: () => ({ can: mocks.can }),
}));

function summary(overrides: Partial<ProposalSummary> = {}): ProposalSummary {
  return {
    proposalId: "prop-1",
    proposalType: "GRAPH_SCHEMA",
    subjectId: "draft-9",
    title: "Add Bay to the return graph",
    status: "REVIEW_PENDING",
    risk: "HIGH",
    affectedKeys: ["entities.bay", "entities.warehouse.bay_id"],
    proposedBy: "analyst-2",
    decidedBy: null,
    createdAt: "2026-08-11T09:00:00Z",
    updatedAt: "2026-08-11T09:30:00Z",
    ...overrides,
  };
}

function detail(overrides: Partial<ProposalDetail> = {}): ProposalDetail {
  return {
    ...summary(),
    before: { entities: { warehouse: {} } },
    after: { entities: { warehouse: {}, bay: {} } },
    diff: [
      { key: "entities.bay", change: "ADDED", after: { label: "Bay" } },
      { key: "entities.warehouse.legacy_slot", change: "REMOVED", before: "slot_code" },
    ],
    evidence: ["snapshot-4f2a", "validation-77"],
    evidenceDigest: "9c1d0ae4",
    validationReceipt: "vr-2026-08-11-01",
    decisionNote: null,
    activationReference: null,
    history: [
      { status: "VALIDATED", actor: "analyst-2", occurred_at: "2026-08-11T09:10:00Z", note: null },
      {
        status: "REVIEW_PENDING",
        actor: "analyst-2",
        occurred_at: "2026-08-11T09:30:00Z",
        note: "Ready for review",
      },
    ],
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<ApprovalsPage />, { wrapper });
}

async function openProposal() {
  renderPage();
  fireEvent.click(await screen.findByText("Add Bay to the return graph"));
  await screen.findByText("vr-2026-08-11-01");
}

describe("ApprovalsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.can.mockReturnValue(true);
    mocks.list.mockResolvedValue([summary()]);
    mocks.get.mockResolvedValue(detail());
    mocks.approve.mockResolvedValue(detail({ status: "APPROVED" }));
    mocks.reject.mockResolvedValue(detail({ status: "REJECTED" }));
    mocks.activate.mockResolvedValue(detail({ status: "ACTIVATED" }));
  });

  it("opens on what is waiting for a decision", async () => {
    // The screen exists to answer "what is waiting on me". Opening on every
    // proposal ever raised buries that behind the archive.
    renderPage();

    await screen.findByText("Add Bay to the return graph");
    expect(mocks.list).toHaveBeenCalledWith({ status: "REVIEW_PENDING", type: undefined });
  });

  it("shows a loading state before the queue arrives", () => {
    mocks.list.mockReturnValue(new Promise(() => {
      // Never settles: the point is the state before an answer, and a resolved
      // promise races the assertion.
    }));
    renderPage();

    expect(screen.getByText("Loading...")).toBeTruthy();
  });

  it("says the queue is empty rather than pretending it failed", async () => {
    mocks.list.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText(/Nothing matches this filter/i)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says the queue could not be read rather than that nothing is waiting", async () => {
    // The dangerous confusion. A reviewer told their queue is clear stops
    // looking; one told it could not be read goes and finds out why.
    mocks.list.mockRejectedValue(new Error("The proposal kernel is not available."));
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The proposal kernel is not available.",
    );
    expect(screen.queryByText(/Nothing matches this filter/i)).toBeNull();
  });

  it("narrows the queue by status and by kind", async () => {
    renderPage();
    await screen.findByText("Add Bay to the return graph");

    fireEvent.change(screen.getByLabelText(/status/i), { target: { value: "APPROVED" } });
    await waitFor(() => {
      expect(mocks.list).toHaveBeenCalledWith({ status: "APPROVED", type: undefined });
    });

    fireEvent.change(screen.getByLabelText(/kind/i), { target: { value: "CONFIGURATION" } });
    await waitFor(() => {
      expect(mocks.list).toHaveBeenCalledWith({ status: "APPROVED", type: "CONFIGURATION" });
    });
  });

  it("shows the whole basis for the decision, not just the title", async () => {
    // A reviewer approving a row is approving *this document*. Every part of
    // it the kernel carries has to be on the screen, or the approval is being
    // made from a headline.
    await openProposal();

    // Risk, and the change that earns it. `entities.bay` is deliberately in
    // two places -- the affected-keys list and the diff -- so both are asserted
    // rather than one of them being an accident.
    expect(screen.getAllByText("HIGH").length).toBeGreaterThan(0);
    expect(screen.getByText("REMOVED")).toBeTruthy();
    expect(screen.getAllByText("entities.bay")).toHaveLength(2);
    expect(screen.getByText("entities.warehouse.legacy_slot")).toBeTruthy();
    // Evidence references and the digest.
    expect(screen.getByText("snapshot-4f2a")).toBeTruthy();
    expect(screen.getByText("9c1d0ae4")).toBeTruthy();
    // Decision history, including the note recorded against a transition.
    expect(screen.getByText("Ready for review")).toBeTruthy();
  });

  it("calls out a proposal nothing has certified", async () => {
    // A VALIDATED-looking record with no receipt is indistinguishable from one
    // nobody checked, and that is exactly what a reviewer must not assume away.
    mocks.get.mockResolvedValue(detail({ validationReceipt: null }));
    renderPage();
    fireEvent.click(await screen.findByText("Add Bay to the return graph"));

    expect(await screen.findByText(/No validation receipt/i)).toBeTruthy();
  });

  it("sends the reason the reviewer typed", async () => {
    await openProposal();

    fireEvent.change(screen.getByLabelText(/reason/i), {
      target: { value: "Checked against the migration plan." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => {
      expect(mocks.approve).toHaveBeenCalledWith("prop-1", "Checked against the migration plan.");
    });
  });

  it("sends no reason rather than an empty one", async () => {
    // `DecisionRequest.note` is `str | None`. An empty string would be recorded
    // as a reason that says nothing, which reads worse in the history than an
    // honest absence.
    await openProposal();
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() => {
      expect(mocks.reject).toHaveBeenCalledWith("prop-1", null);
    });
  });

  // Two halves of one rule: `DECISIONS_BY_STATUS` mirrors the kernel's
  // transition table, so a reviewer is never sent into a 409 to learn something
  // the platform already knows. Split across two tests because each needs its
  // own render -- asserting both against one tree makes the queries ambiguous.
  it("offers approve and reject, but not activate, on a pending proposal", async () => {
    await openProposal();

    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reject" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Activate" })).toBeNull();
  });

  it("offers activate, and nothing else, on an approved proposal", async () => {
    mocks.list.mockResolvedValue([summary({ status: "APPROVED" })]);
    mocks.get.mockResolvedValue(detail({ status: "APPROVED" }));
    renderPage();
    fireEvent.click(await screen.findByText("Add Bay to the return graph"));

    expect(await screen.findByRole("button", { name: "Activate" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
  });

  it("offers nothing on a terminal proposal", async () => {
    mocks.list.mockResolvedValue([summary({ status: "REJECTED" })]);
    mocks.get.mockResolvedValue(detail({ status: "REJECTED", decidedBy: "reviewer-1" }));
    renderPage();
    fireEvent.click(await screen.findByText("Add Bay to the return graph"));

    expect(await screen.findByText(/No decision is available from REJECTED/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("surfaces a refused decision instead of reporting it was made", async () => {
    // The kernel refuses for real reasons -- a forbidden key, a version that
    // moved because someone else decided first. Each needs a different response.
    mocks.approve.mockRejectedValue(
      new Error("proposal prop-1 is APPROVED; APPROVED is not reachable from there."),
    );
    await openProposal();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("is not reachable from there");
  });

  it("says the proposal could not be read rather than showing a blank pane", async () => {
    mocks.get.mockRejectedValue(new Error("no proposal prop-1."));
    renderPage();
    fireEvent.click(await screen.findByText("Add Bay to the return graph"));

    expect(await screen.findByRole("alert")).toHaveTextContent("no proposal prop-1.");
  });

  it("shows nothing at all without the governance read", () => {
    mocks.can.mockReturnValue(false);
    renderPage();

    expect(screen.getByText(/requires\s+governance.proposal.read/i)).toBeTruthy();
    expect(mocks.list).not.toHaveBeenCalled();
  });

  it("does not offer a decision to someone who may only read", async () => {
    mocks.can.mockImplementation((capability: string) => capability === "governance.proposal.read");
    await openProposal();

    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
    expect(screen.getByText(/requires governance.proposal.approve/i)).toBeTruthy();
  });

  it("gates activation separately from approval", async () => {
    // Approving says the change is right; activating says now is the moment,
    // and here that publishes a release. A reviewer with only the approval
    // grant must not be handed the second decision by side effect.
    mocks.can.mockImplementation(
      (capability: string) => capability !== "governance.proposal.activate",
    );
    mocks.list.mockResolvedValue([summary({ status: "APPROVED" })]);
    mocks.get.mockResolvedValue(detail({ status: "APPROVED" }));
    renderPage();
    fireEvent.click(await screen.findByText("Add Bay to the return graph"));

    expect(await screen.findByText(/requires governance.proposal.activate/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Activate" })).toBeNull();
  });
});
