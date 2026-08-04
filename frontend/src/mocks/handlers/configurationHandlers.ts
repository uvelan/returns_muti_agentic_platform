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
      RETURN_PLATFORM: {
        agents: {
          order_discovery: {
            version: "2.0",
            enabled: true,
            human_confirmation_required: true,
          },
        },
      },
      AI_GATEWAY: {
        tasks: {
          RETURN_ELIGIBILITY_V1: {
            promptVersion: "return-eligibility-v2",
            systemPrompt: "Use only supplied operational facts and return a structured decision.",
          },
        },
      },
      DEPENDENCY_SIMULATION: {
        enabled: true,
        modeBanner: "SIMULATION MODE — no production dependency is called.",
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

  // GET runtime config
  http.get("/api/v1/runtime-config", async () => {
    await delay(100);
    return HttpResponse.json(envelope({
      releaseId: "version-controlled-baseline",
      environment: "development",
      apiBasePath: "/api/v1",
      features: {
        orderDiscoveryCopilot: true,
        aiStudioOperationalGeneration: true
      },
      capabilities: {
        availableSourceTypes: ["MONGODB", "SQLSERVER", "NEO4J"],
        availableModelProviders: ["GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC"]
      }
    }, "runtime-config"));
  }),

  // GET overview
  http.get("/data-console/v1/overview", async () => {
    await delay(100);
    return HttpResponse.json(envelope({
      mongodb: { status: "HEALTHY", latency_ms: 1, checked_at: new Date().toISOString(), error_code: null, safe_message: null },
      neo4j: { status: "HEALTHY", latency_ms: 1, checked_at: new Date().toISOString(), error_code: null, safe_message: null },
      sqlserver: { status: "HEALTHY", latency_ms: 1, checked_at: new Date().toISOString(), error_code: null, safe_message: null },
      temporal: { status: "HEALTHY", latency_ms: 1, checked_at: new Date().toISOString(), error_code: null, safe_message: null },
      valkey: { status: "HEALTHY", latency_ms: 1, checked_at: new Date().toISOString(), error_code: null, safe_message: null }
    }, "overview"));
  }),
];
