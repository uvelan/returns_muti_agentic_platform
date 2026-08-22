/**
 * The manifest is the release gate's input, so it has to be derived, complete
 * and honest about what is still owed.
 *
 * These run at unit speed. The browser sweep proves the same claims against a
 * rendered page and takes two minutes; this catches a registry edit that breaks
 * them in two seconds, which is the difference between finding it now and
 * finding it in CI.
 */

import { describe, expect, it } from "vitest";

import { DOMAINS, LANDING_PATH, ROOT_PATH } from "./registry";
import {
  CANONICAL_ROUTES,
  HEADING_MISMATCH_ROUTES,
  PENDING_IDENTITY_ROUTES,
  REQUIRED_VIEWPORTS,
  ROOT_DESTINATION,
  UNKNOWN_ROUTE_DESTINATION,
} from "./routeManifest";

describe("coverage", () => {
  it("holds every domain and every section, derived rather than listed", () => {
    // The spec this replaced kept its own list of four domains and asserted the
    // navigation had exactly four links. Three domains were added afterwards
    // and it went on asserting four, so the only test that would have caught a
    // new screen failing to mount was itself the thing that broke.
    const expected = DOMAINS.flatMap((domain) => [
      domain.path,
      ...domain.sections.map((section) => `${domain.path}/${section.slug}`),
    ]);
    const paths = CANONICAL_ROUTES.map((route) => route.path);

    for (const path of expected) {
      expect(paths, `${path} is missing from the sweep`).toContain(path);
    }
    expect(paths).toContain(ROOT_PATH);
    expect(paths).toContain(LANDING_PATH);
    expect(paths.length).toBe(expected.length + 2);
  });

  it("names each route the way the navigation names it", () => {
    // Not the section label: a section route renders its *domain's* heading,
    // and the section is named in the breadcrumb and the rail.
    for (const route of CANONICAL_ROUTES) {
      if (route.domain === null) continue;
      const domain = DOMAINS.find((candidate) => candidate.path === route.domain);
      expect(route.name).toBe(domain?.name);
    }
  });

  it("lists no path twice", () => {
    const paths = CANONICAL_ROUTES.map((route) => route.path);
    expect(new Set(paths).size).toBe(paths.length);
  });
});

describe("page identity", () => {
  it("has nothing left pending", () => {
    // Nine routes rendered no `<h1>`, because identity was left to eight
    // separate screens to remember. `DomainShell` derives it from the registry
    // now, so a route cannot be built without one.
    expect(PENDING_IDENTITY_ROUTES.map((route) => route.path)).toEqual([]);
  });

  it("has no heading that disagrees with the navigation", () => {
    // Six did: the analyzer called itself "Schema Analyzer Agent" under a rail
    // entry reading "Graph Schema Analyzer".
    expect(HEADING_MISMATCH_ROUTES.map((route) => route.path)).toEqual([]);
  });
});

describe("what the sweep must cover", () => {
  it("includes the width a 1280px display reaches at 200% zoom", () => {
    // 640 is not a device. It is the viewport WCAG 1.4.10 is actually about and
    // the one nobody thinks to test.
    expect(REQUIRED_VIEWPORTS.map((viewport) => viewport.width)).toContain(640);
  });

  it("spans 320 through 1440", () => {
    const widths = REQUIRED_VIEWPORTS.map((viewport) => viewport.width);
    expect(Math.min(...widths)).toBe(320);
    expect(Math.max(...widths)).toBe(1440);
  });
});

describe("routing destinations come from the registry", () => {
  it("sends an unrecognised path to the launcher, not to the copilot", () => {
    // Every legacy bookmark is an unrecognised path. `App.tsx` answers "where
    // did the screens go?" with all of them rather than with one.
    expect(UNKNOWN_ROUTE_DESTINATION).toBe(LANDING_PATH);
  });

  it("opens the root on the work", () => {
    expect(ROOT_DESTINATION).not.toBe(LANDING_PATH);
    expect(DOMAINS.map((domain) => domain.path)).toContain(ROOT_DESTINATION);
  });
});
