import { expect, test } from "@playwright/test";

import { requireRealStack } from "../tests/realStack";

/**
 * `/config/discovery` against the live stack: open, change one field,
 * Validate, Publish, `GET /api/config/runtime` reflects it, then publish the
 * revert.
 *
 * **The field is `selection_vocabulary.conditions` ("Item conditions"), a
 * `TagListInput` add/remove -- not `discovery.strong_anchors`, which this
 * spec tried first.** The live run caught a real cross-field rule
 * `strong_anchors` carries that this lease's own research had missed: each
 * entry must name a field with a matching `discovery.anchor_extractors`
 * pattern, so a uniquely-named test tag with no extractor is refused by the
 * real backend with a 422 (`"strong anchors require extraction patterns"`).
 * `selection_vocabulary.conditions` has no such constraint -- the model
 * itself documents it as free-form, no closed vocabulary -- so a
 * uniquely-named tag that cannot collide with real data is safe to add and
 * remove on a live stack shared with other work (CFG-4.brief.md: "never
 * restart it, and revert every value you change").
 *
 * Run this file (and the rest of the `cfg4-e2e` project) with
 * `--workers=1` -- see `playwright.config.ts`'s own note on why two of
 * these specs racing trips the optimistic lock for real.
 */

const TEST_CONDITION = "E2E_CFG4_DISCOVERY_TEST_CONDITION";

test.describe("Discovery -- real stack", () => {
  test("adds an item condition, publishes, and the runtime snapshot reflects it -- then reverts", async ({
    page,
  }) => {
    requireRealStack();

    await page.goto("/config/discovery");
    const conditionsInput = page.getByRole("textbox", { name: "Item conditions" });
    await expect(conditionsInput).toBeVisible();

    // Add the test tag.
    await conditionsInput.fill(TEST_CONDITION);
    await conditionsInput.press("Enter");
    await expect(page.getByRole("button", { name: new RegExp(`^${TEST_CONDITION}\\.`) })).toBeVisible();

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

    const afterAdd = await page.request.get("/api/config/runtime");
    const addedBody = (await afterAdd.json()) as {
      data: { configuration: { selection_vocabulary: { conditions: string[] } } };
    };
    expect(addedBody.data.configuration.selection_vocabulary.conditions).toContain(TEST_CONDITION);

    // The screen remounts from the new release once the runtime query
    // refetches; wait for the field to reappear with the tag it now carries.
    const conditionsAfterPublish = page.getByRole("textbox", { name: "Item conditions" });
    await expect(conditionsAfterPublish).toBeVisible();
    const chip = page.getByRole("button", { name: new RegExp(`^${TEST_CONDITION}\\.`) });
    await expect(chip).toBeVisible();

    // Revert: remove the tag (click removes it, per TagListInput's own
    // contract) and publish again.
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
      data: { configuration: { selection_vocabulary: { conditions: string[] } } };
    };
    expect(revertedBody.data.configuration.selection_vocabulary.conditions).not.toContain(TEST_CONDITION);
  });
});
