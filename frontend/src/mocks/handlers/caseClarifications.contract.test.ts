/**
 * The clarification mocks, and a tripwire that goes off when they stop being
 * necessary.
 *
 * Every other client module on this surface imports `api/generated/return-platform`,
 * which `npm run contracts:generate` emits from the backend's own OpenAPI.
 * `api/caseClarifications.ts` cannot: `api/case_clarifications.py` exists, is
 * tested, and is **absent from `main.py`** until the batched integration pass
 * mounts it (`.plan/merge.md`, integration debt, V3), so the route is absent
 * from the committed document and there is nothing to generate from.
 *
 * Hand-written types are a defect with a scheduled fix rather than a preference,
 * and the first test below is that schedule made executable: it **fails the day
 * the route appears in the committed OpenAPI**, and its failure message says
 * what to do. Without it, the transcriptions would outlive their reason and
 * nobody would find out until the two drifted.
 *
 * The rest of the file checks the thing a permissive mock cannot: that the
 * client sends **exactly** the three keys `ClarificationAnswerRequest` declares.
 * That model is `extra="forbid"`, so a fourth key is a 422 in production that no
 * amount of testing against an accommodating mock would ever reach.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  MAX_ANSWER_CHARACTERS,
  asClarificationRefusal,
  caseClarificationsApi,
  clarificationAnswerPath,
} from "../../api/caseClarifications";
import type { OpenApiDocument } from "../../test/schemaConformance";
import {
  caseClarificationHandlers,
  resetCaseClarificationMocks,
} from "./caseClarificationHandlers";

const document = Object.values(
  import.meta.glob("../../../openapi/return-platform.openapi.json", {
    query: "?raw",
    import: "default",
    eager: true,
  }),
).map((raw) => JSON.parse(raw as string) as OpenApiDocument)[0];

const CONTRACT_PATH = "/api/v1/cases/{case_id}/clarifications/{clarification_id}/answer";
const CASE = "case-mock-2026";
const CLARIFICATION = "clar-1";

beforeEach(() => {
  resetCaseClarificationMocks();
});

describe("the tripwire", () => {
  it("fires when the answer route reaches the committed OpenAPI document", () => {
    const paths = (document as { paths?: Record<string, unknown> }).paths ?? {};
    expect(
      Object.keys(paths).includes(CONTRACT_PATH),
      [
        `${CONTRACT_PATH} is now published, which means this test has done its job.`,
        "",
        "Do three things and delete this test:",
        "  1. delete the hand-written types in `src/api/caseClarifications.ts` and",
        "     import `ClarificationAnswerRequest` / `ClarificationAnswerAcceptedView`",
        "     from `./generated/return-platform` instead;",
        "  2. move `caseClarificationHandlers` into `casePanelHandlers` (or add its",
        "     route to that file's ROUTES table) so the mock body is validated",
        "     against the published schema like every other one;",
        "  3. regenerate: `npm run contracts:generate`.",
      ].join("\n"),
    ).toBe(false);
  });

  it("registers the one route the router declares, at the path the router declares it", () => {
    // Pinned as an equality against the path the client builds, so the mock and
    // the client cannot drift apart while both stay internally consistent -- and
    // the literal is the one in `api/case_clarifications.py`'s decorator.
    const registered = caseClarificationHandlers.map((handler) => {
      const info = (handler as unknown as { info: { method: string; path: string } }).info;
      return `${info.method.toLowerCase()} ${info.path}`;
    });
    expect(registered).toEqual([
      "post /api/v1/cases/:caseId/clarifications/:clarificationId/answer",
    ]);
    expect(clarificationAnswerPath(CASE, CLARIFICATION)).toBe(
      `/api/v1/cases/${CASE}/clarifications/${CLARIFICATION}/answer`,
    );
  });
});

describe("the client sends exactly what the model declares", () => {
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
