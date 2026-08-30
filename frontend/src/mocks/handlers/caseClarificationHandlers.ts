import { HttpResponse, delay, http } from "msw";

/**
 * The clarification answer endpoint, for `npm run dev:mock` and tests.
 *
 * ---
 *
 * **Its own array, and that is the point rather than tidiness.**
 * `casePanelHandlers.contract.test.ts` asserts that every route in its array
 * names a path in the **committed OpenAPI document**, in both directions. This
 * route is not in that document: `api/case_clarifications.py` exists, is tested,
 * and is deliberately absent from `main.py` until the batched integration pass
 * mounts it (`.plan/merge.md`, integration debt, V3). Adding this handler to the
 * panel's array would therefore break a check that is doing its job.
 *
 * So the shapes below are transcribed from the router's own Pydantic models, the
 * same way `api/caseClarifications.ts` transcribes them, and
 * `caseClarifications.contract.test.ts` holds the tripwire: it **fails the day
 * the route appears in the committed document**, which is the day the
 * transcriptions must be deleted in favour of generated types.
 *
 * The store is deliberately tiny -- one answered-set -- because the one piece of
 * behaviour worth walking in `dev:mock` is the **second** submission: the
 * endpoint answers 202 with `duplicate: true` rather than 409 for a repeat of the
 * same answer, and the form says "the answer on file stands" instead of treating
 * it as a failure.
 */

const answered = new Map<string, { answerText: string; recordId: string | null }>();

/** Test seam, named so it is not mistaken for a warmer. */
export function resetCaseClarificationMocks(): void {
  answered.clear();
}

function meta(requestId: string) {
  return {
    schema_version: "1.0",
    request_id: `mock-${requestId}`,
    generated_at: new Date().toISOString(),
    freshness: "LIVE",
    partial: false,
    warnings: [],
  };
}

function refusal(code: string, message: string, status: number, retryable = false) {
  return HttpResponse.json({ detail: { code, message, retryable } }, { status });
}

export const caseClarificationHandlers = [
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

      /*
       * `extra="forbid"` on the server, so the mock forbids too. A permissive
       * mock is how a client ships an extra key and meets its first 422 in
       * production -- which is the entire reason `api/caseClarifications.ts`
       * transcribes the model field by field rather than approximating it.
       */
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

      const held = answered.get(clarificationId);
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
      answered.set(clarificationId, { answerText, recordId });

      return HttpResponse.json(
        {
          data: {
            caseId,
            clarificationId,
            commandId: `cmd-${clarificationId}`,
            signalId: `clarification_answered:${clarificationId}`,
            outboxCommandId: `obx-${clarificationId}`,
            duplicate,
          },
          meta: meta("clarification-answer"),
        },
        // 202, not 200. When this resolves a command is on file and a delivery
        // row is queued; the fact, the relay and the deadline reset all happen
        // after the signal reaches the workflow.
        { status: 202 },
      );
    },
  ),
];
