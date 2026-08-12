import { expect, test } from "@playwright/test";

import { DOMAINS } from "../src/domains/registry";

/**
 * Every canonical route, end to end, against the mock API.
 *
 * Replaces `e2e.spec.ts`, `a11y.spec.ts` and the two `-real` specs, all of
 * which drove Data Console and `/associate/returns` paths that Wave F4 deleted.
 * Deleting them without a replacement would have left the repo with no
 * end-to-end coverage at all, which is a worse state than the one F4 was fixing.
 *
 * Mock-backed, matching `playwright.config.ts`'s existing `dev:mock` web
 * server. What this proves that the unit tests cannot: the shell mounts, the
 * capability provider resolves a real principal over HTTP, and each domain's
 * queries reach a route that answers -- an import cycle, a bad envelope or a
 * mis-mounted router shows up here and nowhere else.
 *
 * **Driven from the registry, not from a copy of it.** This file used to hold
 * its own list of four domains and assert the navigation had exactly four
 * links. Three domains were added after that -- Support, Operations, Sync --
 * and the spec went on asserting four, so the only test that would have caught
 * a new screen failing to mount was itself the thing that broke. A hand-kept
 * duplicate of a list that grows is a test that expires.
 */

for (const domain of DOMAINS) {
  test(`${domain.path} renders its screen`, async ({ page }) => {
    const failures: string[] = [];
    page.on("pageerror", (error) => failures.push(error.message));

    await page.goto(domain.path);

    await expect(page.getByRole("heading", { name: domain.name, level: 1 })).toBeVisible();
    // "You do not have access" is what a broken principal fetch looks like, and
    // it renders without throwing -- so the absence of errors is not enough.
    await expect(page.getByText("You do not have access")).toHaveCount(0);
    expect(failures, `uncaught errors on ${domain.path}`).toEqual([]);
  });
}

test("an unrecognised path lands on returns rather than a dead end", async ({ page }) => {
  // Every legacy bookmark is now an unrecognised path. There are a lot of them.
  await page.goto("/v1/data-console/sources");

  await expect(page).toHaveURL(/\/returns$/);
  await expect(
    page.getByRole("heading", { name: "Return Business Copilot", level: 1 }),
  ).toBeVisible();
});

test("the registry is the whole navigation", async ({ page }) => {
  // The invariant F4 set, stated against the registry rather than against a
  // number: a link in the shell that no domain declares means something was
  // added to the chrome instead of to a domain.
  await page.goto("/returns");

  const nav = page.getByRole("complementary");
  await expect(nav.getByRole("link")).toHaveCount(DOMAINS.length);
});
