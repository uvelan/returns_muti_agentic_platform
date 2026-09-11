import { expect, test } from "@playwright/test";

import { requireRealStack } from "../tests/realStack";

/**
 * `/config/support` against the live stack: open, switch to the Ingress tab,
 * change one field, Validate, Publish, `GET /api/config/runtime` reflects
 * it, then publish the revert.
 *
 * **The field is `support_ingress.intents` ("Intents"), a `TagListInput`
 * add/remove** -- the closed taxonomy the classifier is scored against, but
 * the closure is enforced in code (`normalized_intents()` always unions in
 * `FALLBACK_INTENT`), not by a model constraint on the list itself, so a
 * uniquely-named extra entry is safe to publish and revert on a live stack
 * shared with other work.
 *
 * Run this file (and the rest of the `cfg4-e2e` project) with `--workers=1`
 * -- see `playwright.config.ts`'s own note on why two of these specs racing
 * trips the optimistic lock for real.
 */

const TEST_INTENT = "e2e_cfg5_support_test_intent";

test.describe("Support -- real stack", () => {
  test("adds an ingress intent, publishes, and the runtime snapshot reflects it -- then reverts", async ({
    page,
  }) => {
    requireRealStack();

    await page.goto("/config/support");
    await expect(page.getByText("Support handoff template")).toBeVisible();

    await page.getByRole("tab", { name: "Ingress" }).click();
    // `combobox`, not `textbox`: this `TagListInput` is given `suggestions`,
    // which sets the input's `list` attribute -- and an `<input list>` with a
    // `<datalist>` gets the implicit ARIA role `combobox` (HTML-AAM), not
    // `textbox`. `config-discovery.spec.ts`'s "Item conditions" field has no
    // suggestions and stays `textbox`, which is what made this the one worth
    // a comment.
    const intentsInput = page.getByRole("combobox", { name: "Intents" });
    await expect(intentsInput).toBeVisible();

    // Add the test tag.
    await intentsInput.fill(TEST_INTENT);
    await intentsInput.press("Enter");
    await expect(page.getByRole("button", { name: new RegExp(`^${TEST_INTENT}\\.`) })).toBeVisible();

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
      data: { configuration: { support_ingress: { intents: string[] } } };
    };
    expect(addedBody.data.configuration.support_ingress.intents).toContain(TEST_INTENT);

    // The screen remounts from the new release once the runtime query
    // refetches; the tab state resets to Template, so switch to Ingress
    // again and wait for the field to reappear with the tag it now carries.
    await page.getByRole("tab", { name: "Ingress" }).click();
    const intentsAfterPublish = page.getByRole("combobox", { name: "Intents" });
    await expect(intentsAfterPublish).toBeVisible();
    const chip = page.getByRole("button", { name: new RegExp(`^${TEST_INTENT}\\.`) });
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
      data: { configuration: { support_ingress: { intents: string[] } } };
    };
    expect(revertedBody.data.configuration.support_ingress.intents).not.toContain(TEST_INTENT);
  });
});
