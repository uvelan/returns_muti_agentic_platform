import { describe, expect, it } from "vitest";

import { DOMAINS, DOMAIN_PATHS, isDomainPath } from "./registry";

describe("the four-domain registry", () => {
  it("declares exactly the four canonical domains", () => {
    expect([...DOMAIN_PATHS].sort()).toEqual([
      "/ai",
      "/config",
      "/graph-schema",
      "/returns",
    ]);
  });

  it("matches a domain root and its children", () => {
    expect(isDomainPath("/returns")).toBe(true);
    expect(isDomainPath("/returns/abc-123")).toBe(true);
    expect(isDomainPath("/graph-schema/draft/1")).toBe(true);
  });

  it("does not match lookalikes", () => {
    // A prefix match without the separator would swallow these, and since Wave
    // F4 anything unmatched redirects to /returns -- so a lookalike that
    // wrongly matched would render the wrong domain, and one that wrongly
    // failed would bounce a real route to the front door.
    expect(isDomainPath("/returns-legacy")).toBe(false);
    expect(isDomainPath("/configuration")).toBe(false);
    // Old bookmarks. They no longer resolve to anything and must not be
    // mistaken for a domain on the way to the redirect.
    expect(isDomainPath("/v1/associate/returns")).toBe(false);
    expect(isDomainPath("/v2/config")).toBe(false);
  });

  it("gives every domain a distinct visibility capability", () => {
    const required = DOMAINS.map((domain) => domain.requires);
    expect(new Set(required).size).toBe(required.length);
  });

  it("routes every domain through a read capability, never a write one", () => {
    // Visibility must not depend on being able to change anything, or a
    // read-only auditor loses the domain entirely.
    for (const domain of DOMAINS) {
      expect(domain.requires.endsWith(".read")).toBe(true);
    }
  });
});
