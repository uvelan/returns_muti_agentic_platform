import type { components } from "./generated/return-platform";
import { APIError, apiClient } from "./client";

/**
 * The case panel, and every review action on it (contracts.md sect. 9).
 *
 * **Types are generated, never mirrored.** Everything below imports from
 * `./generated/return-platform`, which `npm run contracts:generate` emits from
 * the backend's own OpenAPI. A second spelling of `CasePanelView` in this file
 * would be a second thing to keep in step, and the whole point of the contract
 * gate is that there is one.
 *
 * What is *not* generated is the ETag cache and the poll rules further down.
 * Those are decisions about the contract -- when to ask again, what a 304
 * means to a caller who has not kept a copy -- rather than its shape.
 */

/* -------------------------------------------------------------------------
 * The shapes
 * ---------------------------------------------------------------------- */

/**
 * The backend serves every field. `openapi-typescript` renders a Pydantic
 * defaulted field as optional *and* nullable because JSON Schema marks it
 * non-required, but `CasePanelView` is a response model and FastAPI
 * serializes all of it -- so `undefined` is a value this API cannot produce,
 * and carrying it would make every reader narrow a third case that never
 * occurs. Same reasoning as `api/cases.ts`'s `Served<T>`, and the same helper
 * would be imported if it were exported; it is copied deliberately as a local
 * because widening `cases.ts`'s surface for one type alias is the larger
 * change.
 */
type Served<T> = T extends readonly (infer Element)[]
  ? readonly Served<Element>[]
  : T extends object
    ? { [K in keyof T]-?: Served<Exclude<T[K], undefined>> }
    : T;

export type CasePanelView = Served<components["schemas"]["CasePanelView"]>;
export type ReviewPanelView = Served<components["schemas"]["ReviewPanelView"]>;
export type PanelExecutionView = Served<components["schemas"]["PanelExecutionView"]>;
export type PanelTimersView = Served<components["schemas"]["PanelTimersView"]>;
export type PanelSectionView = Served<components["schemas"]["PanelSectionView"]>;
export type AcceptedCommandView = Served<components["schemas"]["AcceptedCommandView"]>;
export type EditStateResult = Served<components["schemas"]["EditStateResult"]>;
export type ReviewActionResult = Served<components["schemas"]["ReviewActionResult"]>;

export type ApproveReviewRequest = components["schemas"]["ApproveReviewRequest"];
export type EditStateRequest = components["schemas"]["EditStateRequest"];
export type ResolveEditRequest = components["schemas"]["ResolveEditRequest"];

/** The states a review can no longer be edited or cancelled from. */
export const IN_FLIGHT_REVIEW_STATES: readonly string[] = [
  "APPROVING",
  "SENT",
  "DELIVERY_FAILED",
  "HELD_FOR_OPERATIONS",
  "ABANDONED",
  "CANCELLED",
];

/** Reviews an associate can still act on. */
export function isEditable(review: ReviewPanelView): boolean {
  return review.state === "OPEN";
}

/** Reviews the recovery actions apply to (contracts.md sect. 6). */
export function isRecoverable(review: ReviewPanelView): boolean {
  return review.state === "DELIVERY_FAILED" || review.state === "HELD_FOR_OPERATIONS";
}

/* -------------------------------------------------------------------------
 * The ETag cache
 * ---------------------------------------------------------------------- */

/**
 * How often an open case's panel is re-read. `copilot.case_poll_interval_ms`
 * in the release; contracts.md sect. 9 makes it the **relay-visibility floor**,
 * which is to say the longest an associate can wait to see that Support
 * answered. Kept equal to the case projection's interval on purpose: two polls
 * for one screen at two cadences would make the panel and the record disagree
 * for up to the difference between them.
 */
export const PANEL_POLL_INTERVAL_MS = 10_000;

/**
 * The last body and its ETag, per case.
 *
 * **Module-scoped rather than per-caller**, because a 304 is only useful to
 * something that kept the bytes: the browser will not replay a body it did not
 * store, and a client that sent `If-None-Match` and then could not answer
 * would have to re-request without it. One case has one panel and every viewer
 * of it in this tab is looking at the same thing, which is exactly the
 * principal-independence the shared payload is built on -- so one entry per
 * case is correct rather than merely convenient.
 *
 * Cleared by `forgetPanel` when a query unmounts, so a long session does not
 * accumulate panels for cases nobody has open.
 */
