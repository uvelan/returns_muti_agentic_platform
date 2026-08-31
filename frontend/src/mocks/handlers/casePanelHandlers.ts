import { HttpResponse, delay, http } from "msw";

import { supportPanelSections } from "./supportHandlers";

/**
 * The case panel and its review endpoints, for `npm run dev:mock` and tests.
 *
 * **Stateful on purpose, and this is the one thing worth reading before the
 * fixtures.** A panel whose handler returned the same body forever would let a
 * developer look at the screen and nothing else: approving would appear to do
 * nothing, the conflict banner would never clear, and the autosave/restore path
 * -- which is most of what an associate does here -- could not be walked at
 * all. So the mutations mutate a module-level store and the panel composes from
 * it, exactly as the backend composes from Mongo.
 *
 * The ETag is derived from the composed body rather than from a counter, for
 * the reason DR-10 gives: it has to move when the panel moves and hold still
 * when it does not, and a counter would fail the second half.
 *
 * **The clarification answer route lives here too**, at the bottom. It had its
 * own file for as long as it had to: its router was written and tested but
 * unmounted, so the route was absent from the committed OpenAPI and folding it
 * into this array would have broken `casePanelHandlers.contract.test.ts`'s
 * published-route check -- a check that was doing its job. The integration pass
 * mounted it and regeneration published it, so it now sits in the array whose
 * contract test validates every body against the document, which is the only
 * place a mock body is actually held to the server's shape.
 *
 * Excluded from the production bundle by the mock-mode gate in `main.tsx`;
 * `scripts/check-bundle.js` fails the build if a mock artifact leaks.
 */

const CASE_ID = "case-mock-2026";
const REQUEST_ID = "support:case-mock-2026";

type Review = {
  review_id: string;
  review_kind: string;
  scope_id: string;
  request_id: string;
  state: string;
  draft_version: number;
  canonical_edit_version: number;
  conflict_present: boolean;
  draft: Record<string, unknown>;
  gaps: readonly Record<string, unknown>[];
  approved_by: string | null;
  approved_at_iso: string | null;
  recovery_status: string | null;
  last_delivery_error_code: string | null;
  hold_reason: string | null;
  abandon_audit: Record<string, unknown> | null;
};

type EditRow = {
  edit_version: number;
  base_draft_version: number;
  client_edit_id: string;
  payload: Record<string, unknown>;
};

function seedDraft(): Record<string, unknown> {
  return {
    template_id: "support_handoff",
    variant_id: "default",
    subject: "Return authorisation request - Apex Mechanical",
    text: "",
    gaps: [],
    sections: [
      {
        section_id: "order",
        title: "ORDER",
        return_record_id: null,
        fields: [
          {
            field_id: "order_number",
            label: "Order Number",
            value: "SO-441207",
            source: "case_fact",
            source_path: "confirmed_order_reference",
            fact_id: "fact-order-1",
            applied_fallback: false,
          },
          {
            field_id: "customer_name",
            label: "Customer",
            value: "Apex Mechanical",
            source: "case_fact",
            source_path: "confirmed_customer_name",
            fact_id: "fact-cust-1",
            applied_fallback: false,
          },
        ],
      },
      {
        section_id: "return_details",
        title: "RETURN DETAILS",
        return_record_id: null,
        fields: [
          {
            field_id: "return_reason",
            label: "Reason",
            value: "Motor arrived damaged in transit",
            source: "case_fact",
            source_path: "confirmed_return_reason",
            fact_id: "fact-reason-1",
            applied_fallback: false,
          },
          {
            field_id: "requested_action",
            label: "Requested Action",
            value: "Please issue an RMA and a return label.",
            source: "literal",
            source_path: "literal",
            fact_id: null,
            applied_fallback: false,
          },
        ],
      },
    ],
  };
}

type Store = {
  reviews: Review[];
  edits: Map<string, EditRow>;
  acceptedCommands: {
    signal_id: string;
    kind: string;
    actor_id: string;
    review_id: string | null;
    recorded_at_iso: string | null;
    applied: boolean;
  }[];
};

