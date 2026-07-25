import { http, HttpResponse, delay } from "msw";
import {
  exactIdSearchFixture,
  getNodeFixture,
  getRelationshipFixture,
  expandNeighborhoodFixture
} from "../../fixtures/graphExplorer";

export const graphHandlers = [
  http.get("/data-console/v1/graph/search", async ({ request }) => {
    await delay();
    const url = new URL(request.url);
    const q = url.searchParams.get("q");
    if (!q) {
      return HttpResponse.json({ error: "Missing query parameter 'q'" }, { status: 400 });
    }

    if (request.signal.aborted) {
      return HttpResponse.error();
    }

    try {
      const data = exactIdSearchFixture(q);
      return HttpResponse.json(data);
    } catch (error) {
      return HttpResponse.json({ error: error instanceof Error ? error.message : "Internal Server Error" }, { status: 500 });
    }
  }),

  http.get("/data-console/v1/graph/nodes/:nodeId", async ({ params, request }) => {
    await delay();
    const { nodeId } = params;

    if (request.signal.aborted) {
      return HttpResponse.error();
    }

    // Wrap node response in envelope as requested by http adapter
    const node = getNodeFixture(nodeId as string);
    return HttpResponse.json({
      data: node,
      meta: {
        schema_version: "1.0",
        request_id: `req-${Math.random().toString(36).substring(2, 9)}`,
        generated_at: new Date().toISOString(),
        freshness: "realtime",
        partial: false,
        warnings: []
      },
      page: {
        limit: 1,
        offset: 0,
        total: 1
      }
    });
  }),

  http.get("/data-console/v1/graph/relationships/:relationshipId", async ({ params, request }) => {
    await delay();
    const { relationshipId } = params;

    if (request.signal.aborted) {
      return HttpResponse.error();
    }

    const rel = getRelationshipFixture(relationshipId as string);
    return HttpResponse.json({
      data: rel,
      meta: {
        schema_version: "1.0",
        request_id: `req-${Math.random().toString(36).substring(2, 9)}`,
        generated_at: new Date().toISOString(),
        freshness: "realtime",
        partial: false,
        warnings: []
      },
      page: {
        limit: 1,
        offset: 0,
        total: 1
      }
    });
  }),

  http.get("/data-console/v1/graph/nodes/:nodeId/neighborhood", async ({ params, request }) => {
    await delay();
    const { nodeId } = params;
    const url = new URL(request.url);
    const rawDepth = url.searchParams.get("expansionDepth");
    const expansionDepth = rawDepth ? parseInt(rawDepth, 10) : undefined;

    if (request.signal.aborted) {
      return HttpResponse.error();
    }

    const data = expandNeighborhoodFixture(nodeId as string, expansionDepth);
    return HttpResponse.json(data);
  })
];
