import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import {
  CANONICAL_ROUTES,
  HEADING_MISMATCH_ROUTES,
  PENDING_IDENTITY_ROUTES,
  REQUIRED_VIEWPORTS,
  ROOT_DESTINATION,
  UNKNOWN_ROUTE_DESTINATION,
} from "../src/domains/routeManifest";

/**
 * The route sweep, driven by the manifest.
 *
 * Replaces `canonical-domains.spec.ts`, which asserted an `<h1>` on every
 * domain and a rail containing one link per domain. Both were true before the
 * shell was rebuilt around one domain's sections, and neither had been true
 * since: the spec failed six of eight domains and had presumably been failing
 * for as long, which makes it worse than absent -- a red suite tells you
 * nothing, so nobody reads it, so a real regression hides in it.
 *
 * Three things are asserted separately here, because the audit's central
 * complaint was that they had been conflated:
 *
 *  - **rendered** -- the screen mounted and threw nothing;
 *  - **API success** -- the queries this route makes were answered;
 *  - **persisted outcome** -- the datastore says the thing happened.
 *
 * A green "rendered" proves only that React did not crash. The mock project
 * cannot prove the third at all, which is exactly why it is not the only
 * project: see `playwright.config.ts`.
 */

/**
 * Wait for the route to have rendered, without waiting for the network to go
 * quiet.
 *
 * `networkidle` is wrong here and was the first thing that broke: the AI
 * Control Center refetches every fifteen seconds by design, so the network
 * never idles and the wait runs to the test timeout. What the sweep actually
 * needs is that the screen has painted, which `<main>` having laid out says.
 */
async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("domcontentloaded");
  await page.locator("main").waitFor({ state: "attached" });
  // Queries resolve on a microtask after mount; one animation frame plus a
  // short grace is enough for the first paint of every route in the manifest.
  await page.waitForTimeout(250);
}

