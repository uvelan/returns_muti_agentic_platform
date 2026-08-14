/**
 * The agent configuration editors.
 *
 * The risk here is not rendering: it is an editor that quietly loses or
 * mangles what an operator typed. These assert the three ways that happens --
 * an invalid JSON edit discarded on a mode switch, a nested value written to
 * the wrong place, and a rejection shown as something vaguer than what the
 * backend said.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentsSection } from "./AgentsSection";
import { CapabilityContext } from "../../hooks/capabilityContext";

const mocks = vi.hoisted(() => ({ list: vi.fn(), read: vi.fn(), save: vi.fn() }));

vi.mock("../../api/agentConfig", () => ({
  agentConfigApi: { list: mocks.list, read: mocks.read, save: mocks.save },
}));

function makeWrapper(canWrite: boolean) {
  return function TestWrapper({ children }: { children: ReactNode }) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return (
      <QueryClientProvider client={client}>
        <CapabilityContext.Provider
          value={{
            principal: undefined,
            isLoading: false,
            isUnauthenticated: false,
            error: null,
            can: (capability) =>
              canWrite && capability === "governance.proposal.write",
            canAny: (...capabilities) =>
              canWrite && capabilities.includes("governance.proposal.write"),
          }}
        >
          {children}
        </CapabilityContext.Provider>
      </QueryClientProvider>
    );
  };
}

const wrapper = makeWrapper(true);
const readOnlyWrapper = makeWrapper(false);

const DOCUMENT = {
  module_id: "agent.order_discovery",
  module_type: "AGENT",
  status: "DRAFT",
  payload: {
    name: "Order Discovery Agent",
    enabled: true,
    capabilities: ["rank_candidates", "normalize_evidence"],
    limits: { max_queries: 12 },
  },
};

beforeEach(() => {
  mocks.list.mockReset().mockResolvedValue([
    {
      manifestId: "agent.order_discovery",
      moduleId: "agent.order_discovery",
      name: "Order Discovery Agent",
      enabled: true,
      status: "DRAFT",
      configurationVersion: "2.0.0",
      source: "RELEASE",
    },
  ]);
  mocks.read.mockReset().mockResolvedValue({
    manifestId: "agent.order_discovery",
    moduleId: "agent.order_discovery",
    path: "agents/order_discovery.yaml",
    document: DOCUMENT,
    source: "RELEASE",
  });
  mocks.save.mockReset().mockResolvedValue({
    proposalId: "proposal-agent-1",
    manifestId: "agent.order_discovery",
    status: "REVIEW_PENDING",
    risk: "MEDIUM",
    affectedKeys: ["agent.order_discovery"],
    proposedBy: "operator",
    submittedAt: "2026-08-14T00:00:00Z",
  });
});

describe("agent configuration", () => {
  it("selects the first agent so the pane is never empty beside a full list", async () => {
    render(<AgentsSection />, { wrapper });
    expect(await screen.findByDisplayValue("Order Discovery Agent")).toBeInTheDocument();
    expect(screen.getByText("agents/order_discovery.yaml")).toBeInTheDocument();
    expect(screen.getAllByText("Active release")).toHaveLength(2);
  });
  it("renders a genuinely read-only editor without proposal-write access", async () => {
    render(<AgentsSection />, { wrapper: readOnlyWrapper });
    expect(await screen.findByDisplayValue("Order Discovery Agent")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Submit for review" })).toBeDisabled();
    expect(screen.getByText(/Read-only access/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "JSON" }));
    expect(screen.getByLabelText("Agent configuration JSON")).toHaveAttribute("readonly");
  });

  it("keeps an unsaved draft when agent switching is cancelled", async () => {
    mocks.list.mockResolvedValue([
      {
        manifestId: "agent.order_discovery",
        moduleId: "agent.order_discovery",
        name: "Order Discovery Agent",
        enabled: true,
        status: "DRAFT",
        configurationVersion: "2.0.0",
        source: "RELEASE",
      },
      {
        manifestId: "agent.second",
        moduleId: "agent.second",
        name: "Second Agent",
        enabled: false,
        status: "DRAFT",
        configurationVersion: "1.0.0",
        source: "PACKAGED_BASELINE",
      },
    ]);
    mocks.read.mockImplementation((manifestId: string) =>
      Promise.resolve(manifestId === "agent.second" ? {
        manifestId,
        moduleId: manifestId,
        path: "agents/second.yaml",
        source: "PACKAGED_BASELINE",
        document: {
          ...DOCUMENT,
          module_id: manifestId,
          payload: { ...DOCUMENT.payload, name: "Second Agent" },
        },
      } : {
        manifestId,
        moduleId: manifestId,
        path: "agents/order_discovery.yaml",
        source: "RELEASE",
        document: DOCUMENT,
      }),
    );
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<AgentsSection />, { wrapper });
    const name = await screen.findByDisplayValue("Order Discovery Agent");
    fireEvent.change(name, { target: { value: "Unsaved agent name" } });
    fireEvent.click(screen.getByRole("button", { name: /Second Agent/ }));

    expect(confirm).toHaveBeenCalled();
    expect(screen.getByDisplayValue("Unsaved agent name")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Second Agent")).not.toBeInTheDocument();
    confirm.mockRestore();
  });
  it("requires confirmation before Reset discards a draft", async () => {
    const confirm = vi.spyOn(window, "confirm")
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    render(<AgentsSection />, { wrapper });
    const name = await screen.findByDisplayValue("Order Discovery Agent");
    fireEvent.change(name, { target: { value: "Unsaved agent name" } });

    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    const reset = screen.getByRole("button", { name: "Reset" });
    fireEvent.click(reset);
    expect(screen.getByDisplayValue("Unsaved agent name")).toBeInTheDocument();

    fireEvent.click(reset);
    expect(screen.getByDisplayValue("Order Discovery Agent")).toBeInTheDocument();
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit for review" })).toBeDisabled();
    confirm.mockRestore();
  });

  it("restores the editor URL when Back or Forward discard is cancelled", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<AgentsSection />, { wrapper });
    const name = await screen.findByDisplayValue("Order Discovery Agent");
    const protectedUrl = window.location.href;
    fireEvent.change(name, { target: { value: "Unsaved agent name" } });
    await waitFor(() => {
      expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    });

    window.history.pushState(null, "", "/another-workspace");
    window.dispatchEvent(new PopStateEvent("popstate"));

    expect(confirm).toHaveBeenCalled();
    expect(window.location.href).toBe(protectedUrl);
    expect(screen.getByDisplayValue("Unsaved agent name")).toBeInTheDocument();
    confirm.mockRestore();
  });



  it("renders nested objects and arrays, not just the top level", async () => {
    // These payloads nest objects inside arrays inside objects. An editor that
    // stopped at the first level would leave most of the configuration
    // unreachable without dropping into raw JSON.
    render(<AgentsSection />, { wrapper });
    expect(await screen.findByDisplayValue("rank_candidates")).toBeInTheDocument();
    expect(screen.getByDisplayValue("normalize_evidence")).toBeInTheDocument();
    expect(screen.getByDisplayValue("12")).toBeInTheDocument();
    expect(screen.getByText("Max queries")).toBeInTheDocument();
  });

  it("writes a nested edit to that field and nothing else", async () => {
    render(<AgentsSection />, { wrapper });
    const field = await screen.findByDisplayValue("rank_candidates");
    const submit = screen.getByRole("button", { name: "Submit for review" });
    expect(submit).toBeDisabled();
    fireEvent.change(field, { target: { value: "rank_orders" } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => {
      expect(mocks.save).toHaveBeenCalledTimes(1);
    });
    const [, saved] = mocks.save.mock.calls[0] as [string, typeof DOCUMENT];
    expect(saved.payload.capabilities).toEqual(["rank_orders", "normalize_evidence"]);
    expect(saved.payload.name).toBe("Order Discovery Agent");
    expect(saved.module_id).toBe("agent.order_discovery");
    expect(await screen.findByRole("status")).toHaveTextContent(
      "The active configuration has not changed",
    );
    expect(screen.getByRole("link", { name: "Open Approvals" })).toHaveAttribute("href", "/approvals");
    expect(submit).toBeDisabled();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.change(screen.getByDisplayValue("rank_orders"), {
      target: { value: "rank_orders_v2" },
    });
    expect(submit).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(screen.getByDisplayValue("rank_orders")).toBeInTheDocument();
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByDisplayValue("rank_orders"), {
      target: { value: "rank_candidates" },
    });
    expect(submit).toBeDisabled();
    expect(mocks.save).toHaveBeenCalledTimes(1);
    confirm.mockRestore();

  });

  it("edits the same document as JSON", async () => {
    render(<AgentsSection />, { wrapper });
    await screen.findByDisplayValue("Order Discovery Agent");
    fireEvent.click(screen.getByRole("button", { name: "JSON" }));

    const editor = screen.getByLabelText("Agent configuration JSON");
    const edited = { ...DOCUMENT, payload: { ...DOCUMENT.payload, enabled: false } };
    fireEvent.change(editor, { target: { value: JSON.stringify(edited) } });
    fireEvent.click(screen.getByRole("button", { name: "Submit for review" }));

    await waitFor(() => {
      expect(mocks.save).toHaveBeenCalledTimes(1);
    });
    const [, saved] = mocks.save.mock.calls[0] as [string, typeof DOCUMENT];
    expect(saved.payload.enabled).toBe(false);
  });

  it("preserves a JSON edit when opening synchronized split view", async () => {
    render(<AgentsSection />, { wrapper });
    await screen.findByDisplayValue("Order Discovery Agent");
    fireEvent.click(screen.getByRole("button", { name: "JSON" }));

    const edited = { ...DOCUMENT, payload: { ...DOCUMENT.payload, name: "Edited in JSON" } };
    const source = JSON.stringify(edited);
    fireEvent.change(screen.getByLabelText("Agent configuration JSON"), {
      target: { value: source },
    });
    fireEvent.click(screen.getByRole("button", { name: "Split" }));

    expect(screen.getByLabelText("Agent configuration JSON")).toHaveValue(source);
    expect(screen.getByDisplayValue("Edited in JSON")).toBeInTheDocument();
  });

  it("refuses to leave JSON mode rather than silently discarding a broken edit", async () => {
    // The form cannot render text that is not a document. Dropping it without
    // saying so loses work the operator can see on screen.
    render(<AgentsSection />, { wrapper });
    await screen.findByDisplayValue("Order Discovery Agent");
    fireEvent.click(screen.getByRole("button", { name: "JSON" }));
    fireEvent.change(screen.getByLabelText("Agent configuration JSON"), {
      target: { value: "{ not json" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Key-value" }));

    expect(screen.getByRole("alert")).toBeInTheDocument();
    // Still in JSON mode, with the text intact.
    expect(screen.getByLabelText("Agent configuration JSON")).toHaveValue("{ not json");
    expect(mocks.save).not.toHaveBeenCalled();
  });

  it("refuses split view while JSON is invalid", async () => {
    render(<AgentsSection />, { wrapper });
    await screen.findByDisplayValue("Order Discovery Agent");
    fireEvent.click(screen.getByRole("button", { name: "JSON" }));
    fireEvent.change(screen.getByLabelText("Agent configuration JSON"), {
      target: { value: "{ still not json" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Split" }));

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByLabelText("Agent configuration JSON")).toHaveValue("{ still not json");
    expect(screen.getByRole("button", { name: "Split" })).toHaveAttribute("aria-pressed", "false");
  });

  it("will not save text that is not JSON", async () => {
    render(<AgentsSection />, { wrapper });
    await screen.findByDisplayValue("Order Discovery Agent");
    fireEvent.click(screen.getByRole("button", { name: "JSON" }));
    fireEvent.change(screen.getByLabelText("Agent configuration JSON"), {
      target: { value: "[1, 2, 3]" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit for review" }));

    expect(screen.getByRole("alert")).toHaveTextContent("must be an object");
    expect(mocks.save).not.toHaveBeenCalled();
  });

  it("shows the backend's own reason for a rejection", async () => {
    // The backend validates by writing the file and reloading it through the
    // loader the platform boots from, so its message names the field. Replacing
    // that with "invalid configuration" gives an operator nothing to correct.
    mocks.save.mockRejectedValue(
      new Error("Manifest module 'agent.order_discovery' declares module_id 'agent.other'"),
    );
    render(<AgentsSection />, { wrapper });
    const field = await screen.findByDisplayValue("Order Discovery Agent");
    fireEvent.change(field, { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit for review" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("declares module_id 'agent.other'");
  });

  it("says the list could not be loaded rather than showing no agents", async () => {
    mocks.list.mockRejectedValue(new Error("Agent configuration is not available."));
    render(<AgentsSection />, { wrapper });
    expect(await screen.findByRole("alert")).toHaveTextContent("not available");
  });

  it("adds a typed property inside a nested child object", async () => {
    render(<AgentsSection />, { wrapper });
    const keyFields = await screen.findAllByLabelText("New property key");
    const typeFields = screen.getAllByLabelText("New property type");
    const addButtons = screen.getAllByRole("button", { name: "Add property" });

    fireEvent.change(keyFields[0], { target: { value: "query_timeout" } });
    fireEvent.change(typeFields[0], { target: { value: "number" } });
    fireEvent.click(addButtons[0]);
    fireEvent.change(screen.getByDisplayValue("0"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit for review" }));

    await waitFor(() => {
      expect(mocks.save).toHaveBeenCalledTimes(1);
    });
    const [, saved] = mocks.save.mock.calls[0] as [
      string,
      typeof DOCUMENT & { payload: { limits: { query_timeout: number } } },
    ];
    expect(saved.payload.limits.query_timeout).toBe(30);
  });

  it("adds a list entry shaped like the existing ones", async () => {
    mocks.read.mockResolvedValue({
      manifestId: "agent.order_discovery",
      moduleId: "agent.order_discovery",
      path: "agents/order_discovery.yaml",
      document: {
        dependencies: [{ module_id: "policy.clarification", version_constraint: "^2.0" }],
      },
    });
    render(<AgentsSection />, { wrapper });
    fireEvent.click(await screen.findByRole("button", { name: "Add" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit for review" }));

    await waitFor(() => {
      expect(mocks.save).toHaveBeenCalledTimes(1);
    });
    const [, saved] = mocks.save.mock.calls[0] as [string, { dependencies: unknown[] }];
    // The object's fields, not a bare string the operator then has to reshape.
    expect(saved.dependencies[1]).toEqual({ module_id: "", version_constraint: "" });
  });
});
