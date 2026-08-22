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

/**
 * One citation: a path into a named query's result, and the value read there.
 *
 * Not a bare query id. `HallucinationGuard` resolves `result_path` against the
 * result of `query_execution_id` and compares what it finds to
 * `expected_value`, so a reference that named only the query would name a
 * search without naming a fact -- uncheckable by construction. This type was
 * `string[]` here for long enough that a mock was written to match it; the
 * backend model has always been this shape.
 */
export type EvidenceReference = {
  query_execution_id: string;
  /** Segments, not a dotted string -- array indices are their own segment. */
  result_path: string[];
  /** Absent for a citation that points at a subtree rather than a scalar. */
  expected_value?: unknown;
};

export type ResponseStatement = {
  statement_id: string;
  statement_type: StatementType;
  text: string;
  evidence_refs?: EvidenceReference[];
  source_message_id?: string | null;
};

export type QueryEvidence = {
  query_execution_id: string;
  schema_version: string;
  graph_generation_id: string;
  /**
   * All three checksums are required by the contract. The two plan checksums
   * were missing from this type entirely, which is why fixtures could omit
   * them and still type-check.
   */
  logical_plan_checksum: string;
  compiled_query_checksum: string;
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

/**
 * One thing the conversation has established, as the model reported it.
 *
 * **This is the honest source of "extracted facts", and it is not the
 * statements.** A `ResponseStatement` is prose the agent said; a captured fact
 * is a named value the model pulled out of the associate's own sentence,
 * validated against `clarification_policy.fields` and merged across turns. The
 * copilot's facts panel briefly rendered statements instead and showed the
 * agent narrating its own reasoning -- "Line 1 has no product recorded against
 * it" -- under a heading promising extracted fields.
 *
 * `name` is the configured field name (`product_sku`, `return_reason`, …) and
 * `label` its associate-facing wording. `status` is the re-ask rule's verdict:
 * anything other than `USABLE` is a fact the conversation still owes the
 * associate a question about, so it must not be rendered as settled.
 */
export type CapturedFact = {
  name: string;
  value?: unknown;
  /** `USABLE` | `CONFLICTING` | `INVALID` | `AMBIGUOUS` | `STALE` | `CONFIRMATION_REQUIRED`. */
  status: string;
  label?: string;
  /** `STATED` or `DERIVED`. Never `OBSERVED` -- no source system said this. */
  acquisition?: string;
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
  /**
   * Everything this conversation has established, not only this turn's
   * additions. Optional because a turn committed before the backend carried
   * the field replays without it.
   */
  captured_facts?: CapturedFact[];
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
  /**
   * The most recent turn in this conversation that produced results.
   *
   * A whole turn rather than the rows, because which of a turn's several
   * searches it was speaking about is decided by the citations its own
   * statements carry -- so a resumed screen rebuilds the table with
   * `turnCandidates`, exactly as a live turn does.
   *
   * Absent or null when the conversation never searched.
   */
  lastResultTurn?: AgentTurnResult | null;
};

export type SendTurnInput = {
  conversationId: string;
  /** Optimistic concurrency. The backend rejects a turn built on a stale view. */
  expectedConversationVersion: number;
  message: string;
  agentId: string;
  /**
   * Aborts the request, and with it the wait.
   *
   * There was no signal anywhere in this module or in `client.ts`, so a turn
   * held the connection until the server answered -- up to roughly fourteen
   * minutes when every configured route timed out in series, with no way for an
   * associate on a call to stop it.
   *
   * Aborting is a client-side withdrawal: the server keeps working on the turn
   * it accepted. That is the honest scope of this control, and the screen says
   * so rather than implying the work was undone.
   */
  signal?: AbortSignal;
};

export const orderAgentApi = {
  async sendTurn(input: SendTurnInput): Promise<AgentTurnResult> {
    // One id per submission, not per render: retrying *this* send is a no-op,
    // while a deliberate second message is a distinct turn.
    //
    // Random rather than the clock. This value is the `idempotency_key`, and it
    // was `ui-${conversationId}-${Date.now()}` -- millisecond resolution. Two
    // tabs open on one conversation submitting in the same millisecond mint the
    // same key, and the server is *correct* to answer the second with the
    // first's result: the second associate's message is dropped and they read a
    // reply to a question they did not ask. `api/support.ts` already mints its
    // idempotency key this way.
    const turnId = `ui-${input.conversationId}-${crypto.randomUUID()}`;
    const response = await apiClient<AgentTurnResult>(
      `/api/v2/order-agent/conversations/${encodeURIComponent(input.conversationId)}/turns`,
      {
        method: "POST",
        ...(input.signal ? { signal: input.signal } : {}),
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
