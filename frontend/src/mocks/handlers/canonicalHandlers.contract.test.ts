/**
 * The mocks must mirror the server, and be *provably* still mirroring it.
 *
 * Audit finding #14: the MSW handlers were shaped to satisfy the frontend
 * rather than to mirror the backend, and the copilot's page test replaced the
 * API layer wholesale. Between them, 51 tests passed green against a build that
 * could not complete one turn against the real API. The handlers have been
 * rewritten since. That is not the fix -- being correct today is not a
 * property, it is a coincidence. **The fix is that they can no longer drift
 * without something failing.**
 *
 * So this file does two things, and it is worth being precise about which is
 * which:
 *
 * 1. **Conformance.** Every mocked route is called through the same MSW server
 *    the tests and `npm run dev:mock` use, and the body that comes back is
 *    validated against the response schema the committed OpenAPI document
 *    declares for that route. `additionalProperties: false` is on nearly every
 *    model here, so an invented field fails, a missing required field fails,
 *    and a value outside an enum fails.
 *
 * 2. **Coverage.** Every handler in `canonicalHandlers` must name a route the
 *    document actually publishes. A mock for an endpoint the server does not
 *    serve is the same defect one step earlier: it makes a screen work against
 *    a backend that would 404 it.
 *
 * The limits are written out in `src/test/schemaConformance.ts`; the one worth
 * repeating here is that a bare `str` on the wire conforms to anything. The
 * agent's `business_capability` is such a field, and the vocabulary it must
 * come from lives in the active schema rather than in OpenAPI -- so
 * `backend/tests/test_console_mock_speaks_the_agent_policy.py` checks that one,
 * against the YAML that is its authority.
 */

import { describe, expect, it } from "vitest";

import {
  describeViolations,
  responseSchema,
  validateAgainstSchema,
  type OpenApiDocument,
} from "../../test/schemaConformance";
import { canonicalHandlers } from "./canonicalHandlers";

/**
 * The committed document, read off disk as text rather than imported as a module.
 *
 * `import.meta.glob` with `?raw` rather than `node:fs`: the app tsconfig carries
 * no Node types, so a `readFileSync` here type-checks nowhere even though it
 * runs fine under vitest. Same reason `ReturnCopilotFabrication.test.ts` reads
 * source that way.
 *
 * `npm run contracts:check` regenerates this file from the running app and
 * fails on a diff, so the copy on disk *is* the backend's contract. Reading it
 * here rather than importing the generated `.d.ts` is deliberate: the `.d.ts`
 * is erased at runtime and cannot validate a value, and a mock body is a value.
 */
const document = Object.values(
  import.meta.glob("../../../openapi/return-platform.openapi.json", {
    query: "?raw",
    import: "default",
    eager: true,
  }),
).map((raw) => JSON.parse(raw) as OpenApiDocument)[0];

/**
 * One mocked route, and the contract entry it claims to be serving.
 *
 * `url` is a concrete address rather than a pattern because the point is to
 * exercise the handler, not to re-read it. Path parameters are spelled with the
 * ids the fixtures actually key off, so a handler that branches on one is
 * exercised on the branch the mock app takes.
 */
type Route = {
  readonly method: "get" | "post" | "put" | "delete";
  /** As registered with MSW, so coverage can be checked against the handlers. */
  readonly handler: string;
  /** As published by the backend. */
  readonly contract: string;
  readonly url: string;
  /** Bodies for the writes, so the handler runs the same code the app does. */
  readonly body?: unknown;
  /**
   * The status the mock answers with. Several handlers refuse on purpose --
   * that is the state worth looking at in `dev:mock` -- and a refusal is
   * validated against the schema the document declares for *that* status.
   */
  readonly status?: number;
};

const TURN_REQUEST = {
  conversation_id: "conv-mock-101",
  expected_conversation_version: 1,
  client_turn_id: "ui-conv-mock-101-1",
  idempotency_key: "ui-conv-mock-101-1",
  message_id: "ui-conv-mock-101-1",
  message: "melgon heating, motor came back damaged",
  agent_id: "order-discovery-agent",
  session_timezone: "America/New_York",
};

