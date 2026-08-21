/**
 * The copilot's derived state.
 *
 * Everything the middle and right panes show is computed from one turn result,
 * so these assert the derivation rather than the markup. The failure this
 * guards against is a pipeline that advances on optimism: the screen claiming
 * the agent has matched an order when the turn only asked a question.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as CasesModuleNamespace from "../../api/cases";
import type * as ActualModuleNamespace from "../../api/orderAgent";
import type { AgentTurnResult, SendTurnInput } from "../../api/orderAgent";
import type { ReturnHistory } from "../../api/returnHistory";
import type { RuntimeConfig } from "../../api/runtimeConfig";
import { RuntimeConfigContext } from "../../hooks/useRuntimeConfig";
import { ReturnCopilotPage } from "./ReturnCopilotPage";
import {
  approvedItem,
  artifact,
  caseProjection,
  confirmedOrder,
  returnRecord,
  shipment,
} from "./fixtures/modeFixtures";

type ActualModule = typeof ActualModuleNamespace;

// Incrementing, so "New return" can be observed to mint a *different* id.
let conversationCounter = 0;

const mocks = vi.hoisted(() => ({
  sendTurn: vi.fn(),
  listConversations: vi.fn(),
  readTranscript: vi.fn(),
  can: vi.fn(),
  readCase: vi.fn(),
  listCases: vi.fn(),
  historyByOrder: vi.fn(),
  historyByCustomer: vi.fn(),
}));

vi.mock("../../api/orderAgent", async (importOriginal) => ({
  ...(await importOriginal<ActualModule>()),
  orderAgentApi: {
    sendTurn: mocks.sendTurn,
    listConversations: mocks.listConversations,
    readTranscript: mocks.readTranscript,
  },
  newConversationId: () => `disc-${String(conversationCounter++)}`,
}));

// Only the transport is stubbed. `caseRefetchInterval` and `keepNewerRevision`
// are the real contract behaviour -- the screen decides when to ask again and
// which answer to believe through them, and a stub would make those decisions
// disappear from every test in this file.
vi.mock("../../api/cases", async (importOriginal) => ({
  ...(await importOriginal<typeof CasesModuleNamespace>()),
  casesApi: { readProjection: mocks.readCase, list: mocks.listCases },
}));

// Mocked rather than left to MSW, even though a fixture handler exists. These
// assertions are about *which* question the screen asks the graph, and a
// fixture that answers every shape identically cannot distinguish an order
// lookup from a customer lookup.
vi.mock("../../api/returnHistory", () => ({
  returnHistoryApi: { byOrder: mocks.historyByOrder, byCustomer: mocks.historyByCustomer },
}));

vi.mock("../../hooks/capabilityContext", () => ({
  useCapabilities: () => ({ can: mocks.can, principal: { subject: "tester" } }),
}));

/**
 * The bootstrap payload the shell publishes to every screen.
 *
 * Supplied through the context rather than left to MSW because the agent id is
 * the thing under test in two of these cases, and a fixture handler answering
 * one value for all of them could not tell them apart.
 */
function runtimeConfig(agents: RuntimeConfig["agents"]): RuntimeConfig {
  return {
    releaseId: "release-under-test",
    environment: "test",
    apiBasePath: "/api",
    features: { orderDiscoveryCopilot: true },
    capabilities: { availableSourceTypes: [], availableModelProviders: [] },
    agents,
  };
}

function wrapperWith(config: RuntimeConfig | null) {
  return function Wrapper({ children }: { children: ReactNode }) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return (
      <QueryClientProvider client={client}>
        <RuntimeConfigContext.Provider value={config}>{children}</RuntimeConfigContext.Provider>
      </QueryClientProvider>
    );
  };
}

const wrapper = wrapperWith(runtimeConfig({ orderDiscovery: "order-discovery-agent" }));

function turn(overrides: Partial<AgentTurnResult> = {}): AgentTurnResult {
  return {
    conversation_id: "disc-test",
    conversation_version: 1,
    client_turn_id: "t-1",
    graph_generation_id: "gen-abc12345",
    model_provider: "MOCK",
    model_name: "scripted",
    query_evidence: [],
    response: { status: "OK", business_capability: "order-discovery", statements: [] },
    ...overrides,
  };
}

beforeEach(() => {
  // The screen keeps the conversation and case ids in the URL, and jsdom's
  // location outlives a test. Without this, one test's resumed return is the
  // next test's reload.
  window.history.replaceState(null, "", "/returns");
  mocks.can.mockReturnValue(true);
  mocks.sendTurn.mockReset();
  mocks.listConversations.mockReset().mockResolvedValue([]);
  mocks.readTranscript.mockReset();
  mocks.readCase.mockReset().mockResolvedValue(caseProjection({ caseId: "case-1" }));
  // Resuming a conversation asks whether it raised a case. Most conversations
  // did not, and that answer is an empty list -- not an absent one.
  mocks.listCases.mockReset().mockResolvedValue([]);
  // No earlier returns is the ordinary answer and a real one, so it is the
  // default here rather than an unset mock that would reject.
  mocks.historyByOrder.mockReset().mockResolvedValue(emptyHistory());
  mocks.historyByCustomer.mockReset().mockResolvedValue(emptyHistory());
  conversationCounter = 0;
});

function emptyHistory(): ReturnHistory {
  return { orderReference: null, accountId: null, customerId: null, cases: [] };
}

