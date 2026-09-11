/**
 * `/config/source-bindings`: source-binding overrides (list, rebind, clear)
 * and the sync trigger and run history moved here from `/sync`.
 *
 * The sync-half assertions are `SyncControlPage.test.tsx`'s own, moved
 * verbatim with the import updated -- the behaviour did not change, only
 * where it renders. New tests cover the binding panel this step adds.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SyncRun } from "../../api/graphSync";
import type { SourceBinding } from "../../api/sourceBindings";
import { DataSourcesSection } from "./DataSourcesSection";

const mocks = vi.hoisted(() => ({
  listRuns: vi.fn(),
  readRun: vi.fn(),
  startRun: vi.fn(),
  listBindings: vi.fn(),
  rebind: vi.fn(),
  clear: vi.fn(),
  can: vi.fn(),
}));

vi.mock("../../api/graphSync", () => ({
  graphSyncApi: {
    listRuns: mocks.listRuns,
    readRun: mocks.readRun,
    startRun: mocks.startRun,
  },
}));

vi.mock("../../api/sourceBindings", () => ({
  CONNECTOR_TYPES: ["MONGODB", "MSSQL", "POSTGRESQL", "NEO4J"],
  sourceBindingsApi: {
    list: mocks.listBindings,
    rebind: mocks.rebind,
    clear: mocks.clear,
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

const BINDING: SourceBinding = {
  dataset: "source_sales",
  sourceAssetId: "salesInv",
  connectorType: "MONGODB",
  objectRef: { database: "source_db", collection: "salesInv" },
  incrementalCursorField: "updated_at",
  overridden: false,
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<DataSourcesSection />, { wrapper });
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.can.mockReturnValue(true);
  mocks.listRuns.mockResolvedValue([TARGETED, run()]);
  mocks.readRun.mockResolvedValue(TARGETED);
  mocks.startRun.mockResolvedValue(run());
  mocks.listBindings.mockResolvedValue([BINDING]);
  mocks.rebind.mockResolvedValue(undefined);
  mocks.clear.mockResolvedValue(undefined);
});

describe("source bindings", () => {
  it("lists the declared asset and override state per dataset", async () => {
    renderPage();
    expect(await screen.findByText("source_sales")).toBeInTheDocument();
    expect(screen.getByText(/MONGODB · salesInv/)).toBeInTheDocument();
    expect(screen.queryByText("Overridden")).not.toBeInTheDocument();
  });

  it("shows the override badge and a Clear control for an overridden dataset", async () => {
    mocks.listBindings.mockResolvedValue([{ ...BINDING, overridden: true }]);
    renderPage();
    expect(await screen.findByText("Overridden")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
  });

  it("rebinds a dataset with the edited fields", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Rebind" }));

    const assetIdField = screen.getByRole("textbox", { name: /Source asset id/ });
    fireEvent.change(assetIdField, { target: { value: "salesInvV2" } });
    // The row's own toggle reads "Cancel" once the form is open, so this is
    // unambiguously the form's own submit button.
    fireEvent.click(screen.getByRole("button", { name: "Rebind" }));

    await waitFor(() => { expect(mocks.rebind).toHaveBeenCalledTimes(1); });
    const [dataset, input] = mocks.rebind.mock.calls[0] as [string, { sourceAssetId: string }];
    expect(dataset).toBe("source_sales");
    expect(input.sourceAssetId).toBe("salesInvV2");
  });

  it("clears an override after confirmation", async () => {
    mocks.listBindings.mockResolvedValue([{ ...BINDING, overridden: true }]);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Clear" }));
    await waitFor(() => { expect(mocks.clear).toHaveBeenCalledWith("source_sales"); });
  });

  it("does not offer rebind or clear without config.source.rebind", async () => {
    mocks.listBindings.mockResolvedValue([{ ...BINDING, overridden: true }]);
    mocks.can.mockImplementation((capability: string) => capability !== "config.source.rebind");
    renderPage();

    await screen.findByText("source_sales");
    expect(screen.queryByRole("button", { name: "Rebind" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear" })).not.toBeInTheDocument();
  });
});

describe("sync (moved from SyncControlPage)", () => {
  it("lists scheduled and agent-initiated runs in one history", async () => {
    renderPage();

    // The inline summary panel (this screen's replacement for the original
    // `DomainRail` portal, which rendered nothing in a bare test) also shows
    // the newest run's mode, so "ON_DEMAND" appears twice on the page --
    // the run-list row is the one with a button role.
    expect(await screen.findByRole("button", { name: /ON_DEMAND/ })).toBeTruthy();
    expect(screen.getByText("FULL")).toBeTruthy();
  });

  it("says which conversation caused a targeted run", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /ON_DEMAND/ }));

    expect(await screen.findByText("conv-7")).toBeTruthy();
    expect(screen.getByText("exact_order_key")).toBeTruthy();
  });

  it("shows the anchor's fields and never the anchor's value", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /ON_DEMAND/ }));

    expect(await screen.findByText("order_key")).toBeTruthy();
    expect(screen.queryByText(/CW\d/)).toBeNull();
  });

  it("calls out a run that completed without writing anything", async () => {
    mocks.readRun.mockResolvedValue(run({ nodeWrites: 0, relationshipWrites: 0 }));
    renderPage();
    fireEvent.click(await screen.findByText("FULL"));

    expect(await screen.findByRole("status")).toHaveTextContent(
      /completed without writing anything/i,
    );
  });

  it("sends the chosen scope and record limit", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /sync now/i }));
    fireEvent.change(screen.getByLabelText(/scope/i), { target: { value: "SOURCE_MONGODB" } });
    fireEvent.change(screen.getByLabelText(/records per source/i), { target: { value: "50" } });
    fireEvent.click(screen.getByRole("button", { name: /start sync/i }));

    await waitFor(() => { expect(mocks.startRun).toHaveBeenCalledTimes(1); });
    expect(mocks.startRun).toHaveBeenCalledWith({
      mode: "SOURCE_MONGODB",
      incremental: false,
      maxRecordsPerAsset: 50,
    });
  });

  it("defaults to rereading everything rather than resuming", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /sync now/i }));

    expect(screen.getByLabelText(/^read$/i)).toHaveValue("full");
  });

  it("sends an incremental run when the operator asks for one", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /sync now/i }));
    fireEvent.change(screen.getByLabelText(/^read$/i), { target: { value: "incremental" } });
    fireEvent.click(screen.getByRole("button", { name: /start sync/i }));

    await waitFor(() => { expect(mocks.startRun).toHaveBeenCalledTimes(1); });
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

    await waitFor(() => { expect(mocks.startRun).toHaveBeenCalledTimes(1); });
    expect(mocks.startRun).toHaveBeenCalledWith({ mode: "FULL", incremental: false });
  });

  it("does not call out an incremental run that had nothing to do", async () => {
    mocks.readRun.mockResolvedValue(
      run({ nodeWrites: 0, relationshipWrites: 0, recordScope: "INCREMENTAL" }),
    );
    renderPage();
    fireEvent.click(await screen.findByText("FULL"));

    await screen.findByText("Only what changed");
    expect(screen.queryByText(/completed without writing anything/i)).toBeNull();
  });

  it("names the sources an incremental run could not resume", async () => {
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
    mocks.listRuns.mockRejectedValue(new Error("Graph synchronization is unavailable."));
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Graph synchronization is unavailable.",
    );
    expect(screen.queryByText(/No sync runs recorded/i)).toBeNull();
  });
});
