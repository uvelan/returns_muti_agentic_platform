/**
 * A deadline has to say whether it has passed. Both screens that showed one
 * printed a raw ISO string, which says neither the zone nor the answer.
 */

import { describe, expect, it } from "vitest";

import { ABSENT } from "./datetime";
import { readSlaDue } from "./sla";

const NOW = Date.parse("2026-08-22T12:00:00Z");

describe("readSlaDue", () => {
  it("reports a passed deadline as breached, in words", () => {
    const reading = readSlaDue("2026-08-22T09:30:00Z", NOW);
    expect(reading.breached).toBe(true);
    // The word, not only a colour: tone alone fails an operator who cannot see
    // it and a screen reader that never renders it.
    expect(reading.text).toContain("breached");
  });

  it("reports a future deadline as due, with how long is left", () => {
    const reading = readSlaDue("2026-08-22T15:00:00Z", NOW);
    expect(reading.breached).toBe(false);
    expect(reading.text).toContain("due");
    expect(reading.text).toContain("hour");
  });

  it("treats the exact deadline as breached", () => {
    expect(readSlaDue("2026-08-22T12:00:00Z", NOW).breached).toBe(true);
  });

  it("names the zone, which the raw string never did", () => {
    const reading = readSlaDue("2026-08-22T09:30:00Z", NOW);
    expect(reading.text).not.toBe("2026-08-22T09:30:00Z");
    expect(reading.text.split("\u00b7")[0].trim().split(/\s+/).length).toBeGreaterThan(3);
  });

  it("says nothing about a deadline it does not have", () => {
    expect(readSlaDue(null, NOW)).toEqual({ text: ABSENT, breached: false });
  });

  it("does not claim a breach for a value it could not read", () => {
    // Showing the raw value is right -- something wrong was sent. Guessing a
    // breach state for it would be inventing an answer.
    const reading = readSlaDue("not-a-date", NOW);
    expect(reading.breached).toBe(false);
    expect(reading.text).toBe("not-a-date");
  });
});
