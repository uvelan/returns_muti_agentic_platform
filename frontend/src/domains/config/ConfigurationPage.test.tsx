/**
 * The Configuration screen's promotion controls, and a bug they uncovered.
 *
 * **`ReleaseStatus` named the wrong lifecycle.** It listed the Mongo one --
 * `APPROVED`, `ACTIVE`, `REJECTED` -- which D3 deleted after finding
 * `ReleaseService` was constructed nowhere outside a test. The Overview tab
 * searched for `status === "ACTIVE"`, matched nothing, and reported "No ACTIVE
 * release found" in every deployment including ones with a published release.
 * Nothing failed, because the field is optional and the values are strings. The
 * first test below fails against the old union.
 *
 * **The promotion buttons come from the transition table, not from a guess.**
 * `ALLOWED_PROMOTIONS` mirrors `graph_repository.RELEASE_TRANSITIONS`, so an
 * operator is never shown a promotion the backend will refuse. The tests assert
 * per-status, because the tempting simplification is one fixed set of buttons.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as ActualModuleNamespace from "../../api/configuration";

type ActualModule = typeof ActualModuleNamespace;

import { ConfigurationPage } from "./ConfigurationPage";

const mocks = vi.hoisted(() => ({
  runtime: vi.fn(),
  releases: vi.fn(),
  release: vi.fn(),
  sources: vi.fn(),
  audit: vi.fn(),
  promote: vi.fn(),
  can: vi.fn(),
}));

vi.mock("../../api/configuration", async (importOriginal) => {
  const actual = await importOriginal<ActualModule>();
  return {
    ...actual,
    configApi: {
      runtime: mocks.runtime,
      releases: mocks.releases,
      release: mocks.release,
      sources: mocks.sources,
      audit: mocks.audit,
      promote: mocks.promote,
    },
  };
});

vi.mock("../../hooks/capabilityContext", () => ({
  useCapabilities: () => ({ can: mocks.can }),
}));

function release(status: string) {
  return {
    release_id: "rel-1",
    status,
    checksum: "abc123",
    created_at: "2026-08-10T10:00:00Z",
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}


/**
 * Put the screen on a section.
 *
 * Sections moved from local state into the URL when the sidebar took over
 * navigation, so a test selects one by being at its path rather than by
 * clicking a tab that no longer exists. `wouter` reads `window.location`, so
 * pushing history before render is all that is required -- and it exercises
 * the same deep-link path a bookmark would.
 */
function goToSection(domainPath: string, slug: string): void {
  window.history.pushState(null, "", `${domainPath}/${slug}`);
}

async function openRelease(status: string) {
  mocks.releases.mockResolvedValue([release(status)]);
  mocks.release.mockResolvedValue(release(status));
  goToSection("/config", "releases");
  render(<ConfigurationPage />, { wrapper });
  fireEvent.click(await screen.findByText("rel-1"));
  await screen.findByText("Checksum");
}

