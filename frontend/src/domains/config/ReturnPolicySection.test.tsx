/**
 * `/config/return-policy`: load, edit a typed field, Validate (errors
 * mapped), Publish through `POST /api/config/publish` with the merge patch
 * under the right domain and `expected_head_revision`, and the success
 * notice.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { APIError } from "../../api/client";
import { CapabilityContext } from "../../hooks/capabilityContext";
import { ReturnPolicySection } from "./ReturnPolicySection";

const mocks = vi.hoisted(() => ({ runtime: vi.fn(), validateDomain: vi.fn(), publish: vi.fn() }));

vi.mock("../../api/configuration", () => ({
  configApi: { runtime: mocks.runtime, validateDomain: mocks.validateDomain, publish: mocks.publish },
}));

function configuration() {
  return {
    return_policy: {
      normalized_return_methods: ["PARCEL_RETURN", "FREIGHT_RETURN"],
      return_method_derivation: {
        default_method: "PARCEL_RETURN",
        freight_method: "FREIGHT_RETURN",
        freight_keywords: [],
        ship_via_methods: {},
      },
      return_method_requirements: [{ method: "PARCEL_RETURN", requires: ["RMA", "LABEL"] }],
      bol_tendering_instruction_types: ["CARRIER_SCHEDULED"],
    },
    return_eligibility_policy: {
      precedence: ["FERGUSON_STANDARD_RETURN"],
      standard_stock_return: { purchase_window: { days: 30, basis: "PURCHASE_DATE" }, decision_when_satisfied: "APPROVE" },
      outside_standard_window: { decision: "REVIEW_REQUIRED" },
      stock_classification: { unresolved_default: "REVIEW_REQUIRED" },
      delivery_claim: { conditions: [], reporting_window: { business_days: 2 } },
      warranty_issue: { reasons: [] },
    },
    policy_evaluation: { enabled: true, disabled_reason: null },
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

describe("Return policy screen", () => {
  it("loads the active release's return-policy slice", async () => {
    render(<ReturnPolicySection />, { wrapper: Wrapper });
    expect(await screen.findByRole("combobox", { name: "Default method" })).toHaveValue("PARCEL_RETURN");
  });

  it("requires a reason when policy evaluation is switched off, and maps the Validate error onto it", async () => {
    const user = userEvent.setup();
    mocks.validateDomain.mockResolvedValue({
      valid: false,
      errors: [{ path: "policy_evaluation.disabled_reason", message: "A reason is required.", type: "value_error" }],
    });
    render(<ReturnPolicySection />, { wrapper: Wrapper });

    const toggle = await screen.findByRole("checkbox", { name: "Policy evaluation enabled" });
    await user.click(toggle);
    // Toggle's own reason field appears once required.
    expect(screen.getByRole("textbox", { name: "Reason" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Validate" }));
    await waitFor(() => { expect(mocks.validateDomain).toHaveBeenCalled(); });
    const [domainKey, body] = mocks.validateDomain.mock.calls[0] as [string, { patch: Record<string, unknown> }];
    expect(domainKey).toBe("RETURN_PLATFORM");
    expect((body.patch.policy_evaluation as Record<string, unknown>).enabled).toBe(false);

    expect(await screen.findAllByText("A reason is required.")).not.toHaveLength(0);
  });

  it("publishes the merge patch under return_policy on RETURN_PLATFORM with the loaded head revision", async () => {
    const user = userEvent.setup();
    render(<ReturnPolicySection />, { wrapper: Wrapper });

    const select = await screen.findByRole("combobox", { name: "Default method" });
    await user.selectOptions(select, "FREIGHT_RETURN");

    await user.click(screen.getByRole("button", { name: "Publish" }));

    await waitFor(() => { expect(mocks.publish).toHaveBeenCalled(); });
    const [options] = mocks.publish.mock.calls[0] as [
      { domainKey: string; patch: Record<string, unknown>; expectedHeadRevision: number },
    ];
    expect(options.domainKey).toBe("RETURN_PLATFORM");
    expect(options.expectedHeadRevision).toBe(41);
    const returnPolicyPatch = options.patch.return_policy as Record<string, unknown>;
    const derivationPatch = returnPolicyPatch.return_method_derivation as Record<string, unknown>;
    expect(derivationPatch.default_method).toBe("FREIGHT_RETURN");
    expect(options.patch.policy_evaluation).toBeUndefined();

    expect(await screen.findByText(/Release publish-1 is published/)).toBeInTheDocument();
  });

  it("toggles a return-method-requirement checkbox and includes it in the published patch", async () => {
    const user = userEvent.setup();
    render(<ReturnPolicySection />, { wrapper: Wrapper });

    const checkbox = await screen.findByRole("checkbox", { name: "FREIGHT_RETURN requires BOL" });
    expect(checkbox).not.toBeChecked();
    await user.click(checkbox);

    await user.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => { expect(mocks.publish).toHaveBeenCalled(); });
    const [options] = mocks.publish.mock.calls[0] as [{ patch: Record<string, unknown> }];
    const requirements = (options.patch.return_policy as Record<string, unknown>)
      .return_method_requirements as { method: string; requires: string[] }[];
    const freight = requirements.find((row) => row.method === "FREIGHT_RETURN");
    expect(freight?.requires).toEqual(["BOL"]);
  });

  it("disables Publish when config.release.promote is missing", async () => {
    grants = ["config.runtime.read", "config.release.write"];
    render(<ReturnPolicySection />, { wrapper: Wrapper });
    await screen.findByRole("combobox", { name: "Default method" });
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  // RV F2.
  it("disables every typed field and shows the read-only notice when config.release.write is missing", async () => {
    grants = ["config.runtime.read", "config.release.promote"];
    render(<ReturnPolicySection />, { wrapper: Wrapper });

    const select = await screen.findByRole("combobox", { name: "Default method" });
    expect(select).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Policy evaluation enabled" })).toBeDisabled();
    expect(screen.getByText(/Read-only access\. Editing this section requires config\.release\.write\./)).toBeInTheDocument();
  });

  // RV F9: no test anywhere covered a refused publish.
  it("shows the error and keeps the draft when Publish is refused", async () => {
    const user = userEvent.setup();
    mocks.publish.mockRejectedValue(new APIError("Domain patch refused: unknown return method", 422));
    render(<ReturnPolicySection />, { wrapper: Wrapper });

    const select = await screen.findByRole("combobox", { name: "Default method" });
    await user.selectOptions(select, "FREIGHT_RETURN");
    await user.click(screen.getByRole("button", { name: "Publish" }));

    expect(await screen.findByText(/Domain patch refused/)).toBeInTheDocument();
    expect(select).toHaveValue("FREIGHT_RETURN");
    expect(screen.queryByText(/is published/)).not.toBeInTheDocument();
  });
});