describe("the discovery copilot", () => {
  it("opens on the chat, not on a queue", () => {
    // The screen that used to live here listed return sessions. Asserting the
    // prompt is what stops the operations screen drifting back in.
    render(<ReturnCopilotPage />, { wrapper });
    expect(screen.getByLabelText("Message the discovery agent")).toBeInTheDocument();
    expect(screen.getByText(/let's find that order/)).toBeInTheDocument();
  });

  it("refuses the domain without the read capability", () => {
    mocks.can.mockReturnValue(false);
    render(<ReturnCopilotPage />, { wrapper });
    expect(screen.getByText(/do not have access/)).toBeInTheDocument();
  });

  it("shows the context pane empty until a search returns candidates", () => {
    render(<ReturnCopilotPage />, { wrapper });
    expect(screen.getByText(/Matches and their evidence appear here/)).toBeInTheDocument();
  });

  it("marks an evidenced claim without shouting a label over every line", async () => {
    // A GRAPH_FACT is traceable to query evidence and a REASONED_SUGGESTION is
    // the model's inference, so the distinction has to survive -- but it used
    // to be an uppercase banner stamped on every bubble, which is the single
    // thing that made the screen read as a machine filing a report. The mark
    // stays; the shouting does not. A question needs no label: it ends in a
    // question mark.
    mocks.sendTurn.mockResolvedValue(
      turn({
        response: {
          status: "NEEDS_INPUT",
          business_capability: "order-discovery",
          statements: [
            { statement_id: "a", statement_type: "USER_PROVIDED_FACT", text: "Atlas Mechanical" },
            { statement_id: "b", statement_type: "CLARIFICATION_QUESTION", text: "Which branch?" },
          ],
        },
      }),
    );
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas Mechanical");

    expect(await screen.findByText("Which branch?")).toBeInTheDocument();
    // Appears as the associate's own message, as the fact the agent recorded
    // back, and in the progress pane's anchor list -- all three belong.
    expect(screen.getAllByText("Atlas Mechanical").length).toBeGreaterThan(0);
    expect(screen.queryByText("CLARIFICATION QUESTION")).not.toBeInTheDocument();
    expect(screen.queryByText("USER PROVIDED FACT")).not.toBeInTheDocument();
  });

  it("says which claims came from the order records", async () => {
    mocks.sendTurn.mockResolvedValue(
      turn({
        response: {
          status: "RESOLVED",
          business_capability: "order-discovery",
          statements: [
            { statement_id: "g", statement_type: "GRAPH_FACT", text: "Order SO-A1." },
            { statement_id: "s", statement_type: "REASONED_SUGGESTION", text: "Probably that one." },
          ],
        },
      }),
    );
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "melgon");

    expect(await screen.findByText("From order records")).toBeInTheDocument();
    expect(screen.getByText("Suggestion")).toBeInTheDocument();
  });

  it("does not advance past identification while the agent is still asking", async () => {
    mocks.sendTurn.mockResolvedValue(
      turn({
        pending_clarification_thread_id: "thread-1",
        response: {
          status: "NEEDS_INPUT",
          business_capability: "order-discovery",
          statements: [
            { statement_id: "b", statement_type: "CLARIFICATION_QUESTION", text: "Which branch?" },
          ],
        },
      }),
    );
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas");

    await screen.findByText("Which branch?");
    expect(screen.getByText(/has not matched an order yet/)).toBeInTheDocument();
  });

  it("lists candidates once a search produced evidence", async () => {
    mocks.sendTurn.mockResolvedValue(
      turn({
        response: {
          status: "RESOLVED",
          business_capability: "order-discovery",
          statements: [{ statement_id: "c", statement_type: "GRAPH_FACT", text: "Found 1." }],
        },
        query_evidence: [
          {
            query_execution_id: "qe-1",
            schema_version: "v2",
            graph_generation_id: "gen-abc12345",
            logical_plan_checksum: "plan-x",
            compiled_query_checksum: "compiled-x",
            result_checksum: "x",
            result: {
              candidates: [{ data: { sales_order_number: "CQ363350", account_id: "CHARLOTTE" } }],
            },
          },
        ],
      }),
    );
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas");

    expect(await screen.findByText("CQ363350")).toBeInTheDocument();
    expect(screen.getByText("CHARLOTTE")).toBeInTheDocument();
    expect(screen.getByText("Candidates (1)")).toBeInTheDocument();
  });

  it("keeps the matched order when the next turn only answers a question", async () => {
    // A follow-up like "and was it shipped" runs a GRAPH_QUERY, whose evidence
    // carries `rows` and no `candidates`. Derived from the latest turn alone
    // this reset the pane to "not matched yet" and walked the rail back off
    // Orders identified -- the associate had lost nothing, only asked.
    mocks.sendTurn.mockResolvedValueOnce(
      turn({
        query_evidence: [
          {
            query_execution_id: "qe-1",
            schema_version: "v2",
            graph_generation_id: "gen-abc12345",
            logical_plan_checksum: "plan-x",
            compiled_query_checksum: "compiled-x",
            result_checksum: "x",
            result: { candidates: [{ data: { sales_order_number: "CQ363350" } }] },
          },
        ],
      }),
    );
    mocks.sendTurn.mockResolvedValueOnce(
      turn({
        conversation_version: 2,
        query_evidence: [
          {
            query_execution_id: "qe-2",
            schema_version: "v2",
            graph_generation_id: "gen-abc12345",
            logical_plan_checksum: "plan-x",
            compiled_query_checksum: "compiled-x",
            result_checksum: "y",
            result: { rows: [{ order_status: "CALLCSR" }], count: 1 },
          },
        ],
      }),
    );
    const { container } = render(<ReturnCopilotPage />, { wrapper });

    fire(container, "melgon heating");
    expect(await screen.findByText("CQ363350")).toBeInTheDocument();

    fire(container, "was it shipped");
    await waitFor(() => {
      expect(mocks.sendTurn).toHaveBeenCalledTimes(2);
    });
    expect(screen.getByText("CQ363350")).toBeInTheDocument();
    expect(screen.getByText("Candidates (1)")).toBeInTheDocument();
  });

  it("clears the matched order when a later search finds nothing", async () => {
    // The other half of the same rule: an empty result *is* an answer, so a
    // search that genuinely finds nothing must not leave the previous match on
    // screen looking current.
    mocks.sendTurn.mockResolvedValueOnce(
      turn({
        query_evidence: [
          {
            query_execution_id: "qe-1",
            schema_version: "v2",
            graph_generation_id: "gen-abc12345",
            logical_plan_checksum: "plan-x",
            compiled_query_checksum: "compiled-x",
            result_checksum: "x",
            result: { candidates: [{ data: { sales_order_number: "CQ363350" } }] },
          },
        ],
      }),
    );
    mocks.sendTurn.mockResolvedValueOnce(
      turn({
        conversation_version: 2,
        query_evidence: [
          {
            query_execution_id: "qe-2",
            schema_version: "v2",
            graph_generation_id: "gen-abc12345",
            logical_plan_checksum: "plan-x",
            compiled_query_checksum: "compiled-x",
            result_checksum: "y",
            result: { candidates: [] },
          },
        ],
      }),
    );
    const { container } = render(<ReturnCopilotPage />, { wrapper });

    fire(container, "melgon heating");
    expect(await screen.findByText("CQ363350")).toBeInTheDocument();

    fire(container, "actually it was for someone else entirely");
    await waitFor(() => {
      expect(screen.queryByText("CQ363350")).not.toBeInTheDocument();
    });
  });


  it("starts a fresh conversation, and does not carry the last one into it", async () => {
    // The agent's memory is scoped to the conversation, so a new return must
    // mint a new id -- reusing it would let the previous customer's details
    // inform this customer's search. There used to be no way to do this at all
    // short of reloading the browser.
    mocks.sendTurn.mockResolvedValue(turn({ conversation_version: 4 }));
    const { container } = render(<ReturnCopilotPage />, { wrapper });

    fire(container, "melgon heating");
    await waitFor(() => {
      expect(mocks.sendTurn).toHaveBeenCalledTimes(1);
    });
    fireEvent.click(screen.getByRole("button", { name: "New return" }));
    fire(container, "someone else");
    await waitFor(() => {
      expect(mocks.sendTurn).toHaveBeenCalledTimes(2);
    });

    const sent = mocks.sendTurn.mock.calls.map(([input]) => input as SendTurnInput);
    expect(sent[0].conversationId).not.toBe(sent[1].conversationId);
    // And back to 0: a new conversation has no committed version, so carrying
    // the previous one's would be rejected as stale on the first turn.
    expect(sent[1].expectedConversationVersion).toBe(0);
  });

  it("clears the screen when a new return starts", async () => {
    mocks.sendTurn.mockResolvedValue(
      turn({
        response: {
          status: "RESOLVED",
          business_capability: "order-discovery",
          statements: [{ statement_id: "g", statement_type: "GRAPH_FACT", text: "Order CW1." }],
        },
      }),
    );
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "melgon");
    // A `GRAPH_FACT` now appears twice by design -- once in the conversation and
    // once as an established-fact chip in the Extracted & Verified Facts panel,
    // which previously admitted only `USER_PROVIDED_FACT` and so sat empty while
    // the agent had established plenty. `findAllByText` is the assertion that
    // survives that, and clearing must still remove *every* copy.
    expect(await screen.findAllByText("Order CW1.")).not.toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "New return" }));
    expect(screen.queryAllByText("Order CW1.")).toHaveLength(0);
    expect(screen.getByText(/let's find that order/)).toBeInTheDocument();
  });

  it("lists previous returns and reopens one", async () => {
    mocks.listConversations.mockResolvedValue([
      {
        conversationId: "disc-old",
        title: "melgon heating draft motor",
        messageCount: 4,
        updatedAt: "2026-08-11T10:00:00Z",
      },
    ]);
    mocks.readTranscript.mockResolvedValue({
      conversationId: "disc-old",
      conversationVersion: 3,
      messages: [
        { role: "associate", text: "melgon heating draft motor" },
        { role: "agent", text: "Order SO-A1." },
      ],
    });
    render(<ReturnCopilotPage />, { wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("melgon heating draft motor"));

    // Replayed as plain speech: the record keeps role and text, not the typed
    // statements, so a reopened turn cannot claim it cited evidence.
    expect(await screen.findByText("Order SO-A1.")).toBeInTheDocument();
    expect(screen.queryByText("From order records")).not.toBeInTheDocument();
  });

  it("continues a reopened conversation at its own version", async () => {
    // Not 0. The conversation already has committed turns, and a turn built on
    // version 0 is rejected as a stale view.
    mocks.listConversations.mockResolvedValue([
      { conversationId: "disc-old", title: "earlier", messageCount: 2, updatedAt: null },
    ]);
    mocks.readTranscript.mockResolvedValue({
      conversationId: "disc-old",
      conversationVersion: 7,
      messages: [{ role: "associate", text: "earlier" }],
    });
    mocks.sendTurn.mockResolvedValue(turn({ conversation_version: 8 }));
    const { container } = render(<ReturnCopilotPage />, { wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("earlier"));
    await screen.findByText("earlier");

    fire(container, "and the price?");
    await waitFor(() => {
      expect(mocks.sendTurn).toHaveBeenCalledTimes(1);
    });
    const [input] = mocks.sendTurn.mock.calls[0] as [SendTurnInput];
    expect(input.conversationId).toBe("disc-old");
    expect(input.expectedConversationVersion).toBe(7);
  });

  it("says why a return could not be reopened, and keeps the rest of the list", async () => {
    // A conversation the platform cannot serve -- deleted, or never persisted --
    // answers 404. Nothing read the failed mutation's error, so clicking a row
    // in your own history did nothing at all: no transcript, no message, no
    // sign the click landed. Observed live with two open cases pointing at
    // conversations that were gone.
    mocks.listConversations.mockResolvedValue([
      { conversationId: "disc-gone", title: "vanished return", messageCount: 2, updatedAt: null },
      { conversationId: "disc-fine", title: "working return", messageCount: 1, updatedAt: null },
    ]);
    mocks.readTranscript.mockRejectedValue(new Error("Conversation disc-gone does not exist."));
    render(<ReturnCopilotPage />, { wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("vanished return"));

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be opened/i);
    // The one dead row must not take the panel with it.
    expect(screen.getByText("working return")).toBeInTheDocument();
  });

  it("says so plainly when there is no history yet", async () => {
    render(<ReturnCopilotPage />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    expect(await screen.findByText(/Nothing yet/)).toBeInTheDocument();
  });

  it("carries the version forward so the next turn is not rejected as stale", async () => {
    // The backend refuses a turn built on a stale view. The first turn must go
    // out at 0 and the second at whatever the first returned.
    mocks.sendTurn.mockResolvedValue(turn({ conversation_version: 7 }));
    const { container } = render(<ReturnCopilotPage />, { wrapper });

    fire(container, "one");
    await waitFor(() => {
      expect(mocks.sendTurn).toHaveBeenCalledTimes(1);
    });
    fire(container, "two");
    await waitFor(() => {
      expect(mocks.sendTurn).toHaveBeenCalledTimes(2);
    });

    const sent = mocks.sendTurn.mock.calls.map(([input]) => input as SendTurnInput);
    expect(sent[0].expectedConversationVersion).toBe(0);
    expect(sent[1].expectedConversationVersion).toBe(7);
  });

  it("shows progress as stages only, with no platform internals", async () => {
    // The pane is what an associate reads mid-return. The model name, the
    // graph generation and notes about which stages the API can report on are
    // all true and all about the platform rather than the return.
    mocks.sendTurn.mockResolvedValue(turn());
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas");

    expect(await screen.findByText("Progress")).toBeInTheDocument();
    expect(screen.queryByText(/No signal on the turn result/)).not.toBeInTheDocument();
    expect(screen.queryByText(/scripted/)).not.toBeInTheDocument();
    expect(screen.queryByText(/generation/)).not.toBeInTheDocument();
    for (const milestone of ["Orders identified", "Order selected", "Case created"]) {
      expect(screen.getByText(milestone)).toBeInTheDocument();
    }
  });

  it("names the agent that owns each milestone", async () => {
    // Six milestones across four agents. Without the owner, a stuck return
    // says nothing about whose work to go and look at.
    mocks.sendTurn.mockResolvedValue(turn());
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas");

    await screen.findByText("Progress");
    expect(screen.getAllByText("Order Discovery")).toHaveLength(2);
    expect(screen.getByText("Return Workflow")).toBeInTheDocument();
    expect(screen.getByText("Return Fulfillment")).toBeInTheDocument();
    expect(screen.getByText("Bay Allocation")).toBeInTheDocument();
  });

  it("does not claim a milestone whose status still means 'nothing yet'", () => {
    // A fresh session carries NOT_STARTED / NOT_REQUIRED_OR_PENDING / OPEN.
    // Treating a present-but-idle status as progress would mark a return
    // shipped and received the moment it was created.
    render(<ReturnCopilotPage />, { wrapper });
    for (const milestone of ["Shipment in progress", "Reached warehouse", "Completed"]) {
      expect(screen.getByText(milestone)).toBeInTheDocument();
    }
    expect(screen.queryByText("RMA")).not.toBeInTheDocument();
    expect(screen.queryByText("Tracking")).not.toBeInTheDocument();
  });

  it("shows the backend's refusal verbatim", async () => {
    mocks.sendTurn.mockRejectedValue(
      new Error("The request exceeded the configured clarification limits."),
    );
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas");

    expect(await screen.findByRole("alert")).toHaveTextContent(/clarification limits/);
  });
});

/**
 * Which agent a turn is addressed to.
 *
 * The page sent the literal `"order_discovery"`. The active schema keys the
 * policy `order-discovery-agent`, so `agent_policies.get` missed and every turn
 * came back `422 ORDER_AGENT_OUT_OF_SCOPE` -- the copilot could not complete a
 * single conversation. The id is served by `/api/runtime-config` now, and these
 * assert the two halves of that: it is used when present, and nothing is sent
 * when it is not.
 */
describe("the agent the copilot addresses", () => {
  beforeEach(() => {
    mocks.sendTurn.mockResolvedValue(turn());
  });

  it("addresses the agent the runtime configuration names", async () => {
    render(<ReturnCopilotPage />, {
      wrapper: wrapperWith(runtimeConfig({ orderDiscovery: "some-other-agent" })),
    });
    fire(document.body, "Atlas");

    await waitFor(() => {
      expect(mocks.sendTurn).toHaveBeenCalledTimes(1);
    });
    const [input] = mocks.sendTurn.mock.calls[0] as [SendTurnInput];
    // Deliberately not the shipped id: matching `order-discovery-agent` would
    // also pass if the literal had merely been swapped for a newer literal.
    expect(input.agentId).toBe("some-other-agent");
  });

  it("sends nothing when no agent is configured", async () => {
    render(<ReturnCopilotPage />, {
      wrapper: wrapperWith(runtimeConfig({ orderDiscovery: null })),
    });

    expect(screen.getByLabelText("Message the discovery agent")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Message the discovery agent"), {
      target: { value: "Atlas" },
    });
    fireEvent.click(screen.getByLabelText("Send"));

    // A guessed id would spend a real turn to discover what the missing setting
    // already says.
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/order_discovery_agent_id/);
    });
    expect(mocks.sendTurn).not.toHaveBeenCalled();
  });

  it("claims no search is running while the composer is disabled", () => {
    // The disable used to be spelled `isPending`, which is also what draws
    // "Searching order graph..." -- so the unconfigured screen showed a
    // configuration error and a spinner for a search that had never started.
    // Nothing was in flight, and saying otherwise is the fabrication this
    // programme exists to remove.
    render(<ReturnCopilotPage />, {
      wrapper: wrapperWith(runtimeConfig({ orderDiscovery: null })),
    });

    expect(screen.getByLabelText("Message the discovery agent")).toBeDisabled();
    expect(screen.getByLabelText("Send")).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(/order_discovery_agent_id/);
    expect(screen.queryByText(/Searching order graph/)).not.toBeInTheDocument();
  });

  it("sends nothing while the bootstrap payload is still in flight", () => {
    render(<ReturnCopilotPage />, { wrapper: wrapperWith(null) });

    fireEvent.click(screen.getByLabelText("Send"));

    expect(mocks.sendTurn).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/has not loaded yet/);
    expect(screen.queryByText(/Searching order graph/)).not.toBeInTheDocument();
  });

  it("survives a backend that does not serve the agent block at all", () => {
    // An older API answers without `agents`. Reading straight through it would
    // be a TypeError on first render -- a blank screen instead of a named
    // configuration problem.
    render(<ReturnCopilotPage />, { wrapper: wrapperWith(runtimeConfig(undefined)) });

    expect(screen.getByRole("alert")).toHaveTextContent(/order_discovery_agent_id/);
    expect(mocks.sendTurn).not.toHaveBeenCalled();
  });
});

