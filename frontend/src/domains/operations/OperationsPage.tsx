import { requireDomain, type OPERATIONS_SECTIONS } from "../registry";
import { useDomainSection } from "../useDomainSection";
import { CaseOperationsPage } from "./CaseOperationsPage";
import { ReturnsOperationsPage } from "./ReturnsOperationsPage";

/**
 * The Operations domain, which now holds two genuinely different screens.
 *
 * They are sections rather than one screen with a toggle because they are not
 * views over one list: a *case* is the canonical unit -- one confirmation
 * identity, N RMAs, its own durable workflow -- and a *return session* is the
 * older session-scoped record with its own event surface. Putting them in the
 * path is what makes each one linkable and survive a reload, which a `useState`
 * toggle never did.
 *
 * Cases first, because it is the canonical one.
 */
export function OperationsPage() {
  const section = useDomainSection(requireDomain("/operations"));
  // Exhaustive over the `as const` tuple, so renaming a section label in the
  // registry is a compile error here rather than a rail entry that renders
  // nothing.
  const active: (typeof OPERATIONS_SECTIONS)[number] =
    section === "Return sessions" ? "Return sessions" : "Cases";
  return active === "Return sessions" ? <ReturnsOperationsPage /> : <CaseOperationsPage />;
}
