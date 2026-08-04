import { APIError } from "./client";
import type {
  OrderAgentQueryEvidence,
  OrderAgentStatement,
  OrderAgentStatementType,
  OrderAgentTurnRequest,
  OrderAgentTurnResult,
  StructuredOrderAgentResponse,
} from "../contracts/orderAgent";

export const ORDER_AGENT_BASE = "/api/v2/order-agent";
export const ORDER_AGENT_ID = "order-discovery-agent" as const;

const statementTypes = new Set<OrderAgentStatementType>([
  "GRAPH_FACT",
  "USER_PROVIDED_FACT",
  "REASONED_SUGGESTION",
  "CLARIFICATION_QUESTION",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is readonly string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isStatement(value: unknown): value is OrderAgentStatement {
  if (!isRecord(value) || !Array.isArray(value.evidence_refs)) return false;
  return typeof value.statement_id === "string"
    && typeof value.statement_type === "string"
    && statementTypes.has(value.statement_type as OrderAgentStatementType)
    && typeof value.text === "string"
    && value.evidence_refs.every((item) => (
      isRecord(item)
      && typeof item.query_execution_id === "string"
      && isStringArray(item.result_path)
    ))
    && (
      value.source_message_id === null
      || typeof value.source_message_id === "string"
    );
}

function isStructuredResponse(
  value: unknown,
): value is StructuredOrderAgentResponse {
  return isRecord(value)
    && typeof value.status === "string"
    && typeof value.business_capability === "string"
    && Array.isArray(value.statements)
    && value.statements.every(isStatement)
    && isStringArray(value.suggestions)
    && (
      value.requested_input === null
      || typeof value.requested_input === "string"
    );
}

function isEvidence(value: unknown): value is OrderAgentQueryEvidence {
  return isRecord(value)
    && typeof value.query_execution_id === "string"
    && typeof value.schema_version === "string"
    && typeof value.graph_generation_id === "string"
    && typeof value.logical_plan_checksum === "string"
    && typeof value.compiled_query_checksum === "string"
    && typeof value.result_checksum === "string";
}

function isTurnResult(value: unknown): value is OrderAgentTurnResult {
  return isRecord(value)
    && typeof value.conversation_id === "string"
    && typeof value.conversation_version === "number"
    && Number.isInteger(value.conversation_version)
    && typeof value.client_turn_id === "string"
    && typeof value.graph_generation_id === "string"
    && isStructuredResponse(value.response)
    && Array.isArray(value.query_evidence)
    && value.query_evidence.every(isEvidence)
    && typeof value.model_provider === "string"
    && typeof value.model_name === "string";
}

function errorMessage(payload: unknown): string | undefined {
  if (!isRecord(payload)) return undefined;
  if (typeof payload.detail === "string") return payload.detail;
  if (isRecord(payload.detail) && typeof payload.detail.message === "string") {
    return payload.detail.message;
  }
  if (!isRecord(payload.meta) || !Array.isArray(payload.meta.warnings)) {
    return undefined;
  }
  const warning: unknown = payload.meta.warnings.at(0);
  return isRecord(warning) && typeof warning.message === "string"
    ? warning.message
    : undefined;
}

async function readPayload(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text.trim()) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch (error) {
    throw new APIError(
      "The Order Agent returned malformed JSON.",
      response.status,
      response.headers.get("X-Correlation-ID") ?? undefined,
      { cause: error },
    );
  }
}

export async function processOrderAgentTurn(
  request: OrderAgentTurnRequest,
  signal?: AbortSignal,
): Promise<OrderAgentTurnResult> {
  let response: Response;
  try {
    response = await fetch(
      `${ORDER_AGENT_BASE}/conversations/${encodeURIComponent(
        request.conversation_id,
      )}/turns`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Correlation-ID": request.client_turn_id,
        },
        body: JSON.stringify(request),
        signal,
      },
    );
  } catch (error) {
    throw new APIError(
      "Unable to reach the Order Discovery Agent.",
      0,
      undefined,
      { cause: error },
    );
  }

  const payload = await readPayload(response);
  const correlationId =
    response.headers.get("X-Correlation-ID") ?? undefined;

  if (!response.ok) {
    throw new APIError(
      errorMessage(payload)
        ?? `Order Agent request failed with status ${String(response.status)}.`,
      response.status,
      correlationId,
    );
  }
  if (!isTurnResult(payload)) {
    throw new APIError(
      "The Order Agent returned an invalid response contract.",
      response.status,
      correlationId,
    );
  }
  return payload;
}
