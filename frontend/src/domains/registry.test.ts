import { describe, expect, it } from "vitest";

import { DOMAINS, DOMAIN_PATHS, isDomainPath, LANDING_PATH } from "./registry";

describe("the domain registry", () => {
  it("declares exactly the canonical domains", () => {
    expect([...DOMAIN_PATHS].sort()).toEqual([
      "/ai",
      "/config",
      "/graph-schema",
      "/operations",
      "/returns",
      "/support",
      "/sync",
    ]);
  });

  it("treats the launcher as in-shell, and it is not itself a domain", () => {
    // `App` sends anything that is not a shell path to a redirect. If the
    // launcher were not a shell path it would redirect to itself forever.
    expect(isDomainPath(LANDING_PATH)).toBe(true);
    expect(DOMAIN_PATHS).not.toContain(LANDING_PATH);
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

  it("shares a visibility capability only where that is deliberate", () => {
    // Two domains resolving to the same capability is usually a copy-paste
    // slip, which is what this catches. Two are intended, and both for the same
    // reason: the capability they would want does not exist yet, and gating on
    // an invented one the backend never grants would hide the domain from
    // everyone. Operations has no backend of its own; Support is a distinct
    // *role* that has no `support.*` capability to be granted. Both are named
    // so that a third, accidental collision still fails.
    const shared = new Map<string, string[]>();
    for (const domain of DOMAINS) {
      shared.set(domain.requires, [...(shared.get(domain.requires) ?? []), domain.path]);
    }
    const collisions = [...shared.entries()]
      .filter(([, paths]) => paths.length > 1)
      .map(([capability, paths]) => `${capability}: ${paths.sort().join(", ")}`)
      // Sorted so the assertion does not depend on the order domains happen to
      // be declared in: reordering the registry is not a regression.
      .sort();

    expect(collisions).toEqual([
      "config.runtime.read: /config, /operations",
      "returns.session.read: /returns, /support",
    ]);
  });

  it("marks a domain without a backend, rather than letting it look finished", () => {
    const pending = DOMAINS.filter((domain) => domain.status !== undefined);
    expect(pending.map((domain) => domain.path)).toEqual(["/operations"]);
  });

  it("gives every domain a landing-card purpose distinct from its description", () => {
    for (const domain of DOMAINS) {
      expect(domain.purpose.length).toBeGreaterThan(0);
      expect(domain.purpose).not.toBe(domain.description);
    }
  });

  it("routes every domain through a read capability, never a write one", () => {
    // Visibility must not depend on being able to change anything, or a
    // read-only auditor loses the domain entirely.
    for (const domain of DOMAINS) {
      expect(domain.requires.endsWith(".read")).toBe(true);
    }
  });
});
