/**
 * WCAG 2.4.2 Page Titled, which the shell failed at Level A on every route.
 *
 * `index.html` set "Return Platform Console" once and nothing ever changed it,
 * so all forty routes reported the same page. Navigating produced no signal a
 * screen reader could use, and a browser history or a row of open tabs could
 * not tell Approvals from the AI Control Center.
 *
 * These hold two separate claims. The first is that the title is *derived* --
 * from the registry, so a new domain cannot ship without one and the tab cannot
 * disagree with the breadcrumb. The second is that it is actually *applied* to
 * the live document, because a correct string nobody assigns is the bug this
 * replaced.
 */

import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DomainApp } from "./DomainShell";
import { DOMAINS, domainForPath } from "./registry";
import { PLATFORM_NAME, routeDocumentTitle } from "./useDocumentTitle";

const mocks = vi.hoisted(() => ({ can: vi.fn(() => true) }));

vi.mock("../hooks/capabilityContext", () => ({
  useCapabilities: () => ({
    can: mocks.can,
    isLoading: false,
    isUnauthenticated: false,
    principal: { subject: "operator@returns.test" },
  }),
}));

vi.mock("../hooks/useRuntimeConfig", () => ({
  useRuntimeConfig: () => ({ environment: "test", releaseId: "release-1" }),
}));

vi.mock("./domainScreens", () => ({ DOMAIN_SCREENS: {} }));

function visit(path: string) {
  window.history.pushState({}, "", path);
}

/**
 * Deliberately not `PLATFORM_NAME`. Seeding the document with the value one of
 * these tests asserts made that test pass with the hook removed entirely --
 * it was reading back its own setup. A sentinel nothing produces means every
 * assertion below has to have been written by the code under test.
 */
const UNSET_TITLE = "title never assigned";

beforeEach(() => {
  mocks.can.mockReturnValue(true);
  document.title = UNSET_TITLE;
});

afterEach(() => {
  visit("/");
});

describe("the title a route derives", () => {
  it("is the platform alone off any domain", () => {
    expect(routeDocumentTitle(null, "/all")).toBe(PLATFORM_NAME);
  });

  it("names the domain inside one", () => {
    const approvals = domainForPath("/approvals");
    expect(approvals).not.toBeNull();
    expect(routeDocumentTitle(approvals, "/approvals")).toBe(`Approvals · ${PLATFORM_NAME}`);
  });

  it("leads with the section, because a tab is read truncated", () => {
    const sectioned = DOMAINS.find((domain) => domain.sections.length > 0);
    expect(sectioned).toBeDefined();
    if (sectioned === undefined) return;

    const second = sectioned.sections[1] ?? sectioned.sections[0];
    const title = routeDocumentTitle(sectioned, `${sectioned.path}/${second.slug}`);

    expect(title).toBe(`${second.label} · ${sectioned.name} · ${PLATFORM_NAME}`);
    expect(title.startsWith(second.label)).toBe(true);
  });

  it("gives every registered domain a distinct title", () => {
    // The failure this replaced was forty identical strings. One duplicate here
    // is the same defect at smaller scale.
    const titles = DOMAINS.map((domain) => routeDocumentTitle(domain, domain.path));
    expect(new Set(titles).size).toBe(DOMAINS.length);
  });

  it("says the platform's name in every one of them", () => {
    for (const domain of DOMAINS) {
      expect(routeDocumentTitle(domain, domain.path).endsWith(PLATFORM_NAME)).toBe(true);
    }
  });
});

describe("the title the document actually carries", () => {
  it("is set from the route on first render", () => {
    visit("/approvals");
    render(<DomainApp />);

    expect(document.title).toBe(`Approvals · ${PLATFORM_NAME}`);
  });

  it("follows a real navigation, section and all", async () => {
    // Through the sidebar rather than through history, because the defect was
    // that navigating changed nothing: a title assigned once on mount would
    // pass a pushState test and still fail the user.
    const user = userEvent.setup();
    const sectioned = DOMAINS.find((domain) => domain.sections.length > 1);
    expect(sectioned).toBeDefined();
    if (sectioned === undefined) return;

    const [first, second] = sectioned.sections;
    visit(`${sectioned.path}/${first.slug}`);
    render(<DomainApp />);
    expect(document.title).toBe(`${first.label} · ${sectioned.name} · ${PLATFORM_NAME}`);

    const link = document.querySelector(`a[href="${sectioned.path}/${second.slug}"]`);
    expect(link).not.toBeNull();
    await user.click(link as HTMLElement);

    expect(document.title).toBe(`${second.label} · ${sectioned.name} · ${PLATFORM_NAME}`);
  });

  it("falls back to the platform on the launcher", () => {
    visit("/all");
    render(<DomainApp />);

    expect(document.title).toBe(PLATFORM_NAME);
  });
});
