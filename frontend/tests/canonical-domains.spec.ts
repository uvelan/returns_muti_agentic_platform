import { expect, test } from "@playwright/test";

/**
 * The four canonical routes, end to end, against the mock API.
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
 */

const DOMAINS = [
  { path: "/returns", heading: "Return Business Copilot" },
  { path: "/config", heading: "Configuration" },
  { path: "/graph-schema", heading: "Graph Schema Analyzer" },
  { path: "/ai", heading: "AI Control Center" },
] as const;

for (const domain of DOMAINS) {
  test(`${domain.path} renders its screen`, async ({ page }) => {
    const failures: string[] = [];
    page.on("pageerror", (error) => failures.push(error.message));

    await page.goto(domain.path);

    await expect(page.getByRole("heading", { name: domain.heading, level: 1 })).toBeVisible();
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

test("the four domains are the whole navigation", async ({ page }) => {
  // F4's stated end state. If a fifth entry appears here, something was added
  // to the shell rather than to a domain.
  await page.goto("/returns");

  const nav = page.getByRole("complementary");
  await expect(nav.getByRole("link")).toHaveCount(DOMAINS.length);
});