const ROUTES: readonly Route[] = [
  { method: "get", handler: "/api/runtime-config", contract: "/api/runtime-config", url: "/api/runtime-config" },
  { method: "get", handler: "/api/principal", contract: "/api/principal", url: "/api/principal" },

  // --- the copilot's own surface, which is where the fabrication lived -------
  {
    method: "post",
    handler: "/api/v2/order-agent/conversations/:id/turns",
    contract: "/api/v2/order-agent/conversations/{conversation_id}/turns",
    url: "/api/v2/order-agent/conversations/conv-mock-101/turns",
    body: TURN_REQUEST,
  },
  {
    method: "post",
    handler: "/api/v2/order-agent/conversations/:id/turns",
    contract: "/api/v2/order-agent/conversations/{conversation_id}/turns",
    url: "/api/v2/order-agent/conversations/conv-mock-101/turns",
    // The clarifying branch. Both are validated because they build different
    // bodies, and only one of them used to be looked at.
    body: { ...TURN_REQUEST, expected_conversation_version: 0, message: "a customer in Charlotte" },
  },
  {
    method: "get",
    handler: "/api/v2/order-agent/conversations",
    contract: "/api/v2/order-agent/conversations",
    url: "/api/v2/order-agent/conversations",
  },
  {
    method: "get",
    handler: "/api/v2/order-agent/conversations/:id/transcript",
    contract: "/api/v2/order-agent/conversations/{conversation_id}/transcript",
    url: "/api/v2/order-agent/conversations/conv-mock-101/transcript",
  },
  { method: "get", handler: "/api/cases", contract: "/api/cases", url: "/api/cases" },
  {
    method: "get",
    handler: "/api/cases/:id",
    contract: "/api/cases/{case_id}",
    url: "/api/cases/case-mock-2026",
  },
  {
    method: "get",
    handler: "/api/cases/:id/order-lines",
    contract: "/api/cases/{case_id}/order-lines",
    url: "/api/cases/case-mock-2026/order-lines",
  },
  {
    method: "post",
    handler: "/api/cases/:id/selected-items",
    contract: "/api/cases/{case_id}/selected-items",
    url: "/api/cases/case-mock-2026/selected-items",
    body: { items: [{ orderLineReference: "Line 1", quantity: 1, reason: "SHIPPING_DAMAGE" }] },
  },
  {
    method: "get",
    handler: "/api/return-history",
    contract: "/api/return-history",
    url: "/api/return-history?accountId=CHARLOTTE&customerId=CUST-4417",
  },

  // --- returns -------------------------------------------------------------
  { method: "get", handler: "/api/returns", contract: "/api/returns", url: "/api/returns" },
  {
    method: "get",
    handler: "/api/returns/:sessionId",
    contract: "/api/returns/{session_id}",
    url: "/api/returns/ret-mock-1",
  },
  {
    method: "get",
    handler: "/api/returns/:sessionId/timeline",
    contract: "/api/returns/{session_id}/timeline",
    url: "/api/returns/ret-mock-1/timeline",
  },
  {
    method: "get",
    handler: "/api/returns/:sessionId/support",
    contract: "/api/returns/{session_id}/support",
    url: "/api/returns/ret-mock-1/support",
  },
  {
    method: "get",
    handler: "/api/returns/:sessionId/conversation",
    contract: "/api/returns/{session_id}/conversation",
    url: "/api/returns/ret-mock-1/conversation",
  },
  {
    method: "post",
    handler: "/api/returns/:sessionId/events",
    contract: "/api/returns/{session_id}/events",
    url: "/api/returns/ret-mock-1/events",
    body: { eventType: "BOL_TENDERED" },
    status: 409,
  },

  // --- configuration -------------------------------------------------------
  { method: "get", handler: "/api/config/runtime", contract: "/api/config/runtime", url: "/api/config/runtime" },
  { method: "get", handler: "/api/config/releases", contract: "/api/config/releases", url: "/api/config/releases" },
  {
    method: "get",
    handler: "/api/config/releases/:releaseId",
    contract: "/api/config/releases/{release_id}",
    url: "/api/config/releases/rel-mock-1",
  },
  {
    method: "post",
    handler: "/api/config/releases/:releaseId/promote",
    contract: "/api/config/releases/{release_id}/promote",
    url: "/api/config/releases/rel-mock-1/promote",
    body: {},
    status: 422,
  },
  { method: "get", handler: "/api/config/audit", contract: "/api/config/audit", url: "/api/config/audit" },
  { method: "get", handler: "/api/config/sources", contract: "/api/config/sources", url: "/api/config/sources" },
  {
    method: "get",
    handler: "/api/config/sources/:sourceId",
    contract: "/api/config/sources/{source_id}",
    url: "/api/config/sources/source-mongodb",
  },
  { method: "get", handler: "/api/source-bindings", contract: "/api/source-bindings", url: "/api/source-bindings" },
  {
    method: "put",
    handler: "/api/source-bindings/:dataset",
    contract: "/api/source-bindings/{dataset}",
    url: "/api/source-bindings/source_sales",
    body: {
      sourceAssetId: "salesInv",
      connectorType: "MONGODB",
      objectRef: { database: "source_db", collection: "salesInv" },
      incrementalCursorField: "updated_at",
    },
  },
  {
    method: "delete",
    handler: "/api/source-bindings/:dataset",
    contract: "/api/source-bindings/{dataset}",
    url: "/api/source-bindings/source_sales",
  },

  // --- approvals -----------------------------------------------------------
  { method: "get", handler: "/api/proposals", contract: "/api/proposals", url: "/api/proposals" },
  {
    method: "get",
    handler: "/api/proposals/:proposalId",
    contract: "/api/proposals/{proposal_id}",
    url: "/api/proposals/prop-mock-1",
  },
  {
    method: "post",
    handler: "/api/proposals/:proposalId/approve",
    contract: "/api/proposals/{proposal_id}/approve",
    url: "/api/proposals/prop-mock-1/approve",
    body: { note: "agreed" },
  },
  {
    method: "post",
    handler: "/api/proposals/:proposalId/reject",
    contract: "/api/proposals/{proposal_id}/reject",
    url: "/api/proposals/prop-mock-1/reject",
    body: { note: null },
    status: 409,
  },
  {
    method: "post",
    handler: "/api/proposals/:proposalId/activate",
    contract: "/api/proposals/{proposal_id}/activate",
    url: "/api/proposals/prop-mock-1/activate",
    body: {},
  },

  // --- source sync ---------------------------------------------------------
  { method: "get", handler: "/api/graph-sync/runs", contract: "/api/graph-sync/runs", url: "/api/graph-sync/runs" },
  {
    method: "get",
    handler: "/api/graph-sync/runs/:runId",
    contract: "/api/graph-sync/runs/{run_id}",
    url: "/api/graph-sync/runs/sync-mock-full",
  },
  {
    method: "post",
    handler: "/api/graph-sync/runs",
    contract: "/api/graph-sync/runs",
    url: "/api/graph-sync/runs",
    body: { mode: "FULL" },
  },

  // --- graph schema --------------------------------------------------------
  {
    method: "get",
    handler: "/api/graph-schema/analyses",
    contract: "/api/graph-schema/analyses",
    url: "/api/graph-schema/analyses",
  },
  {
    method: "get",
    handler: "/api/graph-schema/analyses/:analysisId/clarifications",
    contract: "/api/graph-schema/analyses/{analysis_id}/clarifications",
    url: "/api/graph-schema/analyses/an-mock-1/clarifications",
  },
  {
    method: "get",
    handler: "/api/graph-schema/drafts/:draftId",
    contract: "/api/graph-schema/drafts/{draft_id}",
    url: "/api/graph-schema/drafts/dr-mock-1",
  },
  {
    method: "get",
    handler: "/api/graph-schema/drafts/:draftId/shape",
    contract: "/api/graph-schema/drafts/{draft_id}/shape",
    url: "/api/graph-schema/drafts/dr-mock-1/shape",
  },
  {
    method: "get",
    handler: "/api/graph-schema/drafts/:draftId/revisions",
    contract: "/api/graph-schema/drafts/{draft_id}/revisions",
    url: "/api/graph-schema/drafts/dr-mock-1/revisions",
  },

  // --- AI control centre ---------------------------------------------------
  { method: "get", handler: "/api/ai/routes", contract: "/api/ai/routes", url: "/api/ai/routes" },
  { method: "get", handler: "/api/ai/tasks", contract: "/api/ai/tasks", url: "/api/ai/tasks" },
  { method: "get", handler: "/api/ai/metrics", contract: "/api/ai/metrics", url: "/api/ai/metrics" },
  {
    method: "get",
    handler: "/api/ai/metrics/summary",
    contract: "/api/ai/metrics/summary",
    url: "/api/ai/metrics/summary",
  },
  {
    method: "get",
    handler: "/api/ai/requests/:traceId",
    contract: "/api/ai/requests/{trace_id}",
    url: "/api/ai/requests/trace-mock-1",
  },
  { method: "get", handler: "/api/ai/interceptions", contract: "/api/ai/interceptions", url: "/api/ai/interceptions" },
  {
    method: "get",
    handler: "/api/ai/interceptions/:id/request",
    contract: "/api/ai/interceptions/{interception_id}/request",
    url: "/api/ai/interceptions/air-mock-1/request",
  },
  {
    method: "post",
    handler: "/api/ai/interceptions/:id/allow",
    contract: "/api/ai/interceptions/{interception_id}/allow",
    url: "/api/ai/interceptions/int-mock-1/allow",
    body: {},
  },
  {
    method: "post",
    handler: "/api/ai/interceptions/:id/answer",
    contract: "/api/ai/interceptions/{interception_id}/answer",
    url: "/api/ai/interceptions/int-mock-1/answer",
    body: { answer: "proceed" },
  },
  {
    method: "post",
    handler: "/api/ai/interceptions/:id/cancel",
    contract: "/api/ai/interceptions/{interception_id}/cancel",
    url: "/api/ai/interceptions/int-mock-1/cancel",
    body: {},
  },
];