/** Fails the test on an uncaught error, a console error, or a 4xx/5xx. */
function watch(page: Page): { assertClean: (route: string) => void } {
  const problems: string[] = [];
  page.on("pageerror", (error) => problems.push(`uncaught: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(`console: ${message.text()}`);
  });
  page.on("response", (response) => {
    const status = response.status();
    // Only the app's own API. A 404 for a favicon is not a defect in a route.
    if (status >= 400 && response.url().includes("/api/")) {
      problems.push(`${String(status)} ${response.url()}`);
    }
  });
  return {
    assertClean: (route: string) => {
      expect(problems, `${route} produced errors`).toEqual([]);
    },
  };
}

test.describe("every canonical route renders", () => {
  for (const route of CANONICAL_ROUTES) {
    test(`${route.path} mounts and answers`, async ({ page }) => {
      const watcher = watch(page);
      await page.goto(route.path);

      // The shell's own failure mode renders without throwing, so the absence
      // of errors is not enough on its own.
      await expect(page.getByText("You do not have access")).toHaveCount(0);
      await expect(page.getByRole("main")).toBeVisible();

      if (route.identity === "implemented" && route.headingMismatch === undefined) {
        await expect(page.getByRole("heading", { name: route.name, level: 1 })).toBeVisible();
      }
      watcher.assertClean(route.path);
    });
  }
});

test.describe("the shell's keyboard contract holds on every route", () => {
  for (const route of CANONICAL_ROUTES) {
    test(`${route.path} lets the keyboard past the chrome`, async ({ page }) => {
      await page.goto(route.path);
      await settle(page);

      // Pressed on `body` rather than through `page.keyboard`: after `goto`
      // nothing in the document holds focus, so a bare Tab goes nowhere and
      // `:focus` matches no element at all -- which fails for a reason that has
      // nothing to do with the skip link.
      await page.locator("body").press("Tab");

      const first = await page.evaluate(() => ({
        tag: document.activeElement?.tagName ?? null,
        text: document.activeElement?.textContent?.trim() ?? null,
      }));
      expect(first, `first Tab on ${route.path}`).toEqual({
        tag: "A",
        text: "Skip to main content",
      });

      // And the jump moves focus, rather than only scrolling. Without
      // `tabindex="-1"` on the target the browser leaves focus on the link and
      // the next Tab returns to the top of the rail.
      await page.keyboard.press("Enter");
      await expect(page.locator("main")).toBeFocused();
    });
  }
});

test.describe("every canonical route reflows", () => {
  for (const viewport of REQUIRED_VIEWPORTS) {
    test(`no route scrolls sideways at ${viewport.name}`, async ({ page }) => {
      // One test walks every route at this width -- thirty-six navigations --
      // so it needs a budget the per-route default does not give it. Kept as
      // one test per width rather than one per route-and-width because the
      // question "does anything scroll sideways at 320" wants a single answer
      // listing every offender, not two hundred and sixteen separate verdicts.
      test.setTimeout(600_000);
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      const overflowing: string[] = [];

      for (const route of CANONICAL_ROUTES) {
        await page.goto(route.path);
        await settle(page);
        const measure = () => page.evaluate(() => {
          const document_ = document.documentElement.scrollWidth - window.innerWidth;
          // `<main>` carries `overflow-x-auto`, so a route whose content is
          // wider than the viewport scrolls *inside* main and the document
          // never grows. Measuring only the document would report every such
          // route as reflowing when the operator still has to scroll sideways
          // to read it -- which is the thing 1.4.10 forbids.
          //
          // A wide data table inside its own scroller is the permitted case,
          // and it is not this: this is main itself, which holds the page.
          const main = document.querySelector("main");
          const inside = main === null ? 0 : main.scrollWidth - main.clientWidth;
          return Math.max(document_, inside);
        });

        // Measured twice, and a route only counts as overflowing if it still
        // does after settling again. Under eight parallel workers the first
        // measurement can land mid-layout, and a reflow suite that reports a
        // route as broken because the machine was busy is a suite nobody
        // trusts. A genuine overflow does not go away on a second look.
        let overflow = await measure();
        if (overflow > 0) {
          await page.waitForTimeout(500);
          overflow = await measure();
        }
        // A scrollbar makes this slightly negative; only positive is overflow.
        if (overflow > 0) overflowing.push(`${route.path} (+${String(overflow)}px)`);
      }

      expect(overflowing, `two-dimensional scrolling at ${viewport.name}`).toEqual([]);
    });
  }
});

test.describe("accessibility", () => {
  for (const route of CANONICAL_ROUTES) {
    test(`${route.path} has no critical or serious violation`, async ({ page }) => {
      await page.goto(route.path);
      await settle(page);

      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();

      const blocking = results.violations.filter(
        (violation) => violation.impact === "critical" || violation.impact === "serious",
      );
      expect(
        blocking.map((violation) => `${violation.id}: ${violation.help}`),
        `${route.path} axe violations`,
      ).toEqual([]);
    });
  }
});

test.describe("routing", () => {
  test("an unrecognised path lands where App.tsx sends it", async ({ page }) => {
    // Every legacy bookmark is an unrecognised path, and there are a lot of
    // them. `App.tsx` sends them to the launcher, which shows every domain the
    // principal can reach -- answering "where did the screens go?" with all of
    // them rather than with one. The spec this replaces asserted `/returns`,
    // which was never what the code did.
    await page.goto("/v1/data-console/sources");
    await expect(page).toHaveURL(new RegExp(`${UNKNOWN_ROUTE_DESTINATION}$`));
  });

  test("the root opens the work, not the launcher", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(new RegExp(`${ROOT_DESTINATION}$`));
  });
});

test.describe("the manifest itself", () => {
  test("records what is still owed, so the release gate can read it", () => {
    // Not an assertion that the lists are empty -- they are not, and T16 is
    // what empties them. This test exists so the numbers are visible in the
    // report rather than discovered at the release gate.
    // eslint-disable-next-line no-console
    console.log(
      `identity pending: ${String(PENDING_IDENTITY_ROUTES.length)} of ${String(CANONICAL_ROUTES.length)}`,
      `\nheading mismatch: ${String(HEADING_MISMATCH_ROUTES.length)}`,
    );
    expect(CANONICAL_ROUTES.length).toBeGreaterThan(0);
  });
});
