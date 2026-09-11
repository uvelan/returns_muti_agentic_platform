import { expect, test } from "@playwright/test";

import { requireRealStack } from "../tests/realStack";

/**
 * `/config/fulfilment` against the live stack: open, change one field,
 * Validate, Publish, `GET /api/config/runtime` reflects it, then publish the
 * revert.
 *
 * The field is `bay.eligible_statuses`, a `TagListInput` add/remove -- see
 * `config-discovery.spec.ts`'s own note on why. `require_physical_receipt`,
 * `allow_prearrival_reservation` and any shipment status's `terminal`/
 * `allowed_next` are deliberately not what this spec touches: each governs
 * real bay-assignment or shipment-transition behaviour on a graph other work
 * reads, where a status code nothing will ever carry is the safer edit by a
 * wide margin.
 *
 * Run this file (and the rest of the `cfg4-e2e` project) with
 * `--workers=1` -- see `playwright.config.ts`'s own note on why two of
 * these specs racing trips the optimistic lock for real.
 */

const TEST_STATUS = "E2E_CFG4_TEST_STATUS";

test.describe("Fulfilment -- real stack", () => {
  test("adds a bay-eligible status, publishes, and the runtime snapshot reflects it -- then reverts", async ({
    page,
  }) => {
    requireRealStack();

    await page.goto("/config/fulfilment");
    const statusesInput = page.getByRole("combobox", { name: "Eligible statuses" });
    await expect(statusesInput).toBeVisible();

    await statusesInput.fill(TEST_STATUS);
    await statusesInput.press("Enter");
    await expect(page.getByRole("button", { name: new RegExp(`^${TEST_STATUS}\\.`) })).toBeVisible();

    await page.getByRole("button", { name: "Validate" }).click();

    const firstPublish = page.waitForResponse(
      (response) => response.url().includes("/api/config/publish") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Publish" }).click();
    const publishResponse = await firstPublish;
    expect(publishResponse.status(), await publishResponse.text()).toBe(200);

    const afterAdd = await page.request.get("/api/config/runtime");
    const addedBody = (await afterAdd.json()) as {
      data: { configuration: { bay: { eligible_statuses: string[] } } };
    };
    expect(addedBody.data.configuration.bay.eligible_statuses).toContain(TEST_STATUS);

    const statusesAfterPublish = page.getByRole("combobox", { name: "Eligible statuses" });
    await expect(statusesAfterPublish).toBeVisible();
    const chip = page.getByRole("button", { name: new RegExp(`^${TEST_STATUS}\\.`) });
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
      data: { configuration: { bay: { eligible_statuses: string[] } } };
    };
    expect(revertedBody.data.configuration.bay.eligible_statuses).not.toContain(TEST_STATUS);
  });
});
