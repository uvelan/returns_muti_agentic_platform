/**
 * `/config/integrations`: load the active release's integrations/copilot
 * slice, edit a typed field, Validate (errors mapped onto the field),
 * Publish through the single-call `/api/config/publish` with the merge
 * patch under `RETURN_PLATFORM` and `expected_head_revision` from the
 * loaded snapshot, and see the success notice. Same shape as
 * `WorkflowSection.test.tsx`/`FulfilmentSection.test.tsx`.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { APIError } from "../../api/client";
import { CapabilityContext } from "../../hooks/capabilityContext";
import { IntegrationsSection } from "./IntegrationsSection";

const mocks = vi.hoisted(() => ({
  runtime: vi.fn(),
  validateDomain: vi.fn(),
  publish: vi.fn(),
  listAgents: vi.fn(),
}));

vi.mock("../../api/configuration", () => ({
  configApi: {
    runtime: mocks.runtime,
    validateDomain: mocks.validateDomain,
    publish: mocks.publish,
  },
}));

vi.mock("../../api/agentConfig", () => ({
  agentConfigApi: { list: mocks.listAgents },
}));

const INTEGRATIONS = {
  omc_return_create: { enabled: true, topic: "omc.return.create", authority: "OMC", ai_may_fabricate_success: false },
  external_support_mirror: { enabled: true, topic: "support.mirror", authority: "SUPPORT", ai_may_fabricate_success: false },
  carrier_booking: { enabled: false, topic: "carrier.booking", authority: "CARRIER", ai_may_fabricate_success: false },
  customer_notification: { enabled: true, topic: "customer.notify", authority: "PLATFORM", ai_may_fabricate_success: false },
};

const COPILOT = {
  order_discovery_agent_id: "order-discovery-agent",
  candidate_columns: [{ label: "Order", fields: ["orderNumber"] }],
};

function configuration() {
  return { integrations: INTEGRATIONS, copilot: COPILOT };
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
    configuration: configuration(),
  });
  mocks.listAgents.mockReset().mockResolvedValue([
    { manifestId: "order-discovery-agent", moduleId: "order_discovery", name: "Order Discovery Agent", enabled: true, status: "ACTIVE", configurationVersion: "3", source: "RELEASE" },
    { manifestId: "return-status-agent", moduleId: "return_status", name: "Return Status Agent", enabled: false, status: "DISABLED", configurationVersion: "1", source: "RELEASE" },
  ]);
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

describe("Integrations screen", () => {
  it("loads the active release's integrations and copilot slice", async () => {
    render(<IntegrationsSection />, { wrapper: Wrapper });
    expect(await screen.findByDisplayValue("omc.return.create")).toBeInTheDocument();
    // Four topic rows, each with its own "Enabled" toggle -- the first is
    // OMC return create's, which the fixture carries enabled.
    expect(screen.getAllByRole("checkbox", { name: "Enabled" })[0]).toBeChecked();
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: /Order discovery agent/ })).toHaveValue("order-discovery-agent");
    });
    expect(screen.getByRole("option", { name: "Order Discovery Agent" })).toBeInTheDocument();
  });

  it("maps a Validate error onto the field it names", async () => {
    const user = userEvent.setup();
    mocks.validateDomain.mockResolvedValue({
      valid: false,
      errors: [{ path: "integrations.omc_return_create.topic", message: "Required.", type: "value_error" }],
    });
    render(<IntegrationsSection />, { wrapper: Wrapper });

    const field = await screen.findByDisplayValue("omc.return.create");
    await user.clear(field);
    await user.click(screen.getByRole("button", { name: "Validate" }));

    await waitFor(() => { expect(mocks.validateDomain).toHaveBeenCalled(); });
    const [domainKey, body] = mocks.validateDomain.mock.calls[0] as [string, { patch: Record<string, unknown> }];
    expect(domainKey).toBe("RETURN_PLATFORM");
    expect(body.patch.integrations).toBeDefined();

    expect(await screen.findAllByText("Required.")).not.toHaveLength(0);
    expect(field).toHaveAttribute("aria-invalid", "true");
  });

  it("publishes the merge patch under copilot on RETURN_PLATFORM with the loaded head revision, and shows the success notice", async () => {
    const user = userEvent.setup();
    render(<IntegrationsSection />, { wrapper: Wrapper });

    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: /Order discovery agent/ })).toHaveValue("order-discovery-agent");
    });
    await user.selectOptions(screen.getByRole("combobox", { name: /Order discovery agent/ }), "return-status-agent");
    await user.click(screen.getByRole("button", { name: "Publish" }));

    await waitFor(() => { expect(mocks.publish).toHaveBeenCalled(); });
    const [options] = mocks.publish.mock.calls[0] as [
      { domainKey: string; patch: Record<string, unknown>; expectedHeadRevision: number },
    ];
    expect(options.domainKey).toBe("RETURN_PLATFORM");
    expect(options.expectedHeadRevision).toBe(41);
    expect((options.patch.copilot as Record<string, unknown>).order_discovery_agent_id).toBe("return-status-agent");
    expect(options.patch.integrations).toBeUndefined();

    expect(await screen.findByText(/Release publish-1 is published/)).toBeInTheDocument();
  });

  it("disables Publish when config.release.promote is missing", async () => {
    grants = ["config.runtime.read", "config.release.write"];
    render(<IntegrationsSection />, { wrapper: Wrapper });
    await screen.findByDisplayValue("omc.return.create");
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  it("disables every typed field and shows the read-only notice when config.release.write is missing", async () => {
    grants = ["config.runtime.read", "config.release.promote"];
    render(<IntegrationsSection />, { wrapper: Wrapper });

    const field = await screen.findByDisplayValue("omc.return.create");
    expect(field).toBeDisabled();
    expect(screen.getByText(/Read-only access\. Editing this section requires config\.release\.write\./)).toBeInTheDocument();
  });

  it("shows the error and keeps the draft when Publish is refused", async () => {
    const user = userEvent.setup();
    mocks.publish.mockRejectedValue(new APIError("Domain patch refused: topic must not be blank", 422));
    render(<IntegrationsSection />, { wrapper: Wrapper });

    const field = await screen.findByDisplayValue("omc.return.create");
    await user.clear(field);
    await user.type(field, "omc.return.create.v2");
    await user.click(screen.getByRole("button", { name: "Publish" }));

    expect(await screen.findByText(/Domain patch refused/)).toBeInTheDocument();
    expect(field).toHaveValue("omc.return.create.v2");
    expect(screen.queryByText(/is published/)).not.toBeInTheDocument();
  });

  it("adds and removes a candidate column", async () => {
    const user = userEvent.setup();
    render(<IntegrationsSection />, { wrapper: Wrapper });

    await screen.findByDisplayValue("omc.return.create");
    await user.click(screen.getByRole("button", { name: "Add candidate column" }));
    // Required, so the accessible name carries the sr-only "required" suffix
    // too -- a regex, not an exact match (the same reason other CFG-5 tests
    // match required fields with /Version/, not "Version").
    const labels = screen.getAllByRole("textbox", { name: /^Label/ });
    expect(labels).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: /Remove candidate column 2/ }));
    expect(screen.getAllByRole("textbox", { name: /^Label/ })).toHaveLength(1);
  });
});
