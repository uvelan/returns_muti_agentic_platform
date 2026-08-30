import { APIError, apiClient } from "./client";

/**
 * Answering one clarification (contracts.md sect. 9).
 *
 * `POST /api/v1/cases/{case_id}/clarifications/{clarification_id}/answer`.
 *
 * ---
 *
 * **The types here are hand-written, and that is a defect with a scheduled
 * fix rather than a preference.** Every other client module on this surface
 * imports `./generated/return-platform`, which `npm run contracts:generate`
 * emits from the backend's own OpenAPI -- `casePanel.ts` says so at the top and
 * is right to. This route is **not mounted yet**: `api/case_clarifications.py`
 * exists, is tested, and is absent from `main.py`, so it is absent from the
 * committed document and there is nothing to generate from.
 *
 * The shapes below are therefore transcribed from
 * `ClarificationAnswerRequest` and `ClarificationAnswerAcceptedView` in that
 * module, and `caseClarifications.contract.test.ts` **fails the moment the
 * route appears in the committed OpenAPI** -- which is the point. The
 * integration pass that mounts the router is told, by a red test, to come here
 * and delete these declarations in favour of the generated ones.
 *
 * Transcribing rather than approximating matters more than usual here because
 * the request model is `extra="forbid"`: one wrong key name is a 422 in
 * production that no amount of frontend testing against a permissive mock would
 * find.
 */

/** The two things an associate may do with an unmatched artifact. */
export const MAP_CHOICE = "map";
export const REJECT_CHOICE = "reject";

export type ResolutionChoice = typeof MAP_CHOICE | typeof REJECT_CHOICE;

/**
 * `MAX_ANSWER_CHARACTERS` in `api/case_clarifications.py`.
 *
 * The server **refuses** rather than truncates, for the reason that module
 * gives: the cut half of a truncated answer may be the part that identified the
 * record. So the form must refuse too, rather than sending something it knows
 * will bounce.
 */
export const MAX_ANSWER_CHARACTERS = 4_000;

/**
 * Exactly the three fields `ClarificationAnswerRequest` declares.
 *
 * No `actorId` and no `caseId`: the actor comes from the capability check and
 * the case from the path. A body that could name either would be a body that
 * could answer somebody else's clarification.
 */
export type ClarificationAnswerRequest = {
  readonly answerText: string;
  readonly resolutionChoice: ResolutionChoice | null;
  readonly returnRecordId: string | null;
};

/** Exactly the six fields `ClarificationAnswerAcceptedView` declares. */
export type ClarificationAnswerAccepted = {
  readonly caseId: string;
  readonly clarificationId: string;
  readonly commandId: string;
  readonly signalId: string;
  readonly outboxCommandId: string;
  readonly duplicate: boolean;
};

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
