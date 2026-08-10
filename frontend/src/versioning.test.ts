import { describe, expect, it } from "vitest";

import { normalizeBrowserPath } from "./versioning";

/**
 * What is left of this file after Wave F4.
 *
 * It used to assert that every legacy route stayed reachable under `/v1` and
 * that `/v2/copilot` was reserved. Both were true and both are gone: there is
 * no legacy app to stay reachable, and `App` now redirects anything that is not
 * a canonical domain to `/returns`.
 *
 * `normalizeBrowserPath` survives because `App` and `isDomainPath` have to
 * agree on what a path *is* before either compares one.
 */
describe("browser path normalisation", () => {
  it("strips trailing slashes so /returns/ still matches /returns", () => {
    // The bug this prevents: wouter reports the trailing slash, `isDomainPath`
    // compares exactly, the match fails, and the shell redirects a canonical
    // route to itself in a loop.
    expect(normalizeBrowserPath("/returns/")).toBe("/returns");
    expect(normalizeBrowserPath("/graph-schema///")).toBe("/graph-schema");
  });

  it("leaves an already-normal path alone", () => {
    expect(normalizeBrowserPath("/config")).toBe("/config");
    expect(normalizeBrowserPath("/returns/abc-123")).toBe("/returns/abc-123");
  });

  it("normalises the root to a single slash rather than an empty string", () => {
    // An empty string is falsy, and every caller here branches on the result.
    expect(normalizeBrowserPath("/")).toBe("/");
    expect(normalizeBrowserPath("")).toBe("/");
  });
});
