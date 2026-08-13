/**
 * S6 -- what the sync screen shows, and what it sends.
 *
 * These assert behaviour an operator depends on, not layout. Three things are
 * worth catching: a targeted run that does not say which conversation caused it
 * (which makes an agent-initiated write to the graph untraceable), a completed
 * run with no writes rendered as an ordinary success (the exact shape of the
 * defect this screen shipped alongside), and a "Sync now" control offered to
 * someone the backend will refuse.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SyncRun } from "../../api/graphSync";
import { SyncControlPage } from "./SyncControlPage";

const mocks = vi.hoisted(() => ({
  listRuns: vi.fn(),
  readRun: vi.fn(),
  startRun: vi.fn(),
  can: vi.fn(),
}));

vi.mock("../../api/graphSync", () => ({
  graphSyncApi: {
    listRuns: mocks.listRuns,
    readRun: mocks.readRun,
    startRun: mocks.startRun,
  },
}));

vi.mock("../../hooks/capabilityContext", () => ({
  useCapabilities: () => ({ can: mocks.can }),
}));

function run(overrides: Partial<SyncRun> = {}): SyncRun {
  return {
    id: "run-1",
    mode: "FULL",
    status: "COMPLETED",
    schemaVersion: "2026.08.04",
    sourceCounts: { source_sales: 120 },
    nodeWrites: 300,
    relationshipWrites: 240,
    constraintsApplied: [],
    configurationDigest: "7c9d67a6",
    errorCode: null,
    startedBy: "operator-1",
    startedAt: "2026-08-11T06:00:00Z",
    completedAt: "2026-08-11T06:04:00Z",
    graphGenerationId: null,
    requestDigest: null,
    requestedBy: null,
    ...overrides,
  };
}

const TARGETED = run({
  id: "run-2",
  mode: "ON_DEMAND",
  sourceCounts: { source_sales: 1 },
  nodeWrites: 5,
  relationshipWrites: 4,
  startedBy: "order-discovery-agent",
  graphGenerationId: "legacy-live",
  requestDigest: "0f3a91c4",
  requestedBy: {
    agentId: "order-discovery-agent",
    conversationId: "conv-7",
    clientTurnId: "turn-4",
    entityId: "sales_order",
    strongAnchorId: "exact_order_key",
    anchorFieldIds: ["order_key"],
  },
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<SyncControlPage />, { wrapper });
}

describe("SyncControlPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.can.mockReturnValue(true);
    mocks.listRuns.mockResolvedValue([TARGETED, run()]);
    mocks.readRun.mockResolvedValue(TARGETED);
    mocks.startRun.mockResolvedValue(run());
  });

  it("lists scheduled and agent-initiated runs in one history", async () => {
    renderPage();

    expect(await screen.findByText("ON_DEMAND")).toBeTruthy();
    expect(screen.getByText("FULL")).toBeTruthy();
  });

  it("says which conversation caused a targeted run", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("ON_DEMAND"));

    expect(await screen.findByText("conv-7")).toBeTruthy();
    expect(screen.getByText("exact_order_key")).toBeTruthy();
  });

  it("shows the anchor's fields and never the anchor's value", async () => {
    // The anchor is a customer's order number. A run list is browsed, exported
    // and kept, so the field id is the whole of what belongs here.
    renderPage();
    fireEvent.click(await screen.findByText("ON_DEMAND"));

    expect(await screen.findByText("order_key")).toBeTruthy();
    expect(screen.queryByText(/CW\d/)).toBeNull();
  });

  it("calls out a run that completed without writing anything", async () => {
    // A green status over zero writes is what "the source answered and the
    // projection discarded it" looks like from the outside.
    mocks.readRun.mockResolvedValue(run({ nodeWrites: 0, relationshipWrites: 0 }));
    renderPage();
    fireEvent.click(await screen.findByText("FULL"));

    expect(await screen.findByRole("status")).toHaveTextContent(
      /completed without writing anything/i,
    );
  });

  it("does not call out a healthy run", async () => {
    // This test was wrong, not the screen. `readRun` is mocked in `beforeEach`
    // to the *targeted* run (5 writes), so the detail pane never showed "300"
    // and `findByText("300")` failed on the setup line -- the assertion below
    // has never once run. The list row is no help either: its span reads
    // "300 nodes" as a single text content, which an exact `findByText("300")`
    // does not match.
    mocks.readRun.mockResolvedValue(run());
    renderPage();
    fireEvent.click(await screen.findByText("FULL"));

    await screen.findByText("300");
    expect(screen.queryByText(/completed without writing anything/i)).toBeNull();
  });

  it("sends the chosen scope and record limit", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /sync now/i }));
    fireEvent.change(screen.getByLabelText(/scope/i), { target: { value: "SOURCE_MONGODB" } });
    fireEvent.change(screen.getByLabelText(/records per source/i), { target: { value: "50" } });
    fireEvent.click(screen.getByRole("button", { name: /start sync/i }));

    await waitFor(() => {
      expect(mocks.startRun).toHaveBeenCalledTimes(1);
    });
    expect(mocks.startRun).toHaveBeenCalledWith({
      mode: "SOURCE_MONGODB",
      // Sent explicitly, including when false: the request now carries two
      // independent choices, and leaving one implicit is how "which records did
      // that run actually read" stops being answerable.
      incremental: false,
      maxRecordsPerAsset: 50,
    });
  });

  it("defaults to rereading everything rather than resuming", async () => {
    // A manual sync is usually pressed because the graph looks wrong, and
    // resuming from a cursor is the wrong default for that.
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /sync now/i }));

    expect(screen.getByLabelText(/^read$/i)).toHaveValue("full");
  });

  it("sends an incremental run when the operator asks for one", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /sync now/i }));
    fireEvent.change(screen.getByLabelText(/^read$/i), { target: { value: "incremental" } });
    fireEvent.click(screen.getByRole("button", { name: /start sync/i }));

    await waitFor(() => {
      expect(mocks.startRun).toHaveBeenCalledTimes(1);
    });
    expect(mocks.startRun).toHaveBeenCalledWith({
      mode: "FULL",
      incremental: true,
      maxRecordsPerAsset: 1000,
    });
  });

  it("omits a record limit the operator cleared rather than sending NaN", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /sync now/i }));
    fireEvent.change(screen.getByLabelText(/records per source/i), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /start sync/i }));

    await waitFor(() => {
      expect(mocks.startRun).toHaveBeenCalledTimes(1);
    });
    expect(mocks.startRun).toHaveBeenCalledWith({ mode: "FULL", incremental: false });
  });

  it("does not call out an incremental run that had nothing to do", async () => {
    // The one case where zero writes is the healthy answer: no source changed
    // since the last run. Warning about it in error tone on every quiet run is
    // how the genuine warning above stops being read.
    mocks.readRun.mockResolvedValue(
      run({ nodeWrites: 0, relationshipWrites: 0, recordScope: "INCREMENTAL" }),
    );
    renderPage();
    fireEvent.click(await screen.findByText("FULL"));

    await screen.findByText("Only what changed");
    expect(screen.queryByText(/completed without writing anything/i)).toBeNull();
  });

  it("names the sources an incremental run could not resume", async () => {
    // Skipped silently, this source stops syncing until someone runs a full
    // scan -- and the run still says COMPLETED.
    mocks.readRun.mockResolvedValue(
      run({ recordScope: "INCREMENTAL", skippedSources: ["sql_bay_assignment"] }),
    );
    renderPage();
    fireEvent.click(await screen.findByText("FULL"));

    expect(await screen.findByText("sql_bay_assignment")).toBeTruthy();
    expect(screen.getByText(/have no cursor and were not read/i)).toBeTruthy();
  });

  it("surfaces a refused sync instead of reporting it ran", async () => {
    mocks.startRun.mockRejectedValue(new Error("The sync did not complete (ConnectionFailure)."));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /sync now/i }));
    fireEvent.click(screen.getByRole("button", { name: /start sync/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("ConnectionFailure");
  });

  it("does not offer the control to someone who may only read", async () => {
    mocks.can.mockImplementation((capability: string) => capability === "config.source.read");
    renderPage();

    await screen.findByText("FULL");
    expect(screen.queryByRole("button", { name: /sync now/i })).toBeNull();
  });

  it("says the list could not be read rather than that there are no runs", async () => {
    // An empty sync history is something an operator would act on. Reporting
    // one because the request failed sends them after the wrong problem.
    mocks.listRuns.mockRejectedValue(new Error("Graph synchronization is unavailable."));
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Graph synchronization is unavailable.",
    );
    expect(screen.queryByText(/No sync runs recorded/i)).toBeNull();
  });
});
