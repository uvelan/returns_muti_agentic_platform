import { http, HttpResponse, delay } from "msw";
import { FIXTURE_BROWSER_ASSETS, FIXTURE_BROWSER_RECORDS } from "../../fixtures/browser";

export const browserHandlers = [
  http.get("/data-console/v1/browser/assets", async () => {
    await delay(300);
    return new HttpResponse(JSON.stringify({
      data: FIXTURE_BROWSER_ASSETS,
      page: { next_cursor: null, has_more: false, page_size: 10 },
      meta: {
        schema_version: "1.0",
        request_id: "req-fixture-browser",
        generated_at: new Date().toISOString(),
        freshness: "LIVE",
        partial: false,
        warnings: []
      }
    }), { headers: { 'Content-Type': 'application/json' } });
  }),

  http.get("/data-console/v1/browser/:engine/:assetId/records", async ({ request, params }) => {
    await delay(300);
    const { engine, assetId } = params;
    const url = new URL(request.url);
    const pageSize = Number(url.searchParams.get("page_size")) || 10;

    const key = Object.keys(FIXTURE_BROWSER_RECORDS).find((candidate) => {
      const records = FIXTURE_BROWSER_RECORDS[candidate] ?? [];
      return (
        candidate.endsWith(`:${assetId as string}`)
        && records.every((record) => record.identity.engine === engine)
      );
    });
    const records = key ? FIXTURE_BROWSER_RECORDS[key] ?? [] : [];

    return new HttpResponse(JSON.stringify({
      data: records.slice(0, pageSize),
      page: {
        next_cursor: records.length > pageSize ? "mock_next_page" : null,
        has_more: records.length > pageSize,
        page_size: pageSize
      },
      meta: {
        schema_version: "1.0",
        request_id: `req-fixture-records-${assetId as string}`,
        generated_at: new Date().toISOString(),
        freshness: "LIVE",
        partial: false,
        warnings: []
      }
    }), { headers: { 'Content-Type': 'application/json' } });
  }),

  http.get("/data-console/v1/browser/:engine/:assetId/records/:recordId", async ({ params }) => {
    await delay(300);
    const { engine, assetId, recordId } = params;

    const key = Object.keys(FIXTURE_BROWSER_RECORDS).find((candidate) => {
      const records = FIXTURE_BROWSER_RECORDS[candidate] ?? [];
      return (
        candidate.endsWith(`:${assetId as string}`)
        && records.every((record) => record.identity.engine === engine)
      );
    });
    const records = key ? FIXTURE_BROWSER_RECORDS[key] ?? [] : [];
    const record = records.find(r => r.identity.id === recordId);

    if (!record) {
      return new HttpResponse(JSON.stringify({ error: "Not Found" }), { status: 404 });
    }

    return new HttpResponse(JSON.stringify({
      data: record,
      page: { next_cursor: null, has_more: false, page_size: 10 },
      meta: {
        schema_version: "1.0",
        request_id: `req-fixture-record-${recordId as string}`,
        generated_at: new Date().toISOString(),
        freshness: "LIVE",
        partial: false,
        warnings: []
      }
    }), { headers: { 'Content-Type': 'application/json' } });
  })
];
