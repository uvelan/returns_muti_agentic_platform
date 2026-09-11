/**
 * `/config/workflow`: load the active release's workflow/return_case/
 * business_calendars/housekeeping slice, edit a typed field, Validate (errors
 * mapped onto the field), Publish through the single-call
 * `/api/config/publish` with the merge patch under `RETURN_PLATFORM` and
 * `expected_head_revision` from the loaded snapshot, and see the success
 * notice. Same shape as `DiscoverySection.test.tsx`/`FulfilmentSection.test.tsx`.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { APIError } from "../../api/client";
import { CapabilityContext } from "../../hooks/capabilityContext";
import { WorkflowSection } from "./WorkflowSection";

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

const WORKFLOW = {
  version: "2026.1",
  stages: ["DISCOVERY", "ITEM_SELECTION"],
  sla_minutes: { DISCOVERY: 10, ITEM_SELECTION: 15 },
  completion_dimensions: ["PHYSICAL_RETURN_COMPLETE"],
};

const RETURN_CASE = {
  bay_wait_seconds: 120,
  item_reservation_ttl_seconds: 1_800,
  return_details_wait_seconds: 1_800,
  return_details_required: false,
  support_response_wait_seconds: 28_800,
  reminder_interval_seconds: 7_200,
  max_reminders: 3,
  on_reminders_exhausted: "PARK_FOR_OPERATIONS",
  business_calendar_id: "default",
  timezone: "UTC",
};

const HOUSEKEEPING = {
  enabled: true,
  interval_seconds: 900,
  temporal_executions: { enabled: true, minimum_age_seconds: 3_600, batch_limit: 500 },
  graph_generations: {
    enabled: true,
    retention_seconds: 86_400,
    batch_limit: 5,
    node_delete_batch_size: 1_000,
    abandoned_build_seconds: 21_600,
    orphaned_active_seconds: 86_400,
  },
  stalled_sync_runs: { enabled: true, stall_seconds: 150, batch_limit: 20 },
  probe_databases: { enabled: true, name_suffixes: ["_probe"], minimum_age_seconds: 3_600, batch_limit: 50 },
  order_line_reservations: { batch_limit: 200 },
  ai_interceptions: { batch_limit: 200 },
};

function configuration() {
  return {
    workflow: WORKFLOW,
    return_case: RETURN_CASE,
    business_calendars: [],
    housekeeping: HOUSEKEEPING,
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
    audit_ids: ["a1", "a2", "a3", "a4", "a5"],
  });
});

describe("Workflow screen", () => {
  it("loads the active release's workflow slice", async () => {
    render(<WorkflowSection />, { wrapper: Wrapper });
    expect(await screen.findByRole("spinbutton", { name: /Max reminders/ })).toHaveValue(3);
    expect(screen.getByRole("textbox", { name: /Version/ })).toHaveValue("2026.1");
    expect(screen.getByRole("textbox", { name: /Stage 1/ })).toHaveValue("DISCOVERY");
  });

  it("maps a Validate error onto the field it names", async () => {
    const user = userEvent.setup();
    mocks.validateDomain.mockResolvedValue({
      valid: false,
      errors: [{ path: "return_case.max_reminders", message: "Too many.", type: "value_error" }],
    });
    render(<WorkflowSection />, { wrapper: Wrapper });

    const field = await screen.findByRole("spinbutton", { name: /Max reminders/ });
    await user.clear(field);
    await user.type(field, "40");
    await user.click(screen.getByRole("button", { name: "Validate" }));

    await waitFor(() => { expect(mocks.validateDomain).toHaveBeenCalled(); });
    const [domainKey, body] = mocks.validateDomain.mock.calls[0] as [string, { patch: Record<string, unknown> }];
    expect(domainKey).toBe("RETURN_PLATFORM");
    expect((body.patch.return_case as Record<string, unknown>).max_reminders).toBe(40);

    expect(await screen.findAllByText("Too many.")).not.toHaveLength(0);
    expect(field).toHaveAttribute("aria-invalid", "true");
  });

  it("publishes the merge patch under return_case on RETURN_PLATFORM with the loaded head revision, and shows the success notice", async () => {
    const user = userEvent.setup();
    render(<WorkflowSection />, { wrapper: Wrapper });

    const field = await screen.findByRole("spinbutton", { name: /Max reminders/ });
    await user.clear(field);
    await user.type(field, "5");

    await user.click(screen.getByRole("button", { name: "Publish" }));

    await waitFor(() => { expect(mocks.publish).toHaveBeenCalled(); });
    const [options] = mocks.publish.mock.calls[0] as [
      { domainKey: string; patch: Record<string, unknown>; expectedHeadRevision: number },
    ];
    expect(options.domainKey).toBe("RETURN_PLATFORM");
    expect(options.expectedHeadRevision).toBe(41);
    expect((options.patch.return_case as Record<string, unknown>).max_reminders).toBe(5);
    // Nothing else on the slice moved.
    expect(options.patch.workflow).toBeUndefined();

    expect(await screen.findByText(/Release publish-1 is published/)).toBeInTheDocument();
  });

  it("disables Publish when config.release.promote is missing", async () => {
    grants = ["config.runtime.read", "config.release.write"];
    render(<WorkflowSection />, { wrapper: Wrapper });
    await screen.findByRole("spinbutton", { name: /Max reminders/ });
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  // RV F2 (CFG-4, carried convention): a principal with promote but not
  // write must see every typed field disabled, not just the Advanced branch.
  it("disables every typed field and shows the read-only notice when config.release.write is missing", async () => {
    grants = ["config.runtime.read", "config.release.promote"];
    render(<WorkflowSection />, { wrapper: Wrapper });

    const field = await screen.findByRole("spinbutton", { name: /Max reminders/ });
    expect(field).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Return details required" })).toBeDisabled();
    expect(screen.getByText(/Read-only access\. Editing this section requires config\.release\.write\./)).toBeInTheDocument();
  });

  it("shows the error and keeps the draft when Publish is refused", async () => {
    const user = userEvent.setup();
    mocks.publish.mockRejectedValue(new APIError("Domain patch refused: max_reminders must be at most 50", 422));
    render(<WorkflowSection />, { wrapper: Wrapper });

    const field = await screen.findByRole("spinbutton", { name: /Max reminders/ });
    await user.clear(field);
    await user.type(field, "40");
    await user.click(screen.getByRole("button", { name: "Publish" }));

    expect(await screen.findByText(/Domain patch refused/)).toBeInTheDocument();
    expect(field).toHaveValue(40);
    expect(screen.queryByText(/is published/)).not.toBeInTheDocument();
  });

  it("adds and removes a workflow stage", async () => {
    const user = userEvent.setup();
    render(<WorkflowSection />, { wrapper: Wrapper });

    await screen.findByRole("textbox", { name: /Stage 1/ });
    await user.click(screen.getByRole("button", { name: "Add stage" }));
    expect(screen.getByRole("textbox", { name: /Stage 3/ })).toHaveValue("");

    await user.click(screen.getByRole("button", { name: /Remove stage 3/ }));
    expect(screen.queryByRole("textbox", { name: /Stage 3/ })).not.toBeInTheDocument();
  });

  it("adds a business calendar with a default working period", async () => {
    const user = userEvent.setup();
    render(<WorkflowSection />, { wrapper: Wrapper });

    await screen.findByRole("spinbutton", { name: /Max reminders/ });
    expect(screen.queryByRole("textbox", { name: /Calendar id/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add calendar" }));
    expect(screen.getByRole("textbox", { name: /Calendar id/ })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /Weekday/ })).toHaveValue("0");
  });
});
