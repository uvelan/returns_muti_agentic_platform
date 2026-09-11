/**
 * `/config/overview`: the release/head/releases cards (existing), plus
 * (CFG-4) the undecided-keys panel.
 *
 * F5: a bare unit name is a `RETURN_PLATFORM` key; `AI_GATEWAY`/
 * `DEPENDENCY_SIMULATION` units are shown with their domain. F11: the panel
 * must render `would_adopt` correctly whether it is empty (no active
 * release, `RETURN_PLATFORM`'s own shape) or full (the other two domains'
 * fallback-to-packaged shape) in the same response, without treating either
 * as wrong.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CapabilityContext } from "../../hooks/capabilityContext";
import { OverviewSection } from "./OverviewSection";

const mocks = vi.hoisted(() => ({
  runtime: vi.fn(),
  releases: vi.fn(),
  packagedDrift: vi.fn(),
  adoptPackaged: vi.fn(),
}));

vi.mock("../../api/configuration", () => ({
  configApi: {
    runtime: mocks.runtime,
    releases: mocks.releases,
    packagedDrift: mocks.packagedDrift,
    adoptPackaged: mocks.adoptPackaged,
  },
}));

let grants: string[] = [];

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <CapabilityContext.Provider
        value={{
          principal: undefined,
          isLoading: false,
          isUnauthenticated: false,
          error: null,
          can: (capability) => grants.includes(capability),
          canAny: (...capabilities) => capabilities.some((one) => grants.includes(one)),
        }}
      >
        {children}
      </CapabilityContext.Provider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  grants = ["config.runtime.read", "config.release.read", "config.release.write", "config.release.promote"];
  mocks.runtime.mockReset().mockResolvedValue({
    release_id: "rel-1",
    head_revision: 41,
    configuration: {},
  });
  mocks.releases.mockReset().mockResolvedValue([
    { releaseId: "rel-1", status: "RELEASED", createdAt: "2026-08-01T00:00:00Z", createdBy: "op", checksumSha256: "a" },
  ]);
  mocks.packagedDrift.mockReset().mockResolvedValue({
    // RETURN_PLATFORM: no active release to merge against -- empty would_adopt.
    RETURN_PLATFORM: { undecided: ["discovery"], would_adopt: [], filled_leaves: [] },
    // AI_GATEWAY/DEPENDENCY_SIMULATION: fall back to the packaged file itself
    // in that same state -- every unit reported in would_adopt.
    AI_GATEWAY: { undecided: [], would_adopt: ["tasks.T1", "other"], filled_leaves: [] },
    DEPENDENCY_SIMULATION: { undecided: [], would_adopt: ["dependencies.OMC"], filled_leaves: [] },
  });
  mocks.adoptPackaged.mockReset().mockResolvedValue({
    release_id: "adopt-1",
    status: "RELEASED",
    created_at: "2026-09-01T00:00:00Z",
    created_by: "operator",
    checksum_sha256: "abc",
    domains: {},
    head_revision: 42,
    undecided: { RETURN_PLATFORM: [], AI_GATEWAY: [], DEPENDENCY_SIMULATION: [] },
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("Overview screen -- undecided keys panel", () => {
  it("labels a RETURN_PLATFORM unit bare, and other domains' units with their domain (F5)", async () => {
    render(<OverviewSection canReadReleases />, { wrapper: Wrapper });
    expect(await screen.findByText("RETURN_PLATFORM: discovery")).toBeInTheDocument();
  });

  it("renders both would_adopt shapes in one response without treating either as wrong (F11)", async () => {
    render(<OverviewSection canReadReleases />, { wrapper: Wrapper });

    // RETURN_PLATFORM's own would_adopt is empty in this state -- not shown
    // in the "would adopt" list at all, and that is correct, not a bug.
    await screen.findByText("RETURN_PLATFORM: discovery");
    expect(screen.queryByText(/RETURN_PLATFORM:.*would/)).not.toBeInTheDocument();

    // AI_GATEWAY/DEPENDENCY_SIMULATION report every packaged unit in the same
    // response -- both shapes rendered side by side, neither suppressed.
    expect(screen.getByText("AI_GATEWAY: tasks.T1")).toBeInTheDocument();
    expect(screen.getByText("AI_GATEWAY: other")).toBeInTheDocument();
    expect(screen.getByText("DEPENDENCY_SIMULATION: dependencies.OMC")).toBeInTheDocument();
  });

  it("states the vocabulary explicitly: units are per domain, and a bare name means RETURN_PLATFORM (F5)", async () => {
    render(<OverviewSection canReadReleases />, { wrapper: Wrapper });
    await screen.findByText("RETURN_PLATFORM: discovery");
    expect(screen.getByText(/a key with no domain prefix is a/i)).toBeInTheDocument();
  });

  it("adopts the packaged file for an undecided key, confirms first, and shows the success notice", async () => {
    const user = userEvent.setup();
    render(<OverviewSection canReadReleases />, { wrapper: Wrapper });

    const button = await screen.findByRole("button", { name: "Take packaged file" });
    await user.click(button);

    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => { expect(mocks.adoptPackaged).toHaveBeenCalledWith(["discovery"], 41); });
    expect(await screen.findByText(/Adopted discovery from the packaged file/)).toBeInTheDocument();
  });

  it("does not adopt when the confirm dialog is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<OverviewSection canReadReleases />, { wrapper: Wrapper });

    const button = await screen.findByRole("button", { name: "Take packaged file" });
    await user.click(button);

    expect(mocks.adoptPackaged).not.toHaveBeenCalled();
  });

  it("requires config.release.write to see the panel at all", async () => {
    grants = ["config.runtime.read", "config.release.read"];
    render(<OverviewSection canReadReleases />, { wrapper: Wrapper });
    expect(await screen.findByText(/requires config.release.write/)).toBeInTheDocument();
    expect(mocks.packagedDrift).not.toHaveBeenCalled();
  });
});