/**
 * Handlers whose body is not checked against a schema, each with the reason.
 *
 * Kept as an explicit list rather than by silently skipping unmatched routes:
 * an unexplained gap is how a mock stops being checked without anyone
 * deciding that it should.
 */
const UNCHECKED_BODIES: Readonly<Record<string, string>> = {
  // A refusal the document declares no body for -- FastAPI publishes 200 and
  // 422 only. The status is asserted; there is no schema to check it against.
  "post /api/returns/:sessionId/events": "409 has no declared response schema",
  "post /api/proposals/:proposalId/reject": "409 has no declared response schema",
};

function key(route: Pick<Route, "method" | "handler">): string {
  return `${route.method} ${route.handler}`;
}

/** Every route MSW is actually serving, read off the handlers themselves. */
function registeredRoutes(): readonly string[] {
  return canonicalHandlers.map((handler) => {
    const info = (handler as unknown as { info: { method: string; path: string } }).info;
    return `${info.method.toLowerCase()} ${info.path}`;
  });
}

describe("every canonical mock route is one the backend publishes", () => {
  it("names a path in the committed OpenAPI document", () => {
    const undocumented = ROUTES.filter(
      (route) => responseSchema(document, route.method, route.contract) === null,
    ).map((route) => `${key(route)} -> ${route.contract}`);

    // A mock for a route the server does not serve makes a screen work against
    // a backend that would 404 it -- the same defect as a wrong body, one step
    // earlier.
    expect(undocumented).toEqual([]);
  });

  it("leaves no handler unaccounted for", () => {
    const covered = new Set(ROUTES.map(key));
    const missing = registeredRoutes().filter((route) => !covered.has(route));

    // Adding a handler without adding it here is how the last set of mocks
    // drifted: nothing was watching them.
    expect(missing).toEqual([]);
  });

  it("checks a handler for every route it claims to cover", () => {
    const registered = new Set(registeredRoutes());
    const orphans = [...new Set(ROUTES.map(key))].filter((route) => !registered.has(route));

    // The other direction: a table entry for a handler that no longer exists
    // is a check that silently stopped checking anything.
    expect(orphans).toEqual([]);
  });
});

describe("every canonical mock body conforms to the schema it claims", () => {
  for (const route of ROUTES) {
    const label = `${key(route)}${
      route.body === undefined ? "" : ` (${JSON.stringify(route.body).slice(0, 48)})`
    }`;

    it(label, async () => {
      const response = await fetch(route.url, {
        method: route.method.toUpperCase(),
        ...(route.body === undefined
          ? {}
          : {
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(route.body),
            }),
      });

      expect(response.status).toBe(route.status ?? 200);

      if (key(route) in UNCHECKED_BODIES) return;

      const schema = responseSchema(document, route.method, route.contract, String(response.status));
      expect(schema, `${route.contract} declares no ${String(response.status)} body`).not.toBeNull();

      const body: unknown = await response.json();
      const violations = validateAgainstSchema(body, schema ?? {}, document);

      expect(
        violations,
        `${label} does not conform to ${route.contract}:\n${describeViolations(violations)}`,
      ).toEqual([]);
    });
  }
});
