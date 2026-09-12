import { expect, test } from "@playwright/test";

import { requireRealStack } from "../tests/realStack";

/**
 * `/config/deployment` against the live stack: open, change one field,
 * Validate, Publish, `GET /api/config/runtime` reflects it, then publish the
 * revert -- closing CFG-6's own carried item ("Live no-restart proof...
 * deliberately not executed from this worktree", `drop.json.remaining`) and
 * CFG.brief.md §6 item 3's requirement that every `RETURN_PLATFORM` section
 * be "proven once in the live loop with pasted evidence".
 *
 * **The field is `deployment.feedback_learning.enabled`** ("Feedback
 * learning enabled"), a bare boolean `Toggle` with no environment-dependent
 * production gate (unlike `deployment.ai.provider_order` or
 * `deployment.dependencies.*`, which the production gate refuses specific
 * values for -- safe on any environment, but this lease still checks the
 * live host's own `environment` first so a future run against a production
 * host does not silently exercise the wrong field) and no cross-field
 * validation, so flipping it and flipping it back is safe on a live stack
 * shared with other work.
 *
 * Run this file (and the rest of the `cfg4-e2e` project) with `--workers=1`
 * -- see `playwright.config.ts`'s own note on the optimistic lock.
 */

type RuntimeBody = {
  data: {
    environment: string;
    configuration: { deployment: { feedback_learning: { enabled: boolean } } };
  };
};

test.describe("Deployment -- real stack", () => {
  test("flips feedback_learning.enabled, publishes, and the runtime snapshot reflects it -- then reverts", async ({
    page,
  }) => {
    requireRealStack();

    const preflight = await page.request.get("/api/config/runtime");
    const preflightBody = (await preflight.json()) as RuntimeBody;
    // This lease's own note: the field this spec touches carries no
    // production gate either way, but a host that is somehow production is
    // not the host to learn that on -- fail loudly instead.
    expect(
      preflightBody.data.environment,
      "this spec assumes a non-production host; verify before running elsewhere",
    ).not.toBe("production");
    const original = preflightBody.data.configuration.deployment.feedback_learning.enabled;

    await page.goto("/config/deployment");
    const toggle = page.getByRole("checkbox", { name: "Feedback learning enabled" });
    await expect(toggle).toBeVisible();
    await expect(toggle).toBeChecked({ checked: original });

    await toggle.click();
    await expect(toggle).toBeChecked({ checked: !original });

    const validate = page.waitForResponse(
      (response) => response.url().includes("/api/config/validate/") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Validate" }).click();
    const validateResponse = await validate;
    expect(validateResponse.status(), await validateResponse.text()).toBe(200);
    const validateBody = (await validateResponse.json()) as { data: { valid: boolean } };
    expect(validateBody.data.valid).toBe(true);

    const firstPublish = page.waitForResponse(
      (response) => response.url().includes("/api/config/publish") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Publish" }).click();
    const publishResponse = await firstPublish;
    expect(publishResponse.status(), await publishResponse.text()).toBe(200);

    const afterFlip = await page.request.get("/api/config/runtime");
    const flippedBody = (await afterFlip.json()) as RuntimeBody;
    expect(flippedBody.data.configuration.deployment.feedback_learning.enabled).toBe(!original);

    // The publish creates a new release, which remounts the screen with the
    // published value as its own loaded baseline.
    const toggleAfterPublish = page.getByRole("checkbox", { name: "Feedback learning enabled" });
    await expect(toggleAfterPublish).toBeVisible();
    await expect(toggleAfterPublish).toBeChecked({ checked: !original });

    // Revert.
    await toggleAfterPublish.click();
    await expect(toggleAfterPublish).toBeChecked({ checked: original });

    const secondPublish = page.waitForResponse(
      (response) => response.url().includes("/api/config/publish") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Publish" }).click();
    const revertResponse = await secondPublish;
    expect(revertResponse.status(), await revertResponse.text()).toBe(200);

    const afterRevert = await page.request.get("/api/config/runtime");
    const revertedBody = (await afterRevert.json()) as RuntimeBody;
    expect(revertedBody.data.configuration.deployment.feedback_learning.enabled).toBe(original);
  });
});