/**
 * Type into the controlled input and submit, the way an associate would.
 *
 * `fireEvent.change` rather than setting `.value` directly: React tracks the
 * last value it wrote and ignores a mutation it did not see, so a raw
 * assignment leaves the component's state on the previous text.
 */
function fire(_container: HTMLElement, text: string): void {
  fireEvent.change(screen.getByLabelText("Message the discovery agent"), {
    target: { value: text },
  });
  fireEvent.click(screen.getByLabelText("Send"));
}

describe("the RMA panel", () => {
  /**
   * A multi-item return can produce two RMAs with different labels going to
   * different places. The panel that preceded this one held one of each, so it
   * had to pick -- and whichever it picked would have sent half the shipment to
   * the wrong dock while looking correct.
   */
  function record(reference: string, label: string, location: string, lines: string[]) {
    const shipmentId = `SHP-${reference}`;
    return returnRecord({
      returnRecordId: `rec-${reference}`,
      returnReference: reference,
      status: "OPEN",
      returnLocation: location,
      shipments: [shipment({ shipmentId, trackingNumber: `1Z-${reference}` })],
      // Attributed to this record's own package, which is what makes "label
      // LBL-1 belongs to RMA-2" unsayable rather than merely unlikely.
      artifacts: [artifact({ artifactId: label, fileName: label, shipmentId })],
      approvedItems: lines.map((line) =>
        approvedItem({ returnItemId: `item-${line}`, orderLineReference: line }),
      ),
    });
  }

  function renderWithCase(returnRecords: ReturnType<typeof record>[]) {
    mocks.readCase.mockResolvedValue(
      caseProjection({
        caseId: "case-1",
        status: "AWAITING_SUPPORT",
        // The real `aec15726…` shape: RMAs on the case while Support still
        // holds it. The panel under test is the progress rail's, which shows
        // every record whatever pane the stage happens to be drawing.
        stage: "AWAITING_SUPPORT",
        conversationId: "disc-0",
        confirmedOrder: confirmedOrder(),
        returnRecords,
      }),
    );
    mocks.sendTurn.mockResolvedValue(turn({ case_id: "case-1" }));

    render(<ReturnCopilotPage />, { wrapper });
    fireEvent.change(screen.getByLabelText("Message the discovery agent"), {
      target: { value: "yes that one" },
    });
    fireEvent.click(screen.getByLabelText("Send"));
  }

  it("shows every RMA, not just the first", async () => {
    renderWithCase([
      record("RMA-1", "LBL-1", "DOCK-3", ["L1", "L2"]),
      record("RMA-2", "LBL-2", "BAY-12", ["L3"]),
    ]);

    expect(await screen.findByText("RMA-1")).toBeInTheDocument();
    expect(screen.getByText("RMA-2")).toBeInTheDocument();
  });

  it("keeps each RMA's label and return location with that RMA", async () => {
    renderWithCase([
      record("RMA-1", "LBL-1", "DOCK-3", ["L1", "L2"]),
      record("RMA-2", "LBL-2", "BAY-12", ["L3"]),
    ]);

    // The assertion that matters is positional: LBL-1 must sit inside the
    // RMA-1 panel, not merely somewhere on the screen.
    const first = (await screen.findByText("RMA-1")).closest("div")?.parentElement;
    expect(first).not.toBeNull();
    expect(first).toHaveTextContent("LBL-1");
    expect(first).toHaveTextContent("DOCK-3");
    expect(first).not.toHaveTextContent("LBL-2");
    expect(first).not.toHaveTextContent("BAY-12");
  });

  it("says which lines each RMA covers", async () => {
    renderWithCase([record("RMA-1", "LBL-1", "DOCK-3", ["L1", "L2"])]);

    expect(await screen.findByText(/Covers L1, L2/)).toBeInTheDocument();
  });

  it("reads nothing until an order has been confirmed", () => {
    mocks.sendTurn.mockResolvedValue(turn());
    render(<ReturnCopilotPage />, { wrapper });

    // No case id on the turn means no case fetch: the copilot no longer infers
    // a return from a candidate list of length one.
    expect(mocks.readCase).not.toHaveBeenCalled();
  });
});

