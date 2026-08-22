import { describe, expect, it } from "vitest";

import { readReturnStatus, UNKNOWN_STATUS } from "./returnStatus";

describe("readReturnStatus", () => {
  it.each([null, undefined, "", "   "])("reads %p as unknown rather than as nothing", (value) => {
    // An empty pill is a badge shaped like a status with no status in it, which
    // reads as "this return has none" rather than "we do not know".
    expect(readReturnStatus(value)).toBe(UNKNOWN_STATUS);
  });

  it("never invents ISSUED for a missing status", () => {
    // The one substitution that would be dangerous: a return presenting as
    // issued because nobody recorded that it was not.
    expect(readReturnStatus(null)).not.toBe("ISSUED");
  });

  it("passes a real status through untouched", () => {
    expect(readReturnStatus("AWAITING_RECEIPT")).toBe("AWAITING_RECEIPT");
  });
});
