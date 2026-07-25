import { http, HttpResponse, delay } from "msw";
import { FIXTURE_SOURCES, FIXTURE_SOURCE_DETAILS } from "../../fixtures/sources";

export const sourcesHandlers = [
  http.get("/data-console/v1/sources", async () => {
    await delay(300);
    return new HttpResponse(JSON.stringify({
      data: FIXTURE_SOURCES,
      page: { next_cursor: null, has_more: false, page_size: 10 },
      meta: {
        schema_version: "1.0",
        request_id: "req-fixture-sources",
        generated_at: new Date().toISOString(),
        freshness: "LIVE",
        partial: false,
        warnings: []
      }
    }), { headers: { 'Content-Type': 'application/json' } });
  }),

  http.get("/data-console/v1/sources/:sourceId", async ({ params }) => {
    await delay(300);
    const sourceId = String(params.sourceId);
    if (!Object.hasOwn(FIXTURE_SOURCE_DETAILS, sourceId)) {
      return new HttpResponse(JSON.stringify({ error: "Not Found" }), { status: 404 });
    }
    const source = FIXTURE_SOURCE_DETAILS[sourceId];
    return new HttpResponse(JSON.stringify({
      data: source,
      page: { next_cursor: null, has_more: false, page_size: 10 },
      meta: {
        schema_version: "1.0",
        request_id: `req-fixture-source-${sourceId}`,
        generated_at: new Date().toISOString(),
        freshness: "LIVE",
        partial: false,
        warnings: []
      }
    }), { headers: { 'Content-Type': 'application/json' } });
  })
];

