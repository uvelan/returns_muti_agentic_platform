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
  /**
   * Set once this conversation has confirmed an order and a case exists.
   *
   * The handle everything downstream of discovery hangs off, and the reason the
   * copilot no longer has to infer "an order was found" from a candidate list
   * of length one.
   */
  case_id?: string | null;
  query_evidence: QueryEvidence[];
  model_provider: string;
  model_name: string;
  /**
   * What "now" meant while the turn reasoned, and in whose calendar.
   *
   * Returned so a turn that asked about "last week" can be explained afterwards
   * without guessing when it was asked. Optional because turns committed before
   * the backend recorded it read back without these fields.
   */
  as_of?: string | null;
  session_timezone?: string | null;
};

/**
 * The associate's IANA zone, as the browser reports it.
 *
 * Sent with every turn rather than stored once: a laptop that travels changes
 * zone between conversations, and a stale stored value silently shifts every
 * "yesterday" the agent resolves. Wrapped because `Intl` can throw in a
 * sufficiently stripped runtime, and an unavailable zone is a reason for the
 * backend to fall back to UTC, not a reason to fail the send.
 */
function sessionTimezone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

/** One row of the history list. Summary only -- turn bodies are not fetched. */
export type ConversationSummary = {
  conversationId: string;
  /** The associate's opening message: what they will recognise it by. */
  title: string;
  messageCount: number;
  updatedAt: string | null;
};

export type ConversationTranscript = {
  conversationId: string;
  conversationVersion: number;
  messages: { role: "associate" | "agent"; text: string }[];
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
          session_timezone: sessionTimezone(),
        }),
      },
    );
    if (!response.data) {
      throw new Error("The agent returned no result.");
    }
    return response.data;
  },

  /** Recent conversations, newest first. */
  async listConversations(): Promise<ConversationSummary[]> {
    const response = await apiClient<ConversationSummary[]>("/api/v2/order-agent/conversations");
    return response.data ?? [];
  },

  /** What was said in one conversation, so reopening it is not a blank pane. */
  async readTranscript(conversationId: string): Promise<ConversationTranscript> {
    const response = await apiClient<ConversationTranscript>(
      `/api/v2/order-agent/conversations/${encodeURIComponent(conversationId)}/transcript`,
    );
    if (!response.data) {
      throw new Error("The conversation could not be read.");
    }
    return response.data;
  },
};

/** A stable conversation id for a fresh discovery session. */
export function newConversationId(): string {
  return `disc-${crypto.randomUUID()}`;
}
