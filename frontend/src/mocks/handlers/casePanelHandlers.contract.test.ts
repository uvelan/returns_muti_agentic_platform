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
import { fixtureServer } from "../../test/server";
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
const CLARIFICATION = "clar-1";

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
  {
    // 202, and the document declares no 200 here at all: when this returns a
    // command is on file and a delivery row is queued, and nothing about the
    // relay to Support has happened yet.
    method: "post",
    handler: "/api/v1/cases/:caseId/clarifications/:clarificationId/answer",
    contract: "/api/v1/cases/{case_id}/clarifications/{clarification_id}/answer",
    url: `/api/v1/cases/${CASE}/clarifications/${CLARIFICATION}/answer`,
    body: { answerText: "It is the pallet in bay 3.", resolutionChoice: "reject", returnRecordId: null },
    status: 202,
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

/**
 * The bytes the ETag is actually computed over: the `CasePanelView`, not the
 * HTTP response carrying it.
 *
 * **This boundary is the contract's, not a convenience.** The response envelope
 * carries `meta.generated_at`, which is a per-response wall-clock stamp and
 * *must* differ between two reads -- it says when this reply was composed. The
 * panel's digest is taken over `body.data` for exactly that reason
 * (`casePanelHandlers.ts`'s `etagFor(body.data)`), and sect. 9's "identical
 * body" is a claim about the composed view.
 *
 * Recorded because ACC4 wrote both tests below against the whole response
 * first, and both failed on `generated_at` alone -- a red that looks like a
 * principal-independence failure and is nothing of the kind. The next reader
 * gets the answer without repeating the run.
 *
 * `JSON.parse` preserves key insertion order and `JSON.stringify` re-emits it,
 * so this still compares ordering: two views differing only in key order would
 * hash differently and break the 304, and would fail here too.
 */
function panelBytes(responseText: string): string {
  return JSON.stringify((JSON.parse(responseText) as { data: unknown }).data);
}

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

  /**
   * **The half of hash stability the test above cannot reach.**
   *
   * `moves the ETag when the panel moves, and holds it when nothing does`
   * issues its two reads back to back, so it pins stability against a
   * millisecond-resolution wall-clock leak and nothing coarser. ACC4 measured
   * that: a `Math.floor(Date.now()/1000)` value on a declared field
   * (INJ-F7b) was invisible to both stability tests and was caught only by an
   * unrelated fixture assertion elsewhere -- which would not exist for a field
   * nobody renders.
   *
   * The contract's own wording is *"two polls with no state change **while a
   * timer ticks** -> identical ETag (no wall-clock values in payload)"*, and a
   * poll interval is ten seconds. So this one lets real time pass across a
   * second boundary, which is the smallest gap that can catch the class.
   */
  it("holds the ETag across a real wall-clock second, with the deadline ticking", async () => {
    const first = await fetch(`/api/v1/cases/${CASE}/panel`);
    const firstEtag = first.headers.get("ETag");
    const firstBody = panelBytes(await first.text());

    // **Premise 1: a timer is actually ticking.** "No wall-clock value in the
    // payload" is trivially true of a payload with no deadline in it, and a
    // fixture that lost its deadline would leave this test passing for the
    // wrong reason for ever.
    const deadline = (JSON.parse(firstBody) as { timers: { template_review_deadline_iso: string | null } })
      .timers.template_review_deadline_iso;
    expect(deadline).not.toBeNull();
    expect(Date.parse(deadline ?? "")).toBeGreaterThan(Date.now());

    // **Premise 2: the clock genuinely moves between the two reads.**
    //
    // Real time rather than `vi.setSystemTime`, and the honest reason is not
    // that a fake clock could not detect the leak -- it could. It is that fake
    // timers and MSW's `delay()` have to be reconciled (`shouldAdvanceTime`)
    // before a request will resolve at all, which makes the test's result
    // depend on that reconciliation being right. 1.1 s of real time cannot lie
    // about having passed, and this is one test.
    const startedAt = Date.now();
    await new Promise((resolve) => setTimeout(resolve, 1_100));
    expect(Date.now() - startedAt).toBeGreaterThanOrEqual(1_000);

    const second = await fetch(`/api/v1/cases/${CASE}/panel`);

    // Nothing about the case changed, so the digest must not have either --
    // and the bytes must be identical, which is the property the digest is
    // standing in for. Both, because an ETag that held while the body moved
    // would be the worse of the two failures.
    expect(second.headers.get("ETag")).toBe(firstEtag);
    expect(panelBytes(await second.text())).toBe(firstBody);
  });

  /**
   * Two principals, same case -> identical body **and** identical ETag
   * (contracts.md sect. 9).
   *
   * The trap this test is built to avoid: if the panel carried nothing
   * attributable to any particular actor, "identical for two principals" would
   * be true of an empty room and would prove nothing. So it **seeds an
   * actor-attributed value first** -- an accepted command, which sect. 9 names
   * explicitly as the field that stays unfiltered -- and asserts it is present
   * before comparing. The comparison then has something to be wrong about.
   */
  it("serves two principals the same bytes and the same ETag, commands included", async () => {
    const seeded = await fetch(`/api/v1/cases/${CASE}/reviews/${REVIEW}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer principal-one" },
      body: JSON.stringify({
        draft_version: 1,
        canonical_edit_version: 0,
        canonical_approved_payload_hash: "0".repeat(64),
      }),
    });
    // Asserted so a failure here reads as "the seed did not take" rather than
    // surfacing two screens later as an empty `accepted_commands`. Premise 2
    // below would still catch it; this just says where it went wrong.
    expect(seeded.status).toBe(200);

    const seen: (string | null)[] = [];
    fixtureServer.events.on("request:start", ({ request }) => {
      if (request.url.includes("/panel")) seen.push(request.headers.get("Authorization"));
    });

    const one = await fetch(`/api/v1/cases/${CASE}/panel`, {
      headers: { Authorization: "Bearer principal-one" },
    });
    const two = await fetch(`/api/v1/cases/${CASE}/panel`, {
      headers: { Authorization: "Bearer principal-two" },
    });

    const oneBody = panelBytes(await one.text());
    const twoBody = panelBytes(await two.text());
    fixtureServer.events.removeAllListeners("request:start");

    // **Premise 1: two genuinely different principals reached the handler.**
    // Asserted on what the server saw, not on what this test passed -- a fetch
    // layer that dropped the header would otherwise make the whole test a
    // comparison of one principal with itself.
    expect(seen).toEqual(["Bearer principal-one", "Bearer principal-two"]);

    // **Premise 2: there is something actor-attributed in the body to filter.**
    const commands = (JSON.parse(oneBody) as {
      accepted_commands: { actor_id: string; kind: string }[];
    }).accepted_commands;
    expect(commands.length).toBeGreaterThan(0);
    expect(commands[0].actor_id).toBeTruthy();
    expect(commands[0].kind).toBe("template_approved");

    // Byte-identical, not merely deep-equal: sect. 9 asks for a canonical
    // order-stable serialization, and two bodies that differ only in key order
    // would hash differently and break the 304 while passing `toEqual`.
    expect(twoBody).toBe(oneBody);
    expect(two.headers.get("ETag")).toBe(one.headers.get("ETag"));
  });
});
