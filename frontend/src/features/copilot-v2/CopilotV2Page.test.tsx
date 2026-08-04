import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import type { OrderAgentTurnResult } from "../../contracts/orderAgent";
import { CopilotV2Page } from "./CopilotV2Page";

const mocks = vi.hoisted(() => ({
  processOrderAgentTurn: vi.fn(),
}));

vi.mock("../../api/orderAgent", () => ({
  ORDER_AGENT_ID: "order-discovery-agent",
  processOrderAgentTurn: mocks.processOrderAgentTurn,
}));

const result: OrderAgentTurnResult = {
  conversation_id: "conversation-1",
  conversation_version: 1,
  client_turn_id: "turn-1",
  graph_generation_id: "graph-1",
  response: {
    status: "DISCOVERY_READY",
    business_capability: "ORDER_DISCOVERY",
    statements: [{
      statement_id: "statement-1",
      statement_type: "GRAPH_FACT",
      text: "Order SO-00010001 was found.",
      evidence_refs: [{
        query_execution_id: "query-1",
        result_path: ["orders", "0"],
      }],
      source_message_id: null,
    }],
    suggestions: ["Show order lines"],
    requested_input: null,
  },
  query_evidence: [{
    query_execution_id: "query-1",
    schema_version: "1.0",
    graph_generation_id: "graph-1",
    logical_plan_checksum: "logical",
    compiled_query_checksum: "compiled",
    result: { orderNumber: "SO-00010001" },
    result_checksum: "result",
  }],
  model_provider: "GOOGLE",
  model_name: "gemini",
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <CopilotV2Page />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mocks.processOrderAgentTurn.mockReset();
});

describe("CopilotV2Page", () => {
  it("does not invoke AI when the page opens", () => {
    renderPage();
    expect(
      screen.getByRole("heading", { name: "Returns Assistant" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: /Order SO-00010001 arrived completely scratched/,
      }),
    ).toBeInTheDocument();
    expect(mocks.processOrderAgentTurn).not.toHaveBeenCalled();
  });

  it("submits directly to the dynamic Order Agent", async () => {
    mocks.processOrderAgentTurn.mockResolvedValue(result);
    renderPage();

    fireEvent.change(
      screen.getByLabelText("Ask Copilot about returns"),
      { target: { value: "Find order SO-00010001" } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Send message" }),
    );

    await waitFor(() => {
      expect(mocks.processOrderAgentTurn).toHaveBeenCalledTimes(1);
    });
    expect(mocks.processOrderAgentTurn).toHaveBeenCalledWith(
      expect.objectContaining({
        expected_conversation_version: 0,
        message: "Find order SO-00010001",
        agent_id: "order-discovery-agent",
      }),
    );
    expect(
      await screen.findByText("Order SO-00010001 was found."),
    ).toBeInTheDocument();
  });

  it("opens and closes the mobile context drawer", () => {
    renderPage();
    fireEvent.click(
      screen.getByRole("button", { name: "Open context" }),
    );
    expect(screen.getByText("Order context")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Close context" }),
    );
    expect(screen.queryByText("Order context")).not.toBeInTheDocument();
  });
});
