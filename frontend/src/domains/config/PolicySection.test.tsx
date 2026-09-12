/**
 * `/config/policy`: load, toggle policy evaluation off (requires a reason),
 * edit the return window, reset it to the packaged default, publish through
 * `POST /api/config/publish` with the merge patch under `RETURN_PLATFORM`,
 * and preview a decision against the draft.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { APIError } from "../../api/client";
import { CapabilityContext } from "../../hooks/capabilityContext";
import { PolicySection } from "./PolicySection";

const mocks = vi.hoisted(() => ({
  runtime: vi.fn(),
  validateDomain: vi.fn(),
  publish: vi.fn(),
  packagedDomain: vi.fn(),
  previewPolicy: vi.fn(),
}));

vi.mock("../../api/configuration", () => ({
  configApi: {
    runtime: mocks.runtime,
    validateDomain: mocks.validateDomain,
    publish: mocks.publish,
    packagedDomain: mocks.packagedDomain,
    previewPolicy: mocks.previewPolicy,
  },
}));

function eligibilityPolicy(days: number) {
  return {
    id: "ferguson-standard-return-policy",
    version: "2026-08-15",
    authority: "FERGUSON_PUBLIC_TERMS",
    source_document: "Ferguson Terms and Conditions of Sale",
    source_revision: "Rev. May 2025",
    precedence: ["FERGUSON_STANDARD_RETURN"],
    standard_stock_return: {
      purchase_window: { days, basis: "PURCHASE_DATE" },
      requirements: {
        seller_stocked: true,
        special_order: false,
        condition: {
          new: true,
          suitable_for_resale: true,
          original_packaging: true,
          packaging_undamaged: true,
          all_original_parts: true,
        },
        prohibited_states: {
          used: false,
          installed: false,
          modified: false,
          rebuilt: false,
          reconditioned: false,
          repaired: false,
          altered: false,
          damaged: false,
        },
      },
      decision_when_satisfied: "APPROVE",
      conditions: ["RESTOCKING_FEE_APPLIES"],
      unstated_condition_facts: "NOT_EVALUATED",
    },
    restocking_fee: {
      applies_by_default: true,
      seller_can_waive: true,
      amount_source: ["SELLER_CONFIGURATION", "SELLER_OVERRIDE", "MANUFACTURER"],
      seller_schedule: { default_rate_basis_points: 1500, currency: "USD" },
    },
    stock_classification: {
      unresolved_default: "STANDARD_STOCK",
      special_order_skus: [],
      special_order_sku_prefixes: [],
      special_order_product_ids: [],
    },
    special_or_nonstock: {
      buyer_fee_acceptance_required: true,
      decisions: {
        manufacturer_acceptance_unknown: "REVIEW_REQUIRED",
        manufacturer_acceptance_rejected: "REJECT",
        manufacturer_acceptance_accepted_buyer_fee_unknown: "REVIEW_REQUIRED",
        manufacturer_acceptance_accepted_buyer_fee_rejected: "REJECT",
        manufacturer_acceptance_accepted_buyer_fee_accepted: "APPROVE",
      },
    },
    outside_standard_window: { decision: "REVIEW_REQUIRED", reason_code: "OUTSIDE_STANDARD_RETURN_WINDOW" },
    delivery_claim: { conditions: ["SHIPPING_DAMAGE"], reporting_window: { business_days: 2, basis: "DELIVERY_DATE" } },
    warranty_issue: { reasons: ["MANUFACTURER_WARRANTY_ISSUE"] },
  };
}

function configuration() {
  return {
    return_eligibility_policy: eligibilityPolicy(30),
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
  // The packaged file's own value -- 30/PURCHASE_DATE, matching the loaded
  // release above, so "Reset to packaged default" is invisible until a test
  // actually diverges from it.
  mocks.packagedDomain.mockReset().mockResolvedValue({
    return_eligibility_policy: eligibilityPolicy(30),
    policy_evaluation: { enabled: true, disabled_reason: null },
  });
  mocks.previewPolicy.mockReset().mockResolvedValue({
    evaluation_enabled: true,
    decision: "APPROVE",
    route: "STANDARD_RETURN",
    applied_rules: ["POLICY_RELEASE_VALIDATED", "STANDARD_STOCK_ITEM", "WITHIN_30_DAYS"],
    conditions: ["RESTOCKING_FEE_APPLIES"],
    unanswered_checks: [],
    reason_codes: ["WITHIN_STANDARD_RETURN_WINDOW"],
    policy_evaluation_state: "EVALUATED",
    policy_evaluation_skip_reason: null,
  });
});

describe("Policy screen", () => {
  it("loads the active release's policy slice", async () => {
    render(<PolicySection />, { wrapper: Wrapper });
    expect(await screen.findByRole("spinbutton", { name: "Return window" })).toHaveValue(30);
  });

  it("requires a reason when policy evaluation is switched off, and blocks Validate until given", async () => {
    const user = userEvent.setup();
    mocks.validateDomain.mockResolvedValue({
      valid: false,
      errors: [{ path: "policy_evaluation.disabled_reason", message: "A reason is required.", type: "value_error" }],
    });
    render(<PolicySection />, { wrapper: Wrapper });

    const toggle = await screen.findByRole("checkbox", { name: "Policy evaluation enabled" });
    await user.click(toggle);
    expect(screen.getByRole("textbox", { name: "Reason" })).toBeInTheDocument();
    expect(screen.getByText(/SKIPPED_BY_CONFIGURATION/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Validate" }));
    await waitFor(() => { expect(mocks.validateDomain).toHaveBeenCalled(); });
    const [domainKey, body] = mocks.validateDomain.mock.calls[0] as [string, { patch: Record<string, unknown> }];
    expect(domainKey).toBe("RETURN_PLATFORM");
    expect((body.patch.policy_evaluation as Record<string, unknown>).enabled).toBe(false);
    expect(await screen.findAllByText("A reason is required.")).not.toHaveLength(0);
  });

  it("editing the return window produces a merge patch under standard_stock_return.purchase_window.days", async () => {
    const user = userEvent.setup();
    render(<PolicySection />, { wrapper: Wrapper });

    const window = await screen.findByRole("spinbutton", { name: "Return window" });
    await user.clear(window);
    await user.type(window, "45");
    await user.tab();

    await user.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => { expect(mocks.publish).toHaveBeenCalled(); });
    const [options] = mocks.publish.mock.calls[0] as [
      { domainKey: string; patch: Record<string, unknown>; expectedHeadRevision: number },
    ];
    expect(options.domainKey).toBe("RETURN_PLATFORM");
    expect(options.expectedHeadRevision).toBe(41);
    const eligibilityPatch = options.patch.return_eligibility_policy as Record<string, unknown>;
    const standard = eligibilityPatch.standard_stock_return as Record<string, unknown>;
    const window_ = standard.purchase_window as Record<string, unknown>;
    expect(window_.days).toBe(45);
  });

  it("Reset to packaged default sets 30/PURCHASE_DATE and empties the patch", async () => {
    const user = userEvent.setup();
    render(<PolicySection />, { wrapper: Wrapper });

    const window = await screen.findByRole("spinbutton", { name: "Return window" });
    expect(screen.queryByRole("button", { name: "Reset to packaged default" })).not.toBeInTheDocument();

    await user.clear(window);
    await user.type(window, "45");
    await user.tab();

    const [firstResetLink] = await screen.findAllByRole("button", { name: "Reset to packaged default" });
    expect(screen.getByText(/return_eligibility_policy\.standard_stock_return\.purchase_window\.days/)).toBeInTheDocument();

    await user.click(firstResetLink);

    await waitFor(() => { expect(window).toHaveValue(30); });
    expect(screen.getByText("Nothing changed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset to packaged default" })).not.toBeInTheDocument();
  });

  it("previews a decision against the draft in the editor, not the loaded document", async () => {
    const user = userEvent.setup();
    render(<PolicySection />, { wrapper: Wrapper });

    const window = await screen.findByRole("spinbutton", { name: "Return window" });
    await user.clear(window);
    await user.type(window, "45");
    await user.tab();

    await user.click(screen.getByRole("button", { name: "Evaluate" }));

    await waitFor(() => { expect(mocks.previewPolicy).toHaveBeenCalled(); });
    const [call] = mocks.previewPolicy.mock.calls[0] as [
      { returnEligibilityPolicy: { standard_stock_return: { purchase_window: { days: number } } } },
    ];
    // The draft's edited value (45), not the loaded release's (30).
    expect(call.returnEligibilityPolicy.standard_stock_return.purchase_window.days).toBe(45);

    expect(await screen.findByText("Decision: APPROVE")).toBeInTheDocument();
  });

  it("disables Publish when config.release.promote is missing", async () => {
    grants = ["config.runtime.read", "config.release.write"];
    render(<PolicySection />, { wrapper: Wrapper });
    await screen.findByRole("spinbutton", { name: "Return window" });
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  it("disables every typed field and shows the read-only notice when config.release.write is missing", async () => {
    grants = ["config.runtime.read", "config.release.promote"];
    render(<PolicySection />, { wrapper: Wrapper });

    const window = await screen.findByRole("spinbutton", { name: "Return window" });
    expect(window).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Policy evaluation enabled" })).toBeDisabled();
    expect(screen.getByText(/Read-only access\. Editing this section requires config\.release\.write\./)).toBeInTheDocument();
  });

  it(
    // CFG-8 A3: Evaluate writes nothing into the draft and the route is
    // scoped to `config.runtime.read` alone -- it must stay reachable for a
    // principal who has only that, even though every typed field is
    // disabled by the write fieldset right next to it.
    "keeps Evaluate enabled for a read-only operator who lacks config.release.write",
    async () => {
      grants = ["config.runtime.read"];
      render(<PolicySection />, { wrapper: Wrapper });

      const window = await screen.findByRole("spinbutton", { name: "Return window" });
      expect(window).toBeDisabled();
      const evaluate = screen.getByRole("button", { name: "Evaluate" });
      expect(evaluate).toBeEnabled();
    },
  );

  it("shows the error and keeps the draft when Publish is refused", async () => {
    const user = userEvent.setup();
    mocks.publish.mockRejectedValue(new APIError("Domain patch refused: precedence must end in FERGUSON_STANDARD_RETURN", 422));
    render(<PolicySection />, { wrapper: Wrapper });

    const window = await screen.findByRole("spinbutton", { name: "Return window" });
    await user.clear(window);
    await user.type(window, "45");
    await user.tab();
    await user.click(screen.getByRole("button", { name: "Publish" }));

    expect(await screen.findByText(/Domain patch refused/)).toBeInTheDocument();
    expect(window).toHaveValue(45);
    expect(screen.queryByText(/is published/)).not.toBeInTheDocument();
  });

  it("a 422 from Validate maps onto the return-window field", async () => {
    const user = userEvent.setup();
    mocks.validateDomain.mockResolvedValue({
      valid: false,
      errors: [
        {
          path: "return_eligibility_policy.standard_stock_return.purchase_window.days",
          message: "must be at least 1 day.",
          type: "value_error",
        },
      ],
    });
    render(<PolicySection />, { wrapper: Wrapper });

    // Validate is disabled until something is staged (`PublishBar`).
    const window = await screen.findByRole("spinbutton", { name: "Return window" });
    await user.clear(window);
    await user.type(window, "0");
    await user.tab();
    await user.click(screen.getByRole("button", { name: "Validate" }));

    expect(await screen.findAllByText("must be at least 1 day.")).not.toHaveLength(0);
  });
});
