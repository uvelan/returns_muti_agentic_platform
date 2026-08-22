/**
 * Every canonical route, and whether its page identity is settled.
 *
 * **Derived from the registry, never hand-kept.** The e2e spec this replaces
 * carried its own list of four domains and asserted the navigation had exactly
 * four links; three domains were added afterwards and the spec went on
 * asserting four, so the only test that would have caught a new screen failing
 * to mount was itself the thing that broke. A duplicate of a list that grows is
 * a test that expires.
 *
 * **Why identity is a field and not an assertion.** The audit found nine
 * canonical routes rendering no `<h1>` in their normal loaded state. A route
 * sweep that asserted a heading everywhere would have failed on all nine at
 * once, and the honest options then are to weaken the assertion or to block
 * every other route's coverage behind nine unrelated fixes. So identity is
 * recorded per route: `implemented` routes are asserted, `pending` routes are
 * swept for everything else -- reflow, keyboard, axe -- and their heading is
 * the one thing not claimed. T16 resolves each entry, and the release gate
 * fails if any remains `pending`.
 *
 * The count is not asserted against a number here for the same reason the list
 * is not hand-kept: adding a section to the registry should extend the sweep,
 * not fail it.
 */

import { DOMAINS, LANDING_PATH, ROOT_PATH, ROOT_REDIRECT_PATH } from "./registry";

/**
 * Whether the route renders its own page identity -- one `<h1>` naming what
 * you are looking at -- in its normal loaded state.
 *
 * `pending` is a statement about the product, not about the test: the route
 * works, and it does not yet say what it is.
 */
export type RouteIdentity = "implemented" | "pending";

export type CanonicalRoute = {
  readonly path: string;
  /** The domain this route belongs to, or `null` for the launcher and root. */
  readonly domain: string | null;
  /** What the platform calls this route -- the registry name, not the page's. */
  readonly name: string;
  readonly identity: RouteIdentity;
  /**
   * The `<h1>` this route actually renders, when it disagrees with `name`.
   *
   * Separate from `identity` on purpose. The audit's criterion was *presence*
   * -- nine routes render no heading -- and quietly widening it to *agreement*
   * would change the finding count under the release gate's feet. But the
   * disagreement is real and nobody had recorded it: the rail says "Graph
   * Schema Analyzer" and the page says "Schema Analyzer Agent", so an operator
   * who clicked one thing is looking at a page that calls itself another. T16
   * resolves these alongside the nine.
   */
  readonly headingMismatch?: string;
};

/**
 * The routes the audit found rendering no `<h1>`.
 *
 * Listed as paths rather than as a rule, because there is no rule -- it is nine
 * specific screens that were built before the shell had a heading convention.
 * Every entry here is a T16 deliverable and a G3b blocker.
 */
const PENDING_IDENTITY: readonly string[] = [
  ROOT_PATH,
  "/returns",
  "/support",
  "/support/work-queue",
  "/support/rma-tickets",
  "/approvals",
  "/sync",
  "/operations",
  "/operations/cases",
];

function identityOf(path: string): RouteIdentity {
  return PENDING_IDENTITY.includes(path) ? "pending" : "implemented";
}

/**
 * Routes whose heading disagrees with the name the navigation uses for them.
 *
 * Measured in the browser across all thirty-six routes, not transcribed. A
 * section route inherits its domain's heading -- `/config/agents` renders
 * "Configuration" -- and that is deliberate, so only the two genuine
 * disagreements are listed.
 */
// A `Map`, not a record. Indexing a `Record<string, string>` is typed as
// returning `string`, so the `undefined` check below reads as dead code to the
// compiler even though a lookup obviously can miss. `Map.get` says so honestly.
const HEADING_MISMATCH = new Map<string, string>(Object.entries({
  "/graph-schema": "Schema Analyzer Agent",
  "/graph-schema/data-sources": "Schema Analyzer Agent",
  "/graph-schema/graph-analyzer": "Schema Analyzer Agent",
  "/graph-schema/schema": "Schema Analyzer Agent",
  "/graph-schema/sync": "Schema Analyzer Agent",
  "/operations/return-sessions": "Returns Operations",
}));

function build(): readonly CanonicalRoute[] {
  const routes: CanonicalRoute[] = [
    // The root is a redirect, not a screen. It is in the manifest because a
    // sweep that skipped it would not notice the redirect breaking.
    { path: ROOT_PATH, domain: null, name: "", identity: identityOf(ROOT_PATH) },
    {
      path: LANDING_PATH,
      domain: null,
      // "Returns Platform", not "All domains". The rail link that points here
      // is labelled "All domains" because that is what it does; the page names
      // itself after the product, which is the right identity for a launcher.
      name: "Returns Platform",
      identity: identityOf(LANDING_PATH),
    },
  ];

  for (const domain of DOMAINS) {
    for (const path of [
      domain.path,
      // A section route renders its *domain's* heading, not the section's, and
      // that is the intended design -- the section is named in the breadcrumb
      // and in the rail. So the expected name is the domain's for both.
      ...domain.sections.map((section) => `${domain.path}/${section.slug}`),
    ]) {
      const mismatch = HEADING_MISMATCH.get(path);
      routes.push({
        path,
        domain: domain.path,
        name: domain.name,
        identity: identityOf(path),
        ...(mismatch === undefined ? {} : { headingMismatch: mismatch }),
      });
    }
  }
  return routes;
}

export const CANONICAL_ROUTES: readonly CanonicalRoute[] = build();

/** Where an unrecognised path lands. `App.tsx` is the authority, not this file. */
export const UNKNOWN_ROUTE_DESTINATION = LANDING_PATH;

/** Where `/` lands. */
export const ROOT_DESTINATION = ROOT_REDIRECT_PATH;

/**
 * The widths every route must reflow to.
 *
 * 640 is not a device; it is a 1280px display at 200% zoom, which is the
 * viewport WCAG 1.4.10 is actually about and the one nobody thinks to test.
 */
export const REQUIRED_VIEWPORTS: readonly { readonly name: string; readonly width: number; readonly height: number }[] = [
  { name: "320", width: 320, height: 800 },
  { name: "390", width: 390, height: 844 },
  { name: "640-zoom200", width: 640, height: 800 },
  { name: "768", width: 768, height: 1024 },
  { name: "1280", width: 1280, height: 800 },
  { name: "1440", width: 1440, height: 900 },
];

/** Routes T16 must give a heading. G3b fails while this is non-empty. */
export const PENDING_IDENTITY_ROUTES: readonly CanonicalRoute[] = CANONICAL_ROUTES.filter(
  (route) => route.identity === "pending",
);

/** Routes T16 must rename. G3b fails while this is non-empty. */
export const HEADING_MISMATCH_ROUTES: readonly CanonicalRoute[] = CANONICAL_ROUTES.filter(
  (route) => route.headingMismatch !== undefined,
);