function freshStore(): Store {
  return {
    reviews: [
      {
        review_id: "review-mock-1",
        review_kind: "TEMPLATE",
        scope_id: REQUEST_ID,
        request_id: REQUEST_ID,
        state: "OPEN",
        draft_version: 1,
        canonical_edit_version: 0,
        conflict_present: false,
        draft: seedDraft(),
        gaps: [],
        approved_by: null,
        approved_at_iso: null,
        recovery_status: null,
        last_delivery_error_code: null,
        hold_reason: null,
        abandon_audit: null,
      },
    ],
    edits: new Map(),
    acceptedCommands: [],
  };
}

let store = freshStore();

/**
 * The answers on file, keyed by clarification id.
 *
 * Deliberately tiny -- one answered-set -- because the one piece of behaviour
 * worth walking in `dev:mock` is the **second** submission: the endpoint answers
 * 202 with `duplicate: true` rather than 409 for a repeat of the same answer,
 * and the form says "the answer on file stands" instead of treating it as a
 * failure. Kept out of `Store` because nothing composes it into the panel.
 */
const answeredClarifications = new Map<
  string,
  { answerText: string; recordId: string | null }
>();

/** Test seam. `dev:mock` never calls it; a test that wants a clean panel does. */
export function resetCasePanelMocks(): void {
  store = freshStore();
  answeredClarifications.clear();
}

const ACTOR = "associate-mock";

/** The absolute instant the panel counts down from. Never a duration. */
const DEADLINE_ISO = new Date(Date.now() + 45 * 60_000).toISOString();

function findReview(reviewId: string): Review | undefined {
  return store.reviews.find((review) => review.review_id === reviewId);
}

function panelBody() {
  return {
    case_id: CASE_ID,
    execution: {
      status: "ok",
      reason: null,
      case_status: "AWAITING_TEMPLATE_REVIEW",
      work_item_id: null,
      awaiting: ["SUPPORT"],
      business_complete: false,
      parked_reason: null,
    },
    reviews: store.reviews,
    return_records: [
      {
        return_record_id: "rec-mock-1",
        return_reference: "RMA-88120",
        status: "OPEN",
        return_method: "PARCEL",
      },
    ],
    timers: {
      template_review_deadline_iso: DEADLINE_ISO,
      template_review_reminders_sent: 1,
      template_review_max_reminders: 3,
      support_deadline_iso: null,
    },
    accepted_commands: store.acceptedCommands,
    /*
     * **Composed from every contributing slice, exactly as the backend registry
     * composes it.** Two rules meet on this line and both survived the merge
     * that produced it:
     *
     * V2's: a second `GET .../panel` handler is not an option. MSW takes the
     * first match, so a second one would shadow this handler and silently take
     * the reviews off the screen.
     *
     * V3's: one element per contributing slice, and **merges compose rather
     * than replace**. Taking either side of a conflict here drops a slice's
     * section from `dev:mock` while both suites stay green -- nothing asserts
     * that a section it has never heard of is present. Add the element; do not
     * swap the array.
     *
     * This is the **only** place a contributed section appears in this body.
     * There used to be top-level `clarifications`, `support_digest` and
     * `parked_messages` keys above, hardcoded empty and pointedly not where
     * V3's and V2's sections lived, because no registered contributor could
     * write a top-level field. AMENDMENT-6 retired all three from
     * `CasePanelView`, and `schemaConformance`'s `additionalProperties: false`
     * now makes re-adding one to this mock a red test rather than a comment.
     *
     * Each payload is opaque to this file by design -- the seam is a JSON object
     * precisely so V2's and V3's shapes never enter V1's DTO -- and each slice's
     * own tests own its shape.
     */
    sections: [
      ...supportPanelSections(),
      {
        section_id: "clarifications",
        status: "ok",
        reason: null,
        payload: {
          clarifications: [
            {
              clarificationId: "clar-mock-1",
              verbatimQuestion:
                "Support gave a tracking number (1Z999AA10123456784) for a return this case does not hold. Map it to one of this case's returns, or reject it.",
              whyUnresolvable: "the named return reference is not on this case",
              neededField: "TRACKING_NUMBER",
              resolutionAttempts: ["UNMATCHED"],
              supportEventId: "evt-mock-1",
              artifactValue: "1Z999AA10123456784",
              evidenceSpan: "RMA-99999",
              candidateRecordIds: ["rec-mock-1"],
              choice: "MAP_OR_REJECT",
            },
          ],
        },
      },
    ],
  };
}