const cache = new Map<string, { etag: string; view: CasePanelView }>();

export function forgetPanel(caseId: string): void {
  cache.delete(caseId);
}

/** Test seam. Named so its purpose is not mistaken for a cache warmer. */
export function resetPanelCacheForTests(): void {
  cache.clear();
}

type PanelEnvelope = { data?: CasePanelView | null };

/**
 * Read one case's panel, revalidating against the ETag we hold.
 *
 * **`apiClient` cannot be used here and that is the whole reason this
 * function exists.** It reads the JSON body unconditionally, and a 304 has no
 * body -- so the shared client would throw "Unable to read the API response"
 * on the successful half of the mechanism DR-10 chose.
 *
 * A 304 with nothing cached is not an error the caller should see: it means
 * the browser revalidated an entry this module dropped. The request is retried
 * once without the conditional header, which is the only way to get a body
 * back, and the retry is not conditional itself so it cannot loop.
 */
export async function readCasePanel(caseId: string): Promise<CasePanelView> {
  const held = cache.get(caseId);
  const answer = await requestPanel(caseId, held?.etag);
  if (answer.notModified) {
    if (held) return held.view;
    const again = await requestPanel(caseId, undefined);
    if (!again.view) throw new APIError("The case panel could not be read.", again.status);
    remember(caseId, again);
    return again.view;
  }
  if (!answer.view) throw new APIError("The case panel could not be read.", answer.status);
  remember(caseId, answer);
  return answer.view;
}

function remember(
  caseId: string,
  answer: { etag: string | null; view: CasePanelView | null },
): void {
  if (answer.etag && answer.view) cache.set(caseId, { etag: answer.etag, view: answer.view });
}

async function requestPanel(
  caseId: string,
  etag: string | undefined,
): Promise<{
  notModified: boolean;
  status: number;
  etag: string | null;
  view: CasePanelView | null;
}> {
  const headers = new Headers({ Accept: "application/json" });
  if (etag) headers.set("If-None-Match", etag);

  let response: Response;
  try {
    response = await fetch(`/api/v1/cases/${encodeURIComponent(caseId)}/panel`, { headers });
  } catch (error) {
    throw new APIError("Unable to reach the Return Platform API.", 0, undefined, {
      cause: error,
    });
  }

  if (response.status === 304) {
    return { notModified: true, status: 304, etag: response.headers.get("ETag"), view: null };
  }
  if (!response.ok) {
    throw new APIError(
      await panelErrorMessage(response),
      response.status,
      response.headers.get("X-Correlation-ID") ?? undefined,
    );
  }

  const envelope = (await response.json()) as PanelEnvelope;
  return {
    notModified: false,
    status: response.status,
    etag: response.headers.get("ETag"),
    view: envelope.data ?? null,
  };
}

async function panelErrorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: { message?: string } | string };
    if (typeof body.detail === "string") return body.detail;
    if (body.detail?.message) return body.detail.message;
  } catch {
    // A refusal with no JSON body is still a refusal; the status carries it.
  }
  return response.status === 404
    ? "This case is not available."
    : "The case panel could not be read.";
}

/**
 * Whether to keep polling this panel.
 *
 * Stops on a refusal for `caseRefetchInterval`'s reason -- a 403 or 404 will be
 * the same answer in ten seconds, and polling it is a request every ten seconds
 * for as long as the tab is open, to be told the same no.
 *
 * It does **not** stop on "every review is terminal". A case whose reviews have
 * all been sent is still a case the workflow is running, and the panel's
 * execution block, timers and (from V2) parked count keep moving. Stopping
 * there would freeze the screen at the moment the associate handed off.
 */
export function panelRefetchInterval(error?: unknown): number | false {
  if (error instanceof APIError && error.status >= 400 && error.status < 500) return false;
  return PANEL_POLL_INTERVAL_MS;
}

/* -------------------------------------------------------------------------
 * The actions
 * ---------------------------------------------------------------------- */

