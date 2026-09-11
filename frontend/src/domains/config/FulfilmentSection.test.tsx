/**
 * `/config/fulfilment`: load, edit a typed field, Validate (errors mapped),
 * Publish through `POST /api/config/publish` with the merge patch under the
 * right domain and `expected_head_revision`, and the success notice.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { APIError } from "../../api/client";
import { CapabilityContext } from "../../hooks/capabilityContext";
import { FulfilmentSection } from "./FulfilmentSection";

const mocks = vi.hoisted(() => ({ runtime: vi.fn(), validateDomain: vi.fn(), publish: vi.fn() }));

vi.mock("../../api/configuration", () => ({
  configApi: { runtime: mocks.runtime, validateDomain: mocks.validateDomain, publish: mocks.publish },
}));

function configuration() {
  return {
    shipment_tracking: {
      statuses: [
        {
          code: "AWAITING_HANDOFF",
          label: "Awaiting handoff",
          ladder: "parcel",
          ordinal: 0,
          terminal: false,
          exception_state: false,
          color_token: "progress",
          allowed_next: ["IN_TRANSIT"],
          projection_status: "AWAITING_HANDOFF",
        },
        {
          code: "DELIVERED",
          label: "Delivered",
          ladder: "parcel",
          ordinal: 1,
          terminal: true,
          exception_state: false,
          color_token: "success",
          allowed_next: [],
          projection_status: "DELIVERED",
        },
      ],
      initial_status_parcel: "AWAITING_HANDOFF",
      initial_status_freight: "AWAITING_HANDOFF",
      freight_methods: [],
      collection: "shipmentInfo",
      fields: {},
      source_mirror: {},
      source_constants: {},
    },
    bay: {
      authority_mode: "WAREHOUSE_MANAGED",
      require_physical_receipt: true,
      allow_prearrival_reservation: false,
      eligible_statuses: ["AWAITING_RECEIPT"],
    },
    omc: {
      v2_customer_return_table: "customerReturnV2",
      v1_customer_return_table: "customerReturnV1",
      customer_return_display: {},
      normalized_statuses: {},
      tendered_is_pickup: false,
      license_plate_implies_receipt: true,
      rga_is_customer_return: true,
    },
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

describe("Fulfilment screen", () => {
  it("loads the active release's shipment tracking, bay and omc slices", async () => {
    render(<FulfilmentSection />, { wrapper: Wrapper });
    expect(await screen.findByDisplayValue("customerReturnV2")).toBeInTheDocument();
    // "Code" is a required field, so its accessible name also carries the
    // sr-only "required" text `Field` appends -- matched with a substring
    // regex rather than the exact visible label.
    expect(screen.getAllByRole("textbox", { name: /^Code/ })[0]).toHaveValue("AWAITING_HANDOFF");
    expect(screen.getByDisplayValue("WAREHOUSE_MANAGED")).toBeInTheDocument();
  });

  it("maps a Validate error onto the bay field it names", async () => {
    const user = userEvent.setup();
    mocks.validateDomain.mockResolvedValue({
      valid: false,
      errors: [{ path: "bay.authority_mode", message: "Unknown authority mode.", type: "value_error" }],
    });
    render(<FulfilmentSection />, { wrapper: Wrapper });

    const field = await screen.findByRole("textbox", { name: "Authority mode" });
    await user.clear(field);
    await user.type(field, "BOGUS_MODE");
    await user.click(screen.getByRole("button", { name: "Validate" }));

    await waitFor(() => { expect(mocks.validateDomain).toHaveBeenCalled(); });
    const [domainKey, body] = mocks.validateDomain.mock.calls[0] as [string, { patch: Record<string, unknown> }];
    expect(domainKey).toBe("RETURN_PLATFORM");
    expect((body.patch.bay as Record<string, unknown>).authority_mode).toBe("BOGUS_MODE");

    expect(await screen.findAllByText("Unknown authority mode.")).not.toHaveLength(0);
  });

  it("publishes the merge patch under bay on RETURN_PLATFORM with the loaded head revision", async () => {
    const user = userEvent.setup();
    render(<FulfilmentSection />, { wrapper: Wrapper });

    const toggle = await screen.findByRole("checkbox", { name: "Allow pre-arrival reservation" });
    await user.click(toggle);

    await user.click(screen.getByRole("button", { name: "Publish" }));

    await waitFor(() => { expect(mocks.publish).toHaveBeenCalled(); });
    const [options] = mocks.publish.mock.calls[0] as [
      { domainKey: string; patch: Record<string, unknown>; expectedHeadRevision: number },
    ];
    expect(options.domainKey).toBe("RETURN_PLATFORM");
    expect(options.expectedHeadRevision).toBe(41);
    expect((options.patch.bay as Record<string, unknown>).allow_prearrival_reservation).toBe(true);
    expect(options.patch.omc).toBeUndefined();

    expect(await screen.findByText(/Release publish-1 is published/)).toBeInTheDocument();
  });

  it("edits a status's projection mapping, including clearing it to none", async () => {
    const user = userEvent.setup();
    render(<FulfilmentSection />, { wrapper: Wrapper });

    const selects = await screen.findAllByRole("combobox", { name: "Projection status" });
    expect(selects[1]).toHaveValue("DELIVERED");
    await user.selectOptions(selects[1], "(none)");

    await user.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => { expect(mocks.publish).toHaveBeenCalled(); });
    const [options] = mocks.publish.mock.calls[0] as [{ patch: Record<string, unknown> }];
    const statuses = (options.patch.shipment_tracking as Record<string, unknown>)
      .statuses as { code: string; projection_status: string | null }[];
    const delivered = statuses.find((status) => status.code === "DELIVERED");
    expect(delivered?.projection_status).toBeNull();
  });

  it("disables Publish when config.release.promote is missing", async () => {
    grants = ["config.runtime.read", "config.release.write"];
    render(<FulfilmentSection />, { wrapper: Wrapper });
    await screen.findByDisplayValue("customerReturnV2");
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  // RV F2.
  it("disables every typed field and shows the read-only notice when config.release.write is missing", async () => {
    grants = ["config.runtime.read", "config.release.promote"];
    render(<FulfilmentSection />, { wrapper: Wrapper });

    const field = await screen.findByDisplayValue("customerReturnV2");
    expect(field).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Require physical receipt" })).toBeDisabled();
    expect(screen.getByText(/Read-only access\. Editing this section requires config\.release\.write\./)).toBeInTheDocument();
  });

  // RV F9: no test anywhere covered a refused publish.
  it("shows the error and keeps the draft when Publish is refused", async () => {
    const user = userEvent.setup();
    mocks.publish.mockRejectedValue(new APIError("Domain patch refused: unknown status code", 422));
    render(<FulfilmentSection />, { wrapper: Wrapper });

    const toggle = await screen.findByRole("checkbox", { name: "Allow pre-arrival reservation" });
    await user.click(toggle);
    await user.click(screen.getByRole("button", { name: "Publish" }));

    expect(await screen.findByText(/Domain patch refused/)).toBeInTheDocument();
    expect(toggle).toBeChecked();
    expect(screen.queryByText(/is published/)).not.toBeInTheDocument();
  });

  // RV F3: `errorsByPath` used to key its map verbatim off `error.path`, so
  // the backend's bracketed index (`_dotted_error_path`,
  // `"agents[2].version", not "agents.2.version"`) never matched this
  // screen's own dot-plus-index lookup (`shipment_tracking.statuses.0.code`).
  it("maps a bracketed indexed error path (shipment_tracking.statuses[0].code) onto the right status card", async () => {
    const user = userEvent.setup();
    mocks.validateDomain.mockResolvedValue({
      valid: false,
      errors: [{ path: "shipment_tracking.statuses[0].code", message: "Duplicate code.", type: "value_error" }],
    });
    render(<FulfilmentSection />, { wrapper: Wrapper });

    await screen.findByDisplayValue("customerReturnV2");
    // Validate is disabled until something is staged.
    await user.click(await screen.findByRole("checkbox", { name: "Require physical receipt" }));
    await user.click(screen.getByRole("button", { name: "Validate" }));

    const codeFields = await screen.findAllByRole("textbox", { name: /^Code/ });
    await waitFor(() => { expect(codeFields[0]).toHaveAttribute("aria-invalid", "true"); });
    expect(codeFields[1]).not.toHaveAttribute("aria-invalid");
    expect(await screen.findAllByText("Duplicate code.")).not.toHaveLength(0);
  });
});
