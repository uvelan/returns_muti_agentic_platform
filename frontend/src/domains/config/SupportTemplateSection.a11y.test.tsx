/**
 * The same audit `AgentsSection.a11y.test.tsx` runs, on the screen that reuses
 * its editor -- plus the two things this screen adds.
 *
 * The editor's defect was structural rather than a count: the field's name
 * comes from the *parent* object because the name comes from the key, so every
 * scalar leaf rendered a visually-labelled, programmatically-unlabelled
 * control. Asserting the relationship rather than a number means a template
 * with more fields cannot reintroduce it here either.
 *
 * The two additions are the preview's own controls -- an operator describing a
 * case shape is filling in a form, and every input needs a name -- and the
 * keyboard path an operator actually walks: edit, render, read the result.
 * The result is announced through a status region rather than by moving focus,
 * because taking focus back mid-edit is how a caret gets lost.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

/** Deliberately mixed: strings, a boolean, a number, and nested objects. */
const TEMPLATE = {
  template_id: "support-handoff",
  default_variant_id: "default",
  variants: [
    {
      variant_id: "default",
      selector: { min_item_count: 1 },
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
          can: () => true,
          canAny: () => true,
        }}
      >
        {children}
      </CapabilityContext.Provider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  mocks.runtime.mockReset().mockResolvedValue({
    release_id: "rel-1",
    head_revision: 41,
    configuration: { support_template: TEMPLATE },
  });
  mocks.createRelease.mockReset().mockResolvedValue({});
  mocks.patchDomain.mockReset().mockResolvedValue({});
  mocks.promote.mockReset().mockResolvedValue({});
  mocks.preview.mockReset().mockResolvedValue({
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
            fact_id: null,
            applied_fallback: true,
          },
        ],
      },
    ],
    gaps: [],
    review_blocked: false,
  });
});

describe("every control on the template screen says what it is", () => {
  it("leaves no control without an accessible name", async () => {
    render(<SupportTemplateSection />, { wrapper: Wrapper });
    await screen.findByDisplayValue("Return {order_number}");

    const unnamed = screen
      .getAllByRole("textbox")
      .concat(screen.getAllByRole("spinbutton"), screen.queryAllByRole("checkbox"))
      .filter((control) => (control.getAttribute("aria-label") ?? "") === "")
      .filter((control) => {
        const labelledBy = control.getAttribute("aria-labelledby");
        if (labelledBy === null) {
          // An implicit `<label>` wrapper counts, and so does `htmlFor`.
          return (
            control.closest("label") === null
            && (control.id === "" || document.querySelector(`label[for="${control.id}"]`) === null)
          );
        }
        return document.getElementById(labelledBy) === null;
      });

    expect(
      unnamed.map((control) => control.outerHTML.slice(0, 90)),
      "controls with no programmatic label",
    ).toEqual([]);
  });

  it("names a template field after its key, not after its value", async () => {
    render(<SupportTemplateSection />, { wrapper: Wrapper });
    const field = await screen.findByDisplayValue("Return {order_number}");

    const labelledBy = field.getAttribute("aria-labelledby");
    expect(labelledBy).not.toBeNull();
    expect(document.getElementById(labelledBy ?? "")).toHaveTextContent("Subject template");
  });

  it("gives each field its own name rather than one shared id", async () => {
    render(<SupportTemplateSection />, { wrapper: Wrapper });
    await screen.findByDisplayValue("Return {order_number}");

    const ids = screen
      .getAllByRole("textbox")
      .concat(screen.getAllByRole("spinbutton"))
      .map((control) => control.getAttribute("aria-labelledby"))
      .filter((value): value is string => value !== null);

    expect(new Set(ids).size, "distinct label ids").toBe(ids.length);
  });

  it("names every preview control an operator has to fill in", async () => {
    render(<SupportTemplateSection />, { wrapper: Wrapper });
    await screen.findByDisplayValue("Return {order_number}");

    expect(screen.getByLabelText("Shipping modes")).toBeInTheDocument();
    expect(screen.getByLabelText("Return reason classes")).toBeInTheDocument();
    expect(screen.getByLabelText("Order sources")).toBeInTheDocument();
    expect(screen.getByLabelText("Item count")).toBeInTheDocument();
  });

  it("walks edit -> render -> read on the keyboard, and keeps the caret", async () => {
    const user = userEvent.setup();
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    const subject = await screen.findByDisplayValue("Return {order_number}");
    subject.focus();
    await user.keyboard("!");

    const button = screen.getByRole("button", { name: /render preview/i });
    button.focus();
    await user.keyboard("{Enter}");

    await waitFor(() => { expect(mocks.preview).toHaveBeenCalled(); });
    // Announced in place. The result arriving must not pull focus off the
    // control the operator is standing on.
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("default");
    expect(document.activeElement).toBe(button);
  });

  it("says a value came from a fallback in words, not by colour alone", async () => {
    const user = userEvent.setup();
    render(<SupportTemplateSection />, { wrapper: Wrapper });

    await screen.findByDisplayValue("Return {order_number}");
    await user.click(screen.getByRole("button", { name: /render preview/i }));

    expect(await screen.findByText("Fallback used")).toBeInTheDocument();
  });
});
