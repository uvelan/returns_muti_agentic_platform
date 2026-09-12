/**
 * The Agents screen: a typed table over the live `RETURN_PLATFORM.agents`
 * section (CFG-5b), with the pre-CFG-5b JSON editor kept as the Advanced
 * escape hatch.
 *
 * The risk is the same shape it always was for a governed edit -- a save
 * that changes the wrong field, a rejection shown as something vaguer than
 * what the backend said, a read-only principal who can still submit -- plus
 * one CFG-5b-specific one: a typed field save must round-trip the agent's
 * *whole* document, not a hand-built subset, or a dead knob a previous edit
 * set would silently reset to its default.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentsSection } from "./AgentsSection";
import { CapabilityContext } from "../../hooks/capabilityContext";

const mocks = vi.hoisted(() => ({ list: vi.fn(), read: vi.fn(), save: vi.fn(), runtime: vi.fn() }));

vi.mock("../../api/agentConfig", () => ({
  agentConfigApi: { list: mocks.list, read: mocks.read, save: mocks.save },
}));
vi.mock("../../api/configuration", () => ({
  configApi: { runtime: mocks.runtime },
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
            can: (capability) => canWrite && capability === "governance.proposal.write",
            canAny: (...capabilities) => canWrite && capabilities.includes("governance.proposal.write"),
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

const ORDER_DISCOVERY_SUMMARY = {
  manifestId: "order_discovery",
  name: "Order Discovery Agent",
  version: "2.0",
  enabled: true,
  aiAssisted: true,
  aiRouteRef: "ORDER_CANDIDATE_ANALYSIS_V1",
  source: "RELEASE",
};

const ORDER_DISCOVERY_DOCUMENT = {
  name: "Order Discovery Agent",
  version: "2.0",
  enabled: true,
  ai_assisted: true,
  ai_route_ref: "ORDER_CANDIDATE_ANALYSIS_V1",
  // A dead knob a previous edit set to something non-default -- a typed-field
  // save must carry this through unchanged.
  retry_max_attempts: 7,
};

function agentDocument(manifestId: string) {
  if (manifestId === "order_discovery") {
    return { manifestId, path: `RETURN_PLATFORM.agents.${manifestId}`, document: ORDER_DISCOVERY_DOCUMENT, source: "RELEASE" };
  }
  return {
    manifestId,
    path: `RETURN_PLATFORM.agents.${manifestId}`,
    document: {
      name: "Feedback Learning Agent",
      version: "2.0",
      enabled: false,
      ai_assisted: false,
      ai_route_ref: null,
    },
    source: "RELEASE",
  };
}

beforeEach(() => {
  mocks.list.mockReset().mockResolvedValue([ORDER_DISCOVERY_SUMMARY]);
  mocks.read.mockReset().mockImplementation((manifestId: string) => Promise.resolve(agentDocument(manifestId)));
  mocks.save.mockReset().mockResolvedValue({
    proposalId: "proposal-agent-1",
    manifestId: "order_discovery",
    status: "REVIEW_PENDING",
    risk: "MEDIUM",
    affectedKeys: ["agent.enabled"],
    proposedBy: "operator",
    submittedAt: "2026-08-14T00:00:00Z",
  });
  mocks.runtime.mockReset().mockResolvedValue({
    ai_gateway_configuration: {
      tasks: {
        ORDER_CANDIDATE_ANALYSIS_V1: {},
        SUPPORT_MESSAGE_CLASSIFY_V1: {},
      },
    },
  });
});

/**
 * The row's accessible name includes the loading placeholder's own text
 * ("Loading order_discovery...") until the row's document resolves, so
 * `findByRole("row", {name: /order_discovery/})` would match too early --
 * waiting on the loaded field itself and walking up to its `<tr>` instead.
 */
async function agentRow(): Promise<HTMLElement> {
  const field = await screen.findByDisplayValue("Order Discovery Agent");
  const row = field.closest("tr");
  if (row === null) throw new Error("expected the agent's name field to be inside a table row");
  return row;
}

