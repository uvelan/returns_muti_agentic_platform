import { describe, expect, it } from "vitest";

import { DOMAIN_SCREENS } from "./domainScreens";
import {
  CONFIG_SECTIONS,
  DOMAINS,
  GRAPH_SCHEMA_SECTIONS,
  DOMAIN_PATHS,
  domainForPath,
  isDomainPath,
  LANDING_PATH,
  requireDomain,
  ROOT_PATH,
  ROOT_REDIRECT_PATH,
  toSlug,
} from "./registry";

describe("the domain registry", () => {
  it("declares exactly the canonical domains", () => {
    expect([...DOMAIN_PATHS].sort()).toEqual([
      "/ai",
      "/approvals",
      "/config",
      "/graph-schema",
      "/operations",
      "/returns",
      "/shipments",
      "/support",
      "/sync",
    ]);
  });

  it("keeps Data Sources with the analyzer, and nowhere else", () => {
    // Three arrangements, two of them wrong. It was a `/config` tab, which made
    // the platform's whole source surface a nested selection inside a screen
    // about releases. Then it was its own domain, one rail away from the only
    // screen that reads sources -- and once Graph Analyzer grew its own
    // connection management, the same capability existed twice.
    //
    // Asserted from all three ends, so any of them returning fails here rather
    // than in review.
    expect([...GRAPH_SCHEMA_SECTIONS]).toContain("Data Sources");
    expect(DOMAIN_PATHS).not.toContain("/data-sources");
    expect([...CONFIG_SECTIONS]).not.toContain("Data Sources");
  });

  it("treats the launcher as in-shell, and it is not itself a domain", () => {
    // `App` sends anything that is not a shell path to a redirect. If the
    // launcher were not a shell path it would redirect to itself forever.
    expect(isDomainPath(LANDING_PATH)).toBe(true);
    expect(DOMAIN_PATHS).not.toContain(LANDING_PATH);
  });

  it("puts the launcher at its own path, not at the root", () => {
    // The root opens Returns now. Asserted as an equality rather than just
    // "not /" so moving the launcher again has to be a deliberate edit here.
    expect(LANDING_PATH).toBe("/all");
    expect(LANDING_PATH).not.toBe(ROOT_PATH);
  });

  it("keeps the root in the shell so the redirect can run", () => {
    // `App` bounces anything that is not a shell path to the launcher. If the
    // root were not a shell path it would never reach the redirect below it,
    // and arriving at `/` would land on the launcher rather than on Returns.
    expect(isDomainPath(ROOT_PATH)).toBe(true);
    expect(DOMAIN_PATHS).not.toContain(ROOT_PATH);
  });

  it("sends the root to a real domain", () => {
    // A redirect target that is not a domain would loop through the shell's
    // unknown-path fallback.
    expect(DOMAIN_PATHS).toContain(ROOT_REDIRECT_PATH);
    expect(ROOT_REDIRECT_PATH).toBe("/returns");
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
    // More than one domain resolving to the same capability is usually a
    // copy-paste slip, which is what this catches. Two groups are intended,
    // each for its own reason. `config.runtime.read`: Operations has two
    // backed sections but still no `operations.*` capability, since gating on
    // an invented one the backend never grants would hide the domain from
    // everyone. `returns.session.read`: Support is a distinct *role* with no
    // `support.*` capability of its own, and Shipments is visible on the same
    // read everyone operating returns already has -- its own module comment
    // says so -- rather than on an invented `shipments.*` capability, for the
    // identical reason. All three of that group, and both of the other, are
    // named so a fourth, accidental collision still fails.
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
      "returns.session.read: /returns, /shipments, /support",
    ]);
  });

  it("marks a domain without a backend, and only one without a backend", () => {
    // The invariant, not the census. `status` means "no backend surface exists
    // yet", so it must never sit on a domain that has a screen -- which is what
    // `DOMAIN_SCREENS` registers. `/operations` carried it until its Cases and
    // Return sessions sections were both backed; asserting the rule rather than
    // the list means the next domain to gain a screen fails here if its badge
    // is left behind.
    const badged = DOMAINS.filter((domain) => domain.status !== undefined).map((d) => d.path);
    const built = Object.keys(DOMAIN_SCREENS);
    expect(badged.filter((path) => built.includes(path))).toEqual([]);
  });

  it("gives every section a slug that resolves to its own domain's route", () => {
    // A rail entry that routes nowhere is worse than an absent one. Every
    // section renders at `/{domain}/{slug}`, which `isDomainPath` must accept
    // and `domainForPath` must resolve back to the domain that declared it.
    for (const domain of DOMAINS) {
      for (const section of domain.sections) {
        const path = `${domain.path}/${section.slug}`;
        expect(isDomainPath(path)).toBe(true);
        expect(domainForPath(path)?.path).toBe(domain.path);
        expect(section.slug).toBe(toSlug(section.label));
      }
    }
  });

  it("gives Operations its two backed sections", () => {
    // Cases is first because it is the canonical unit: `useDomainSection` falls
    // back to `sections[0]`, so this decides what a bare `/operations` shows.
    expect(requireDomain("/operations").sections.map((section) => section.label)).toEqual([
      "Cases",
      "Return sessions",
    ]);
  });

  it("gives every domain a landing-card purpose distinct from its description", () => {
    for (const domain of DOMAINS) {
      expect(domain.purpose.length).toBeGreaterThan(0);
      expect(domain.purpose).not.toBe(domain.description);
    }
  });

  it("gives Configuration a Policy section next to Return Policy (CFG-8)", () => {
    // `/config/policy` is `return_eligibility_policy` and `policy_evaluation`,
    // moved out of `/config/return-policy` into a screen of their own -- the
    // structural tests above already cover its slug and icon generically;
    // this pins the one thing they cannot, that it actually landed where the
    // brief put it rather than at the end of the rail.
    const labels = requireDomain("/config").sections.map((section) => section.label);
    const policyAt = labels.indexOf("Policy");
    expect(policyAt).toBeGreaterThan(-1);
    expect(labels[policyAt - 1]).toBe("Return Policy");
  });

  it("routes every domain through a read capability, never a write one", () => {
    // Visibility must not depend on being able to change anything, or a
    // read-only auditor loses the domain entirely.
    for (const domain of DOMAINS) {
      expect(domain.requires.endsWith(".read")).toBe(true);
    }
  });
});
