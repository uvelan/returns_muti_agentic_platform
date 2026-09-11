import { expect, test } from "@playwright/test";

import { requireRealStack } from "../tests/realStack";

/**
 * `/config/return-policy` against the live stack: open, change one field,
 * Validate, Publish, `GET /api/config/runtime` reflects it, then publish the
 * revert.
 *
 * The field is `return_policy.return_method_derivation.freight_keywords`, a
 * `TagListInput` add/remove -- see `config-discovery.spec.ts`'s own note on
 * why a uniquely-named tag rather than a scalar toggle is what this suite
 * touches on a shared live stack. `policy_evaluation` and the eligibility
 * decision fields are deliberately not what this spec exercises: both are
 * real operational switches on a graph other work reads, and a tag that
 * cannot collide with real data is the safer choice by a wide margin.
 *
 * Run this file (and the rest of the `cfg4-e2e` project) with
 * `--workers=1` -- see `playwright.config.ts`'s own note on why two of
 * these specs racing trips the optimistic lock for real.
 */

const TEST_KEYWORD = "e2e-cfg4-return-policy-test-keyword";

test.describe("Return policy -- real stack", () => {
  test("adds a freight keyword, publishes, and the runtime snapshot reflects it -- then reverts", async ({
    page,
  }) => {
    requireRealStack();

    await page.goto("/config/return-policy");
    const keywordsInput = page.getByRole("textbox", { name: "Freight keywords" });
    await expect(keywordsInput).toBeVisible();

    await keywordsInput.fill(TEST_KEYWORD);
    await keywordsInput.press("Enter");
    await expect(page.getByRole("button", { name: new RegExp(`^${TEST_KEYWORD}\\.`) })).toBeVisible();

    await page.getByRole("button", { name: "Validate" }).click();

    const firstPublish = page.waitForResponse(
      (response) => response.url().includes("/api/config/publish") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Publish" }).click();
    const publishResponse = await firstPublish;
    expect(publishResponse.status(), await publishResponse.text()).toBe(200);

    const afterAdd = await page.request.get("/api/config/runtime");
    const addedBody = (await afterAdd.json()) as {
      data: {
        configuration: {
          return_policy: { return_method_derivation: { freight_keywords: string[] } };
        };
      };
    };
    expect(addedBody.data.configuration.return_policy.return_method_derivation.freight_keywords).toContain(
      TEST_KEYWORD,
    );

    const keywordsAfterPublish = page.getByRole("textbox", { name: "Freight keywords" });
    await expect(keywordsAfterPublish).toBeVisible();
    const chip = page.getByRole("button", { name: new RegExp(`^${TEST_KEYWORD}\\.`) });
    await expect(chip).toBeVisible();

    await chip.click();
    await expect(chip).toHaveCount(0);

    const secondPublish = page.waitForResponse(
      (response) => response.url().includes("/api/config/publish") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Publish" }).click();
    const revertResponse = await secondPublish;
    expect(revertResponse.status(), await revertResponse.text()).toBe(200);

    const afterRevert = await page.request.get("/api/config/runtime");
    const revertedBody = (await afterRevert.json()) as {
      data: {
        configuration: {
          return_policy: { return_method_derivation: { freight_keywords: string[] } };
        };
      };
    };
    expect(
      revertedBody.data.configuration.return_policy.return_method_derivation.freight_keywords,
    ).not.toContain(TEST_KEYWORD);
  });
});
