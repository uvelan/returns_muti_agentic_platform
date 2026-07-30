import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  applySeed,
  cancelSeedOperation,
  deleteSeedData,
  getSeedOperation,
  getSeedStatus,
} from "../../api/operations";
import type { SeedOperation, SeedStatus } from "../../contracts/operations";
import { ToastProvider } from "../../components/ToastProvider";
import { SeedDataPage } from "./SystemPages";

vi.mock("../../api/operations", () => ({
  applySeed: vi.fn(),
  cancelSeedOperation: vi.fn(),
  deleteSeedData: vi.fn(),
  getOperationalDependency: vi.fn(),
  getSeedOperation: vi.fn(),
  getSeedStatus: vi.fn(),
  listOperationalDependencies: vi.fn(),
}));

const seedStatus: SeedStatus = {
  version: "e2e-v2",
  digest: "seed-digest",
  appliedAt: "2026-07-30T00:00:00Z",
  appliedBy: "seed-admin",
  ready: true,
  counts: { customers: 1_000, products: 1_000, orders: 1_000 },
  scenarioCounts: { positive: 5, negative: 3, reviewRequired: 2, total: 10 },
  validationErrors: [],
  requestedRecordLimit: 1_000,
};

const idleOperation: SeedOperation = {
  operationId: null,
  kind: null,
  status: "IDLE",
  requestedRecordLimit: null,
  processedRecords: 0,
  totalRecords: 0,
  phase: "Idle",
  startedAt: null,
  finishedAt: null,
  error: null,
};

function renderSeedPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <SeedDataPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("SeedDataPage controls", () => {
  it("submits the selected record limit and supports seed-only deletion", async () => {
    vi.mocked(getSeedStatus).mockResolvedValue(seedStatus);
    vi.mocked(getSeedOperation).mockResolvedValue(idleOperation);
    vi.mocked(applySeed).mockResolvedValue(seedStatus);
    vi.mocked(deleteSeedData).mockResolvedValue({
      ...seedStatus,
      digest: "",
      ready: false,
      requestedRecordLimit: null,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderSeedPage();
    const input = await screen.findByLabelText("Maximum records per seeded dataset");
    fireEvent.change(input, { target: { value: "2500" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply seed" }));

    await waitFor(() => {
      expect(vi.mocked(applySeed).mock.calls[0]?.[0]).toEqual({ recordLimit: 2_500 });
    });

    fireEvent.click(screen.getByRole("button", { name: "Delete all seed data" }));
    await waitFor(() => {
      expect(deleteSeedData).toHaveBeenCalledOnce();
    });
  });

  it("lets an operator stop a running seed operation", async () => {
    const runningOperation: SeedOperation = {
      ...idleOperation,
      operationId: "seed-op-1",
      kind: "APPLY",
      status: "RUNNING",
      requestedRecordLimit: 100_000,
      processedRecords: 12_000,
      totalRecords: 340_000,
      phase: "Writing salesInv",
      startedAt: "2026-07-30T00:00:00Z",
    };
    vi.mocked(getSeedStatus).mockResolvedValue(seedStatus);
    vi.mocked(getSeedOperation).mockResolvedValue(runningOperation);
    vi.mocked(cancelSeedOperation).mockResolvedValue({
      ...runningOperation,
      status: "CANCELLING",
      phase: "Stopping at a safe boundary",
    });

    renderSeedPage();
    const stop = await screen.findByRole("button", { name: "Stop process" });
    fireEvent.click(stop);

    await waitFor(() => {
      expect(cancelSeedOperation).toHaveBeenCalledOnce();
    });
  });
});
