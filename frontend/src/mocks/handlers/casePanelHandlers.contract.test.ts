/**
 * The panel mocks must mirror the server, and be *provably* still mirroring it.
 *
 * Same instrument as `canonicalHandlers.contract.test.ts` and for the same
 * reason: a mock shaped to satisfy the frontend rather than the backend makes a
 * screen work against an API that would refuse it. Both directions are checked
 * -- every handler names a published route, and every route has a handler --
 * because a table entry for a handler that no longer exists is a check that
 * silently stopped checking anything.
 *
 * One thing this file tests that the canonical one does not: **the 304**. The
 * panel is the only surface on this platform with a conditional read, it is the
 * state a poll spends most of its life in, and `readCasePanel` has a branch
 * that only runs there.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  describeViolations,
  responseSchema,
  validateAgainstSchema,
  type OpenApiDocument,
} from "../../test/schemaConformance";
import { casePanelHandlers, resetCasePanelMocks } from "./casePanelHandlers";

const document = Object.values(
  import.meta.glob("../../../openapi/return-platform.openapi.json", {
    query: "?raw",
    import: "default",
    eager: true,
  }),
).map((raw) => JSON.parse(raw) as OpenApiDocument)[0];

const CASE = "case-mock-2026";
const REVIEW = "review-mock-1";

type Route = {
  readonly method: "get" | "post" | "put";
  readonly handler: string;
  readonly contract: string;
  readonly url: string;
  readonly body?: unknown;
  readonly status?: number;
};

const ROUTES: readonly Route[] = [
  {
    method: "get",
    handler: "/api/v1/cases/:caseId/panel",
    contract: "/api/v1/cases/{case_id}/panel",
    url: `/api/v1/cases/${CASE}/panel`,
  },
  {
    method: "get",
    handler: "/api/v1/cases/:caseId/reviews/:reviewId/edit-state",
    contract: "/api/v1/cases/{case_id}/reviews/{review_id}/edit-state",
    url: `/api/v1/cases/${CASE}/reviews/${REVIEW}/edit-state`,
  },
  {
    method: "put",
    handler: "/api/v1/cases/:caseId/reviews/:reviewId/edit-state",
    contract: "/api/v1/cases/{case_id}/reviews/{review_id}/edit-state",
    url: `/api/v1/cases/${CASE}/reviews/${REVIEW}/edit-state`,
    body: { client_edit_id: "c-1", base_draft_version: 1, payload: { subject: "edited" } },
  },
  {
    method: "put",
    handler: "/api/v1/cases/:caseId/reviews/:reviewId/edit-state",
    contract: "/api/v1/cases/{case_id}/reviews/{review_id}/edit-state",
    url: `/api/v1/cases/${CASE}/reviews/${REVIEW}/edit-state`,
    // The stale-base branch. Refusals are mocked too, because "the draft moved
    // under you" is a state the panel has to render and a developer has to be
    // able to look at.
    body: { client_edit_id: "c-2", base_draft_version: 99, payload: {} },
    status: 409,
  },
  {
    method: "post",
    handler: "/api/v1/cases/:caseId/reviews/:reviewId/edit-state/resolve",
    contract: "/api/v1/cases/{case_id}/reviews/{review_id}/edit-state/resolve",
    url: `/api/v1/cases/${CASE}/reviews/${REVIEW}/edit-state/resolve`,
    body: { canonical_payload: { subject: "agreed" }, resolved_from_actor_edit_ids: [] },
  },
  {
    method: "post",
    handler: "/api/v1/cases/:caseId/reviews/:reviewId/revise",
    contract: "/api/v1/cases/{case_id}/reviews/{review_id}/revise",
    url: `/api/v1/cases/${CASE}/reviews/${REVIEW}/revise`,
    body: { note: "the RMA is missing" },
  },
  {
    method: "post",
    handler: "/api/v1/cases/:caseId/reviews/:reviewId/template-review/redraft",
    contract: "/api/v1/cases/{case_id}/reviews/{review_id}/template-review/redraft",
    url: `/api/v1/cases/${CASE}/reviews/${REVIEW}/template-review/redraft`,
  },
  {
    method: "post",
    handler: "/api/v1/cases/:caseId/reviews/:reviewId/approve",
    contract: "/api/v1/cases/{case_id}/reviews/{review_id}/approve",
    url: `/api/v1/cases/${CASE}/reviews/${REVIEW}/approve`,
    body: {
      draft_version: 1,
      canonical_edit_version: 0,
      canonical_approved_payload_hash: "0".repeat(64),
    },
  },
  {
    method: "post",
    handler: "/api/v1/cases/:caseId/reviews/:reviewId/cancel",
    contract: "/api/v1/cases/{case_id}/reviews/{review_id}/cancel",
    url: `/api/v1/cases/${CASE}/reviews/${REVIEW}/cancel`,
    body: { reason: "support answered another way" },
  },
  {
    method: "post",
    handler: "/api/v1/cases/:caseId/reviews/:reviewId/recovery/retry",
    contract: "/api/v1/cases/{case_id}/reviews/{review_id}/recovery/retry",
    url: `/api/v1/cases/${CASE}/reviews/${REVIEW}/recovery/retry`,
    body: { reason: "support came back up" },
  },
  {
    method: "post",
    handler: "/api/v1/cases/:caseId/reviews/:reviewId/recovery/abandon",
    contract: "/api/v1/cases/{case_id}/reviews/{review_id}/recovery/abandon",
    url: `/api/v1/cases/${CASE}/reviews/${REVIEW}/recovery/abandon`,
    body: { reason: "support resolved it on the phone" },
  },
];

function key(route: Pick<Route, "method" | "handler">): string {
  return `${route.method} ${route.handler}`;
}

function registeredRoutes(): readonly string[] {
  return casePanelHandlers.map((handler) => {
    const info = (handler as unknown as { info: { method: string; path: string } }).info;
    return `${info.method.toLowerCase()} ${info.path}`;
  });
}

describe("every case-panel mock route is one the backend publishes", () => {
  it("names a path in the committed OpenAPI document", () => {
    const undocumented = ROUTES.filter(
      (route) =>
        responseSchema(document, route.method, route.contract, String(route.status ?? 200)) ===
          null && responseSchema(document, route.method, route.contract) === null,
    ).map((route) => `${key(route)} -> ${route.contract}`);

    expect(undocumented).toEqual([]);
  });

  it("leaves no handler unaccounted for", () => {
    const covered = new Set(ROUTES.map(key));
    expect(registeredRoutes().filter((route) => !covered.has(route))).toEqual([]);
  });

  it("checks a handler for every route it claims to cover", () => {
    const registered = new Set(registeredRoutes());
    expect([...new Set(ROUTES.map(key))].filter((route) => !registered.has(route))).toEqual([]);
  });
});

describe("every case-panel mock body conforms to the schema it claims", () => {
  for (const route of ROUTES) {
    const label = `${key(route)}${
      route.body === undefined ? "" : ` (${JSON.stringify(route.body).slice(0, 48)})`
    }`;

    it(label, async () => {
      // The handlers are stateful, so each body is validated against a fresh
      // store -- otherwise the approve fixture would meet a review a previous
      // test had already cancelled and be validated against a 409 it did not
      // mean to assert.
      resetCasePanelMocks();

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

      const schema = responseSchema(
        document,
        route.method,
        route.contract,
        String(response.status),
      );
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

describe("the panel mock serves the conditional read the contract is built on", () => {
  beforeEach(() => {
    resetCasePanelMocks();
  });

  it("answers 304 with no body when the ETag still matches", async () => {
    const first = await fetch(`/api/v1/cases/${CASE}/panel`);
    const etag = first.headers.get("ETag");
    expect(etag).toBeTruthy();

    const second = await fetch(`/api/v1/cases/${CASE}/panel`, {
      headers: { "If-None-Match": etag ?? "" },
    });

    expect(second.status).toBe(304);
    expect(await second.text()).toBe("");
    expect(second.headers.get("Cache-Control")).toBe("private, no-cache");
  });

  it("moves the ETag when the panel moves, and holds it when nothing does", async () => {
    // Both halves. An ETag that never changed would give a permanently cached
    // panel; one that changed every time would make the 304 unreachable and
    // the mock would be testing nothing.
    const before = (await fetch(`/api/v1/cases/${CASE}/panel`)).headers.get("ETag");
    const unchanged = (await fetch(`/api/v1/cases/${CASE}/panel`)).headers.get("ETag");
    expect(unchanged).toBe(before);

    await fetch(`/api/v1/cases/${CASE}/reviews/${REVIEW}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "changed my mind" }),
    });

    const after = (await fetch(`/api/v1/cases/${CASE}/panel`)).headers.get("ETag");
    expect(after).not.toBe(before);
  });

  it("declares the cache headers the contract fixes, on both surfaces", async () => {
    const panel = await fetch(`/api/v1/cases/${CASE}/panel`);
    expect(panel.headers.get("Cache-Control")).toBe("private, no-cache");
    expect(panel.headers.get("Vary")).toBe("Authorization");

    // `no-store`, not `no-cache`: an autosaved draft is one person's
    // unfinished thinking and `no-cache` still permits storage.
    const edit = await fetch(`/api/v1/cases/${CASE}/reviews/${REVIEW}/edit-state`);
    expect(edit.headers.get("Cache-Control")).toBe("private, no-store");
  });
});
