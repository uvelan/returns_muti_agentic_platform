/**
 * `/config/simulation`: load the active release's `DEPENDENCY_SIMULATION`
 * domain document (a sibling of `configuration`, not a key inside it), edit
 * a typed field, Validate (errors mapped onto the field), Publish through
 * the single-call `/api/config/publish` against the `DEPENDENCY_SIMULATION`
 * domain with `expected_head_revision` from the loaded snapshot, and see
 * the success notice. Same shape as `WorkflowSection.test.tsx`.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { APIError } from "../../api/client";
import { CapabilityContext } from "../../hooks/capabilityContext";
import { SimulationSection } from "./SimulationSection";

const mocks = vi.hoisted(() => ({
  runtime: vi.fn(),
  validateDomain: vi.fn(),
  publish: vi.fn(),
}));

vi.mock("../../api/configuration", () => ({
  configApi: {
    runtime: mocks.runtime,
    validateDomain: mocks.validateDomain,
    publish: mocks.publish,
  },
}));

const DEPENDENCY_SIMULATION = {
  schemaVersion: "1.0",
  enabled: true,
  templateVersion: "2026.1",
  modeBanner: "Simulated dependencies -- no external system is actually called.",
  defaultScenario: "SUCCESS",
  ai: {
    enabled: true,
    taskId: "SIMULATOR_OPERATION_NARRATIVE_V1",
    providerOrder: ["GOOGLE", "NVIDIA"],
    timeoutSeconds: 4,
    maxOutputTokens: 256,
    temperature: 0,
    fallbackAlwaysEnabled: true,
    pricingMicrousdPerMillionTokens: {},
  },
  dependencies: {
    OMC: { operations: ["cancel"], statusSequence: ["PENDING", "COMPLETE"] },
    PARCEL: { operations: ["book"], statusSequence: ["BOOKED"] },
    FREIGHT: { operations: ["book"], statusSequence: ["BOOKED"] },
    LSI: { operations: ["notify"], statusSequence: ["SENT"] },
  },
};

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
    configuration: {},
    dependency_simulation_configuration: DEPENDENCY_SIMULATION,
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
    audit_ids: ["a1", "a2", "a3", "a4", "a5"],
  });
});

describe("Simulation screen", () => {
  it("loads the active release's dependency-simulation document, from its own sibling key", async () => {
    render(<SimulationSection />, { wrapper: Wrapper });
    expect(await screen.findByDisplayValue(DEPENDENCY_SIMULATION.modeBanner)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Enabled" })).toBeChecked();
  });

  it("maps a Validate error onto the field it names", async () => {
    const user = userEvent.setup();
    mocks.validateDomain.mockResolvedValue({
      valid: false,
      errors: [{ path: "modeBanner", message: "Too short.", type: "value_error" }],
    });
    render(<SimulationSection />, { wrapper: Wrapper });

    const field = await screen.findByDisplayValue(DEPENDENCY_SIMULATION.modeBanner);
    await user.clear(field);
    await user.type(field, "short");
    await user.click(screen.getByRole("button", { name: "Validate" }));

    await waitFor(() => { expect(mocks.validateDomain).toHaveBeenCalled(); });
    const [domainKey, body] = mocks.validateDomain.mock.calls[0] as [string, { patch: Record<string, unknown> }];
    expect(domainKey).toBe("DEPENDENCY_SIMULATION");
    expect(body.patch.modeBanner).toBe("short");

    expect(await screen.findAllByText("Too short.")).not.toHaveLength(0);
    expect(field).toHaveAttribute("aria-invalid", "true");
  });

  it("publishes the merge patch on DEPENDENCY_SIMULATION with the loaded head revision, and shows the success notice", async () => {
    const user = userEvent.setup();
    render(<SimulationSection />, { wrapper: Wrapper });

    const field = await screen.findByDisplayValue(DEPENDENCY_SIMULATION.modeBanner);
    await user.clear(field);
    await user.type(field, "A revised simulation banner for the operator to see.");

    await user.click(screen.getByRole("button", { name: "Publish" }));

    await waitFor(() => { expect(mocks.publish).toHaveBeenCalled(); });
    const [options] = mocks.publish.mock.calls[0] as [
      { domainKey: string; patch: Record<string, unknown>; expectedHeadRevision: number },
    ];
    expect(options.domainKey).toBe("DEPENDENCY_SIMULATION");
    expect(options.expectedHeadRevision).toBe(41);
    expect(options.patch.modeBanner).toBe("A revised simulation banner for the operator to see.");
    // Nothing else moved.
    expect(options.patch.dependencies).toBeUndefined();

    expect(await screen.findByText(/Release publish-1 is published/)).toBeInTheDocument();
  });

  it("edits one dependency's operations without touching the other three", async () => {
    const user = userEvent.setup();
    render(<SimulationSection />, { wrapper: Wrapper });

    await screen.findByDisplayValue(DEPENDENCY_SIMULATION.modeBanner);
    const omcOperations = screen.getByRole("textbox", { name: /OMC operations/ });
    await user.type(omcOperations, "reschedule");
    await user.keyboard("{Enter}");

    await user.click(screen.getByRole("button", { name: "Publish" }));

    await waitFor(() => { expect(mocks.publish).toHaveBeenCalled(); });
    const [options] = mocks.publish.mock.calls[0] as [{ patch: Record<string, unknown> }];
    const dependencies = options.patch.dependencies as Record<string, unknown>;
    expect(dependencies.OMC).toEqual({ operations: ["cancel", "reschedule"] });
    expect(dependencies.PARCEL).toBeUndefined();
  });

  it("disables Publish when config.release.promote is missing", async () => {
    grants = ["config.runtime.read", "config.release.write"];
    render(<SimulationSection />, { wrapper: Wrapper });
    await screen.findByDisplayValue(DEPENDENCY_SIMULATION.modeBanner);
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  it("disables every typed field and shows the read-only notice when config.release.write is missing", async () => {
    grants = ["config.runtime.read", "config.release.promote"];
    render(<SimulationSection />, { wrapper: Wrapper });

    const field = await screen.findByDisplayValue(DEPENDENCY_SIMULATION.modeBanner);
    expect(field).toBeDisabled();
    expect(screen.getByText(/Read-only access\. Editing this section requires config\.release\.write\./)).toBeInTheDocument();
  });

  it("shows the error and keeps the draft when Publish is refused", async () => {
    const user = userEvent.setup();
    mocks.publish.mockRejectedValue(new APIError("Domain patch refused: modeBanner too short", 422));
    render(<SimulationSection />, { wrapper: Wrapper });

    const field = await screen.findByDisplayValue(DEPENDENCY_SIMULATION.modeBanner);
    await user.clear(field);
    await user.type(field, "A revised simulation banner.");
    await user.click(screen.getByRole("button", { name: "Publish" }));

    expect(await screen.findByText(/Domain patch refused/)).toBeInTheDocument();
    expect(field).toHaveValue("A revised simulation banner.");
    expect(screen.queryByText(/is published/)).not.toBeInTheDocument();
  });
});
