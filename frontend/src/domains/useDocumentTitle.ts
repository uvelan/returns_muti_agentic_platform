import { useEffect } from "react";
import { useLocation } from "wouter";

import type { DomainDefinition } from "./registry";
import { sectionLabelFor } from "./useDomainSection";

/**
 * The page title, derived from the route.
 *
 * `index.html` set one title -- "Return Platform Console" -- and nothing ever
 * changed it, so all forty routes announced the same page. A screen reader user
 * got no confirmation that navigating had done anything, and browser history
 * and tab switching were useless for telling one screen from another. That is
 * WCAG 2.4.2 Page Titled, and it is a Level A failure rather than a nicety.
 *
 * Derived from the registry for the same reason the breadcrumb is: identity
 * left to each screen to remember is identity that drifts, and a new domain
 * should get a correct title without anyone editing this file.
 *
 * Most specific first. A tab is read truncated from its left edge, so the
 * section is the part worth keeping when the browser has room for four words.
 */

/**
 * The suffix every title ends with.
 *
 * Deliberately the value `index.html` already shipped. The shell's breadcrumb
 * says "Returns Intelligence Platform" instead, and the two have disagreed for
 * as long as both have existed -- recorded in PRODUCT.md as a naming decision
 * rather than settled here, because picking one is a rename and a rename is not
 * an accessibility fix. When it is decided, this constant and the breadcrumb
 * string are the two places to change.
 */
export const PLATFORM_NAME = "Return Platform Console";

/** Shown while capabilities are still loading, and on the sign-in screen. */
export const SIGN_IN_TITLE = `Sign in · ${PLATFORM_NAME}`;

/**
 * `null` domain means the launcher or an unmatched path, which is the platform
 * itself rather than any screen inside it.
 */
export function routeDocumentTitle(domain: DomainDefinition | null, location: string): string {
  if (domain === null) return PLATFORM_NAME;
  const section = sectionLabelFor(domain, location);
  return section === ""
    ? `${domain.name} · ${PLATFORM_NAME}`
    : `${section} · ${domain.name} · ${PLATFORM_NAME}`;
}

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title;
  }, [title]);
}

export function useRouteDocumentTitle(domain: DomainDefinition | null): void {
  const [location] = useLocation();
  useDocumentTitle(routeDocumentTitle(domain, location));
}
