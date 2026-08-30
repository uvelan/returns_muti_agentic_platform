/**
 * The support template tab: edit the document, publish the release, and see
 * what the draft renders *before* either.
 *
 * The behaviours worth pinning are the ones a screenshot cannot show. The
 * preview must render the draft in the editor rather than the last published
 * template -- previewing what is already live would be an elaborate way of
 * telling an operator nothing. And publishing must ride the platform's one
 * release lifecycle with the edit under `support_template` on the
 * `RETURN_PLATFORM` domain, because a patch that lands anywhere else validates
 * against a model that has never heard of it.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SupportTemplateSection } from "./SupportTemplateSection";
import { CapabilityContext } from "../../hooks/capabilityContext";

const mocks = vi.hoisted(() => ({
  runtime: vi.fn(),
  createRelease: vi.fn(),
  patchDomain: vi.fn(),
  promote: vi.fn(),
  preview: vi.fn(),
}));

vi.mock("../../api/configuration", () => ({
  configApi: {
    runtime: mocks.runtime,
    createRelease: mocks.createRelease,
    patchDomain: mocks.patchDomain,
    promote: mocks.promote,
  },
}));

vi.mock("../../api/supportTemplate", () => ({
  supportTemplateApi: { preview: mocks.preview },
}));

const TEMPLATE = {
  template_id: "support-handoff",
  default_variant_id: "default",
  variants: [
    {
      variant_id: "default",
      selector: {},
      subject_template: "Return {order_number}",
      sections: [
        {
          section_id: "order",
          title: "Order:",
          fields: [
            {
              field_id: "order_number",
              label: "Order Number",
              source_binding: "case_fact:confirmed_order_reference",
              required: true,
            },
          ],
        },
      ],
    },
  ],
};

const RENDERED = {
  template_id: "support-handoff",
  variant_id: "default",
  subject: "Return SAMPLE-ORDER-1",
  text: "Order:\n- Order Number: SAMPLE-ORDER-1",
  sections: [
    {
      section_id: "order",
      title: "Order:",
      return_record_id: null,
      fields: [
        {
          field_id: "order_number",
          label: "Order Number",
          value: "SAMPLE-ORDER-1",
          source: "case_fact",
          source_path: "confirmed_order_reference",
          fact_id: "fact-1",
          applied_fallback: false,
        },
      ],
    },
  ],
  gaps: [],
  review_blocked: false,
};

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

let grants: string[] = [];

beforeEach(() => {
  grants = ["config.runtime.read", "config.release.read", "config.release.promote"];
  mocks.runtime.mockReset().mockResolvedValue({
    release_id: "rel-1",
    head_revision: 41,
    configuration: { support_template: TEMPLATE },
  });
  mocks.createRelease.mockReset().mockResolvedValue({});
  mocks.patchDomain.mockReset().mockResolvedValue({});
  mocks.promote.mockReset().mockResolvedValue({});
  mocks.preview.mockReset().mockResolvedValue(RENDERED);
  // Publishing asks first; jsdom has no dialogs, so the answer is stubbed.
  // Overridden in the test that checks the refusal path.
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the template an operator is editing", () => {
  it("loads the active release's template rather than an invented one", async () => {
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    expect(await screen.findByDisplayValue("Return {order_number}")).toBeInTheDocument();
    expect(screen.getByText(/Release rel-1/)).toBeInTheDocument();
  });

  it("says so when the release carries no template yet", async () => {
    mocks.runtime.mockResolvedValue({
      release_id: "rel-0",
      head_revision: 3,
      configuration: {},
    });
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    expect(
      await screen.findByText(/carries no template yet/),
    ).toBeInTheDocument();
  });

  it("previews the draft in the editor, not the published one", async () => {
    const user = userEvent.setup();
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    const subject = await screen.findByDisplayValue("Return {order_number}");
    await user.clear(subject);
    // `{{` is userEvent's escape for a literal brace.
    await user.type(subject, "Edited {{order_number}");
    await user.click(screen.getByRole("button", { name: /render preview/i }));

    await waitFor(() => { expect(mocks.preview).toHaveBeenCalled(); });
    const [sent] = mocks.preview.mock.calls[0] as [Record<string, unknown>];
    const variants = sent.variants as { subject_template: string }[];
    expect(variants[0].subject_template).toBe("Edited {order_number}");
  });

  it("judges the selectors against the case shape the operator described", async () => {
    const user = userEvent.setup();
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    await screen.findByDisplayValue("Return {order_number}");
    await user.type(screen.getByLabelText("Shipping modes"), "BRANCH_LTL, PREPAID_PARCEL");
    await user.clear(screen.getByLabelText("Item count"));
    await user.type(screen.getByLabelText("Item count"), "4");
    await user.click(screen.getByRole("button", { name: /render preview/i }));

    await waitFor(() => { expect(mocks.preview).toHaveBeenCalled(); });
    const [, context] = mocks.preview.mock.calls[0] as [unknown, Record<string, unknown>];
    expect(context.shipping_modes).toEqual(["BRANCH_LTL", "PREPAID_PARCEL"]);
    expect(context.item_count).toBe(4);
  });

  it("shows the chosen variant, the text, and where each value came from", async () => {
    const user = userEvent.setup();
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    await screen.findByDisplayValue("Return {order_number}");
    await user.click(screen.getByRole("button", { name: /render preview/i }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("default");
    expect(status).toHaveTextContent(/every required field filled/);
    expect(screen.getByText("Return SAMPLE-ORDER-1")).toBeInTheDocument();
    expect(screen.getByText("case_fact: confirmed_order_reference")).toBeInTheDocument();
    expect(screen.getByText("fact fact-1")).toBeInTheDocument();
  });

  it("names a gap as a hold rather than as a rendering detail", async () => {
    mocks.preview.mockResolvedValue({
      ...RENDERED,
      gaps: [{ field_id: "order_number", reason: "no value for case_fact:confirmed_order_reference" }],
      review_blocked: true,
    });
    const user = userEvent.setup();
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    await screen.findByDisplayValue("Return {order_number}");
    await user.click(screen.getByRole("button", { name: /render preview/i }));

    expect(await screen.findByText(/would be held rather than sent/)).toBeInTheDocument();
    expect(screen.getByText("order_number")).toBeInTheDocument();
  });

  it("refuses to preview unparseable JSON, and says why", async () => {
    const user = userEvent.setup();
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    await screen.findByDisplayValue("Return {order_number}");
    await user.click(screen.getByRole("button", { name: /^json$/i }));
    const editor = screen.getByLabelText("Support template JSON");
    await user.clear(editor);
    await user.type(editor, "{{ not json");

    const button = screen.getByRole("button", { name: /render preview/i });
    expect(button).toBeDisabled();
    expect(screen.getByText(/does not parse yet/)).toBeInTheDocument();
    expect(mocks.preview).not.toHaveBeenCalled();
  });
});

describe("publishing the change", () => {
  it("patches support_template on the RETURN_PLATFORM domain of a fresh draft", async () => {
    const user = userEvent.setup();
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    const subject = await screen.findByDisplayValue("Return {order_number}");
    await user.clear(subject);
    await user.type(subject, "Return of {{order_number}");
    await user.click(screen.getByRole("button", { name: /publish release/i }));

    await waitFor(() => { expect(mocks.patchDomain).toHaveBeenCalled(); });
    const [releaseId, domainKey, patch] = mocks.patchDomain.mock.calls[0] as [
      string,
      string,
      { support_template: { variants: { subject_template: string }[] } },
    ];
    expect(releaseId).toMatch(/^support-template-/);
    expect(domainKey).toBe("RETURN_PLATFORM");
    expect(patch.support_template.variants[0].subject_template).toBe("Return of {order_number}");
    // The head revision guards the publish, so two operators cannot both win.
    expect(mocks.promote).toHaveBeenLastCalledWith(releaseId, "RELEASED", 41);
  });

  it("asks before publishing, and publishes nothing when the answer is no", async () => {
    // The Agents tab's button proposes; this one changes what the platform
    // runs. Declining has to leave the release lifecycle untouched.
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    const subject = await screen.findByDisplayValue("Return {order_number}");
    await user.type(subject, "!");
    await user.click(screen.getByRole("button", { name: /publish release/i }));

    expect(mocks.createRelease).not.toHaveBeenCalled();
    expect(mocks.patchDomain).not.toHaveBeenCalled();
    expect(mocks.promote).not.toHaveBeenCalled();
  });

  it("reports the published release without claiming running cases changed", async () => {
    const user = userEvent.setup();
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    const subject = await screen.findByDisplayValue("Return {order_number}");
    await user.type(subject, "!");
    await user.click(screen.getByRole("button", { name: /publish release/i }));

    const published = await screen.findByText(/is published/);
    expect(published).toHaveTextContent(/keep the template they started with/);
  });

  it("keeps the confirmation when the refetch brings back a new release", async () => {
    // RV advisory A2. Publishing invalidates the runtime query, and in
    // production the refetch answers with the *new* release id -- which changes
    // the editor's `key`, remounts it, and destroys anything rendered inside
    // it. The old test could not see this because the mock returned `rel-1`
    // forever, so the key never changed and the confirmation never had to
    // survive anything.
    const user = userEvent.setup();
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    const subject = await screen.findByDisplayValue("Return {order_number}");
    await user.type(subject, "!");
    mocks.runtime.mockResolvedValue({
      release_id: "support-template-20260830-120000",
      head_revision: 42,
      configuration: { support_template: TEMPLATE },
    });
    await user.click(screen.getByRole("button", { name: /publish release/i }));

    // The editor has remounted onto the new release...
    expect(await screen.findByText(/Release support-template-20260830-120000/)).toBeInTheDocument();
    // ...and the operator is still told what happened.
    expect(screen.getByText(/is published/)).toHaveTextContent(
      /keep the template they started with/,
    );
  });

  it("shows the backend's refusal verbatim", async () => {
    mocks.promote.mockRejectedValue(new Error("expected_head_revision does not match"));
    const user = userEvent.setup();
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    const subject = await screen.findByDisplayValue("Return {order_number}");
    await user.type(subject, "!");
    await user.click(screen.getByRole("button", { name: /publish release/i }));

    expect(
      await screen.findByText("expected_head_revision does not match"),
    ).toBeInTheDocument();
  });

  it("offers preview but not publishing without the promote capability", async () => {
    grants = ["config.runtime.read"];
    const user = userEvent.setup();
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    await screen.findByDisplayValue("Return {order_number}");
    expect(screen.getByRole("button", { name: /publish release/i })).toBeDisabled();
    expect(screen.getByText(/Preview works without it/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /render preview/i }));
    await waitFor(() => { expect(mocks.preview).toHaveBeenCalled(); });
  });

  it("keeps the editor's modes reachable", async () => {
    const user = userEvent.setup();
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    await screen.findByDisplayValue("Return {order_number}");
    await user.click(screen.getByRole("button", { name: /^split$/i }));
    const split = screen.getByLabelText("Support template JSON");
    expect(within(document.body).getByText("Nested key-value")).toBeInTheDocument();
    expect(split).toHaveValue(JSON.stringify(TEMPLATE, null, 2));
  });
});
