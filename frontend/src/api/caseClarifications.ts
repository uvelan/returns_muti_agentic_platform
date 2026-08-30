import type { components } from "./generated/return-platform";
import { APIError, apiClient } from "./client";

/**
 * Answering one clarification (contracts.md sect. 9).
 *
 * `POST /api/v1/cases/{case_id}/clarifications/{clarification_id}/answer`.
 *
 * ---
 *
 * **Types are generated, never mirrored**, the same way `casePanel.ts` puts it.
 * They were hand-transcribed for exactly as long as they had to be: the router
 * existed and was tested but was absent from `main.py`, so the route was absent
 * from the committed document and there was nothing to generate from. The
 * integration pass mounted it, regeneration published it, and the tripwire in
 * `caseClarifications.contract.test.ts` -- which existed to fail on this very
 * day -- sent the transcriptions here to be deleted.
 *
 * What is *not* generated is the refusal vocabulary below. Which of three
 * refusals came back, and whether pressing the button again could plausibly do
 * anything, are decisions about the contract rather than its shape.
 */

/**
 * The two things an associate may do with an unmatched artifact.
 *
 * The document types `resolutionChoice` as a bare pattern-constrained string,
 * which `openapi-typescript` renders as `string | null`. The pattern is
 * `^(map|reject)$`, so the union below is that pattern read back as a type: it
 * is what the form's radio state is, and keeping it means a misspelt choice is
 * a compile error here rather than a 422 at the counter.
 */
export const MAP_CHOICE = "map";
export const REJECT_CHOICE = "reject";

export type ResolutionChoice = typeof MAP_CHOICE | typeof REJECT_CHOICE;

/**
 * `MAX_ANSWER_CHARACTERS` in `api/case_clarifications.py`, published as
 * `answerText.maxLength`.
 *
 * A `maxLength` is one of the keywords `openapi-typescript` cannot carry into a
 * type, so this stays a constant. The server **refuses** rather than truncates,
 * for the reason that module gives: the cut half of a truncated answer may be
 * the part that identified the record. So the form must refuse too, rather than
 * sending something it knows will bounce.
 */
export const MAX_ANSWER_CHARACTERS = 4_000;

/**
 * Exactly the three fields `ClarificationAnswerRequest` declares.
 *
 * No `actorId` and no `caseId`: the actor comes from the capability check and
 * the case from the path. A body that could name either would be a body that
 * could answer somebody else's clarification -- and the model is
 * `extra="forbid"`, so a fourth key is a 422 rather than a field ignored.
 *
 * `Required<>` because the two nullable fields carry `None` defaults, which
 * JSON Schema marks non-required and the generator therefore renders optional.
 * Omitting them and sending `null` are the same request; making the caller
 * write which one it means is what stops a dropped field from reading as a
 * deliberate `null`.
 */
export type ClarificationAnswerRequest = Required<
  components["schemas"]["ClarificationAnswerRequest"]
>;

/** Exactly the six fields `ClarificationAnswerAcceptedView` declares. */
export type ClarificationAnswerAccepted =
  components["schemas"]["ClarificationAnswerAcceptedView"];

export function clarificationAnswerPath(caseId: string, clarificationId: string): string {
  return `/api/v1/cases/${encodeURIComponent(caseId)}/clarifications/${encodeURIComponent(
    clarificationId,
  )}/answer`;
}

/**
 * A refusal this form can act on rather than only repeat.
 *
 * The three the endpoint raises are `CLARIFICATION_MAP_WITHOUT_RECORD` (422),
 * `CLARIFICATION_ALREADY_ANSWERED` (409) and `CASE_CLARIFICATION_NOT_FOUND`
 * (404), and each one means something different to the person at the counter:
 * the first is fixable here, the second means somebody already answered and the
 * first answer stands, and the third means this is not their case any more.
 * Showing a status code instead makes them press the button again.
 */
export type ClarificationRefusal = {
  readonly code: string;
  readonly message: string;
  readonly status: number;
  /** Whether pressing the button again could plausibly do anything. */
  readonly retryable: boolean;
};

export function asClarificationRefusal(error: unknown): ClarificationRefusal | null {
  if (!(error instanceof APIError)) return null;
  const detail: Record<string, unknown> =
    typeof error.detail === "object" && error.detail !== null
      ? (error.detail as Record<string, unknown>)
      : {};
  return {
    code: typeof detail.code === "string" ? detail.code : "CLARIFICATION_REFUSED",
    message: error.message,
    status: error.status,
    retryable: detail.retryable === true,
  };
}

export const caseClarificationsApi = {
  /**
   * Record one answer.
   *
   * `202`, not `200`: when this resolves, a command is on file and a delivery
   * row is queued. The fact, the relay to Support and the deadline reset all
   * happen after the signal reaches the workflow -- so the confirmation this
   * returns must not claim Support has seen it.
   */
  async answer(
    caseId: string,
    clarificationId: string,
    body: ClarificationAnswerRequest,
  ): Promise<ClarificationAnswerAccepted> {
    const response = await apiClient<ClarificationAnswerAccepted>(
      clarificationAnswerPath(caseId, clarificationId),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    if (!response.data) throw new APIError("The answer could not be recorded.", 202);
    return response.data;
  },
};
