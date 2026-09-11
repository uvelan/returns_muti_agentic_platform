import { expect, test } from "@playwright/test";

import { requireRealStack } from "../tests/realStack";

/**
 * `/config/overview` against the live stack.
 *
 * **Read-only, deliberately -- this is the one screen in this lease's four
 * whose spec does not publish and revert.** The other three edit a scalar
 * list field a merge patch can cleanly undo. Overview's own mutating action,
 * "Take packaged file" (`POST /api/config/adopt-packaged`), is not that: it
 * publishes a release built from `packaged_configuration.py`'s carry-forward
 * decision across *every* undecided key in the domain it targets, and
 * rewrites the release's `PACKAGED_KEY_DIGESTS`/`PACKAGED_DOMAIN_KEY_DIGESTS`
 * baseline metadata for the whole release, not just the unit requested.
 * Reverting that cleanly on a live stack this run does not own -- shared with
 * whatever else is using it, per the brief's "never restart it, and revert
 * every value you change" -- would mean either second-guessing which of the
 * live graph's currently-undecided keys (`GET /packaged-drift` lists several
 * real ones, e.g. `agents`, `policy_evaluation`) are safe to adopt-and-revert,
 * or publishing a second adoption to try to put the baseline back, which is
 * not the same operation run in reverse. The safer scope is to prove the
 * screen reads the live graph correctly and renders both `would_adopt`
 * shapes (CFG-3a F11) without writing anything.
 */

test.describe("Overview -- real stack", () => {
  test("reads the active release and renders the undecided-keys panel from the live packaged-drift", async ({
    page,
  }) => {
    requireRealStack();

    const drift = await page.request.get("/api/config/packaged-drift");
    expect(drift.status(), await drift.text()).toBe(200);
    const driftBody = (await drift.json()) as {
      data: Record<string, { undecided: string[]; would_adopt: string[]; filled_leaves: string[] }>;
    };

    await page.goto("/config/overview");
    await expect(page.getByText("Runtime snapshot")).toBeVisible();
    await expect(page.getByText("Active release")).toBeVisible();
    await expect(page.getByText("Undecided keys")).toBeVisible();

    // Every undecided unit the live API reports is rendered, labelled per
    // F5's vocabulary (a bare name for RETURN_PLATFORM, DOMAIN: unit
    // otherwise), and offers no action this spec takes.
    for (const [domain, domainDrift] of Object.entries(driftBody.data)) {
      for (const unit of domainDrift.undecided) {
        await expect(page.getByText(`${domain}: ${unit}`)).toBeVisible();
      }
    }

    await expect(page.getByRole("heading", { name: "Undecided keys" })).toBeVisible();
    // The panel states its own vocabulary explicitly (F5) rather than
    // leaving the domain-qualification to be inferred.
    await expect(page.getByText(/a key with no domain prefix is a/i)).toBeVisible();

    // Nothing published: no confirm dialog was ever raised, and no
    // "Adopted ... from the packaged file" success notice appears.
    await expect(page.getByRole("status")).toHaveCount(0);
  });
});