describe("Configuration promotion", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.can.mockReturnValue(true);
    mocks.runtime.mockResolvedValue({});
    mocks.releases.mockResolvedValue([]);
    mocks.sources.mockResolvedValue([]);
    mocks.audit.mockResolvedValue([]);
    mocks.promote.mockResolvedValue({ release_id: "rel-1", status: "VALIDATED" });
  });

  it("recognises RELEASED as the published status", async () => {
    // Against the old union this said "No RELEASED release found", because the
    // page looked for "ACTIVE" -- a status the graph lifecycle never produces.
    mocks.releases.mockResolvedValue([release("RELEASED")]);
    render(<ConfigurationPage />, { wrapper });

    expect(await screen.findByText("rel-1")).toBeInTheDocument();
    expect(screen.queryByText(/No RELEASED release found/)).toBeNull();
  });

  it("offers VALIDATED and ARCHIVED from DRAFT", async () => {
    await openRelease("DRAFT");

    expect(screen.getByRole("button", { name: "VALIDATED" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ARCHIVED" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "RELEASED" })).toBeNull();
  });

  it("offers RELEASED and ARCHIVED from VALIDATED", async () => {
    await openRelease("VALIDATED");

    expect(screen.getByRole("button", { name: "RELEASED" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ARCHIVED" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "VALIDATED" })).toBeNull();
  });

  it("offers nothing from RELEASED", async () => {
    // A release leaves RELEASED by being superseded, which publishing its
    // successor does -- not by a promotion of its own.
    await openRelease("RELEASED");

    expect(await screen.findByText(/No promotion is available from RELEASED/)).toBeInTheDocument();
  });

  it("promotes to VALIDATED without a head revision", async () => {
    await openRelease("DRAFT");

    fireEvent.click(screen.getByRole("button", { name: "VALIDATED" }));

    await waitFor(() => {
      expect(mocks.promote).toHaveBeenCalledWith("rel-1", "VALIDATED", undefined);
    });
  });

  it("requires the head revision before publishing", async () => {
    // The backend rejects a RELEASED promotion without it. Enforcing it here
    // saves a round trip; the backend remains the boundary.
    await openRelease("VALIDATED");

    expect(screen.getByRole("button", { name: "RELEASED" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Expected head revision"), {
      target: { value: "7" },
    });
    expect(screen.getByRole("button", { name: "RELEASED" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "RELEASED" }));
    await waitFor(() => {
      expect(mocks.promote).toHaveBeenCalledWith("rel-1", "RELEASED", 7);
    });
  });

  it("shows the backend's refusal verbatim", async () => {
    mocks.promote.mockRejectedValue(
      new Error("Release is missing behavior domains: ai_gateway"),
    );
    await openRelease("DRAFT");

    fireEvent.click(screen.getByRole("button", { name: "VALIDATED" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("missing behavior domains");
  });

  it("offers no promotion without the capability", async () => {
    mocks.can.mockImplementation((capability: string) => capability !== "config.release.promote");
    await openRelease("DRAFT");

    expect(await screen.findByText(/requires config.release.promote/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "VALIDATED" })).toBeNull();
  });
});

describe("Configuration tabs D3 backed", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.can.mockReturnValue(true);
    mocks.runtime.mockResolvedValue({});
    mocks.releases.mockResolvedValue([]);
    mocks.sources.mockResolvedValue([{ sourceId: "mongo_main", status: "HEALTHY" }]);
    mocks.audit.mockResolvedValue([{ action: "PROMOTE_RELEASE" }]);
  });

  it("no longer serves data sources, and does not read them", async () => {
    // Data Sources is the `/data-sources` domain now. The old slug is a stale
    // bookmark: `useDomainSection` resolves an unrecognised one to the first
    // section, so it lands on Overview rather than on a blank screen -- and
    // nothing here calls the sources route any more, which is what stops this
    // screen from being a second, worse source viewer.
    goToSection("/config", "data-sources");
    render(<ConfigurationPage />, { wrapper });

    await screen.findByText("Runtime snapshot");
    expect(mocks.sources).not.toHaveBeenCalled();
    expect(screen.queryByText(/mongo_main/)).toBeNull();
  });

  it("reads audit through the canonical route", async () => {
    goToSection("/config", "audit");
    render(<ConfigurationPage />, { wrapper });

    await waitFor(() => {
      expect(mocks.audit).toHaveBeenCalled();
    });
    expect(await screen.findByText(/PROMOTE_RELEASE/)).toBeInTheDocument();
  });

  it("says integrations are already served rather than pending", async () => {
    // The distinction matters: "pending" invites someone to build a duplicate
    // endpoint for data the runtime snapshot already carries.
    goToSection("/config", "integrations");
    render(<ConfigurationPage />, { wrapper });

    expect(await screen.findByText(/Already served/)).toBeInTheDocument();
  });
});
