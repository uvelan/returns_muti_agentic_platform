/**
 * UIAUDIT-011 -- "14 agent labels", and why the number moved around.
 *
 * It was never fourteen source lines. `AgentsSection` renders the agent
 * document recursively: the human-readable field name comes from the *parent*
 * object, because the name comes from the key and the key is the parent's to
 * know, while the input is rendered by the child. So every scalar leaf produced
 * one visually-labelled, programmatically-unlabelled control, and the count was
 * whatever the selected agent's document happened to contain -- fourteen for
 * `bay_allocation`, twenty-four for `order_discovery`.
 *
 * That is why these tests assert the relationship rather than a count: a
 * document with more fields must not be able to reintroduce the defect.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentsSection } from "./AgentsSection";
import { CapabilityContext } from "../../hooks/capabilityContext";

const mocks = vi.hoisted(() => ({ list: vi.fn(), read: vi.fn(), save: vi.fn() }));

vi.mock("../../api/agentConfig", () => ({
  agentConfigApi: { list: mocks.list, read: mocks.read, save: mocks.save },
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
          can: (capability) => capability === "governance.proposal.write",
          canAny: (...capabilities) => capabilities.includes("governance.proposal.write"),
        }}
      >
        {children}
      </CapabilityContext.Provider>
    </QueryClientProvider>
  );
}

/** Deliberately mixed: a string, a number, a boolean and a nested object. */
const DOCUMENT = {
  module_id: "agent.bay_allocation",
  module_type: "AGENT",
  payload: {
    name: "Bay Allocation Agent",
    enabled: true,
    max_bays: 12,
    limits: { max_queries: 8, strict_mode: false },
  },
};

beforeEach(() => {
  mocks.list.mockReset().mockResolvedValue([
    {
      manifestId: "agent.bay_allocation",
      moduleId: "agent.bay_allocation",
      name: "Bay Allocation Agent",
      enabled: true,
      status: "DRAFT",
      configurationVersion: "2.0.0",
      source: "RELEASE",
    },
  ]);
  mocks.read.mockReset().mockResolvedValue({
    manifestId: "agent.bay_allocation",
    moduleId: "agent.bay_allocation",
    path: "agents/bay_allocation.yaml",
    document: DOCUMENT,
    source: "RELEASE",
  });
  mocks.save.mockReset();
});

describe("every editable field says what it is", () => {
  it("leaves no control without an accessible name", async () => {
    render(<AgentsSection />, { wrapper: Wrapper });
    await screen.findByDisplayValue("Bay Allocation Agent");

    const unnamed = screen
      .getAllByRole("textbox")
      .concat(screen.getAllByRole("spinbutton"), screen.getAllByRole("checkbox"))
      .filter((control) => (control.getAttribute("aria-label") ?? "") === "")
      .filter((control) => {
        const labelledBy = control.getAttribute("aria-labelledby");
        if (labelledBy === null) {
          // An implicit `<label>` wrapper counts, and so does `htmlFor`.
          return control.closest("label") === null;
        }
        return document.getElementById(labelledBy) === null;
      });

    expect(
      unnamed.map((control) => control.outerHTML.slice(0, 90)),
      "controls with no programmatic label",
    ).toEqual([]);
  });

  it("names the string field after its key, not after its value", async () => {
    render(<AgentsSection />, { wrapper: Wrapper });
    const field = await screen.findByDisplayValue("Bay Allocation Agent");

    const labelledBy = field.getAttribute("aria-labelledby");
    expect(labelledBy).not.toBeNull();
    expect(document.getElementById(labelledBy ?? "")).toHaveTextContent("Name");
  });

  it("names the boolean after its key rather than 'Yes'", async () => {
    // The checkbox sat inside a `<label>` whose text was `{value ? "Yes" : "No"}`,
    // so its accessible name described its own state and never the field. On a
    // document with several booleans every one of them announced as "Yes".
    render(<AgentsSection />, { wrapper: Wrapper });
    await screen.findByDisplayValue("Bay Allocation Agent");

    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes.length).toBeGreaterThan(0);
    const names = checkboxes.map((box) => {
      const labelledBy = box.getAttribute("aria-labelledby") ?? "";
      return document.getElementById(labelledBy)?.textContent?.trim() ?? "";
    });
    expect(names).not.toContain("Yes");
    expect(names).not.toContain("No");
    expect(names).toContain("Enabled");
  });

  it("gives each field its own name rather than one shared id", async () => {
    // `useId` is per-`ObjectNode`, and the key is appended -- so two objects
    // holding the same key still produce two ids. A single shared id would make
    // every field announce as the first one.
    render(<AgentsSection />, { wrapper: Wrapper });
    await screen.findByDisplayValue("Bay Allocation Agent");

    const ids = screen
      .getAllByRole("textbox")
      .concat(screen.getAllByRole("spinbutton"))
      .map((control) => control.getAttribute("aria-labelledby"))
      .filter((value): value is string => value !== null);

    expect(new Set(ids).size, "distinct label ids").toBe(ids.length);
  });
});
