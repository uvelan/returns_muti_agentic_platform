import { describe, expect, it } from "vitest";

import { DOMAINS, DOMAIN_PATHS, isDomainPath } from "./registry";
import { legacyRouteDestination, normalizeBrowserPath } from "../versioning";

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

  it("does not match legacy paths or lookalikes", () => {
    expect(isDomainPath("/v1/associate/returns")).toBe(false);
    expect(isDomainPath("/v2/config")).toBe(false);
    // A prefix match without the separator would swallow this.
    expect(isDomainPath("/returns-legacy")).toBe(false);
    expect(isDomainPath("/configuration")).toBe(false);
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

describe("the legacy fallback no longer swallows the domain routes", () => {
  it("would have redirected every domain path before Phase 17", () => {
    // Guards the reason App.tsx had to change: if this ever stops being true
    // the domain branch in App.tsx is dead code and the routes silently
    // regress to the legacy app.
    for (const path of DOMAIN_PATHS) {
      const destination = legacyRouteDestination(normalizeBrowserPath(path), "", "");
      expect(destination.startsWith("/v1")).toBe(true);
    }
  });
});