/**
 * A stable digest of the composed body.
 *
 * Not cryptographic and does not need to be -- the ETag's only job here is to
 * be equal for equal panels and different for different ones. Deriving it from
 * the bytes rather than from a mutation counter is what makes the 304 path
 * exercisable: a counter would move on a write that changed nothing.
 */
function etagFor(body: unknown): string {
  const text = JSON.stringify(body);
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `"mock-${(hash >>> 0).toString(16)}"`;
}

function envelope<T>(data: T, requestId: string) {
  return {
    data,
    meta: {
      schema_version: "1.0",
      request_id: `mock-${requestId}`,
      generated_at: new Date().toISOString(),
      freshness: "LIVE",
      partial: false,
      warnings: [],
    },
  };
}

function actionResult(review: Review, signalId: string | null) {
  return {
    review_id: review.review_id,
    state: review.state,
    draft_version: review.draft_version,
    canonical_edit_version: review.canonical_edit_version,
    signal_id: signalId,
    duplicate: false,
  };
}

function recordCommand(kind: string, reviewId: string): string {
  const signalId = `${kind}:${reviewId}:${String(store.acceptedCommands.length + 1)}`;
  store.acceptedCommands.push({
    signal_id: signalId,
    kind,
    actor_id: ACTOR,
    review_id: reviewId,
    recorded_at_iso: new Date().toISOString(),
    applied: false,
  });
  return signalId;
}

function notFound(message: string) {
  return HttpResponse.json(
    { detail: { code: "REVIEW_NOT_FOUND", message, retryable: false } },
    { status: 404 },
  );
}

/** A refusal with no review to report the state of. */
function refusal(code: string, message: string, status: number, retryable = false) {
  return HttpResponse.json({ detail: { code, message, retryable } }, { status });
}

function conflict(code: string, message: string, review: Review) {
  return HttpResponse.json(
    { detail: { code, message, retryable: false, state: review.state } },
    { status: 409 },
  );
}

const PANEL_HEADERS = {
  "Cache-Control": "private, no-cache",
  Vary: "Authorization",
};

