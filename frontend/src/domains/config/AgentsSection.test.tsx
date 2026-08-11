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

const mocks = vi.hoisted(() => ({ list: vi.fn(), read: vi.fn(), save: vi.fn() }));

vi.mock("../../api/agentConfig", () => ({
  agentConfigApi: { list: mocks.list, read: mocks.read, save: mocks.save },
}));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

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
    },
  ]);
  mocks.read.mockReset().mockResolvedValue({
    manifestId: "agent.order_discovery",
    moduleId: "agent.order_discovery",
    path: "agents/order_discovery.yaml",
    document: DOCUMENT,
  });
  mocks.save.mockReset().mockImplementation((_id: string, document: unknown) =>
    Promise.resolve({
      manifestId: "agent.order_discovery",
      moduleId: "agent.order_discovery",
      path: "agents/order_discovery.yaml",
      document,
    }),
  );
});

describe("agent configuration", () => {
  it("selects the first agent so the pane is never empty beside a full list", async () => {
    render(<AgentsSection />, { wrapper });
    expect(await screen.findByDisplayValue("Order Discovery Agent")).toBeInTheDocument();
    expect(screen.getByText("agents/order_discovery.yaml")).toBeInTheDocument();
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
    fireEvent.change(field, { target: { value: "rank_orders" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mocks.save).toHaveBeenCalledTimes(1);
    });
    const [, saved] = mocks.save.mock.calls[0] as [string, typeof DOCUMENT];
    expect(saved.payload.capabilities).toEqual(["rank_orders", "normalize_evidence"]);
    expect(saved.payload.name).toBe("Order Discovery Agent");
    expect(saved.module_id).toBe("agent.order_discovery");
  });

  it("edits the same document as JSON", async () => {
    render(<AgentsSection />, { wrapper });
    await screen.findByDisplayValue("Order Discovery Agent");
    fireEvent.click(screen.getByRole("button", { name: "JSON" }));

    const editor = screen.getByLabelText("Agent configuration JSON");
    const edited = { ...DOCUMENT, payload: { ...DOCUMENT.payload, enabled: false } };
    fireEvent.change(editor, { target: { value: JSON.stringify(edited) } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mocks.save).toHaveBeenCalledTimes(1);
    });
    const [, saved] = mocks.save.mock.calls[0] as [string, typeof DOCUMENT];
    expect(saved.payload.enabled).toBe(false);
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
    fireEvent.click(screen.getByRole("button", { name: "Form" }));

    expect(screen.getByRole("alert")).toBeInTheDocument();
    // Still in JSON mode, with the text intact.
    expect(screen.getByLabelText("Agent configuration JSON")).toHaveValue("{ not json");
    expect(mocks.save).not.toHaveBeenCalled();
  });

  it("will not save text that is not JSON", async () => {
    render(<AgentsSection />, { wrapper });
    await screen.findByDisplayValue("Order Discovery Agent");
    fireEvent.click(screen.getByRole("button", { name: "JSON" }));
    fireEvent.change(screen.getByLabelText("Agent configuration JSON"), {
      target: { value: "[1, 2, 3]" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

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
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("declares module_id 'agent.other'");
  });

  it("says the list could not be loaded rather than showing no agents", async () => {
    mocks.list.mockRejectedValue(new Error("Agent configuration is not available."));
    render(<AgentsSection />, { wrapper });
    expect(await screen.findByRole("alert")).toHaveTextContent("not available");
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
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mocks.save).toHaveBeenCalledTimes(1);
    });
    const [, saved] = mocks.save.mock.calls[0] as [string, { dependencies: unknown[] }];
    // The object's fields, not a bare string the operator then has to reshape.
    expect(saved.dependencies[1]).toEqual({ module_id: "", version_constraint: "" });
  });
});
