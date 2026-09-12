/**
 * `/config/deployment` (CFG-6): load, reorder the provider order, DiffPreview,
 * publish through `POST /api/config/publish` on RETURN_PLATFORM, and a
 * production-refused option rendering its reason.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CapabilityContext } from "../../hooks/capabilityContext";
import { DeploymentSection } from "./DeploymentSection";

const mocks = vi.hoisted(() => ({ runtime: vi.fn(), validateDomain: vi.fn(), publish: vi.fn() }));

vi.mock("../../api/configuration", () => ({
  configApi: { runtime: mocks.runtime, validateDomain: mocks.validateDomain, publish: mocks.publish },
}));

function configuration(overrides: Record<string, unknown> = {}) {
  return {
    deployment: {
      ai: {
        provider_order: ["GOOGLE", "NVIDIA", "SIMULATOR"],
        model_pools: { NVIDIA: { lightweight: ["small-model"], standard: ["big-model"] } },
        google: { thinking_budget: 2048, response_schema: false },
      },
      dependencies: { omc: "SIMULATED", parcel: "SIMULATED", freight: "SIMULATED", lsi: "SIMULATED" },
      feedback_learning: { enabled: true },
      support_ticket: { mode: "INTERNAL", base_url: null },
    },
    runtime_integrations: {
      ai_providers: [{ provider_key: "GOOGLE", enabled: true }],
    },
    ...overrides,
  };
}

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
  grants = ["config.runtime.read", "config.release.write", "config.release.promote"];
  mocks.runtime.mockReset().mockResolvedValue({
    release_id: "rel-1",
    head_revision: 41,
    environment: "development",
    configuration: configuration(),
  });
  mocks.validateDomain.mockReset().mockResolvedValue({ valid: true, errors: [] });
  mocks.publish.mockReset().mockResolvedValue({
    release_id: "publish-1",
    status: "RELEASED",
    created_at: "2026-09-01T00:00:00Z",
    created_by: "operator",
    checksum_sha256: "abc",
    domains: {},
    head_revision: 42,
    audit_ids: ["a1"],
  });
});

describe("Deployment screen", () => {
  it("loads the active release's deployment slice", async () => {
    render(<DeploymentSection />, { wrapper: Wrapper });
    const list = await screen.findByRole("list", { name: "Provider order" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual(["GOOGLE", "NVIDIA", "SIMULATOR"]);
  });

  it("renders a governed provider's model pool read-only", async () => {
    render(<DeploymentSection />, { wrapper: Wrapper });
    await screen.findByRole("list", { name: "Provider order" });
    expect(screen.getByText(/Governed by the AI Control Center provider bindings/)).toBeInTheDocument();
    // NVIDIA is not governed in this fixture, so it gets an editable field.
    expect(screen.getByRole("textbox", { name: "NVIDIA standard models" })).toBeInTheDocument();
  });

  it("reorders the provider order with the keyboard and publishes the new order", async () => {
    const user = userEvent.setup();
    render(<DeploymentSection />, { wrapper: Wrapper });
    await screen.findByRole("list", { name: "Provider order" });

    // OrderedList's real mechanism -- not drag and drop, which jsdom cannot
    // fire anyway.
    await user.click(screen.getByRole("button", { name: "Move NVIDIA up" }));

    const list = screen.getByRole("list", { name: "Provider order" });
    expect(within(list).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "NVIDIA",
      "GOOGLE",
      "SIMULATOR",
    ]);

    await user.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => { expect(mocks.publish).toHaveBeenCalled(); });
    const [options] = mocks.publish.mock.calls[0] as [
      { domainKey: string; patch: Record<string, unknown>; expectedHeadRevision: number },
    ];
    expect(options.domainKey).toBe("RETURN_PLATFORM");
    expect(options.expectedHeadRevision).toBe(41);
    const deploymentPatch = options.patch.deployment as Record<string, unknown>;
    const aiPatch = deploymentPatch.ai as Record<string, unknown>;
    expect(aiPatch.provider_order).toEqual(["NVIDIA", "GOOGLE", "SIMULATOR"]);

    expect(await screen.findByText(/Release publish-1 is published/)).toBeInTheDocument();
  });

  it("disables SIMULATED with its reason on every dependency mode in production, not hidden", async () => {
    mocks.runtime.mockResolvedValue({
      release_id: "rel-1",
      head_revision: 41,
      environment: "production",
      configuration: configuration(),
    });
    render(<DeploymentSection />, { wrapper: Wrapper });

    const omc = await screen.findByRole("combobox", { name: "OMC" });
    const simulated = within(omc).getByRole("option", { name: /Simulated/ });
    expect(simulated).toBeDisabled();
    expect(simulated.textContent).toContain("forbidden in production");
  });

  it("does not disable SIMULATED outside production", async () => {
    render(<DeploymentSection />, { wrapper: Wrapper });
    const omc = await screen.findByRole("combobox", { name: "OMC" });
    const simulated = within(omc).getByRole("option", { name: "Simulated" });
    expect(simulated).not.toBeDisabled();
  });

  it("toggles feedback learning and includes it in the published patch", async () => {
    const user = userEvent.setup();
    render(<DeploymentSection />, { wrapper: Wrapper });

    const toggle = await screen.findByRole("checkbox", { name: "Feedback learning enabled" });
    expect(toggle).toBeChecked();
    await user.click(toggle);

    await user.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => { expect(mocks.publish).toHaveBeenCalled(); });
    const [options] = mocks.publish.mock.calls[0] as [{ patch: Record<string, unknown> }];
    const deploymentPatch = options.patch.deployment as Record<string, unknown>;
    expect((deploymentPatch.feedback_learning as Record<string, unknown>).enabled).toBe(false);
  });

  it("disables Publish when config.release.promote is missing", async () => {
    grants = ["config.runtime.read", "config.release.write"];
    render(<DeploymentSection />, { wrapper: Wrapper });
    await screen.findByRole("list", { name: "Provider order" });
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  it("disables every typed field and shows the read-only notice when config.release.write is missing", async () => {
    grants = ["config.runtime.read", "config.release.promote"];
    render(<DeploymentSection />, { wrapper: Wrapper });

    await screen.findByRole("list", { name: "Provider order" });
    expect(screen.getByRole("combobox", { name: "OMC" })).toBeDisabled();
    expect(
      screen.getByText(/Read-only access\. Editing this section requires config\.release\.write\./),
    ).toBeInTheDocument();
  });
});
