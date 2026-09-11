import { expect, test } from "@playwright/test";

import { requireRealStack } from "../tests/realStack";

/**
 * `/config/workflow` against the live stack: open, change one field,
 * Validate, Publish, `GET /api/config/runtime` reflects it, then publish the
 * revert.
 *
 * **The field is `workflow.completion_dimensions` ("Completion dimensions"),
 * a `TagListInput` add/remove** -- the same shape `config-discovery.spec.ts`
 * settled on for `selection_vocabulary.conditions`, for the same reason: the
 * model (`WorkflowConfiguration.completion_dimensions`, `tuple[NonBlank,
 * ...]`, min length 1) carries no closed vocabulary and nothing cross-checks
 * a dimension's name against another field, so a uniquely-named test tag is
 * safe to add and remove on a live stack shared with other work.
 *
 * Run this file (and the rest of the `cfg4-e2e` project) with `--workers=1`
 * -- see `playwright.config.ts`'s own note on why two of these specs racing
 * trips the optimistic lock for real.
 */

const TEST_DIMENSION = "E2E_CFG5_WORKFLOW_TEST_DIMENSION";

test.describe("Workflow -- real stack", () => {
  test("adds a completion dimension, publishes, and the runtime snapshot reflects it -- then reverts", async ({
    page,
  }) => {
    requireRealStack();

    await page.goto("/config/workflow");
    const dimensionsInput = page.getByRole("textbox", { name: "Completion dimensions" });
    await expect(dimensionsInput).toBeVisible();

    // Add the test tag.
    await dimensionsInput.fill(TEST_DIMENSION);
    await dimensionsInput.press("Enter");
    await expect(page.getByRole("button", { name: new RegExp(`^${TEST_DIMENSION}\\.`) })).toBeVisible();

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
      data: { configuration: { workflow: { completion_dimensions: string[] } } };
    };
    expect(addedBody.data.configuration.workflow.completion_dimensions).toContain(TEST_DIMENSION);

    // The screen remounts from the new release once the runtime query
    // refetches; wait for the field to reappear with the tag it now carries.
    const dimensionsAfterPublish = page.getByRole("textbox", { name: "Completion dimensions" });
    await expect(dimensionsAfterPublish).toBeVisible();
    const chip = page.getByRole("button", { name: new RegExp(`^${TEST_DIMENSION}\\.`) });
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
      data: { configuration: { workflow: { completion_dimensions: string[] } } };
    };
    expect(revertedBody.data.configuration.workflow.completion_dimensions).not.toContain(TEST_DIMENSION);
  });
});
