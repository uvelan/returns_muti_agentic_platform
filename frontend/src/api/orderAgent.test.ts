import { afterEach, describe, expect, it, vi } from "vitest";

import { APIError } from "./client";
import { ORDER_AGENT_ID, processOrderAgentTurn } from "./orderAgent";
import type {
  OrderAgentTurnRequest,
  OrderAgentTurnResult,
} from "../contracts/orderAgent";

const request: OrderAgentTurnRequest = {
  conversation_id: "conversation-1",
  expected_conversation_version: 0,
  client_turn_id: "11111111-1111-4111-8111-111111111111",
  idempotency_key: "22222222-2222-4222-8222-222222222222",
  message_id: "33333333-3333-4333-8333-333333333333",
  message: "Find order SO-00010001",
  agent_id: ORDER_AGENT_ID,
};

const result: OrderAgentTurnResult = {
  conversation_id: "conversation-1",
  conversation_version: 1,
  client_turn_id: request.client_turn_id,
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
        result_path: ["orders", "0", "orderNumber"],
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
    result: { orders: [{ orderNumber: "SO-00010001" }] },
    result_checksum: "result",
  }],
  model_provider: "GOOGLE",
  model_name: "gemini",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("processOrderAgentTurn", () => {
  it("posts the versioned turn to the dynamic endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify(result),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    await expect(processOrderAgentTurn(request)).resolves.toEqual(result);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v2/order-agent/conversations/conversation-1/turns",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(request),
      }),
    );
  });

  it("surfaces the platform error envelope", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        data: null,
        meta: { warnings: [{ message: "Order Agent unavailable." }] },
      }),
      { status: 503, headers: { "Content-Type": "application/json" } },
    )));

    const error = await processOrderAgentTurn(request).catch(
      (caught: unknown) => caught,
    );
    expect(error).toBeInstanceOf(APIError);
    expect(error).toMatchObject({
      message: "Order Agent unavailable.",
      status: 503,
    });
  });
});
