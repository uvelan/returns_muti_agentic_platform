/**
 * UIAUDIT-022 and 1.4.10 -- the two things the shell owed every route.
 *
 * A domain frame puts a rail and a header ahead of the content. On `/config`
 * that is nineteen links before the first thing the page is about, and they are
 * the same nineteen on every route, so a keyboard user paid for them once per
 * navigation. Nothing let them past.
 *
 * And `html`/`body` carried `min-width: 1280px`, so every viewport under 1280
 * scrolled in two dimensions -- including a 1280px display at 200% zoom, which
 * is a 640px viewport. Removing the floor is only half a fix: at 320 the 288px
 * rail would leave 32px for the screen, so below `lg` it becomes a drawer.
 *
 * These tests hold the shell's half. Per-route reflow belongs to the route.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DomainApp } from "./DomainShell";

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

// The shell is what is under test, not the screens: every domain falls back to
// `DomainLanding`, which renders synchronously and needs no server.
vi.mock("./domainScreens", () => ({ DOMAIN_SCREENS: {} }));

function visit(path: string) {
  window.history.pushState({}, "", path);
}

beforeEach(() => {
  mocks.can.mockReturnValue(true);
});

afterEach(() => {
  visit("/");
});

describe("skip to main content", () => {
  it("is the first thing Tab reaches, ahead of the rail", async () => {
    const user = userEvent.setup();
    visit("/config");
    render(<DomainApp />);

    await user.tab();

    expect(document.activeElement).toHaveTextContent("Skip to main content");
  });

  it("points at a main element that can actually take focus", async () => {
    const user = userEvent.setup();
    visit("/config");
    render(<DomainApp />);

    const link = screen.getByRole("link", { name: "Skip to main content" });
    const href = link.getAttribute("href") ?? "";
    expect(href.startsWith("#")).toBe(true);

    const target = document.querySelector(href);
    expect(target).not.toBeNull();
    expect(target?.tagName).toBe("MAIN");
    // Without this the browser scrolls the target into view and leaves focus on
    // the link, so the next Tab returns to the second rail item and the link
    // has done nothing a keyboard user can feel.
    expect(target).toHaveAttribute("tabindex", "-1");

    await user.tab();
    expect(document.activeElement).toBe(link);
  });

  it("is present on the launcher too, which has no rail but does have chrome", () => {
    visit("/launcher");
    render(<DomainApp />);

    expect(screen.getByRole("link", { name: "Skip to main content" })).toBeInTheDocument();
    const main = document.querySelector("main");
    expect(main).toHaveAttribute("tabindex", "-1");
  });
});

describe("the navigation drawer", () => {
  it("starts closed, and says so to assistive technology", () => {
    visit("/config");
    render(<DomainApp />);

    const trigger = screen.getAllByRole("button", { name: "Open navigation" })[0];
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveAttribute("aria-controls", "domain-rail");
  });

  it("keeps the closed rail out of the tab order", () => {
    visit("/config");
    render(<DomainApp />);

    // `invisible`, not merely translated off-canvas. A translated element is
    // still visible to the focus algorithm, so Tab would walk the whole rail
    // while the reader sees none of it.
    const rail = screen.getByRole("complementary");
    expect(rail.className).toContain("invisible");
    expect(rail.className).toContain("lg:visible");
  });

  it("opens on the trigger and closes on Escape, returning focus", async () => {
    const user = userEvent.setup();
    visit("/config");
    render(<DomainApp />);

    const trigger = screen.getAllByRole("button", { name: "Open navigation" })[0];
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("complementary").className).not.toContain("invisible");

    await user.keyboard("{Escape}");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    // Focus left on a hidden element sends the next Tab back to the top of the
    // document, which is exactly the trip the skip link exists to save.
    expect(document.activeElement).toBe(trigger);
  });

  it("shows the sections even when the desktop rail preference is collapsed", async () => {
    const user = userEvent.setup();
    visit("/config");
    render(<DomainApp />);

    // Collapsing hides the section links -- correct for a desktop rail
    // reclaiming width, wrong for a drawer, whose entire purpose is those links.
    const collapse = screen.getByRole("button", { name: /collapse/i });
    await user.click(collapse);
    await user.click(screen.getAllByRole("button", { name: "Open navigation" })[0]);

    const rail = screen.getByRole("complementary");
    expect(rail.id).toBe("domain-rail");
    expect(within(rail).getAllByRole("link").length).toBeGreaterThan(1);
  });

  it("closes itself once you have navigated, which is what it was opened for", async () => {
    const user = userEvent.setup();
    visit("/config");
    render(<DomainApp />);

    const trigger = screen.getAllByRole("button", { name: "Open navigation" })[0];
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");

    // A *section* link, not the first link in the rail -- that one is the way
    // back to the launcher, and the launcher has no rail to reopen.
    const section = within(screen.getByRole("complementary"))
      .getAllByRole("link")
      .find((link) => (link.getAttribute("href") ?? "").startsWith("/config/"));
    if (section === undefined) {
      throw new Error("the rail rendered no section link to navigate with");
    }
    await user.click(section);

    expect(
      screen.getAllByRole("button", { name: "Open navigation" })[0],
    ).toHaveAttribute("aria-expanded", "false");
  });

  it("reaches the copilot, which renders no header to hang a trigger on", () => {
    visit("/returns");
    render(<DomainApp />);

    // `/returns` suppresses the header because it needs the height. Without its
    // own bar there would be no way to open the rail below `lg` at all.
    expect(screen.getAllByRole("button", { name: "Open navigation" })[0]).toBeInTheDocument();
  });
});
