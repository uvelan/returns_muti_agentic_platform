import { expect, test } from "@playwright/test";

test.describe("Real Happy-Path Return", () => {
  test("completes an associate-driven return end to end", async ({ page }) => {
    await page.goto("/associate/returns");
    await expect(page.getByRole("heading", { name: "Returns Assistant" })).toBeVisible();

    const copilotInput = page.getByRole("textbox", { name: "Message the Discovery Copilot" });
    await copilotInput.fill("ORD-10001");

    const chatResponsePromise = page.waitForResponse((response) =>
      response.url().includes("/api/v1/associate-returns/chat")
      && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Send message" }).click();
    const chatResponse = await chatResponsePromise;
    const chatBody = await chatResponse.text();
    expect(
      chatResponse.ok(),
      `Associate discovery chat failed with ${String(chatResponse.status())}: ${chatBody}`,
    ).toBe(true);

    await expect(page.getByRole("button", { name: "Confirm and lock" })).toBeVisible();
    await page.getByRole("button", { name: "Confirm and lock" }).click();

    await expect(page.getByText("Order evidence locked")).toBeVisible();
    await page.getByRole("button", { name: "Damaged" }).click();
    await page.getByLabel("Quantity").fill("1");
    await page.getByLabel("Packages").fill("1");

    await page.getByRole("button", { name: "Send to workflow" }).click();
    await expect(page.getByRole("link", { name: "Open live return timeline" })).toBeVisible();
    await page.getByRole("link", { name: "Open live return timeline" }).click();

    await expect(page.getByRole("heading", { name: "Return ORD-10001" })).toBeVisible();
    await expect(page.getByText("Completed", { exact: true })).toBeVisible({
      timeout: 120_000,
    });
    await expect(page.getByText("100%")).toBeVisible();
  });
});
