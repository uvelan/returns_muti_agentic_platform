import { expect, test } from "@playwright/test";

import { requireRealStack } from "../tests/realStack";

/**
 * `/config/integrations` against the live stack: open, change one field,
 * Validate, Publish, `GET /api/config/runtime` reflects it, then publish
 * the revert.
 *
 * **The field is `integrations.external_support_mirror.enabled`.** Tried
 * `ai_may_fabricate_success` first, since it is the field this screen's own
 * design table calls out -- the live run caught a real, unconditional
 * validator this lease's research had missed: `ReturnPlatformConfiguration
 * .validate_required_agents` (`return_configuration.py:1879`) refuses `true`
 * on any of the four topics' `ai_may_fabricate_success` in every
 * environment, not only production (`IntegrationsSection.tsx`'s hint text
 * was corrected from "must stay off in production" once this was found).
 * `enabled` carries no such rule -- confirmed with a direct
 * `POST /api/config/validate/RETURN_PLATFORM` call before writing this spec
 * -- so flipping it is safe to publish and revert on a live stack shared
 * with other work.
 *
 * Four topic rows share the same two toggle labels ("Enabled", "AI may
 * fabricate success"), so the row is picked by index rather than by a
 * unique accessible name -- `TOPICS` in `IntegrationsSection.tsx` is
 * declared in the fixed order `omc_return_create, external_support_mirror,
 * carrier_booking, customer_notification`, so the second "Enabled" checkbox
 * on the page is always `external_support_mirror`'s.
 *
 * Run this file (and the rest of the `cfg4-e2e` project) with `--workers=1`
 * -- see `playwright.config.ts`'s own note on why two of these specs racing
 * trips the optimistic lock for real.
 */

test.describe("Integrations -- real stack", () => {
  test("flips external_support_mirror.enabled, publishes, and the runtime snapshot reflects it -- then reverts", async ({
    page,
  }) => {
    requireRealStack();

    await page.goto("/config/integrations");
    const toggle = page.getByRole("checkbox", { name: "Enabled" }).nth(1);
    // `Toggle`'s real `<input type=checkbox>` is visually `sr-only` (1x1px,
    // clipped) inside a `<span>` the CSS switch draws *over* it -- correct
    // for a mouse/keyboard user via the `<label htmlFor>` delegating the
    // click, but it means the checkbox's own coordinates are covered by
    // that span. Click the visible label -- a real sibling, not covered --
    // and assert on the input.
    const toggleLabel = page.locator("label", { hasText: "Enabled" }).nth(1);
    const before = await toggle.isChecked();

    await toggleLabel.click();
    await expect(toggle).toBeChecked({ checked: !before });

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
    const flippedBody = (await afterFlip.json()) as {
      data: { configuration: { integrations: { external_support_mirror: { enabled: boolean } } } };
    };
    expect(flippedBody.data.configuration.integrations.external_support_mirror.enabled).toBe(!before);

    // The screen remounts from the new release once the runtime query
    // refetches; wait for the field to reappear with the value it now carries.
    const toggleAfterPublish = page.getByRole("checkbox", { name: "Enabled" }).nth(1);
    const toggleLabelAfterPublish = page.locator("label", { hasText: "Enabled" }).nth(1);
    await expect(toggleAfterPublish).toBeChecked({ checked: !before });

    // Revert.
    await toggleLabelAfterPublish.click();
    await expect(toggleAfterPublish).toBeChecked({ checked: before });

    const secondPublish = page.waitForResponse(
      (response) => response.url().includes("/api/config/publish") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Publish" }).click();
    const revertResponse = await secondPublish;
    expect(revertResponse.status(), await revertResponse.text()).toBe(200);

    const afterRevert = await page.request.get("/api/config/runtime");
    const revertedBody = (await afterRevert.json()) as {
      data: { configuration: { integrations: { external_support_mirror: { enabled: boolean } } } };
    };
    expect(revertedBody.data.configuration.integrations.external_support_mirror.enabled).toBe(before);
  });
});
