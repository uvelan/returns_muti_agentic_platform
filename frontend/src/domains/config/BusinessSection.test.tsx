/**
 * The Business tab: every section of the release, editable, published through
 * the one release lifecycle with a patch that names only what moved.
 *
 * Pinned here: the section shown is the active release's own (not invented);
 * the dependency simulation domain publishes whole on its own key; and a
 * section the release does not carry is not offered.
 *
 * CFG-5 removed the tab's last `RETURN_PLATFORM`-keyed group (`platform`:
 * `integrations`/`copilot`, to `/config/integrations`), so the
 * "patch under a key on RETURN_PLATFORM" branch of `onSubmit`
 * (`subject.key !== null`) has no live subject to exercise it through any
 * more -- see `BusinessSection.tsx`'s own note above `BUSINESS_GROUPS`. Not
 * tested here for that reason, rather than pinned against a claim nothing
 * offered can back.
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
    // CFG-4/CFG-5 gave each of these a typed screen; this document still
    // carries them (the release does), but the Business tab no longer
    // offers any of them -- see the "no longer offers" test below.
    discovery: { identification_fields: [], max_candidates: 5 },
    policy_evaluation: { enabled: false, disabled_reason: "paused on this host" },
    housekeeping: { retire_after_days: 30 },
    support: { queue: "support-inbox", ship_via_methods: { CPU: "COUNTER", XPW: "PARCEL" } },
    integrations: { omc_return_create: { enabled: true, topic: "omc.events" } },
    copilot: { order_discovery_agent_id: "order-discovery-agent" },
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

    expect(await screen.findByRole("button", { name: "Dependency simulation" })).toBeInTheDocument();
    // Carried by the release but moved to typed screens -- pointed at, not offered.
    expect(screen.queryByRole("button", { name: "Housekeeping" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Support" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Integrations" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copilot" })).not.toBeInTheDocument();
    // Never in the snapshot at all: never offered, because the tab invents nothing.
    expect(screen.queryByRole("button", { name: "Workflow" })).not.toBeInTheDocument();
    // Has a screen of its own: pointed at, not offered.
    expect(screen.getByText(/Edited on their own screens/)).toHaveTextContent("agents");
  });

  // CFG-4: discovery, return_policy/return_eligibility_policy/
  // policy_evaluation, and shipment_tracking/bay/omc each gained a typed
  // screen; CFG-5: workflow/return_case/business_calendars/housekeeping,
  // support/support_gate/support_ingress/support_resolver/context_assembly,
  // and integrations/copilot did too. The Business tab no longer offers any
  // of them, even for a section the release still carries (`discovery`,
  // `housekeeping`, `support`, `integrations`, `copilot`, in `SNAPSHOT`
  // above) -- a second write path to the same field once a typed one exists
  // is exactly what this tab's own module docstring says it is not for.
  it("no longer offers discovery, return-policy, fulfilment, workflow, support or platform sections -- they moved to typed screens", async () => {
    render(<BusinessSection />, { wrapper: Wrapper });
    await screen.findByRole("button", { name: "Dependency simulation" });

    for (const label of [
      "Discovery",
      "Source resolution",
      "Clarification policy",
      "Selection vocabulary",
      "Return policy",
      "Eligibility policy",
      "Policy evaluation",
      "Shipment tracking",
      "Bay placement",
      "Order management",
      "Workflow",
      "Return case",
      "Business calendars",
      "Housekeeping",
      "Support",
      "Support gate",
      "Support ingress",
      "Support resolver",
      "Context assembly",
      "Integrations",
      "Copilot",
    ]) {
      expect(screen.queryByRole("button", { name: label })).not.toBeInTheDocument();
    }

    // Pointed at instead, the same way agents already is.
    const elsewhere = screen.getByText(/Edited on their own screens/);
    expect(elsewhere).toHaveTextContent("discovery");
    expect(elsewhere).toHaveTextContent("Discovery tab");
    expect(elsewhere).toHaveTextContent("policy_evaluation");
    expect(elsewhere).toHaveTextContent("Return Policy tab");
    expect(elsewhere).toHaveTextContent("housekeeping");
    expect(elsewhere).toHaveTextContent("Workflow tab");
    expect(elsewhere).toHaveTextContent("support");
    expect(elsewhere).toHaveTextContent("Support tab");
    expect(elsewhere).toHaveTextContent("integrations");
    expect(elsewhere).toHaveTextContent("copilot");
    expect(elsewhere).toHaveTextContent("Integrations tab");
  });

  it("loads the active release's section rather than an invented one", async () => {
    render(<BusinessSection />, { wrapper: Wrapper });

    // No section click needed: with only one group left, its one subject
    // opens by default.
    expect(await screen.findByDisplayValue("Simulated")).toBeInTheDocument();
    expect(screen.getByDisplayValue(0)).toBeInTheDocument();
    expect(screen.getByText(/Release rel-7/)).toBeInTheDocument();
  });
});

describe("publishing a section", () => {
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
    expect(mocks.promote).toHaveBeenLastCalledWith(releaseId, "RELEASED", 66);
    expect(await screen.findByRole("status")).toHaveTextContent(/is published/);
  });

  it("sends a removed entry as null, which sending the section whole never could", async () => {
    const user = userEvent.setup();
    render(<BusinessSection />, { wrapper: Wrapper });

    await openSection(user, "Dependency simulation");
    await screen.findByDisplayValue("Simulated");
    await replaceJson(user, "Dependency simulation JSON", {
      ...SNAPSHOT.dependency_simulation_configuration,
      dependencies: {},
    });
    await user.click(screen.getByRole("button", { name: /publish release/i }));

    await waitFor(() => { expect(mocks.patchDomain).toHaveBeenCalled(); });
    const [, , patch] = mocks.patchDomain.mock.calls[0] as [string, string, unknown];
    expect(patch).toEqual({ dependencies: { OMC: null } });
  });

  it("is read-only without the promote capability", async () => {
    grants = ["config.runtime.read"];
    render(<BusinessSection />, { wrapper: Wrapper });

    await screen.findByDisplayValue("Simulated");
    expect(await screen.findByText(/Read-only access/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /publish release/i })).toBeDisabled();
  });
});
