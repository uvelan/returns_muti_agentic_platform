import createClient from "openapi-fetch";
import type { paths } from "./generated/return-platform";
import { APIError } from "./client";

export const api = createClient<paths>({
  baseUrl: "",
});

api.use({
  onRequest: ({ request }) => {
    if (!request.headers.has("X-Correlation-ID")) {
      request.headers.set("X-Correlation-ID", crypto.randomUUID());
    }
    return request;
  },
  onResponse: async ({ response }) => {
    if (!response.ok) {
      const cloned = response.clone();
      let payload: unknown = {};
      try {
        payload = await cloned.json();
      } catch {
        // Ignore JSON parse errors
      }
      const correlationId = response.headers.get("X-Correlation-ID") ?? undefined;
      const message =
        typeof payload === "object" && payload !== null && "meta" in payload
          ? (payload as { meta?: { warnings?: { message?: string }[] } }).meta?.warnings?.[0]?.message
          : `The API request failed with status ${String(response.status)}.`;
      throw new APIError(message ?? `The API request failed with status ${String(response.status)}.`, response.status, correlationId);
    }
    return response;
  }
});
