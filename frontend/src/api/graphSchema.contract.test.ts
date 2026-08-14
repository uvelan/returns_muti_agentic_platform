import { afterEach, expect, it, vi } from "vitest";

import type { components } from "./generated/return-platform";
import { graphSchemaApi, type DraftStatus, type SessionStatus } from "./graphSchema";

/**
 * The hand-written analyzer mirror must say what the backend says.
 *
 * `graphSchema.ts` is hand-written because the analyzer returns its view models
 * unwrapped rather than in the `APIResponse` envelope, and its header has always
 * claimed "Nothing is invented". It was: `SessionStatus` named three statuses
 * the backend has never had (`AWAITING_CLARIFICATION`, `PROPOSING`,
 * `AWAITING_REVIEW`) and omitted `DRAFT`, the status every session is created
 * with, while `DraftStatus` added a `SUPERSEDED` borrowed from the unrelated
 * *release* lifecycle. The generated mirror had the right values the whole time,
 * so the app shipped two client mirrors that disagreed and nothing said so.
 *
 * Two assertions, because they fail at different moments and catch different
 * mistakes:
 *
 * 1. The type-level pair below is checked by `tsc -b`, so a hand edit that
 *    reintroduces an invented member cannot compile.
 * 2. The runtime check reads the generated file as text and compares the
 *    *members*, which is what catches the other direction: a backend that adds
 *    a status, regenerates, and leaves this file behind.
 */

/** Fails to compile unless `A` and `B` are the same union. */
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;

const sessionStatusMatches: Same<SessionStatus, components["schemas"]["SessionStatus"]> = true;
const draftStatusMatches: Same<DraftStatus, components["schemas"]["DraftStatus"]> = true;

/**
 * The generated declarations as text.
 *
 * Read as a raw module rather than through `node:fs` for the reason
 * `noVersionedPaths.test.ts` gives: the test then runs under the same
 * browser-targeted tsconfig as the code it inspects.
 */
const generated: string = import.meta.glob<string>(["./generated/return-platform.d.ts"], {
  query: "?raw",
  import: "default",
  eager: true,
})["./generated/return-platform.d.ts"];

/** The quoted members of one `Name: "A" | "B";` enum line in the generated file. */
function generatedEnum(name: string): readonly string[] {
  const line = new RegExp(`^\\s*${name}: (".*");$`, "m").exec(generated);
  if (line === null) throw new Error(`${name} is not declared in the generated contract`);
  return [...line[1].matchAll(/"([^"]+)"/g)].map((match) => match[1]);
}

it.each([
  // Written out rather than derived from the type: a test that read the same
  // source as the thing under test would agree with itself.
  {
    name: "SessionStatus",
    expected: [
      "DRAFT",
      "DISCOVERING",
      "ANALYZING",
      "NEEDS_CLARIFICATION",
      "NEEDS_HUMAN_REVIEW",
      "READY_FOR_APPROVAL",
      "APPROVED",
      "ABANDONED",
      "FAILED",
    ],
  },
  { name: "DraftStatus", expected: ["DRAFT", "VALIDATED", "APPROVED"] },
])("$name mirrors the generated backend contract", ({ name, expected }) => {
  expect(generatedEnum(name)).toEqual(expected);
});

it("asserts the mirrors at the type level too", () => {
  // The values are `true` by construction; referencing them is what keeps
  // `noUnusedLocals` from deleting the compile-time half of this test.
  expect([sessionStatusMatches, draftStatusMatches]).toEqual([true, true]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

it("surfaces object-shaped FastAPI detail messages", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
    JSON.stringify({
      detail: {
        code: "DRAFT_CONFLICT",
        message: "The draft must be validated before approval.",
      },
    }),
    {
      status: 409,
      statusText: "Conflict",
      headers: { "Content-Type": "application/json" },
    },
  ));

  await expect(graphSchemaApi.getDraft("draft-1")).rejects.toMatchObject({
    message: "The draft must be validated before approval.",
    status: 409,
  });
});
