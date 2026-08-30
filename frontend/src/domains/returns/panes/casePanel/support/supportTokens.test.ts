import { describe, expect, it } from "vitest";

import { COPILOT_TOKENS, PENDING_LABEL } from "../../../copilotTokens";

/**
 * The `support` token group's own rules, asserted rather than intended.
 *
 * `copilotTokens.ts` states three rules in prose at the top of the file and in
 * the `review` group's comments -- every value is an M3 role, no hex, and
 * `text-xs` is the floor -- and until this file nothing checked them. A design
 * system whose constraints live only in comments is a style guide, not a
 * system: the next person adding a token reads the neighbouring value, not the
 * paragraph above it.
 *
 * Scoped to `support` on purpose. Widening it to the whole object would make
 * this V2's test of V1's tokens, and a failure in it would land on whoever
 * touched `review` next.
 */

const SUPPORT = COPILOT_TOKENS.support;

/** Every string in the group, with the path it was reached by. */
function tokenEntries(
  node: unknown,
  path: readonly string[] = [],
): readonly { path: string; value: string }[] {
  if (typeof node === "string") return [{ path: path.join("."), value: node }];
  if (typeof node !== "object" || node === null) return [];
  return Object.entries(node).flatMap(([key, child]) => tokenEntries(child, [...path, key]));
}

describe("the support token group", () => {
  it("reaches every token in the group", () => {
    // A walker that silently found nothing would make every rule below vacuous
    // -- the exact shape of green-but-blind this suite is judged on. Pinned as
    // a full sorted list rather than a count, so a token that is added without
    // being considered against the rules fails here first.
    expect(tokenEntries(SUPPORT).map((entry) => entry.path).sort()).toEqual([
      "announcer",
      "card",
      "cardHeader",
      "chip",
      "chipTone.attention",
      "chipTone.neutral",
      "chipTone.parked",
      "digestRow",
      "liveRegion",
      "notice",
      "reference",
      "row",
      "systemEntry",
      "systemEntryKicker",
      "term",
      "value",
      "warning",
    ]);
  });

  it("names no colour by hex", () => {
    // The rule the whole file exists for: a hex is a colour that a theme change
    // cannot reach, so one hex here is one element that stays light when the
    // console goes dark.
    const offenders = tokenEntries(SUPPORT)
      .filter((entry) => /#[0-9a-f]{3,8}\b/i.test(entry.value))
      .map((entry) => `${entry.path}: ${entry.value}`);
    expect(offenders).toEqual([]);
  });

  it("sets no text size under the file's own 0.75rem floor", () => {
    // `text-xs` is 0.75rem and is the floor stated at the top of
    // `copilotTokens.ts`. An arbitrary-value size is how the review group's
    // provenance chip first broke it (`text-[0.6875rem]`), so both spellings
    // are checked: the named scale below `xs`, and a bracketed rem/px value.
    const offenders = tokenEntries(SUPPORT)
      .filter((entry) => {
        if (/\btext-\[(\d*\.?\d+)rem\]/.test(entry.value)) {
          const rem = Number(/\btext-\[(\d*\.?\d+)rem\]/.exec(entry.value)?.[1] ?? "1");
          return rem < 0.75;
        }
        if (/\btext-\[(\d+)px\]/.test(entry.value)) {
          return Number(/\btext-\[(\d+)px\]/.exec(entry.value)?.[1] ?? "16") < 12;
        }
        return /\btext-(?:\[?2?xs\]?-|text-\[0)/.test(entry.value);
      })
      .map((entry) => `${entry.path}: ${entry.value}`);
    expect(offenders).toEqual([]);
  });

  it("gives every chip tone both a ground and a foreground", () => {
    // A tone that set only `bg-` would inherit whatever text colour its parent
    // happened to have, which is how a chip ends up unreadable on one surface
    // and fine on another -- and the call site that reads correctly is never
    // the one that ships broken.
    for (const [tone, value] of Object.entries(SUPPORT.chipTone)) {
      expect(value, `${tone} has no ground`).toMatch(/\bbg-[a-z-]+\b/);
      expect(value, `${tone} has no foreground`).toMatch(/\btext-(?:on-|outline)[a-z-]*\b/);
    }
  });

  it("draws the deliberate-parking notice in a role that is not the error role", () => {
    // Not a palette preference. `nl_enabled: false` parks a message *on
    // purpose* (contracts sect. 5 -- never a 409): it is on file, counted, and
    // replayed in stream order when the switch flips. Painting a working
    // configuration in the error colour teaches an associate to discount the
    // error colour on the day it means something.
    expect(SUPPORT.notice).toContain("secondary-container");
    expect(SUPPORT.notice).not.toMatch(/\berror\b/);
    // And the converse, so this is a statement about the pair rather than about
    // one string: the do-not-mix warning *is* in the error role, because a
    // label filed against the wrong RMA is not recoverable by re-reading.
    expect(SUPPORT.warning).toMatch(/\berror\b/);
  });

  it("keeps the panel announcer inaudible to layout and audible to a reader", () => {
    // `sr-only` and nothing else: `hidden` would remove it from the
    // accessibility tree, which is the one outcome that makes the announcement
    // pointless while leaving every visual test green.
    expect(SUPPORT.announcer).toBe("sr-only");
    expect(SUPPORT.announcer).not.toContain("hidden");
  });

  it("uses one word for a value the platform has not been given", () => {
    // `ProgressTruthPane` declares its own `PENDING` and the fabrication guard
    // allowlists exactly this spelling in its `??`-fallback rule. A second
    // vocabulary would either fail that guard or -- worse -- pass it as a newly
    // invented word.
    expect(PENDING_LABEL).toBe("Pending");
  });
});
