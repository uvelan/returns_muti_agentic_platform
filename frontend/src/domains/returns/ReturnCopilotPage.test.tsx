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

import type * as ActualModuleNamespace from "../../api/orderAgent";
import type { AgentTurnResult, SendTurnInput } from "../../api/orderAgent";
import { ReturnCopilotPage } from "./ReturnCopilotPage";

type ActualModule = typeof ActualModuleNamespace;

const mocks = vi.hoisted(() => ({ sendTurn: vi.fn(), can: vi.fn() }));

vi.mock("../../api/orderAgent", async (importOriginal) => ({
  ...(await importOriginal<ActualModule>()),
  orderAgentApi: { sendTurn: mocks.sendTurn },
  newConversationId: () => "disc-test",
}));

vi.mock("../../hooks/capabilityContext", () => ({
  useCapabilities: () => ({ can: mocks.can, principal: { subject: "tester" } }),
}));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function turn(overrides: Partial<AgentTurnResult> = {}): AgentTurnResult {
  return {
    conversation_id: "disc-test",
    conversation_version: 1,
    client_turn_id: "t-1",
    graph_generation_id: "gen-abc12345",
    model_provider: "MOCK",
    model_name: "scripted",
    query_evidence: [],
    response: { status: "OK", business_capability: "order_discovery", statements: [] },
    ...overrides,
  };
}

beforeEach(() => {
  mocks.can.mockReturnValue(true);
  mocks.sendTurn.mockReset();
});

describe("the discovery copilot", () => {
  it("opens on the chat, not on a queue", () => {
    // The screen that used to live here listed return sessions. Asserting the
    // prompt is what stops the operations screen drifting back in.
    render(<ReturnCopilotPage />, { wrapper });
    expect(screen.getByLabelText("Message the discovery agent")).toBeInTheDocument();
    expect(screen.getByText(/I can help you find an order/)).toBeInTheDocument();
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

  it("labels each statement by the kind of claim it is", async () => {
    // A GRAPH_FACT is traceable to evidence and a REASONED_SUGGESTION is the
    // model's inference. Rendering them identically hides how much to trust a
    // line, which is the whole point of the statement contract.
    mocks.sendTurn.mockResolvedValue(
      turn({
        response: {
          status: "NEEDS_INPUT",
          business_capability: "order_discovery",
          statements: [
            { statement_id: "a", statement_type: "USER_PROVIDED_FACT", text: "Atlas Mechanical" },
            { statement_id: "b", statement_type: "CLARIFICATION_QUESTION", text: "Which branch?" },
          ],
        },
      }),
    );
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas Mechanical");

    expect(await screen.findByText("USER PROVIDED FACT")).toBeInTheDocument();
    expect(screen.getByText("CLARIFICATION QUESTION")).toBeInTheDocument();
  });

  it("does not advance past identification while the agent is still asking", async () => {
    mocks.sendTurn.mockResolvedValue(
      turn({
        pending_clarification_thread_id: "thread-1",
        response: {
          status: "NEEDS_INPUT",
          business_capability: "order_discovery",
          statements: [
            { statement_id: "b", statement_type: "CLARIFICATION_QUESTION", text: "Which branch?" },
          ],
        },
      }),
    );
    const { container } = render(<ReturnCopilotPage />, { wrapper });
    fire(container, "Atlas");

    await screen.findByText("CLARIFICATION QUESTION");
    expect(screen.getByText(/has not matched an order yet/)).toBeInTheDocument();
  });

  it("lists candidates once a search produced evidence", async () => {
    mocks.sendTurn.mockResolvedValue(
      turn({
        response: {
          status: "RESOLVED",
          business_capability: "order_discovery",
          statements: [{ statement_id: "c", statement_type: "GRAPH_FACT", text: "Found 1." }],
        },
        query_evidence: [
          {
            query_execution_id: "qe-1",
            schema_version: "v2",
            graph_generation_id: "gen-abc12345",
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
