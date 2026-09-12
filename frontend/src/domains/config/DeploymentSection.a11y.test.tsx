/**
 * `/config/deployment` accessibility: every control has a programmatic name,
 * and `OrderedList` (provider order) is fully operable from the keyboard --
 * the same shape `AgentsSection.a11y.test.tsx` checks for its own screen.
 * There is no `axe-core` in this suite (it exists only as
 * `@axe-core/playwright`, run against real routes in
 * `tests/canonical-routes.spec.ts`); this is the vitest-side equivalent this
 * codebase already uses for a component-level a11y check.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CapabilityContext } from "../../hooks/capabilityContext";
import { DeploymentSection } from "./DeploymentSection";

const mocks = vi.hoisted(() => ({ runtime: vi.fn(), validateDomain: vi.fn(), publish: vi.fn() }));

vi.mock("../../api/configuration", () => ({
  configApi: { runtime: mocks.runtime, validateDomain: mocks.validateDomain, publish: mocks.publish },
}));

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
    environment: "development",
    configuration: {
      deployment: {
        ai: {
          provider_order: ["GOOGLE", "NVIDIA", "SIMULATOR"],
          model_pools: {},
          google: { thinking_budget: 2048, response_schema: false },
        },
        dependencies: { omc: "SIMULATED", parcel: "SIMULATED", freight: "SIMULATED", lsi: "SIMULATED" },
        feedback_learning: { enabled: true },
        support_ticket: { mode: "INTERNAL", base_url: null },
      },
      runtime_integrations: { ai_providers: [] },
    },
  });
  mocks.validateDomain.mockReset().mockResolvedValue({ valid: true, errors: [] });
  mocks.publish.mockReset();
});

describe("every editable field says what it is", () => {
  it("leaves no combobox, spinbutton or checkbox without an accessible name", async () => {
    render(<DeploymentSection />, { wrapper: Wrapper });
    await screen.findByRole("list", { name: "Provider order" });

    const unnamed = screen
      .getAllByRole("combobox")
      .concat(screen.getAllByRole("spinbutton"), screen.getAllByRole("checkbox"), screen.getAllByRole("textbox"))
      .filter((control) => (control.getAttribute("aria-label") ?? "") === "")
      .filter((control) => {
        const labelledBy = control.getAttribute("aria-labelledby");
        if (labelledBy !== null) return document.getElementById(labelledBy) === null;
        if (control.closest("label") !== null) return false;
        // `Field`'s own mechanism: a sibling `<label htmlFor={id}>`, the
        // ordinary native association -- not a wrapper, and not
        // `aria-labelledby`.
        const id = control.getAttribute("id");
        return id === null || document.querySelector(`label[for="${id}"]`) === null;
      });

    expect(
      unnamed.map((control) => control.outerHTML.slice(0, 90)),
      "controls with no programmatic label",
    ).toEqual([]);
  });

  it("names each dependency-mode select distinctly, not with one shared label", async () => {
    render(<DeploymentSection />, { wrapper: Wrapper });
    await screen.findByRole("list", { name: "Provider order" });

    expect(screen.getByRole("combobox", { name: "OMC" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Parcel" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Freight" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "LSI" })).toBeInTheDocument();
  });
});

describe("provider order is fully keyboard-operable", () => {
  it("moves an item with the Move up/down buttons alone -- no drag needed", async () => {
    const user = userEvent.setup();
    render(<DeploymentSection />, { wrapper: Wrapper });
    const list = await screen.findByRole("list", { name: "Provider order" });

    // The first item's "up" button is disabled -- nowhere to go -- and the
    // last item's "down" button likewise, so every reachable move is a real
    // move, not a no-op the keyboard can still trigger.
    expect(screen.getByRole("button", { name: "Move GOOGLE up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move SIMULATOR down" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Move SIMULATOR up" }));
    expect(within(list).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "GOOGLE",
      "SIMULATOR",
      "NVIDIA",
    ]);
  });
});
