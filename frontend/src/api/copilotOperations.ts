import { apiClient } from "./client";
import type { AssociateConversation } from "../contracts/associateReturns";

function requireData<T>(value: T | null): T {
  if (value === null) throw new Error("The API returned no data.");
  return value;
}

function jsonInit(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export async function listCopilotOperationSessions(
  signal?: AbortSignal,
): Promise<readonly AssociateConversation[]> {
  return requireData((await apiClient<AssociateConversation[]>(
    "/data-console/v1/copilot-operations/sessions",
    { signal },
  )).data);
}

export async function startCopilotOperationSession(payload: {
  message: string;
}): Promise<AssociateConversation> {
  return requireData((await apiClient<AssociateConversation>(
    "/data-console/v1/copilot-operations/sessions",
    jsonInit(payload),
  )).data);
}

export async function continueCopilotOperationSession(payload: {
  conversationId: string;
  message: string;
  expectedVersion: number;
}): Promise<AssociateConversation> {
  return requireData((await apiClient<AssociateConversation>(
    `/data-console/v1/copilot-operations/sessions/${encodeURIComponent(payload.conversationId)}/messages`,
    jsonInit({
      message: payload.message,
      expectedVersion: payload.expectedVersion,
    }),
  )).data);
}
