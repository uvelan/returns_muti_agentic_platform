/**
 * `/config/support`: six tabs over six `RETURN_PLATFORM` keys. Template is
 * `SupportTemplateSection`, moved here unchanged (smoke-tested only -- its
 * own load/edit/publish cycle is `SupportTemplateSection.test.tsx`'s job).
 * The other five ride `TypedSectionScreen`, the same load -> edit -> Validate
 * -> Publish -> notice shape every CFG-4/5 screen tests; one full cycle
 * (Gate) plus a switch-and-render smoke test for the rest.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CapabilityContext } from "../../hooks/capabilityContext";
import { SupportSection } from "./SupportSection";

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

vi.mock("../../api/releasePublish", () => ({
  defaultReleaseId: (prefix: string) => `${prefix}-mock`,
  runPublishPipeline: vi.fn(),
}));

vi.mock("../../api/supportTemplate", () => ({
  supportTemplateApi: { preview: vi.fn() },
}));

function configuration() {
  return {
    support_template: { template_id: "support-handoff", default_variant_id: "default", variants: [] },
    support_gate: {
      request_grouping: "one_per_case",
      template_review: {
        enabled: true,
        review_wait_seconds: 28_800,
        reminder_interval_seconds: 7_200,
        max_reminders: 3,
        on_timeout: "hold",
      },
    },
    support_ingress: {
      nl_enabled: false,
      intents: ["info_request", "other"],
      parking: { retention_seconds: 2_592_000, per_case_quota: 50, alert_dedupe_window_seconds: 900 },
      multi_record_framing_prompt_key: "support-multi-record-do-not-mix",
      agent_disclosure: { display_name: "Returns Assistant", disclosure_line: "Automated." },
      outbound_templates: {},
      limits: {
        max_body_characters: 16_000,
        max_messages_per_case_per_window: 60,
        rate_window_seconds: 60,
        max_identifier_characters: 256,
      },
    },
    support_resolver: {
      fact_confidence_millionths: 900_000,
      graph_confidence_millionths: 900_000,
      tool_bindings: [],
      reply_gate: { default: "review_required", per_intent: {} },
      clarification_resets_deadline: true,
      per_case_llm_budget: 12,
      trigger_intents: ["info_request"],
    },
    context_assembly: {
      pinned_fact_names: [],
      token_budget: 8_000,
      tokenizer_version: "wordpiece-approx.v1",
      compaction: { trigger_fraction_millionths: 800_000, summary_task_id: "support.context.summarize.v1" },
    },
    support: {
      authority_mode: "PLATFORM_MANAGED",
      external_mirror_enabled: true,
      default_priority: "NORMAL",
      queues: ["SUPPORT"],
      external_ticket_outbox_topic: "support.tickets.outbox",
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

describe("Support screen", () => {
  it("opens on Template by default, unchanged", async () => {
    render(<SupportSection />, { wrapper: Wrapper });
    expect(await screen.findByText("Support handoff template")).toBeInTheDocument();
  });

  it("switches to every other tab and renders its own section", async () => {
    const user = userEvent.setup();
    render(<SupportSection />, { wrapper: Wrapper });
    await screen.findByText("Support handoff template");

    await user.click(screen.getByRole("tab", { name: "Gate" }));
    expect(await screen.findByRole("combobox", { name: /Request grouping/ })).toHaveValue("one_per_case");

    await user.click(screen.getByRole("tab", { name: "Ingress" }));
    expect(await screen.findByRole("checkbox", { name: /Natural-language ingress enabled/ })).not.toBeChecked();

    await user.click(screen.getByRole("tab", { name: "Resolver" }));
    expect(await screen.findByRole("spinbutton", { name: /Fact confidence threshold/ })).toHaveValue(900_000);

    await user.click(screen.getByRole("tab", { name: "Context assembly" }));
    expect(await screen.findByRole("spinbutton", { name: /Token budget/ })).toHaveValue(8_000);

    await user.click(screen.getByRole("tab", { name: "Queues" }));
    expect(await screen.findByRole("textbox", { name: /Authority mode/ })).toHaveValue("PLATFORM_MANAGED");

    await user.click(screen.getByRole("tab", { name: "Template" }));
    expect(await screen.findByText("Support handoff template")).toBeInTheDocument();
  });

  it("Gate: publishes the merge patch under support_gate on RETURN_PLATFORM with the loaded head revision, and shows the success notice", async () => {
    const user = userEvent.setup();
    render(<SupportSection />, { wrapper: Wrapper });
    await screen.findByText("Support handoff template");
    await user.click(screen.getByRole("tab", { name: "Gate" }));

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
    expect((options.patch.support_gate as { template_review: Record<string, unknown> }).template_review.max_reminders).toBe(5);

    expect(await screen.findByText(/Release publish-1 is published/)).toBeInTheDocument();
  });

  it("Gate: maps a Validate error onto the field it names", async () => {
    const user = userEvent.setup();
    mocks.validateDomain.mockResolvedValue({
      valid: false,
      errors: [{ path: "support_gate.template_review.max_reminders", message: "Too many.", type: "value_error" }],
    });
    render(<SupportSection />, { wrapper: Wrapper });
    await screen.findByText("Support handoff template");
    await user.click(screen.getByRole("tab", { name: "Gate" }));

    const field = await screen.findByRole("spinbutton", { name: /Max reminders/ });
    await user.clear(field);
    await user.type(field, "18");
    await user.click(screen.getByRole("button", { name: "Validate" }));

    await waitFor(() => { expect(mocks.validateDomain).toHaveBeenCalled(); });
    expect(await screen.findAllByText("Too many.")).not.toHaveLength(0);
    expect(field).toHaveAttribute("aria-invalid", "true");
  });

  it("disables every typed field on a non-Template tab and shows the read-only notice when config.release.write is missing", async () => {
    grants = ["config.runtime.read", "config.release.promote"];
    const user = userEvent.setup();
    render(<SupportSection />, { wrapper: Wrapper });
    await screen.findByText("Support handoff template");
    await user.click(screen.getByRole("tab", { name: "Gate" }));

    const field = await screen.findByRole("spinbutton", { name: /Max reminders/ });
    expect(field).toBeDisabled();
    expect(screen.getByText(/Read-only access\. Editing this section requires config\.release\.write\./)).toBeInTheDocument();
  });
});
