import { http, HttpResponse, delay } from "msw";
import type { ReleaseNode, ActiveSnapshot } from "../../api/configurationQueries";

// --- In-memory fixture store ---
const FIXTURE_RELEASES: Record<string, ReleaseNode> = {
  "rel-baseline-1": {
    release_id: "rel-baseline-1",
    status: "PINNED",
    created_at: "2026-07-01T09:00:00Z",
    created_by: "system",
    checksum_sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    domains: {
      ORDER_DISCOVERY: {
        max_candidates: 5,
        lucence_fuzzy_distance: 1,
        min_score: 0.75,
        search_fields: ["customer_name", "order_number", "product_description"],
      },
      AI_GATEWAY: {
        primary_provider: "openai",
        fallback_provider: "anthropic",
        timeout_seconds: 15,
        max_tokens: 1024,
      },
      DISAMBIGUATION_ATTRIBUTES: {
        required_confidence: 0.85,
        max_clarification_turns: 3,
        slot_order: ["ORDER_NUMBER", "CUSTOMER_NAME", "PRODUCT"],
      },
      SOURCE_RESOLUTION: {
        routing_strategy: "graph_first",
        fallback_strategy: "sql",
        cache_ttl_seconds: 300,
      },
    },
  },
};

const mkMeta = (id: string) => ({
  schema_version: "1.0",
  request_id: `req-fixture-${id}`,
  generated_at: new Date().toISOString(),
  freshness: "LIVE",
  partial: false,
  warnings: [],
});

const envelope = <T>(data: T, id = "config") => ({
  data,
  meta: mkMeta(id),
});

export const configurationHandlers = [
  // GET active snapshot
  http.get("/data-console/v1/configuration/active-snapshot", async () => {
    await delay(200);
    const pinned = Object.values(FIXTURE_RELEASES).find((r) => r.status === "PINNED");
    const snapshot: ActiveSnapshot = {
      release_id: pinned?.release_id ?? "yaml-fallback",
      checksum_sha256: pinned?.checksum_sha256 ?? "00000000",
      loaded_at: new Date().toISOString(),
      source: pinned ? "NEO4J" : "YAML_FALLBACK",
      configuration: pinned?.domains ?? {},
      domain_payloads: pinned?.domains ?? {},
    };
    return HttpResponse.json(envelope(snapshot, "active-snapshot"));
  }),

  // GET all releases
  http.get("/data-console/v1/configuration/releases", async () => {
    await delay(200);
    return HttpResponse.json(envelope(Object.values(FIXTURE_RELEASES), "releases"));
  }),

  // GET single release
  http.get("/data-console/v1/configuration/releases/:releaseId", async ({ params }) => {
    await delay(150);
    const rel = FIXTURE_RELEASES[String(params.releaseId)] as ReleaseNode | undefined;
    if (!rel) return new HttpResponse(JSON.stringify({ detail: "Not found" }), { status: 404 });
    return HttpResponse.json(envelope(rel, String(params.releaseId)));
  }),

  // POST create release
  http.post("/data-console/v1/configuration/releases", async ({ request }) => {
    await delay(300);
    const body = await request.json() as { release_id: string; from_active?: boolean };
    const sourceRelease = Object.values(FIXTURE_RELEASES).find((r) => r.status === "PINNED");
    const newRelease: ReleaseNode = {
      release_id: body.release_id,
      status: "DRAFT",
      created_at: new Date().toISOString(),
      created_by: "admin",
      checksum_sha256: "draft-pending",
      domains: body.from_active ? { ...(sourceRelease?.domains ?? {}) } : {},
    };
    FIXTURE_RELEASES[body.release_id] = newRelease;
    return new HttpResponse(JSON.stringify(envelope(newRelease, body.release_id)), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    });
  }),

  // PUT save domain config
  http.put("/data-console/v1/configuration/releases/:releaseId/domains/:domainKey", async ({ params, request }) => {
    await delay(250);
    const releaseId = String(params.releaseId);
    const domainKey = String(params.domainKey);
    const rel = FIXTURE_RELEASES[releaseId] as ReleaseNode | undefined;
    if (!rel) return new HttpResponse(JSON.stringify({ detail: "Not found" }), { status: 404 });
    const body = await request.json() as { payload: Record<string, unknown> };
    rel.domains ??= {};
    rel.domains[domainKey] = body.payload;
    return HttpResponse.json(envelope({ domain_key: domainKey, payload: body.payload }, domainKey));
  }),

  // POST promote release
  http.post("/data-console/v1/configuration/releases/:releaseId/promote", async ({ params, request }) => {
    await delay(300);
    const releaseId = String(params.releaseId);
    const rel = FIXTURE_RELEASES[releaseId] as ReleaseNode | undefined;
    if (!rel) return new HttpResponse(JSON.stringify({ detail: "Not found" }), { status: 404 });
    const body = await request.json() as { status: string };
    // If pinning, unpin other
    if (body.status === "PINNED") {
      for (const r of Object.values(FIXTURE_RELEASES)) {
        if (r.status === "PINNED" && r.release_id !== releaseId) {
          r.status = "ARCHIVED";
        }
      }
    }
    rel.status = body.status as ReleaseNode["status"];
    return HttpResponse.json(envelope(rel, releaseId));
  }),
];
