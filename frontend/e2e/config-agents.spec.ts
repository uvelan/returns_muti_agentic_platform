import { expect, test, type Page } from "@playwright/test";

import { requireRealStack } from "../tests/realStack";

/**
 * `/config/agents` against the live stack: propose an edit, activate it
 * through `/api/proposals` (not the Approvals UI -- this spec proves the
 * activation path repointed at `RETURN_PLATFORM.agents` actually publishes,
 * not that the Approvals screen can click a button), `GET /api/config/runtime`
 * reflects the field, then propose-and-activate the revert.
 *
 * **Unlike every other CFG-4/CFG-5 typed screen, there is no
 * `/api/config/publish` here.** An agent edit stays a governed proposal: the
 * `PUT` in this screen answers 202 with a proposal id, and nothing the
 * runtime snapshot serves moves until that proposal is activated -- which is
 * exactly the property this spec exercises end to end.
 *
 * **`ai_assisted` on `order_discovery`, flipped and flipped back.** A
 * boolean flag read only by `OrderDiscoveryAgent`'s own construction, safe to
 * toggle twice on a shared live stack without touching anything a concurrent
 * case in flight depends on for its outcome.
 *
 * Run this file (and the rest of `cfg4-e2e`) with `--workers=1` --
 * `playwright.config.ts`'s own note on why two of these specs racing trips
 * the optimistic lock for real applies here too: activation publishes a
 * release the same way `/api/config/publish` does.
 */

const AGENT_ID = "order_discovery";

type RuntimeBody = {
  data: { configuration: { agents: Record<string, { ai_assisted: boolean }> } };
};

async function currentAiAssisted(page: Page): Promise<boolean> {
  const response = await page.request.get("/api/config/runtime");
  expect(response.status(), await response.text()).toBe(200);
  const body = (await response.json()) as RuntimeBody;
  return body.data.configuration.agents[AGENT_ID].ai_assisted;
}

async function proposeAndActivate(
  page: Page,
  { toggleTo }: { toggleTo: boolean },
): Promise<void> {
  await page.goto("/config/agents");
  const row = page.getByRole("row", { name: new RegExp(AGENT_ID) });
  const toggle = row.getByRole("checkbox", { name: "AI-assisted" });
  await expect(toggle).toBeVisible();
  if ((await toggle.isChecked()) !== toggleTo) await toggle.click();
  await expect(toggle).toBeChecked({ checked: toggleTo });

  const put = page.waitForResponse(
    (response) => /\/api\/agents\/[^/]+$/.test(response.url()) && response.request().method() === "PUT",
  );
  await row.getByRole("button", { name: "Save" }).click();
  const putResponse = await put;
  expect(putResponse.status(), await putResponse.text()).toBe(202);
  const proposal = (await putResponse.json()) as { data: { proposalId: string } };
  const proposalId = proposal.data.proposalId;

  await expect(page.getByText(new RegExp(proposalId))).toBeVisible();

  const approve = await page.request.post(`/api/proposals/${proposalId}/approve`, {
    data: { note: "e2e: config-agents.spec.ts" },
  });
  expect(approve.status(), await approve.text()).toBe(200);

  const activate = await page.request.post(`/api/proposals/${proposalId}/activate`, {
    data: { parameters: {} },
  });
  expect(activate.status(), await activate.text()).toBe(200);
}

test.describe("Agents -- real stack", () => {
  test("proposes and activates an ai_assisted flip, the runtime snapshot reflects it, then reverts", async ({
    page,
  }) => {
    requireRealStack();

    const before = await currentAiAssisted(page);
    await proposeAndActivate(page, { toggleTo: !before });

    await expect
      .poll(async () => currentAiAssisted(page), {
        message: "runtime snapshot should reflect the activated agent edit",
        timeout: 15_000,
      })
      .toBe(!before);

    await proposeAndActivate(page, { toggleTo: before });

    await expect
      .poll(async () => currentAiAssisted(page), {
        message: "runtime snapshot should reflect the reverted agent edit",
        timeout: 15_000,
      })
      .toBe(before);
  });
});