/**
 * Resuming a return, not just a chat.
 *
 * The case id only ever arrived on the turn that confirmed the order, so a
 * conversation reopened from history -- or after a reload -- came back with an
 * empty RMA panel on a return that already had one, and the only way to get it
 * back was to type another message.
 */
describe("resuming a return", () => {
  const OPEN_CASE = {
    caseId: "case-9",
    status: "AWAITING_SUPPORT",
    confirmedOrderReference: "SO-A1",
    channelAConversationId: "disc-old",
    returnRecordCount: 2,
    updatedAt: "2026-08-12T00:00:00Z",
  };

  function withHistory() {
    mocks.listConversations.mockResolvedValue([
      { conversationId: "disc-old", title: "earlier", messageCount: 2, updatedAt: null },
    ]);
    mocks.readTranscript.mockResolvedValue({
      conversationId: "disc-old",
      conversationVersion: 7,
      messages: [{ role: "associate", text: "earlier" }],
    });
  }

  it("reads the case a reopened conversation already raised", async () => {
    withHistory();
    mocks.listCases.mockResolvedValue([OPEN_CASE]);
    render(<ReturnCopilotPage />, { wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("earlier"));

    // Scoped to the conversation, not "list everything and search here": the
    // browser-side join is the defect this whole path replaced.
    await waitFor(() => { expect(mocks.listCases).toHaveBeenCalledWith("disc-old"); });
    await waitFor(() => { expect(mocks.readCase).toHaveBeenCalledWith("case-9"); });
  });

  it("does not invent a case for a conversation that never confirmed one", async () => {
    withHistory();
    mocks.listCases.mockResolvedValue([]);
    render(<ReturnCopilotPage />, { wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("earlier"));

    await waitFor(() => { expect(mocks.listCases).toHaveBeenCalledWith("disc-old"); });
    expect(mocks.readCase).not.toHaveBeenCalled();
  });

  it("lists the associate's open returns by order, not by chat title", async () => {
    withHistory();
    mocks.listCases.mockResolvedValue([OPEN_CASE]);
    render(<ReturnCopilotPage />, { wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));

    // The order reference and the RMA count are what identify outstanding
    // work; "earlier" is what the conversation happened to be called.
    expect(await screen.findByText("SO-A1")).toBeInTheDocument();
    expect(screen.getByText(/2 RMAs/)).toBeInTheDocument();
  });

  it("opens a return through its own conversation", async () => {
    withHistory();
    mocks.listCases.mockResolvedValue([OPEN_CASE]);
    render(<ReturnCopilotPage />, { wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("SO-A1"));

    // Not a new chat about an existing return: the same Channel A thread the
    // case has always pointed at.
    await waitFor(() => { expect(mocks.readTranscript).toHaveBeenCalledWith("disc-old"); });
  });

  it("drops the resumed case when the associate starts a new return", async () => {
    withHistory();
    mocks.listCases.mockResolvedValue([OPEN_CASE]);
    mocks.readCase.mockResolvedValue(
      caseProjection({
        caseId: "case-9",
        status: "AWAITING_SUPPORT",
        stage: "AWAITING_SUPPORT",
        conversationId: "disc-old",
        confirmedOrder: confirmedOrder(),
        returnRecords: [
          returnRecord({ returnRecordId: "rr-1", returnReference: "RMA-RESUMED" }),
        ],
      }),
    );
    render(<ReturnCopilotPage />, { wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("earlier"));
    // The RMA is back without the associate typing anything: that is the whole
    // point of resuming the case alongside the conversation.
    expect(await screen.findByText("RMA-RESUMED")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /New return/ }));

    // And it does not follow them into the next return, for the same reason
    // the conversation id is minted afresh.
    await waitFor(() => { expect(screen.queryByText("RMA-RESUMED")).toBeNull(); });
  });

  /**
   * The page of matches the agent had on screen when the conversation was
   * closed.
   *
   * A past search that never raised a case came back with an empty results pane
   * -- so the associate had to run the search again to see rows the agent had
   * already found, and had quoted in the message above them.
   */
  const SERVED_TURN = turn({
    response: {
      status: "AWAITING_INPUT",
      business_capability: "order-discovery",
      statements: [
        {
          statement_id: "s1",
          statement_type: "GRAPH_FACT",
          text: "Seven customers match.",
          evidence_refs: [
            { query_execution_id: "qe-page", result_path: ["candidates", "0"] },
          ],
        },
      ],
    },
    query_evidence: [
      {
        query_execution_id: "qe-page",
        schema_version: "v2",
        graph_generation_id: "gen-abc12345",
        logical_plan_checksum: "plan-x",
        compiled_query_checksum: "compiled-x",
        result_checksum: "x",
        result: {
          total_found: 7,
          candidates: [
            { data: { customer_id: "600654", account_id: "NASH" } },
            { data: { customer_id: "600318", account_id: "GARDEN" } },
          ],
        },
      },
    ],
  });

  it("puts the matches back when a past search is reopened", async () => {
    withHistory();
    mocks.listCases.mockResolvedValue([]);
    mocks.readTranscript.mockResolvedValue({
      conversationId: "disc-old",
      conversationVersion: 7,
      messages: [{ role: "associate", text: "earlier" }],
      lastResultTurn: SERVED_TURN,
    });
    render(<ReturnCopilotPage />, { wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("earlier"));

    expect(await screen.findByText("NASH")).toBeInTheDocument();
    expect(screen.getByText("GARDEN")).toBeInTheDocument();
    // The total the search reported, not the number of rows it handed over.
    expect(screen.getByText(/of 7 matched/)).toBeInTheDocument();
  });

  it("clears the matches for a conversation that carries none", async () => {
    // Not a regression of the above. Whatever is on screen belongs to whatever
    // was open before, and a resumed conversation showing another one's matches
    // is the defect the outright clear was written to prevent.
    withHistory();
    mocks.listCases.mockResolvedValue([]);
    mocks.readTranscript
      .mockResolvedValueOnce({
        conversationId: "disc-old",
        conversationVersion: 7,
        messages: [{ role: "associate", text: "earlier" }],
        lastResultTurn: SERVED_TURN,
      })
      .mockResolvedValueOnce({
        conversationId: "disc-old",
        conversationVersion: 7,
        messages: [{ role: "associate", text: "earlier" }],
      });
    render(<ReturnCopilotPage />, { wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("earlier"));
    expect(await screen.findByText("NASH")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("earlier"));

    await waitFor(() => { expect(screen.queryByText("NASH")).toBeNull(); });
  });

  it("still shows the confirmed order rather than a restored search", async () => {
    // A resumed *return* is unaffected: its order comes off the case, which
    // takes precedence over any candidate list.
    withHistory();
    mocks.listCases.mockResolvedValue([OPEN_CASE]);
    mocks.readTranscript.mockResolvedValue({
      conversationId: "disc-old",
      conversationVersion: 7,
      messages: [{ role: "associate", text: "earlier" }],
      lastResultTurn: SERVED_TURN,
    });
    mocks.readCase.mockResolvedValue(
      caseProjection({
        caseId: "case-9",
        status: "AWAITING_SUPPORT",
        stage: "AWAITING_SUPPORT",
        conversationId: "disc-old",
        confirmedOrder: confirmedOrder(),
      }),
    );
    render(<ReturnCopilotPage />, { wrapper });

    fireEvent.click(screen.getByRole("button", { name: "Previous returns" }));
    fireEvent.click(await screen.findByText("earlier"));

    await waitFor(() => { expect(mocks.readCase).toHaveBeenCalledWith("case-9"); });
    await waitFor(() => { expect(screen.queryByText("NASH")).toBeNull(); });
  });
});

describe("the return history panel", () => {
  /**
   * The read the case surface cannot serve.
   *
   * `/api/cases` answers "show me this case", addressed by an id the confirming
   * turn handed back and scoped to the caller's own cases. An associate holding
   * a box asks the other question -- has this customer sent things back before,
   * and is this line already on somebody else's RMA -- and the answer includes
   * cases raised in conversations this screen has never seen. These pin that the
   * screen asks the graph for it rather than re-deriving it from the one case it
   * happens to hold.
   */

  function candidateTurn(data: Record<string, unknown>): AgentTurnResult {
    return turn({
      response: {
        status: "RESOLVED",
        business_capability: "order-discovery",
        statements: [{ statement_id: "c", statement_type: "GRAPH_FACT", text: "Found 1." }],
      },
      query_evidence: [
        {
          query_execution_id: "qe-1",
          schema_version: "v2",
          graph_generation_id: "gen-abc12345",
          logical_plan_checksum: "plan-x",
          compiled_query_checksum: "compiled-x",
          result_checksum: "x",
          result: { candidates: [{ data }] },
        },
      ],
    });
  }

  const RESOLVED_CUSTOMER = {
    sales_order_number: "CQ363350",
    account_id: "CHARLOTTE",
    customer_id: "9911",
  };

  it("confirms the order when the chosen candidate carries one", async () => {
    mocks.sendTurn.mockResolvedValue(candidateTurn(RESOLVED_CUSTOMER));
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas");

    fireEvent.click(await screen.findByRole("button", { name: "Select" }));

    await waitFor(() => {
      expect(mocks.sendTurn).toHaveBeenLastCalledWith(
        expect.objectContaining({ message: "Confirm order CQ363350." }),
      );
    });
  });

  it("confirms the customer when the chosen candidate carries no order", async () => {
    /**
     * A name search returns customer rows -- `account_id`, `customer_id`,
     * `customer_name` and nothing else. The handler used to read
     * `sales_order_number`, find nothing and return silently, so Select
     * rendered enabled and was **inert**: no turn, no error, no feedback.
     * Observed live on a search for "Melgon Heating & Cooling".
     *
     * Confirming the customer is the honest first step of a narrowing that has
     * not reached an order yet, and it is what lets the flow run name, then
     * product, then order.
     */
    mocks.sendTurn.mockResolvedValue(
      candidateTurn({
        account_id: "OHVAL",
        customer_id: "471565",
        customer_name: "MELGON HEATING & COOLING",
      }),
    );
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Melgon Heating");

    fireEvent.click(await screen.findByRole("button", { name: "Select" }));

    await waitFor(() => {
      expect(mocks.sendTurn).toHaveBeenLastCalledWith(
        // No `customer_id` in the sentence: the internal ERP customer number is
        // not exposed, and this text lands in a stored, replayable transcript.
        expect.objectContaining({
          message: "Confirm the customer MELGON HEATING & COOLING on account OHVAL.",
        }),
      );
    });
  });

  it("submits nothing when a candidate identifies neither an order nor a customer", async () => {
    // Better a dead button than a meaningless turn on the record: an empty
    // submission would cost a reasoning round trip and say nothing.
    mocks.sendTurn.mockResolvedValue(candidateTurn({ source_updated_at: "2025-10-14T15:03:28Z" }));
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "something vague");

    // Count *after* the search turn has landed, or the baseline races it.
    const select = await screen.findByRole("button", { name: "Select" });
    const before = mocks.sendTurn.mock.calls.length;
    fireEvent.click(select);

    await waitFor(() => {
      expect(mocks.sendTurn.mock.calls.length).toBe(before);
    });
  });

  it("asks about the customer, not only the order, when the candidate names one", async () => {
    // The wider question is the useful one: "has this customer returned before"
    // is not answerable from the single order in front of the associate.
    mocks.sendTurn.mockResolvedValue(candidateTurn(RESOLVED_CUSTOMER));
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas");

    await waitFor(() => {
      expect(mocks.historyByCustomer).toHaveBeenCalledWith("CHARLOTTE", "9911");
    });
    // Both halves of the key or neither: an ERP customer number is unique
    // within a branch account and not across them.
    expect(mocks.historyByOrder).not.toHaveBeenCalled();
  });

  it("falls back to the order when the candidate carries no customer", async () => {
    mocks.sendTurn.mockResolvedValue(
      candidateTurn({ sales_order_number: "CQ363350", account_id: "CHARLOTTE" }),
    );
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas");

    await waitFor(() => {
      expect(mocks.historyByOrder).toHaveBeenCalledWith("CQ363350");
    });
    expect(mocks.historyByCustomer).not.toHaveBeenCalled();
  });

  it("asks nothing while more than one candidate is still in play", async () => {
    // Two candidates have not resolved anybody, and one of the two histories
    // would belong to a stranger.
    mocks.sendTurn.mockResolvedValue(
      turn({
        response: {
          status: "NEEDS_INPUT",
          business_capability: "order-discovery",
          statements: [{ statement_id: "c", statement_type: "GRAPH_FACT", text: "Found 2." }],
        },
        query_evidence: [
          {
            query_execution_id: "qe-1",
            schema_version: "v2",
            graph_generation_id: "gen-abc12345",
            logical_plan_checksum: "plan-x",
            compiled_query_checksum: "compiled-x",
            result_checksum: "x",
            result: {
              candidates: [
                { data: { sales_order_number: "CQ363350", account_id: "A", customer_id: "1" } },
                { data: { sales_order_number: "CQ363351", account_id: "A", customer_id: "2" } },
              ],
            },
          },
        ],
      }),
    );
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas");

    expect(await screen.findByText("Candidates (2)")).toBeInTheDocument();
    expect(mocks.historyByCustomer).not.toHaveBeenCalled();
    expect(mocks.historyByOrder).not.toHaveBeenCalled();
  });

  it("says nothing came back before rather than rendering nothing", async () => {
    // A panel that disappears on an empty answer is indistinguishable from one
    // that failed to load, and "no, they have never returned anything" is a
    // result the associate is entitled to be told.
    mocks.sendTurn.mockResolvedValue(candidateTurn(RESOLVED_CUSTOMER));
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas");

    expect(await screen.findByText(/No earlier returns/)).toBeInTheDocument();
  });

  it("shows the earlier RMA, the lines it covers, and where the parcel is staged", async () => {
    mocks.historyByCustomer.mockResolvedValue({
      orderReference: null,
      accountId: "CHARLOTTE",
      customerId: "9911",
      cases: [
        {
          caseId: "case-earlier",
          status: "AWAITING_SUPPORT",
          confirmedOrderReference: "CW111111",
          createdAt: "2026-07-02T10:15:00Z",
          returnRecords: [
            {
              returnRecordId: "rec-1",
              returnReference: "RMA-1001",
              status: "ISSUED",
              returnLocation: "DC-7",
              trackingReference: null,
              items: [
                {
                  returnItemId: "item-1",
                  orderLineReference: "L1",
                  productReference: null,
                  quantity: 1,
                  reason: null,
                },
              ],
            },
          ],
          unassignedItems: [],
          placements: [
            {
              handlingUnitId: "sess-1:HU:1",
              handlingUnitType: "PACKAGE",
              physicalStatus: "WAREHOUSE_STAGED",
              warehouseId: "1969",
              bayId: "BAY-04",
              trackingNumber: null,
            },
          ],
        },
      ],
    } satisfies ReturnHistory);
    mocks.sendTurn.mockResolvedValue(candidateTurn(RESOLVED_CUSTOMER));
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas");

    expect(await screen.findByText("RMA-1001")).toBeInTheDocument();
    expect(screen.getByText("CW111111")).toBeInTheDocument();
    // Which lines, not how many: one RMA covers many items, and a count does
    // not tell the associate whether the line in their hand is already on one.
    expect(screen.getByText("Covers L1")).toBeInTheDocument();
    // The bay, which is the only thing here that answers "where is it now".
    expect(screen.getByText(/BAY-04/)).toBeInTheDocument();
  });

  it("reports a failed history read instead of implying there is none", async () => {
    mocks.historyByCustomer.mockRejectedValue(new Error("graph unavailable"));
    mocks.sendTurn.mockResolvedValue(candidateTurn(RESOLVED_CUSTOMER));
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas");

    expect(await screen.findByText(/could not be read/)).toBeInTheDocument();
    expect(screen.queryByText(/No earlier returns/)).toBeNull();
  });
});
