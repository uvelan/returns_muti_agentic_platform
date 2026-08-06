export type OrderAgentStatementType =
  | "GRAPH_FACT"
  | "USER_PROVIDED_FACT"
  | "REASONED_SUGGESTION"
  | "CLARIFICATION_QUESTION";

export type OrderAgentEvidenceReference = {
  readonly query_execution_id: string;
  readonly result_path: readonly string[];
  readonly expected_value?: unknown;
};

export type OrderAgentStatement = {
  readonly statement_id: string;
  readonly statement_type: OrderAgentStatementType;
  readonly text: string;
  readonly evidence_refs: readonly OrderAgentEvidenceReference[];
  readonly source_message_id: string | null;
};

export type StructuredOrderAgentResponse = {
  readonly status: string;
  readonly business_capability: string;
  readonly statements: readonly OrderAgentStatement[];
  readonly suggestions: readonly string[];
  readonly requested_input: string | null;
};

export type OrderSearchCandidate = {
  readonly data: Readonly<Record<string, unknown>>;
  readonly score: number;
  readonly matches: readonly string[];
};

export type OrderSearchResult = {
  readonly intent: Readonly<Record<string, unknown>>;
  readonly candidates: readonly OrderSearchCandidate[];
  readonly total_found: number;
  readonly unsupported_signals: readonly string[];
};

// Shape of a direct GRAPH_QUERY result (e.g. a traversal to order_line for
// product detail) — distinct from OrderSearchResult, which only ORDER_SEARCH
// produces.
export type GraphQueryRowResult = {
  readonly rows: readonly Readonly<Record<string, unknown>>[];
  readonly count: number;
};

export type OrderAgentQueryEvidence = {
  readonly query_execution_id: string;
  readonly schema_version: string;
  readonly graph_generation_id: string;
  readonly logical_plan_checksum: string;
  readonly compiled_query_checksum: string;
  readonly result: unknown;
  readonly result_checksum: string;
};

export type OrderAgentTurnRequest = {
  readonly conversation_id: string;
  readonly expected_conversation_version: number;
  readonly client_turn_id: string;
  readonly idempotency_key: string;
  readonly message_id: string;
  readonly message: string;
  readonly agent_id: "order-discovery-agent";
};

export type OrderAgentTurnResult = {
  readonly conversation_id: string;
  readonly conversation_version: number;
  readonly client_turn_id: string;
  readonly graph_generation_id: string;
  readonly response: StructuredOrderAgentResponse;
  readonly query_evidence: readonly OrderAgentQueryEvidence[];
  readonly model_provider: string;
  readonly model_name: string;
};
