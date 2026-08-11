import { apiClient } from "./client";

/**
 * The Order Discovery agent's one endpoint.
 *
 * `POST /api/v2/order-agent/conversations/{id}/turns` runs a whole reasoning
 * turn: the backend decides, searches the graph, may pause on a clarifying
 * question, and returns a structured, evidence-cited response. There is no
 * streaming and no separate "get conversation" read -- a turn is the unit.
 *
 * Versioned on purpose. This is the one `/api/v2` path the shell calls, and it
 * is excluded from `noVersionedPaths.test.ts` for that reason: the agent has no
 * canonical `/api` route yet, and pretending otherwise by proxying it would
 * hide the gap rather than close it.
 */

export type StatementType =
  | "GRAPH_FACT"
  | "USER_PROVIDED_FACT"
  | "REASONED_SUGGESTION"
  | "CLARIFICATION_QUESTION";

export type ResponseStatement = {
  statement_id: string;
  statement_type: StatementType;
  text: string;
  evidence_refs?: string[];
  source_message_id?: string | null;
};

export type QueryEvidence = {
  query_execution_id: string;
  schema_version: string;
  graph_generation_id: string;
  result: unknown;
  result_checksum: string;
};

export type StructuredAgentResponse = {
  status: string;
  business_capability: string;
  statements: ResponseStatement[];
  requested_input?: string | null;
  suggestions?: string[];
};

export type AgentTurnResult = {
  conversation_id: string;
  conversation_version: number;
  client_turn_id: string;
  graph_generation_id: string;
  response: StructuredAgentResponse;
  /** Set when the graph suspended on a clarifying question instead of finishing. */
  pending_clarification_thread_id?: string | null;
  query_evidence: QueryEvidence[];
  model_provider: string;
  model_name: string;
};

export type SendTurnInput = {
  conversationId: string;
  /** Optimistic concurrency. The backend rejects a turn built on a stale view. */
  expectedConversationVersion: number;
  message: string;
  agentId: string;
};

export const orderAgentApi = {
  async sendTurn(input: SendTurnInput): Promise<AgentTurnResult> {
    // One id per submission, not per render: retrying *this* send is a no-op,
    // while a deliberate second message is a distinct turn.
    const turnId = `ui-${input.conversationId}-${String(Date.now())}`;
    const response = await apiClient<AgentTurnResult>(
      `/api/v2/order-agent/conversations/${encodeURIComponent(input.conversationId)}/turns`,
      {
        method: "POST",
        // `createHeaders` sets Accept but not Content-Type, so every JSON POST
        // in this codebase declares its own -- omitting it makes FastAPI reject
        // the body as a missing field rather than as a bad content type.
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: input.conversationId,
          expected_conversation_version: input.expectedConversationVersion,
          client_turn_id: turnId,
          idempotency_key: turnId,
          message_id: turnId,
          message: input.message,
          agent_id: input.agentId,
        }),
      },
    );
    if (!response.data) {
      throw new Error("The agent returned no result.");
    }
    return response.data;
  },
};

/** A stable conversation id for a fresh discovery session. */
export function newConversationId(): string {
  return `disc-${crypto.randomUUID()}`;
}
