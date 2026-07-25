import { expect, test } from "@playwright/test";

test.describe("Real Happy-Path Return", () => {
  test("completes an associate-driven return end to end", async ({ page }) => {
    await page.goto("/associate/returns");
    await expect(page.locator("h1")).toHaveText("Returns Assistant");
    await expect(page.getByText(/FIXTURE MODE|NON-DURABLE/i)).toHaveCount(0);

    const orderInput = page.getByRole("textbox", { name: "Order number" });
    await orderInput.fill("ORD-10001");

    const discoveryResponsePromise = page.waitForResponse((response) =>
      response.url().includes("/api/v1/associate-returns/conversations")
      && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Discover orders" }).click();
    const discoveryResponse = await discoveryResponsePromise;
    const discoveryBody = await discoveryResponse.text();
    expect(
      discoveryResponse.ok(),
      `Associate discovery failed with ${String(discoveryResponse.status())}: ${discoveryBody}`,
    ).toBe(true);

    await expect(page.getByText("2. Confirm and lock discovery")).toBeVisible();
    await page.getByRole("button", { name: "Confirm and lock" }).click();

    await expect(page.getByText("3. Complete return details")).toBeVisible();
    await expect(page.getByText("Discovery locked")).toBeVisible();
    await page.getByLabel("Reason").selectOption("DAMAGED");
    await page.getByLabel("Return quantity").fill("1");
    await page.getByLabel("Package count").fill("1");

    await page.getByRole("button", { name: "Create Return Support request" }).click();
    await expect(page.getByText("Return submitted")).toBeVisible();
    await page.getByRole("link", { name: "Open live return timeline" }).click();

    await expect(page.getByText("Return ORD-10001")).toBeVisible();
    await expect(page.getByText("COMPLETED", { exact: true })).toBeVisible({
      timeout: 120_000,
    });
    await expect(page.getByText("100%")).toBeVisible();
  });
});
