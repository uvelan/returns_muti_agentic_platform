import { expect, test } from "@playwright/test";

import { requireRealStack } from "../tests/realStack";

/**
 * `/config/policy` against the live stack: change the return window,
 * Validate, Publish, `GET /api/config/runtime` reflects it, preview a
 * decision that is now inside the widened window, then publish the revert.
 *
 * The field is `return_eligibility_policy.standard_stock_return.purchase_window.days`
 * -- the brief's own example (30 -> 45 -> back to 30). Every checklist fact in
 * the preview sample is answered explicitly (five "Yes" for the resale
 * condition, eight "No" for the prohibited states -- the model's own
 * defaults) rather than left "not stated": the live release's
 * `unstated_condition_facts` is `REVIEW_REQUIRED`, not `NOT_EVALUATED`, so an
 * unanswered fact there reaches `REVIEW_REQUIRED` for a reason that has
 * nothing to do with the window this spec is actually proving -- and that
 * would make "40 days now approves" pass or fail by accident of a field this
 * spec never touches. Answering every fact removes that variable, exactly
 * the way `config-return-policy.spec.ts`'s own note explains its choice of
 * field to touch on a shared live stack.
 *
 * Run this file (and the rest of the `cfg4-e2e` project) with `--workers=1`
 * -- see `playwright.config.ts`'s own note on the optimistic lock.
 */

const ORIGINAL_DAYS = 30;
const CHANGED_DAYS = 45;

const SATISFIED_CONDITION_FACTS = [
  "New",
  "Suitable for resale",
  "In original packaging",
  "Packaging undamaged",
  "All original parts present",
];

const SATISFIED_PROHIBITED_FACTS = [
  "Used",
  "Installed",
  "Modified",
  "Rebuilt",
  "Reconditioned",
  "Repaired",
  "Altered",
  "Damaged",
];

type RuntimeBody = {
  data: {
    configuration: {
      return_eligibility_policy: {
        standard_stock_return: { purchase_window: { days: number } };
      };
      policy_evaluation: { enabled: boolean };
    };
  };
};

test.describe("Policy -- real stack", () => {
  test("widens the return window, publishes, previews inside it, then reverts", async ({ page }) => {
    requireRealStack();

    // CFG-8 forward note: "Decision: APPROVE" below is the preview endpoint's
    // answer to the sample facts *and* `policy_evaluation.enabled` -- if the
    // live release ever ships that flag `false` (D-CFG-6 left it an
    // undecided key, resolved "no" for adoption but not pinned true here),
    // the model skips evaluation and the decision this spec asserts would
    // never be reachable regardless of the window or the facts below. Assert
    // the precondition explicitly so a failure here reads as "the release
    // changed" rather than a confusing preview-decision mismatch.
    const preflight = await page.request.get("/api/config/runtime");
    const preflightBody = (await preflight.json()) as RuntimeBody;
    expect(
      preflightBody.data.configuration.policy_evaluation.enabled,
      "policy_evaluation.enabled must be true on the live release for this spec's preview decision to be meaningful",
    ).toBe(true);

    await page.goto("/config/policy");
    const window_ = page.getByRole("spinbutton", { name: "Return window" });
    await expect(window_).toBeVisible();
    await expect(window_).toHaveValue(String(ORIGINAL_DAYS));

    await window_.fill(String(CHANGED_DAYS));
    await window_.blur();
    await page.getByRole("button", { name: "Validate" }).click();

    const firstPublish = page.waitForResponse(
      (response) => response.url().includes("/api/config/publish") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Publish" }).click();
    const publishResponse = await firstPublish;
    expect(publishResponse.status(), await publishResponse.text()).toBe(200);

    const afterChange = await page.request.get("/api/config/runtime");
    const changedBody = (await afterChange.json()) as RuntimeBody;
    expect(
      changedBody.data.configuration.return_eligibility_policy.standard_stock_return.purchase_window.days,
    ).toBe(CHANGED_DAYS);

    // The publish creates a new release, which remounts the screen with the
    // published value as its own loaded baseline -- confirmed in the UI
    // before driving the preview panel against it.
    await expect(page.getByRole("spinbutton", { name: "Return window" })).toHaveValue(String(CHANGED_DAYS));

    for (const label of SATISFIED_CONDITION_FACTS) {
      await page
        .getByRole("group", { name: label, exact: true })
        .getByRole("radio", { name: "Yes", exact: true })
        .check();
    }
    for (const label of SATISFIED_PROHIBITED_FACTS) {
      await page
        .getByRole("group", { name: label, exact: true })
        .getByRole("radio", { name: "No", exact: true })
        .check();
    }
    await page.getByRole("spinbutton", { name: "Days since purchase" }).fill("40");

    const preview = page.waitForResponse(
      (response) => response.url().includes("/api/config/policy/preview") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Evaluate" }).click();
    const previewResponse = await preview;
    expect(previewResponse.status(), await previewResponse.text()).toBe(200);
    await expect(page.getByText("Decision: APPROVE")).toBeVisible();

    // Revert.
    const revertWindow = page.getByRole("spinbutton", { name: "Return window" });
    await revertWindow.fill(String(ORIGINAL_DAYS));
    await revertWindow.blur();
    await page.getByRole("button", { name: "Validate" }).click();

    const secondPublish = page.waitForResponse(
      (response) => response.url().includes("/api/config/publish") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Publish" }).click();
    const revertResponse = await secondPublish;
    expect(revertResponse.status(), await revertResponse.text()).toBe(200);

    const afterRevert = await page.request.get("/api/config/runtime");
    const revertedBody = (await afterRevert.json()) as RuntimeBody;
    expect(
      revertedBody.data.configuration.return_eligibility_policy.standard_stock_return.purchase_window.days,
    ).toBe(ORIGINAL_DAYS);
  });
});
