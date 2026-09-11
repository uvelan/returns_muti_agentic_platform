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
    // CFG-4 gave discovery/return_policy/policy_evaluation/shipment_tracking
    // their own typed screens; this document still carries `discovery` (the
    // release does), but the Business tab no longer offers it -- see the
    // "no longer offers" test below.
    discovery: { identification_fields: [], max_candidates: 5 },
    policy_evaluation: { enabled: false, disabled_reason: "paused on this host" },
    // CFG-5 gave workflow and support their own typed screens the same way;
    // these two keys are carried but, like discovery above, no longer
    // offered -- pointed at instead.
    housekeeping: { retire_after_days: 30 },
    support: { queue: "support-inbox", ship_via_methods: { CPU: "COUNTER", XPW: "PARCEL" } },
    // The two sections the "platform" group still offers.
    integrations: {
      topics: {
        omc_events: { enabled: true, topic: "omc.events" },
        ai_events: { enabled: false, topic: "ai.events" },
      },
      ai_may_fabricate_success: false,
    },
    copilot: { order_discovery_agent_id: "order-discovery-agent", candidate_columns: [] },
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

    expect(await screen.findByRole("button", { name: "Integrations" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copilot" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dependency simulation" })).toBeInTheDocument();
    // Carried by the release but moved to typed screens -- pointed at, not offered.
    expect(screen.queryByRole("button", { name: "Housekeeping" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Support" })).not.toBeInTheDocument();
    // Never in the snapshot at all: never offered, because the tab invents nothing.
    expect(screen.queryByRole("button", { name: "Workflow" })).not.toBeInTheDocument();
    // Has a screen of its own: pointed at, not offered.
    expect(screen.getByText(/Edited on their own screens/)).toHaveTextContent("agents");
  });

  // CFG-4: discovery, return_policy/return_eligibility_policy/
  // policy_evaluation, and shipment_tracking/bay/omc each gained a typed
  // screen; CFG-5: workflow/return_case/business_calendars/housekeeping and
  // support/support_gate/support_ingress/support_resolver/context_assembly
  // did too. The Business tab no longer offers any of them, even for a
  // section the release still carries (`discovery`, `housekeeping`,
  // `support`, in `SNAPSHOT` above) -- a second write path to the same field
  // once a typed one exists is exactly what this tab's own module docstring
  // says it is not for.
  it("no longer offers discovery, return-policy, fulfilment, workflow or support sections -- they moved to typed screens", async () => {
    render(<BusinessSection />, { wrapper: Wrapper });
    await screen.findByRole("button", { name: "Integrations" });

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
  });

  it("loads the active release's section rather than an invented one", async () => {
    const user = userEvent.setup();
    render(<BusinessSection />, { wrapper: Wrapper });

    await openSection(user, "Copilot");
    expect(await screen.findByDisplayValue("order-discovery-agent")).toBeInTheDocument();
    expect(screen.getByText(/Release rel-7/)).toBeInTheDocument();
  });
});

describe("publishing a section", () => {
  it("patches only the section, under its key, on the business domain", async () => {
    const user = userEvent.setup();
    render(<BusinessSection />, { wrapper: Wrapper });

    await openSection(user, "Copilot");
    await screen.findByDisplayValue("order-discovery-agent");
    await replaceJson(user, "Copilot JSON", {
      order_discovery_agent_id: "order-discovery-agent-v2",
      candidate_columns: [],
    });
    await user.click(screen.getByRole("button", { name: /publish release/i }));

    await waitFor(() => { expect(mocks.promote).toHaveBeenCalledTimes(2); });
    expect(mocks.createRelease).toHaveBeenCalledTimes(1);
    const [releaseId, domainKey, patch] = mocks.patchDomain.mock.calls[0] as [
      string,
      string,
      Record<string, unknown>,
    ];
    expect(releaseId).toMatch(/^business-copilot-/);
    expect(domainKey).toBe("RETURN_PLATFORM");
    expect(patch).toEqual({ copilot: { order_discovery_agent_id: "order-discovery-agent-v2" } });
    expect(mocks.promote).toHaveBeenLastCalledWith(releaseId, "RELEASED", 66);
    expect(await screen.findByRole("status")).toHaveTextContent(/is published/);
  });

  it("sends a removed entry as null, which sending the section whole never could", async () => {
    const user = userEvent.setup();
    render(<BusinessSection />, { wrapper: Wrapper });

    await openSection(user, "Integrations");
    await screen.findByDisplayValue("omc.events");
    await replaceJson(user, "Integrations JSON", {
      topics: { omc_events: { enabled: true, topic: "omc.events" } },
      ai_may_fabricate_success: false,
    });
    await user.click(screen.getByRole("button", { name: /publish release/i }));

    await waitFor(() => { expect(mocks.patchDomain).toHaveBeenCalled(); });
    const [, , patch] = mocks.patchDomain.mock.calls[0] as [string, string, unknown];
    expect(patch).toEqual({
      integrations: { topics: { ai_events: null } },
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

    await openSection(user, "Copilot");
    expect(await screen.findByText(/Read-only access/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /publish release/i })).toBeDisabled();
  });
});
