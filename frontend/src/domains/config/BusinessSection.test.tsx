/**
 * The Business tab: every section of the release, editable, published through
 * the one release lifecycle with a patch that names only what moved.
 *
 * Pinned here: the section shown is the active release's own (not invented);
 * a publish patches the section under its key on `RETURN_PLATFORM`; an entry
 * removed in the editor is sent as `null` rather than silently kept; the
 * dependency simulation domain publishes whole on its own key; and a section
 * the release does not carry is not offered.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BusinessSection } from "./BusinessSection";
import { CapabilityContext } from "../../hooks/capabilityContext";

const mocks = vi.hoisted(() => ({
  runtime: vi.fn(),
  createRelease: vi.fn(),
  patchDomain: vi.fn(),
  promote: vi.fn(),
}));

vi.mock("../../api/configuration", () => ({
  configApi: {
    runtime: mocks.runtime,
    createRelease: mocks.createRelease,
    patchDomain: mocks.patchDomain,
    promote: mocks.promote,
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

const SNAPSHOT = {
  release_id: "rel-7",
  head_revision: 66,
  configuration: {
    agents: { order_discovery: { enabled: true } },
    discovery: { identification_fields: [], max_candidates: 5 },
    policy_evaluation: { enabled: false, disabled_reason: "paused on this host" },
    return_policy: {
      return_method_derivation: {
        default_method: "PREPAID_PARCEL",
        ship_via_methods: { CPU: "COUNTER", XPW: "PARCEL" },
      },
    },
  },
  dependency_simulation_configuration: {
    enabled: true,
    modeBanner: "Simulated",
    ai: { enabled: true, temperature: 0 },
    dependencies: { OMC: { operations: ["cancel"] } },
  },
};

async function openSection(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(await screen.findByRole("button", { name }));
}

async function replaceJson(user: ReturnType<typeof userEvent.setup>, label: string, json: object) {
  await user.click(screen.getByRole("button", { name: /^json$/i }));
  const editor = screen.getByLabelText(label);
  fireEvent.change(editor, { target: { value: JSON.stringify(json, null, 2) } });
}

beforeEach(() => {
  grants = ["config.runtime.read", "config.release.read", "config.release.promote"];
  mocks.runtime.mockReset().mockResolvedValue(SNAPSHOT);
  mocks.createRelease.mockReset().mockResolvedValue({});
  mocks.patchDomain.mockReset().mockResolvedValue({});
  mocks.promote.mockReset().mockResolvedValue({});
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the sections an operator can edit", () => {
  it("offers the sections the release carries, grouped, and not the ones it lacks", async () => {
    render(<BusinessSection />, { wrapper: Wrapper });

    expect(await screen.findByRole("button", { name: "Discovery" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Policy evaluation" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dependency simulation" })).toBeInTheDocument();
    // Not in the snapshot: never offered, because the tab invents nothing.
    expect(screen.queryByRole("button", { name: "Shipment tracking" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Bay placement" })).not.toBeInTheDocument();
    // Has a screen of its own: pointed at, not offered.
    expect(screen.getByText(/Edited on their own screens/)).toHaveTextContent("agents");
  });

  it("loads the active release's section rather than an invented one", async () => {
    const user = userEvent.setup();
    render(<BusinessSection />, { wrapper: Wrapper });

    await openSection(user, "Policy evaluation");
    expect(await screen.findByDisplayValue("paused on this host")).toBeInTheDocument();
    expect(screen.getByText(/Release rel-7/)).toBeInTheDocument();
  });
});

describe("publishing a section", () => {
  it("patches only the section, under its key, on the business domain", async () => {
    const user = userEvent.setup();
    render(<BusinessSection />, { wrapper: Wrapper });

    await openSection(user, "Policy evaluation");
    await screen.findByDisplayValue("paused on this host");
    await replaceJson(user, "Policy evaluation JSON", { enabled: true, disabled_reason: null });
    await user.click(screen.getByRole("button", { name: /publish release/i }));

    await waitFor(() => { expect(mocks.promote).toHaveBeenCalledTimes(2); });
    expect(mocks.createRelease).toHaveBeenCalledTimes(1);
    const [releaseId, domainKey, patch] = mocks.patchDomain.mock.calls[0] as [
      string,
      string,
      Record<string, unknown>,
    ];
    expect(releaseId).toMatch(/^business-policy_evaluation-/);
    expect(domainKey).toBe("RETURN_PLATFORM");
    // The merge patch names what moved -- and `null` is how the reason leaves.
    expect(patch).toEqual({ policy_evaluation: { enabled: true, disabled_reason: null } });
    expect(mocks.promote).toHaveBeenLastCalledWith(releaseId, "RELEASED", 66);
    expect(await screen.findByRole("status")).toHaveTextContent(/is published/);
  });

  it("sends a removed entry as null, which sending the section whole never could", async () => {
    const user = userEvent.setup();
    render(<BusinessSection />, { wrapper: Wrapper });

    await openSection(user, "Return policy");
    await screen.findByDisplayValue("PREPAID_PARCEL");
    await replaceJson(user, "Return policy JSON", {
      return_method_derivation: {
        default_method: "BRANCH_UPS",
        ship_via_methods: { CPU: "COUNTER" },
      },
    });
    await user.click(screen.getByRole("button", { name: /publish release/i }));

    await waitFor(() => { expect(mocks.patchDomain).toHaveBeenCalled(); });
    const [, , patch] = mocks.patchDomain.mock.calls[0] as [string, string, unknown];
    expect(patch).toEqual({
      return_policy: {
        return_method_derivation: { default_method: "BRANCH_UPS", ship_via_methods: { XPW: null } },
      },
    });
  });

  it("publishes the dependency simulation document whole on its own domain", async () => {
    const user = userEvent.setup();
    render(<BusinessSection />, { wrapper: Wrapper });

    await openSection(user, "Dependency simulation");
    await screen.findByDisplayValue("Simulated");
    await replaceJson(user, "Dependency simulation JSON", {
      ...SNAPSHOT.dependency_simulation_configuration,
      modeBanner: "Simulated (staging)",
    });
    await user.click(screen.getByRole("button", { name: /publish release/i }));

    await waitFor(() => { expect(mocks.patchDomain).toHaveBeenCalled(); });
    const [releaseId, domainKey, patch] = mocks.patchDomain.mock.calls[0] as [
      string,
      string,
      unknown,
    ];
    expect(releaseId).toMatch(/^business-simulation-/);
    expect(domainKey).toBe("DEPENDENCY_SIMULATION");
    expect(patch).toEqual({ modeBanner: "Simulated (staging)" });
  });

  it("is read-only without the promote capability", async () => {
    grants = ["config.runtime.read"];
    const user = userEvent.setup();
    render(<BusinessSection />, { wrapper: Wrapper });

    await openSection(user, "Policy evaluation");
    expect(await screen.findByText(/Read-only access/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /publish release/i })).toBeDisabled();
  });
});
