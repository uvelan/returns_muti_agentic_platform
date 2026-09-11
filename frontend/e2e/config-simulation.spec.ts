import { expect, test } from "@playwright/test";

import { requireRealStack } from "../tests/realStack";

/**
 * `/config/simulation` against the live stack: open, change one field,
 * Validate, Publish, `GET /api/config/runtime` reflects it, then publish
 * the revert.
 *
 * **The field is `dependencies.LSI.operations` ("LSI operations"), a
 * `TagListInput` add/remove.** `DependencyDefinition.operations` requires
 * at least one entry with no duplicates but names no closed vocabulary, so
 * a uniquely-named extra operation is safe to add and remove on a live
 * stack shared with other work -- the same reasoning `config-workflow.spec.ts`
 * used for `completion_dimensions`.
 *
 * Run this file (and the rest of the `cfg4-e2e` project) with `--workers=1`
 * -- see `playwright.config.ts`'s own note on why two of these specs racing
 * trips the optimistic lock for real.
 */

const TEST_OPERATION = "E2E_CFG5_SIMULATION_TEST_OPERATION";

test.describe("Simulation -- real stack", () => {
  test("adds an LSI operation, publishes, and the runtime snapshot reflects it -- then reverts", async ({
    page,
  }) => {
    requireRealStack();

    await page.goto("/config/simulation");
    const operationsInput = page.getByRole("textbox", { name: "LSI operations" });
    await expect(operationsInput).toBeVisible();

    // Add the test tag.
    await operationsInput.fill(TEST_OPERATION);
    await operationsInput.press("Enter");
    await expect(page.getByRole("button", { name: new RegExp(`^${TEST_OPERATION}\\.`) })).toBeVisible();

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
      data: { dependency_simulation_configuration: { dependencies: { LSI: { operations: string[] } } } };
    };
    expect(addedBody.data.dependency_simulation_configuration.dependencies.LSI.operations).toContain(
      TEST_OPERATION,
    );

    // The screen remounts from the new release once the runtime query
    // refetches; wait for the field to reappear with the tag it now carries.
    const operationsAfterPublish = page.getByRole("textbox", { name: "LSI operations" });
    await expect(operationsAfterPublish).toBeVisible();
    const chip = page.getByRole("button", { name: new RegExp(`^${TEST_OPERATION}\\.`) });
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
      data: { dependency_simulation_configuration: { dependencies: { LSI: { operations: string[] } } } };
    };
    expect(revertedBody.data.dependency_simulation_configuration.dependencies.LSI.operations).not.toContain(
      TEST_OPERATION,
    );
  });
});
