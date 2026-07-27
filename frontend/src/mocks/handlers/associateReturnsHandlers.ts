import { http, HttpResponse, delay } from "msw";
import { FIXTURE_ASSOCIATE_CONVERSATIONS, FIXTURE_CONFIRMED_CONVERSATION, FIXTURE_SUBMIT_RESULT } from "../../fixtures/associateReturns";

function wrapResponse(data: unknown) {
  return JSON.stringify({
    data,
    page: { next_cursor: null, has_more: false, page_size: 10 },
    meta: {
      schema_version: "1.0",
      request_id: "req-fixture-associate",
      generated_at: new Date().toISOString(),
      freshness: "LIVE",
      partial: false,
      warnings: [],
    },
  });
}

export const associateReturnsHandlers = [
  http.get("/api/v1/associate-returns/conversations", async () => {
    await delay(200);
    return new HttpResponse(wrapResponse(FIXTURE_ASSOCIATE_CONVERSATIONS), {
      headers: { "Content-Type": "application/json" },
    });
  }),

  http.post("/api/v1/associate-returns/conversations", async () => {
    await delay(200);
    return new HttpResponse(wrapResponse(FIXTURE_ASSOCIATE_CONVERSATIONS[0]), {
      headers: { "Content-Type": "application/json" },
    });
  }),

  http.get("/api/v1/associate-returns/conversations/:id", async () => {
    await delay(200);
    return new HttpResponse(wrapResponse(FIXTURE_ASSOCIATE_CONVERSATIONS[0]), {
      headers: { "Content-Type": "application/json" },
    });
  }),

  http.post("/api/v1/associate-returns/chat", async () => {
    await delay(300);
    return new HttpResponse(wrapResponse(FIXTURE_ASSOCIATE_CONVERSATIONS[0]), {
      headers: { "Content-Type": "application/json" },
    });
  }),

  http.post("/api/v1/associate-returns/conversations/:id/chat", async () => {
    await delay(300);
    return new HttpResponse(wrapResponse(FIXTURE_ASSOCIATE_CONVERSATIONS[0]), {
      headers: { "Content-Type": "application/json" },
    });
  }),

  http.post("/api/v1/associate-returns/conversations/:id/confirm", async () => {
    await delay(200);
    return new HttpResponse(wrapResponse(FIXTURE_CONFIRMED_CONVERSATION), {
      headers: { "Content-Type": "application/json" },
    });
  }),

  http.post("/api/v1/associate-returns/conversations/:id/details", async () => {
    await delay(300);
    return new HttpResponse(wrapResponse(FIXTURE_SUBMIT_RESULT), {
      headers: { "Content-Type": "application/json" },
    });
  }),

  http.get("/api/v1/returns/:sessionId", async () => {
    await delay(100);
    return new HttpResponse(wrapResponse(FIXTURE_SUBMIT_RESULT.returnSession), {
      headers: { "Content-Type": "application/json" },
    });
  }),

  http.get("/api/v1/returns/:sessionId/events", async () => {
    await delay(100);
    const mockEvents = [
      {
        id: "ev-1",
        streamId: "ret-sess-10001",
        sequence: 1,
        eventType: "RETURN_CREATED",
        actorType: "SYSTEM",
        actorId: "copilot-agent",
        payload: { message: "Return session initiated by Associate Copilot" },
        occurredAt: new Date().toISOString(),
        publishedAt: new Date().toISOString(),
      },
      {
        id: "ev-2",
        streamId: "ret-sess-10001",
        sequence: 2,
        eventType: "DISCOVERY_CONFIRMED",
        actorType: "ASSOCIATE",
        actorId: "associate-reference",
        payload: { message: "Order evidence locked and verified" },
        occurredAt: new Date().toISOString(),
        publishedAt: new Date().toISOString(),
      },
      {
        id: "ev-3",
        streamId: "ret-sess-10001",
        sequence: 3,
        eventType: "WORKFLOW_COMPLETED",
        actorType: "SYSTEM",
        actorId: "return-engine",
        payload: { message: "Return ORD-10001 completed successfully" },
        occurredAt: new Date().toISOString(),
        publishedAt: new Date().toISOString(),
      },
    ];
    return new HttpResponse(wrapResponse(mockEvents), {
      headers: { "Content-Type": "application/json" },
    });
  }),
];