describe("the typed table", () => {
  it("renders one row per agent with its typed fields", async () => {
    render(<AgentsSection />, { wrapper });
    const row = within(await agentRow());
    expect(row.getByDisplayValue("Order Discovery Agent")).toBeInTheDocument();
    expect(row.getByDisplayValue("2.0")).toBeInTheDocument();
    expect(row.getByRole("checkbox", { name: "Enabled" })).toBeChecked();
    expect(row.getByRole("checkbox", { name: "AI-assisted" })).toBeChecked();
    expect(row.getByRole("combobox", { name: "AI route" })).toHaveValue("ORDER_CANDIDATE_ANALYSIS_V1");
  });

  it("lists the AI gateway tasks from the runtime snapshot as route options", async () => {
    render(<AgentsSection />, { wrapper });
    const select = within(await agentRow()).getByRole("combobox", { name: "AI route" });
    const options = within(select).getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["None", "ORDER_CANDIDATE_ANALYSIS_V1", "SUPPORT_MESSAGE_CLASSIFY_V1"]);
  });

  it("disables every control and states why without proposal-write access", async () => {
    render(<AgentsSection />, { wrapper: readOnlyWrapper });
    await screen.findByDisplayValue("Order Discovery Agent");
    expect(screen.getByRole("checkbox", { name: "Enabled" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByText(/Read-only access/)).toBeInTheDocument();
  });

  // RV round 1, A1 (carrying CFG-5's own H1/H2, never actually applied
  // there): a disabled Save button in its resting state (dirty is false on
  // every row until an operator edits something) must not dim by fading its
  // own text with an opacity utility -- `disabled:opacity-40` on
  // `bg-primary`/`text-on-primary` composited to 2.12:1, the same shape of
  // defect CFG-5's F1 was blocking on. Asserted as a family, not the one
  // spelling that caused it: no `opacity-*` utility at all, and the
  // dimming lands on the non-text channels (background, border) rather
  // than only on `text-*`, so a future edit cannot silently reintroduce the
  // fade by moving it from `opacity` onto `text-primary/40` instead.
  it("dims the disabled Save button without fading its own text", async () => {
    render(<AgentsSection />, { wrapper });
    const save = within(await agentRow()).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    expect(save.className).not.toMatch(/(?:^|[\s:])opacity-\d/);
    expect(save.className).toMatch(/disabled:bg-\S+/);
    expect(save.className).toMatch(/disabled:border-\S+/);
  });

  it("saves the whole document with one field changed, and shows the proposal", async () => {
    render(<AgentsSection />, { wrapper });
    const row = within(await agentRow());
    const save = row.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    fireEvent.click(row.getByRole("checkbox", { name: "Enabled" }));
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() => { expect(mocks.save).toHaveBeenCalledTimes(1); });
    const [manifestId, document] = mocks.save.mock.calls[0] as [string, Record<string, unknown>];
    expect(manifestId).toBe("order_discovery");
    // The one field changed...
    expect(document.enabled).toBe(false);
    // ...and every other field, including the dead knob, travelled unchanged.
    expect(document.name).toBe("Order Discovery Agent");
    expect(document.ai_route_ref).toBe("ORDER_CANDIDATE_ANALYSIS_V1");
    expect(document.retry_max_attempts).toBe(7);

    expect(await screen.findByRole("status")).toHaveTextContent(
      "The active configuration has not changed",
    );
    expect(screen.getByRole("link", { name: "Open Approvals" })).toHaveAttribute("href", "/approvals");
    // RV round 1, A4: Save disables itself again immediately, before any
    // further interaction -- a second click with nothing new typed must not
    // file a second, identical proposal.
    expect(save).toBeDisabled();
  });

  it("re-disables Save after a successful save, and a second click files no duplicate proposal", async () => {
    render(<AgentsSection />, { wrapper });
    const row = within(await agentRow());
    const save = row.getByRole("button", { name: "Save" });
    const enabledToggle = row.getByRole("checkbox", { name: "Enabled" });

    fireEvent.click(enabledToggle);
    fireEvent.click(save);
    await waitFor(() => { expect(mocks.save).toHaveBeenCalledTimes(1); });
    await screen.findByRole("status");
    expect(save).toBeDisabled();

    // A click on a disabled button fires no handler -- still exactly one call.
    fireEvent.click(save);
    expect(mocks.save).toHaveBeenCalledTimes(1);

    // One more edit -- even flipping the same toggle back -- is a genuinely
    // new change relative to what was just saved, and re-enables Save.
    fireEvent.click(enabledToggle);
    expect(save).toBeEnabled();
  });

  it("changes the AI route through the enum select", async () => {
    render(<AgentsSection />, { wrapper });
    const row = within(await agentRow());
    fireEvent.change(row.getByRole("combobox", { name: "AI route" }), {
      target: { value: "SUPPORT_MESSAGE_CLASSIFY_V1" },
    });
    fireEvent.click(row.getByRole("button", { name: "Save" }));

    await waitFor(() => { expect(mocks.save).toHaveBeenCalledTimes(1); });
    const [, document] = mocks.save.mock.calls[0] as [string, Record<string, unknown>];
    expect(document.ai_route_ref).toBe("SUPPORT_MESSAGE_CLASSIFY_V1");
  });

  it("clearing the AI route sends null, not an empty string", async () => {
    render(<AgentsSection />, { wrapper });
    const row = within(await agentRow());
    fireEvent.change(row.getByRole("combobox", { name: "AI route" }), { target: { value: "" } });
    fireEvent.click(row.getByRole("button", { name: "Save" }));

    await waitFor(() => { expect(mocks.save).toHaveBeenCalledTimes(1); });
    const [, document] = mocks.save.mock.calls[0] as [string, Record<string, unknown>];
    expect(document.ai_route_ref).toBeNull();
  });

  it("shows the backend's own reason for a rejection", async () => {
    mocks.save.mockRejectedValue(new Error("order_discovery failed validation: name -- Field required"));
    render(<AgentsSection />, { wrapper });
    const row = within(await agentRow());
    fireEvent.click(row.getByRole("checkbox", { name: "Enabled" }));
    fireEvent.click(row.getByRole("button", { name: "Save" }));

    expect(await row.findByRole("alert")).toHaveTextContent("name -- Field required");
  });

  it("says the list could not be loaded rather than showing no agents", async () => {
    mocks.list.mockRejectedValue(new Error("Agent configuration is not available."));
    render(<AgentsSection />, { wrapper });
    expect(await screen.findByRole("alert")).toHaveTextContent("not available");
  });

  it("says so when there are no agents, rather than an empty table", async () => {
    mocks.list.mockResolvedValue([]);
    render(<AgentsSection />, { wrapper });
    expect(await screen.findByText("No agents are configured.")).toBeInTheDocument();
  });
});

