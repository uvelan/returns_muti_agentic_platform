import { expect, test } from "@playwright/test";

import { requireRealStack } from "../tests/realStack";

/**
 * `/config/source-bindings` against the live stack: rebind one dataset's
 * incremental cursor field, verify `GET /api/source-bindings` reflects it,
 * then clear the override back to the packaged default.
 *
 * **A direct write, not a configuration release.** Unlike every other CFG-5
 * e2e spec, there is no Validate/Publish here -- `PUT`/`DELETE
 * /api/source-bindings/{dataset}` write immediately (`sourceBindings.ts`'s
 * own docstring), so this spec's "revert" is `Clear`, which the API
 * documents as returning the dataset to whatever the configured schema
 * says -- the same effect leaving `incrementalCursorField` alone would have
 * had, since `source_products` starts unoverridden.
 *
 * **The field is `source_products`'s incremental cursor field only** --
 * `sourceAssetId`, `connectorType` and `objectRef` are resubmitted
 * unchanged (the form is pre-filled from the current binding), so this
 * cannot repoint what `source_products` actually reads from, only how it
 * resumes.
 *
 * Run this file (and the rest of the `cfg4-e2e` project) with `--workers=1`,
 * consistent with every other spec in this project even though source
 * bindings carry no optimistic lock of their own to race.
 */

const TEST_CURSOR = "e2e_cfg5_test_cursor";

test.describe("Source bindings -- real stack", () => {
  test("rebinds source_products' cursor field, then clears the override", async ({ page }) => {
    requireRealStack();

    const before = await page.request.get("/api/source-bindings");
    const beforeBody = (await before.json()) as {
      data: { dataset: string; incrementalCursorField: string | null; overridden: boolean }[];
    };
    const original = beforeBody.data.find((binding) => binding.dataset === "source_products");
    expect(original, "source_products must exist in the live source-bindings list").toBeDefined();
    // Assumed by the revert-and-verify shape below: if this dataset already
    // carried an override, `Clear` would not restore the state this test
    // started from.
    expect(original?.overridden, "source_products must start unoverridden for Clear to be a real revert").toBe(false);

    await page.goto("/config/source-bindings");
    const row = page.locator("li").filter({ hasText: /^source_products/ });
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "Rebind" }).click();

    const cursorField = row.getByRole("textbox", { name: "Incremental cursor field" });
    await cursorField.fill(TEST_CURSOR);

    const rebindRequest = page.waitForResponse(
      (response) => response.url().includes("/api/source-bindings/source_products") && response.request().method() === "PUT",
    );
    await row.getByRole("button", { name: "Rebind" }).click();
    const rebindResponse = await rebindRequest;
    expect(rebindResponse.status(), await rebindResponse.text()).toBe(200);

    const afterRebind = await page.request.get("/api/source-bindings");
    const rebindBody = (await afterRebind.json()) as {
      data: { dataset: string; incrementalCursorField: string | null; overridden: boolean }[];
    };
    const rebound = rebindBody.data.find((binding) => binding.dataset === "source_products");
    expect(rebound?.incrementalCursorField).toBe(TEST_CURSOR);
    expect(rebound?.overridden).toBe(true);

    // Revert: Clear the override.
    const reboundRow = page.locator("li").filter({ hasText: /^source_products/ });
    await expect(reboundRow.getByText("Overridden")).toBeVisible();
    page.once("dialog", (dialog) => { void dialog.accept(); });
    const clearRequest = page.waitForResponse(
      (response) => response.url().includes("/api/source-bindings/source_products") && response.request().method() === "DELETE",
    );
    await reboundRow.getByRole("button", { name: "Clear" }).click();
    const clearResponse = await clearRequest;
    expect(clearResponse.status(), await clearResponse.text()).toBe(200);

    const afterClear = await page.request.get("/api/source-bindings");
    const clearBody = (await afterClear.json()) as {
      data: { dataset: string; incrementalCursorField: string | null; overridden: boolean }[];
    };
    const cleared = clearBody.data.find((binding) => binding.dataset === "source_products");
    expect(cleared?.overridden).toBe(false);
    expect(cleared?.incrementalCursorField).toBe(original?.incrementalCursorField);
  });
});