function reviewPath(caseId: string, reviewId: string, suffix: string): string {
  return `/api/v1/cases/${encodeURIComponent(caseId)}/reviews/${encodeURIComponent(reviewId)}${suffix}`;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await apiClient<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.data) throw new APIError("The action returned no result.", 200);
  return response.data;
}

/**
 * A 409 the UI can act on rather than only report.
 *
 * The backend puts the review's **state** in the body precisely so the panel
 * can say "this review is already approving" instead of "409 Conflict" --
 * contracts.md sect. 6 asks for the transition, and an associate who is shown
 * a status code presses the button again.
 */
export type ReviewConflict = {
  readonly code: string;
  readonly message: string;
  readonly state: string | null;
  readonly field: string | null;
};

export function asReviewConflict(error: unknown): ReviewConflict | null {
  if (!(error instanceof APIError) || error.status !== 409) return null;
  const detail: Record<string, unknown> =
    typeof error.detail === "object" && error.detail !== null
      ? (error.detail as Record<string, unknown>)
      : {};
  return {
    code: typeof detail.code === "string" ? detail.code : "CONFLICT",
    message: error.message,
    state: typeof detail.state === "string" ? detail.state : null,
    field: typeof detail.field === "string" ? detail.field : null,
  };
}

export const casePanelApi = {
  read: readCasePanel,

  /**
   * **This actor's** private edit row. Never in the shared panel body, never
   * in its hash, and served `private, no-store`.
   */
  async readEditState(caseId: string, reviewId: string): Promise<EditStateResult> {
    const response = await apiClient<EditStateResult>(
      reviewPath(caseId, reviewId, "/edit-state"),
    );
    if (!response.data) throw new APIError("The edit state could not be read.", 200);
    return response.data;
  },

  /**
   * One coalesced autosave. `client_edit_id` is the browser's id for this
   * keystroke batch, so a retry over a flaky connection is a no-op rather than
   * a version bump.
   */
  async saveEdit(
    caseId: string,
    reviewId: string,
    body: EditStateRequest,
  ): Promise<EditStateResult> {
    const response = await apiClient<EditStateResult>(
      reviewPath(caseId, reviewId, "/edit-state"),
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    if (!response.data) throw new APIError("The edit could not be saved.", 200);
    return response.data;
  },

  /** Select, merge or discard, resolved to one canonical payload. */
  resolveEdit(
    caseId: string,
    reviewId: string,
    body: ResolveEditRequest,
  ): Promise<ReviewActionResult> {
    return post<ReviewActionResult>(reviewPath(caseId, reviewId, "/edit-state/resolve"), body);
  },

  /** The three CAS values are the ones the associate read, never the store's. */
  approve(
    caseId: string,
    reviewId: string,
    body: ApproveReviewRequest,
  ): Promise<ReviewActionResult> {
    return post<ReviewActionResult>(reviewPath(caseId, reviewId, "/approve"), body);
  },

  revise(caseId: string, reviewId: string, note?: string): Promise<ReviewActionResult> {
    return post<ReviewActionResult>(reviewPath(caseId, reviewId, "/revise"), {
      note: note ?? null,
    });
  },

  cancel(caseId: string, reviewId: string, reason: string): Promise<ReviewActionResult> {
    return post<ReviewActionResult>(reviewPath(caseId, reviewId, "/cancel"), { reason });
  },

  redraft(caseId: string, reviewId: string): Promise<ReviewActionResult> {
    return post<ReviewActionResult>(reviewPath(caseId, reviewId, "/template-review/redraft"));
  },

  retryDelivery(
    caseId: string,
    reviewId: string,
    reason = "",
  ): Promise<ReviewActionResult> {
    return post<ReviewActionResult>(reviewPath(caseId, reviewId, "/recovery/retry"), { reason });
  },

  abandon(caseId: string, reviewId: string, reason: string): Promise<ReviewActionResult> {
    return post<ReviewActionResult>(reviewPath(caseId, reviewId, "/recovery/abandon"), {
      reason,
    });
  },
};

/** Query keys, in one place so an invalidation cannot miss a reader. */
export const casePanelKeys = {
  panel: (caseId: string) => ["case-panel", caseId] as const,
  editState: (caseId: string, reviewId: string) =>
    ["case-panel", caseId, "edit-state", reviewId] as const,
};