describe("Advanced (JSON) mode", () => {
  it("edits the same document as JSON and submits it for review", async () => {
    render(<AgentsSection />, { wrapper });
    await screen.findByDisplayValue("Order Discovery Agent");
    fireEvent.click(screen.getByRole("button", { name: "Advanced (JSON)" }));

    fireEvent.click(screen.getByRole("button", { name: "JSON" }));
    const editor = screen.getByLabelText("Agent configuration JSON");
    const edited = { ...ORDER_DISCOVERY_DOCUMENT, enabled: false };
    fireEvent.change(editor, { target: { value: JSON.stringify(edited) } });
    fireEvent.click(screen.getByRole("button", { name: "Submit for review" }));

    await waitFor(() => { expect(mocks.save).toHaveBeenCalledTimes(1); });
    const [manifestId, saved] = mocks.save.mock.calls[0] as [string, typeof ORDER_DISCOVERY_DOCUMENT];
    expect(manifestId).toBe("order_discovery");
    expect(saved.enabled).toBe(false);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "The active configuration has not changed",
    );
  });

  it("switches which agent is being edited", async () => {
    mocks.list.mockResolvedValue([
      ORDER_DISCOVERY_SUMMARY,
      {
        manifestId: "feedback_learning",
        name: "Feedback Learning Agent",
        version: "2.0",
        enabled: false,
        aiAssisted: false,
        aiRouteRef: null,
        source: "RELEASE",
      },
    ]);
    render(<AgentsSection />, { wrapper });
    await screen.findByDisplayValue("Order Discovery Agent");
    fireEvent.click(screen.getByRole("button", { name: "Advanced (JSON)" }));

    await screen.findByRole("combobox", { name: "Agent" });
    fireEvent.change(screen.getByRole("combobox", { name: "Agent" }), {
      target: { value: "feedback_learning" },
    });

    expect(await screen.findByDisplayValue("Feedback Learning Agent")).toBeInTheDocument();
  });

  it("stays genuinely read-only without proposal-write access", async () => {
    render(<AgentsSection />, { wrapper: readOnlyWrapper });
    await screen.findByDisplayValue("Order Discovery Agent");
    fireEvent.click(screen.getByRole("button", { name: "Advanced (JSON)" }));

    expect(await screen.findByDisplayValue("Order Discovery Agent")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Submit for review" })).toBeDisabled();
  });
});
