import { useLocation } from "wouter";

import type { DomainDefinition } from "./registry";

/**
 * The active section for a domain, read from the URL.
 *
 * Sections used to be `useState` inside each screen, which meant the tab you
 * were on could not be linked, survived no reload, and was invisible to the
 * browser's back button -- pressing back from the Interceptions tab left the
 * domain entirely. Putting them in the path fixes all three at once and is
 * what lets the sidebar drive them.
 *
 * An unrecognised slug resolves to the first section rather than to nothing,
 * because a hand-edited or stale URL should land somewhere usable.
 */
export function useDomainSection(domain: DomainDefinition): string {
  const [location] = useLocation();
  return sectionLabelFor(domain, location);
}

/**
 * The same resolution, without the hook.
 *
 * The document title needs this for a domain that may be `null`, and a hook
 * cannot be called conditionally. Extracted rather than copied so the tab, the
 * breadcrumb and the sidebar can never name three different sections.
 */
export function sectionLabelFor(domain: DomainDefinition, location: string): string {
  if (domain.sections.length === 0) return "";
  const fallback = domain.sections[0];

  const rest = location.startsWith(`${domain.path}/`)
    ? location.slice(domain.path.length + 1).split("/")[0]
    : "";
  return domain.sections.find((section) => section.slug === rest)?.label ?? fallback.label;
}