export const casePanelHandlers = [
  /**
   * The panel, with its ETag and a real 304.
   *
   * The conditional half is mocked rather than skipped because it is the half
   * a poll spends most of its life in: an associate reading a draft for two
   * minutes revalidates twelve times and gets a body once.
   */
  http.get("/api/v1/cases/:caseId/panel", async ({ request }) => {
    await delay(30);
    const body = envelope(panelBody(), "case-panel");
    const etag = etagFor(body.data);
    if (request.headers.get("If-None-Match") === etag) {
      return new HttpResponse(null, {
        status: 304,
        headers: { ETag: etag, ...PANEL_HEADERS },
      });
    }
    return HttpResponse.json(body, { headers: { ETag: etag, ...PANEL_HEADERS } });
  }),

  /** This actor's private row. `private, no-store`, and never in the panel. */
  http.get("/api/v1/cases/:caseId/reviews/:reviewId/edit-state", async ({ params }) => {
    await delay(20);
    const reviewId = String(params.reviewId);
    const row = store.edits.get(reviewId);
    return HttpResponse.json(
      envelope(
        {
          review_id: reviewId,
          actor_id: ACTOR,
          edit_version: row?.edit_version ?? null,
          base_draft_version: row?.base_draft_version ?? null,
          client_edit_id: row?.client_edit_id ?? null,
          payload: row?.payload ?? null,
        },
        "edit-state",
      ),
      { headers: { "Cache-Control": "private, no-store" } },
    );
  }),

  http.put(
    "/api/v1/cases/:caseId/reviews/:reviewId/edit-state",
    async ({ params, request }) => {
      await delay(20);
      const reviewId = String(params.reviewId);
      const review = findReview(reviewId);
      if (!review) return notFound(`Review ${reviewId} does not exist.`);
      const body = (await request.json()) as {
        client_edit_id: string;
        base_draft_version: number;
        payload: Record<string, unknown>;
      };
      if (body.base_draft_version !== review.draft_version) {
        return HttpResponse.json(
          {
            detail: {
              code: "ReviewVersionMismatchError",
              message: "The draft was re-rendered while you were editing it.",
              retryable: false,
              state: review.state,
              field: "base_draft_version",
            },
          },
          { status: 409 },
        );
      }
      const held = store.edits.get(reviewId);
      const row: EditRow = {
        edit_version: (held?.edit_version ?? 0) + (held?.client_edit_id === body.client_edit_id ? 0 : 1),
        base_draft_version: body.base_draft_version,
        client_edit_id: body.client_edit_id,
        payload: body.payload,
      };
      store.edits.set(reviewId, row);
      return HttpResponse.json(
        envelope(
          {
            review_id: reviewId,
            actor_id: ACTOR,
            edit_version: row.edit_version,
            base_draft_version: row.base_draft_version,
            client_edit_id: row.client_edit_id,
            payload: row.payload,
          },
          "edit-state-write",
        ),
        { headers: { "Cache-Control": "private, no-store" } },
      );
    },
  ),

  http.post(
    "/api/v1/cases/:caseId/reviews/:reviewId/edit-state/resolve",
    async ({ params, request }) => {
      await delay(20);
      const review = findReview(String(params.reviewId));
      if (!review) return notFound(`Review ${String(params.reviewId)} does not exist.`);
      const body = (await request.json()) as { canonical_payload: Record<string, unknown> };
      review.draft = body.canonical_payload;
      review.canonical_edit_version += 1;
      review.conflict_present = false;
      return HttpResponse.json(envelope(actionResult(review, null), "resolve"));
    },
  ),

  http.post("/api/v1/cases/:caseId/reviews/:reviewId/approve", async ({ params }) => {
    await delay(40);
    const review = findReview(String(params.reviewId));
    if (!review) return notFound(`Review ${String(params.reviewId)} does not exist.`);
    if (review.state !== "OPEN") {
      return conflict("ReviewStateError", "This review is already being sent.", review);
    }
    if (review.conflict_present) {
      return conflict(
        "ReviewConflictError",
        "Somebody else has edited this draft. Resolve the difference before sending.",
        review,
      );
    }
    review.state = "APPROVING";
    review.approved_by = ACTOR;
    review.approved_at_iso = new Date().toISOString();
    return HttpResponse.json(
      envelope(actionResult(review, recordCommand("template_approved", review.review_id)), "approve"),
    );
  }),

  http.post("/api/v1/cases/:caseId/reviews/:reviewId/revise", async ({ params }) => {
    await delay(30);
    const review = findReview(String(params.reviewId));
    if (!review) return notFound(`Review ${String(params.reviewId)} does not exist.`);
    if (review.state !== "OPEN") {
      return conflict("ReviewStateError", "This review is already being sent.", review);
    }
    return HttpResponse.json(
      envelope(actionResult(review, recordCommand("template_revised", review.review_id)), "revise"),
    );
  }),

  http.post("/api/v1/cases/:caseId/reviews/:reviewId/cancel", async ({ params }) => {
    await delay(30);
    const review = findReview(String(params.reviewId));
    if (!review) return notFound(`Review ${String(params.reviewId)} does not exist.`);
    review.state = "CANCELLED";
    return HttpResponse.json(
      envelope(actionResult(review, recordCommand("template_cancelled", review.review_id)), "cancel"),
    );
  }),

  http.post(
    "/api/v1/cases/:caseId/reviews/:reviewId/template-review/redraft",
    async ({ params }) => {
      await delay(40);
      const previous = findReview(String(params.reviewId));
      if (!previous) return notFound(`Review ${String(params.reviewId)} does not exist.`);
      previous.state = "CANCELLED";
      const fresh: Review = {
        ...previous,
        review_id: `review-mock-${String(store.reviews.length + 1)}`,
        state: "OPEN",
        draft_version: 1,
        canonical_edit_version: 0,
        conflict_present: false,
        draft: seedDraft(),
        approved_by: null,
        approved_at_iso: null,
      };
      store.reviews.push(fresh);
      return HttpResponse.json(
        envelope(actionResult(fresh, recordCommand("template_revised", fresh.review_id)), "redraft"),
      );
    },
  ),

  http.post("/api/v1/cases/:caseId/reviews/:reviewId/recovery/retry", async ({ params }) => {
    await delay(40);
    const review = findReview(String(params.reviewId));
    if (!review) return notFound(`Review ${String(params.reviewId)} does not exist.`);
    review.state = "APPROVING";
    review.recovery_status = null;
    return HttpResponse.json(
      envelope(
        actionResult(review, recordCommand("review_delivery_retry", review.review_id)),
        "retry",
      ),
    );
  }),

  http.post("/api/v1/cases/:caseId/reviews/:reviewId/recovery/abandon", async ({ params, request }) => {
    await delay(30);
    const review = findReview(String(params.reviewId));
    if (!review) return notFound(`Review ${String(params.reviewId)} does not exist.`);
    const body = (await request.json()) as { reason: string };
    review.state = "ABANDONED";
    review.recovery_status = null;
    review.abandon_audit = {
      actor_id: ACTOR,
      reason: body.reason,
      at_iso: new Date().toISOString(),
    };
    return HttpResponse.json(envelope(actionResult(review, null), "abandon"));
  }),

  /**
   * Answering one clarification.
   *
   * The strictness below is not decoration. `ClarificationAnswerRequest` is
   * `extra="forbid"`, so this mock forbids too: a permissive one is how a client
   * ships a fourth key and meets its first 422 in production, and no amount of
   * testing against an accommodating mock would ever reach it. The contract test
   * validates what this *returns*; only the mock can refuse what it is *sent*.
   */
  http.post(
    "/api/v1/cases/:caseId/clarifications/:clarificationId/answer",
    async ({ params, request }) => {
      await delay(40);
      const caseId = String(params.caseId);
      const clarificationId = String(params.clarificationId);
      const body = (await request.json()) as {
        answerText?: unknown;
        resolutionChoice?: unknown;
        returnRecordId?: unknown;
      };

      const unknownKeys = Object.keys(body).filter(
        (key) => !["answerText", "resolutionChoice", "returnRecordId"].includes(key),
      );
      if (unknownKeys.length > 0) {
        return refusal(
          "UNPROCESSABLE_ENTITY",
          `Unexpected field(s): ${unknownKeys.join(", ")}.`,
          422,
        );
      }

      const answerText = typeof body.answerText === "string" ? body.answerText.trim() : "";
      if (answerText.length === 0 || answerText.length > 4_000) {
        return refusal(
          "UNPROCESSABLE_ENTITY",
          "The answer must be between 1 and 4000 characters.",
          422,
        );
      }

      const choice = body.resolutionChoice;
      if (choice !== null && choice !== undefined && choice !== "map" && choice !== "reject") {
        return refusal("UNPROCESSABLE_ENTITY", "The choice must be map or reject.", 422);
      }

      const recordId = typeof body.returnRecordId === "string" ? body.returnRecordId : null;
      if (choice === "map" && recordId === null) {
        // The refusal that is not a convenience: "map this to nothing" is not a
        // decision anybody can have meant, and a later step inventing a record
        // for it is the create-from-a-loose-artifact behaviour §4 forbids.
        return refusal(
          "CLARIFICATION_MAP_WITHOUT_RECORD",
          "Mapping this artifact needs the return it belongs to.",
          422,
        );
      }

      const held = answeredClarifications.get(clarificationId);
      const duplicate = held?.answerText === answerText && held.recordId === recordId;
      if (held !== undefined && !duplicate) {
        // A *different* answer to an already-answered clarification is the 409;
        // the same answer again is a retry and is still a 202.
        return refusal(
          "CLARIFICATION_ALREADY_ANSWERED",
          "This clarification was already answered. The answer on file stands.",
          409,
        );
      }
      answeredClarifications.set(clarificationId, { answerText, recordId });

      return HttpResponse.json(
        envelope(
          {
            caseId,
            clarificationId,
            commandId: `cmd-${clarificationId}`,
            signalId: `clarification_answered:${clarificationId}`,
            outboxCommandId: `obx-${clarificationId}`,
            duplicate,
          },
          "clarification-answer",
        ),
        // 202, not 200. When this resolves a command is on file and a delivery
        // row is queued; the fact, the relay and the deadline reset all happen
        // after the signal reaches the workflow.
        { status: 202 },
      );
    },
  ),
];
