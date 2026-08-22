/**
 * The formatter that replaced five.
 *
 * What matters here is not the exact rendered string -- that is `Intl`'s job
 * and it varies with the runner's locale -- but the three decisions the module
 * makes that the private helpers disagreed about: absent is not unparseable,
 * the zone is named, and the clock is a parameter.
 */

import { describe, expect, it } from "vitest";

import {
  ABSENT,
  formatCompactTimestamp,
  formatDate,
  formatDuration,
  formatRelative,
  formatTimestamp,
} from "./datetime";

const MOMENT = "2026-08-22T09:30:00Z";

describe("formatTimestamp", () => {
  it("names the time zone, because the number is ambiguous without it", () => {
    // The old `toLocaleString()` produced a bare local time. Two operators read
    // the same SLA breach as two different moments and neither string said
    // which zone it was in.
    const rendered = formatTimestamp(MOMENT);
    expect(rendered).not.toBe(ABSENT);
    expect(rendered.trim().split(/\s+/).length).toBeGreaterThan(3);
  });

  it.each([null, undefined, ""])("renders %p as absent", (value) => {
    expect(formatTimestamp(value)).toBe(ABSENT);
  });

  it("shows an unparseable value rather than claiming the field was empty", () => {
    // The distinction the private helpers got right and a naive rewrite loses.
    // A dash here would tell the operator nothing was sent, when in fact
    // something wrong was, and they would have nothing to report.
    expect(formatTimestamp("not-a-date")).toBe("not-a-date");
  });

  it("accepts a Date as readily as an ISO string", () => {
    expect(formatTimestamp(new Date(MOMENT))).toBe(formatTimestamp(MOMENT));
  });
});

describe("formatCompactTimestamp and formatDate", () => {
  it("drop the zone and the time respectively, and keep the absent contract", () => {
    expect(formatCompactTimestamp(MOMENT)).not.toBe(ABSENT);
    expect(formatDate(MOMENT)).not.toBe(ABSENT);
    expect(formatCompactTimestamp(null)).toBe(ABSENT);
    expect(formatDate(null)).toBe(ABSENT);
  });

  it("renders the day without a time", () => {
    expect(formatDate(MOMENT)).not.toMatch(/\d{1,2}:\d{2}/);
  });
});

describe("formatRelative", () => {
  const now = Date.parse(MOMENT);

  it("takes the clock as an argument so a render can stay pure", () => {
    // Reading `Date.now()` inside the function would make every render produce
    // a different tree, which is the bug this signature exists to prevent.
    const past = new Date(now - 3 * 60_000).toISOString();
    expect(formatRelative(past, now)).toBe(formatRelative(past, now));
  });

  it.each([
    [3 * 60_000, "minute"],
    [5 * 60 * 60_000, "hour"],
    [3 * 24 * 60 * 60_000, "day"],
  ])("picks the largest unit that fits (%i ms)", (offset, unit) => {
    const rendered = formatRelative(new Date(now - offset).toISOString(), now);
    expect(rendered.toLowerCase()).toContain(unit);
  });

  it("reads forwards for a future moment", () => {
    const soon = new Date(now + 2 * 60 * 60_000).toISOString();
    expect(formatRelative(soon, now).toLowerCase()).toContain("hour");
    expect(formatRelative(soon, now).toLowerCase()).not.toContain("ago");
  });

  it("keeps the absent and unparseable contract", () => {
    expect(formatRelative(null, now)).toBe(ABSENT);
    expect(formatRelative("nonsense", now)).toBe("nonsense");
  });
});

describe("formatDuration", () => {
  it.each([
    [0, "0s"],
    [45, "45s"],
    [90, "1m 30s"],
    [3600, "1h 0m"],
    [7 * 3600 + 25 * 60, "7h 25m"],
  ])("renders %i seconds as %s", (seconds, expected) => {
    expect(formatDuration(seconds)).toBe(expected);
  });

  it("never renders a negative duration", () => {
    // A clock skew between the server and the browser produced these, and
    // "-3s elapsed" reads as a bug in the screen rather than in the clock.
    expect(formatDuration(-5)).toBe("0s");
  });

  it.each([null, undefined, Number.NaN, Number.POSITIVE_INFINITY])(
    "renders %p as absent",
    (value) => {
      expect(formatDuration(value)).toBe(ABSENT);
    },
  );
});
