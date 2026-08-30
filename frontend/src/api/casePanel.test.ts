/**
 * The ETag-aware panel read, against the stateful MSW handlers.
 *
 * Driven through the real mock server rather than a stubbed `fetch`, because
 * the thing under test *is* the conditional exchange: a stub would be the test
 * asserting its own arrangement, and the 304 branch -- which a poll spends most
 * of its life in -- would never run.
 */

import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { APIError } from "./client";
import {
  PANEL_POLL_INTERVAL_MS,
  asReviewConflict,
  casePanelApi,
  panelRefetchInterval,
  resetPanelCacheForTests,
} from "./casePanel";
import { resetCasePanelMocks } from "../mocks/handlers/casePanelHandlers";
import { fixtureServer } from "../test/server";

const CASE = "case-mock-2026";
const REVIEW = "review-mock-1";

beforeEach(() => {
  resetCasePanelMocks();
  resetPanelCacheForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("reading the panel", () => {
  it("returns the composed view", async () => {
    const view = await casePanelApi.read(CASE);

    expect(view.case_id).toBe(CASE);
    expect(view.reviews.map((review) => review.review_id)).toEqual([REVIEW]);
    expect(view.timers.template_review_deadline_iso).toBeTruthy();
  });

  it("revalidates with the ETag it holds and answers from the cache on 304", async () => {
    const seen: (string | null)[] = [];
    fixtureServer.events.on("request:start", ({ request }) => {
      if (request.url.includes("/panel")) seen.push(request.headers.get("If-None-Match"));
    });

    const first = await casePanelApi.read(CASE);
    const second = await casePanelApi.read(CASE);

    // The second request carried the conditional header -- which is the whole
    // mechanism -- and the caller still got a view back.
    expect(seen).toEqual([null, expect.stringContaining("mock-")]);
    expect(second).toEqual(first);
    fixtureServer.events.removeAllListeners("request:start");
  });

  it("re-requests unconditionally when a 304 arrives with nothing cached", async () => {
    // A server that answers 304 to a request carrying no validator. It is not
    // a shape a correct server produces, and that is the point: `readCasePanel`
    // has a branch for it because the alternative is handing a caller
    // `undefined` and blanking the panel. Driven here, the branch runs -- the
    // first answer is a bodyless 304, and the retry, which sends no
    // `If-None-Match` and therefore cannot loop, gets the body.
    let asked = 0;
    fixtureServer.use(
      http.get("/api/v1/cases/:caseId/panel", ({ request }) => {
        asked += 1;
        if (!request.headers.get("If-None-Match") && asked === 1) {
          return new HttpResponse(null, { status: 304, headers: { ETag: '"stale"' } });
        }
        // `undefined` falls through to the real handler below, so the retry is
        // answered by the mock the rest of this file uses.
        return undefined;
      }),
    );

    await expect(casePanelApi.read(CASE)).resolves.toMatchObject({ case_id: CASE });
    expect(asked).toBeGreaterThanOrEqual(2);
  });

  it("sees a change the moment the panel actually moves", async () => {
    const before = await casePanelApi.read(CASE);
    expect(before.reviews[0].state).toBe("OPEN");

    await casePanelApi.cancel(CASE, REVIEW, "support answered another way");
    const after = await casePanelApi.read(CASE);

    // Both halves: the ETag has to move (or this would answer from the cache
    // for ever) *and* the new body has to be the one that comes back.
    expect(after.reviews[0].state).toBe("CANCELLED");
  });

  it("raises a readable refusal rather than an envelope error", async () => {
    fixtureServer.use(
      http.get("/api/v1/cases/:caseId/panel", () =>
        HttpResponse.json(
          {
            detail: {
              code: "CASE_NOT_FOUND",
              message: "Case case-not-mine does not exist.",
              retryable: false,
            },
          },
          { status: 404 },
        ),
      ),
    );

    const error = await casePanelApi.read("case-not-mine").catch((thrown: unknown) => thrown);

    expect(error).toBeInstanceOf(APIError);
    // The refusal's own words, not "The API request failed with status 404".
    // An associate opening a stale link is told what happened.
    expect((error as APIError).message).toBe("Case case-not-mine does not exist.");
    expect((error as APIError).status).toBe(404);
  });
});

describe("polling", () => {
  it("keeps polling an ordinary panel", () => {
    expect(panelRefetchInterval(undefined)).toBe(PANEL_POLL_INTERVAL_MS);
  });

  it("stops on a refusal, because it will be the same refusal in ten seconds", () => {
    expect(panelRefetchInterval(new APIError("gone", 404))).toBe(false);
  });

  it("keeps polling through a server error, which may not be the same in ten seconds", () => {
    expect(panelRefetchInterval(new APIError("boom", 503))).toBe(PANEL_POLL_INTERVAL_MS);
  });

  it("does not stop merely because every review is terminal", async () => {
    await casePanelApi.cancel(CASE, REVIEW, "done");
    const view = await casePanelApi.read(CASE);

    expect(view.reviews[0].state).toBe("CANCELLED");
    // The workflow is still running and its timers still move. Stopping here
    // would freeze the screen at the moment the associate handed off.
    expect(panelRefetchInterval(undefined)).toBe(PANEL_POLL_INTERVAL_MS);
  });
});

describe("the actions", () => {
  it("approves with the versions the associate read", async () => {
    const view = await casePanelApi.read(CASE);
    const review = view.reviews[0];

    const result = await casePanelApi.approve(CASE, REVIEW, {
      draft_version: review.draft_version,
      canonical_edit_version: review.canonical_edit_version,
      canonical_approved_payload_hash: "0".repeat(64),
    });

    expect(result.state).toBe("APPROVING");
    expect(result.signal_id).toBeTruthy();
  });

  it("surfaces the transition on a second approval, not a bare 409", async () => {
    const body = {
      draft_version: 1,
      canonical_edit_version: 0,
      canonical_approved_payload_hash: "0".repeat(64),
    };
    await casePanelApi.approve(CASE, REVIEW, body);

    const error = await casePanelApi.approve(CASE, REVIEW, body).catch((thrown: unknown) => thrown);
    const conflict = asReviewConflict(error);

    // The state is the point. "This review is already being sent" is
    // actionable; "409 Conflict" is not, and an associate who sees the second
    // thing presses the button again.
    expect(conflict?.state).toBe("APPROVING");
    expect(conflict?.code).toBe("ReviewStateError");
  });

  it("names the field that moved on a stale autosave", async () => {
    const error = await casePanelApi
      .saveEdit(CASE, REVIEW, {
        client_edit_id: "c-1",
        base_draft_version: 99,
        payload: {},
      })
      .catch((thrown: unknown) => thrown);

    expect(asReviewConflict(error)?.field).toBe("base_draft_version");
  });

  it("reads back an autosave, which is what restore depends on", async () => {
    const empty = await casePanelApi.readEditState(CASE, REVIEW);
    // "You have not edited this" and "you edited it to nothing" are different
    // answers, and the restore path depends on telling them apart.
    expect(empty.payload).toBeNull();

    await casePanelApi.saveEdit(CASE, REVIEW, {
      client_edit_id: "c-1",
      base_draft_version: 1,
      payload: { subject: "my wording" },
    });
    const restored = await casePanelApi.readEditState(CASE, REVIEW);

    expect(restored.payload).toEqual({ subject: "my wording" });
  });

  it("mints a new attempt on redraft", async () => {
    const result = await casePanelApi.redraft(CASE, REVIEW);

    expect(result.review_id).not.toBe(REVIEW);
    expect(result.state).toBe("OPEN");

    const view = await casePanelApi.read(CASE);
    expect(view.reviews.map((review) => review.state)).toContain("CANCELLED");
  });

  it("returns null from asReviewConflict for anything that is not one", () => {
    // Otherwise every failure would render as a resolvable conflict, and the
    // panel would offer Resolve for a network outage.
    expect(asReviewConflict(new APIError("boom", 500))).toBeNull();
    expect(asReviewConflict(new Error("boom"))).toBeNull();
    expect(asReviewConflict(null)).toBeNull();
  });
});
