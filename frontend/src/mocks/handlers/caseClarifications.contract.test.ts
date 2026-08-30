/**
 * What the clarification client sends, which no other check can see.
 *
 * This file used to open with a tripwire: a test that failed the day the answer
 * route reached the committed OpenAPI document, because on that day the
 * hand-written types in `api/caseClarifications.ts` stopped having a reason to
 * exist. It fired, the runbook in its failure message was carried out, and it
 * was deleted -- along with the separate handler array whose separateness it was
 * guarding. The mock now lives in `casePanelHandlers`, where
 * `casePanelHandlers.contract.test.ts` validates the body it *returns* against
 * the published schema like every other one.
 *
 * What survives here is the half that machinery cannot reach. Schema conformance
 * runs over responses; nothing in it looks at a request. So this file still
 * checks the thing a permissive mock cannot: that the client sends **exactly**
 * the three keys `ClarificationAnswerRequest` declares. That model is
 * `extra="forbid"`, so a fourth key is a 422 in production that no amount of
 * testing against an accommodating mock would ever reach.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  MAX_ANSWER_CHARACTERS,
  asClarificationRefusal,
  caseClarificationsApi,
  clarificationAnswerPath,
} from "../../api/caseClarifications";
import { resetCasePanelMocks } from "./casePanelHandlers";

const CASE = "case-mock-2026";
const CLARIFICATION = "clar-1";

beforeEach(() => {
  resetCasePanelMocks();
});

describe("the client sends exactly what the model declares", () => {
  it("builds the path the router declares, so client and mock cannot drift apart", () => {
    // The mock's half of this equality is pinned by the panel's ROUTES table,
    // which holds the handler path against the OpenAPI path in both directions.
    // This is the client's half, and the literal is the one in
    // `api/case_clarifications.py`'s decorator.
    expect(clarificationAnswerPath(CASE, CLARIFICATION)).toBe(
      `/api/v1/cases/${CASE}/clarifications/${CLARIFICATION}/answer`,
    );
  });

  it("posts the three fields and nothing else, and reads the six that come back", async () => {
    const accepted = await caseClarificationsApi.answer(CASE, CLARIFICATION, {
      answerText: "It is the pallet in bay 3.",
      resolutionChoice: "map",
      returnRecordId: "rec-2",
    });

    // Pinned whole. A field the endpoint stopped serving would still satisfy a
    // spot check of the two the form happens to read.
    expect(accepted).toEqual({
      caseId: CASE,
      clarificationId: CLARIFICATION,
      commandId: "cmd-clar-1",
      signalId: "clarification_answered:clar-1",
      outboxCommandId: "obx-clar-1",
      duplicate: false,
    });
  });

  it("is refused by the mock for an extra key, exactly as `extra=forbid` refuses it", async () => {
    // The mock is strict on purpose. A permissive one is how a client ships a
    // fourth key and meets its first 422 in production.
    const response = await fetch(clarificationAnswerPath(CASE, CLARIFICATION), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        answerText: "It is the pallet in bay 3.",
        resolutionChoice: "reject",
        returnRecordId: null,
        // The two most tempting: the actor comes from the capability check and
        // the case from the path. A body that could name either would be a body
        // that could answer somebody else's clarification.
        actorId: "associate-1",
        caseId: CASE,
      }),
    });
    expect(response.status).toBe(422);
  });

  it("refuses a map with no record, and the refusal survives as something to act on", async () => {
    await expect(
      caseClarificationsApi.answer(CASE, CLARIFICATION, {
        answerText: "That one.",
        resolutionChoice: "map",
        returnRecordId: null,
      }),
    ).rejects.toMatchObject({ status: 422 });
  });

  it("refuses an answer past the server's own limit rather than truncating it", async () => {
    // The server refuses rather than truncates because the cut half is often
    // the part that identified the record.
    await expect(
      caseClarificationsApi.answer(CASE, CLARIFICATION, {
        answerText: "x".repeat(MAX_ANSWER_CHARACTERS + 1),
        resolutionChoice: null,
        returnRecordId: null,
      }),
    ).rejects.toMatchObject({ status: 422 });
  });
});

describe("answering twice", () => {
  const answer = {
    answerText: "Not ours.",
    resolutionChoice: "reject" as const,
    returnRecordId: null,
  };

  it("treats the identical answer as the retry it is: 202, duplicate", async () => {
    expect((await caseClarificationsApi.answer(CASE, CLARIFICATION, answer)).duplicate).toBe(false);
    expect((await caseClarificationsApi.answer(CASE, CLARIFICATION, answer)).duplicate).toBe(true);
  });

  it("refuses a different second answer, and says which of the two refusals it is", async () => {
    await caseClarificationsApi.answer(CASE, CLARIFICATION, answer);
    const error = await caseClarificationsApi
      .answer(CASE, CLARIFICATION, { ...answer, answerText: "Actually it is RMA-88121." })
      .catch((reason: unknown) => reason);

    // The code, not the status: "somebody already answered and their answer
    // stands" and "you sent something malformed" are different things to do
    // next, and an associate shown "409" presses the button again.
    expect(asClarificationRefusal(error)).toEqual({
      code: "CLARIFICATION_ALREADY_ANSWERED",
      message: "This clarification was already answered. The answer on file stands.",
      status: 409,
      retryable: false,
    });
  });
});
