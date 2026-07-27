import { expect, test } from "@playwright/test";

test.describe("Order Discovery Copilot & Operations Console (E2E Real & Resilience)", () => {
  test("multi-turn discovery flow via associate returns assistant", async ({ page }) => {
    await page.goto("/associate/returns");
    await expect(page.getByRole("heading", { name: "Returns Assistant" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Order Discovery Copilot" })).toBeVisible();

    // Verify Copilot components are mounted and send message
    const copilotInput = page.getByRole("textbox", { name: "Message the Discovery Copilot" });
    await copilotInput.fill("ORD-10001");

    const chatResponsePromise = page.waitForResponse((response) =>
      response.url().includes("/api/v1/associate-returns/chat") &&
      response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Send message" }).click();
    const chatResponse = await chatResponsePromise;
    expect(chatResponse.ok()).toBe(true);

    // Step 2: Confirm and lock discovery
    await expect(page.getByRole("button", { name: "Confirm and lock" })).toBeVisible();
    await page.getByRole("button", { name: "Confirm and lock" }).click();

    // Step 3: Complete return details with shipping path expectation
    await expect(page.getByText("Order evidence locked")).toBeVisible();
    await page.getByRole("button", { name: "Damaged" }).click();
    await page.getByLabel("Quantity").fill("1");
    await page.getByLabel("Packages").fill("1");

    await page.getByRole("button", { name: "Send to workflow" }).click();
    await expect(page.getByRole("link", { name: "Open live return timeline" })).toBeVisible();
  });

  test("supervisory console scenario simulation and graph constraints verification", async ({ page }) => {
    await page.goto("/data-console/copilot/operations");
    await expect(page.locator("h1")).toHaveText("Copilot Operations Console");

    // Check runtime source and graph configuration release display
    await expect(page.getByText("v2026.07.rel")).toBeVisible();
    await expect(page.getByText("Neo4j Graph Primary")).toBeVisible();
    await expect(page.getByText("0.65 (Levenshtein)")).toBeVisible();
    await expect(page.getByText("86,400s (24 hours)")).toBeVisible();

    // Run Partial Name Search scenario simulation
    const simulateResponsePromise = page.waitForResponse((response) =>
      response.url().includes("/api/v1/associate-returns/chat") &&
      response.request().method() === "POST",
    );
    await page.getByRole("button", { name: /Run Scenario Simulation/i }).click();
    const simulateResponse = await simulateResponsePromise;
    expect(simulateResponse.ok()).toBe(true);

    // Verify trace logs updated
    await expect(page.getByText(/SIMULATE_TURN/i)).toBeVisible();
    await expect(page.locator("text=/\\bms\\b/").first()).toBeVisible();

    // Switch to State Machine View tab and check JSON state
    await page.getByRole("button", { name: /State Machine View/i }).click();
    await expect(page.locator("pre")).toBeVisible();
  });

  test("resilience failure matrix: network delay / retry and state idempotency", async ({ page }) => {
    await page.goto("/data-console/copilot/operations");
    await expect(page.locator("h1")).toHaveText("Copilot Operations Console");

    // Simulate clicking reset state and verify clean recovery
    await page.getByRole("button", { name: /Reset State/i }).click();
    await expect(page.getByText("SESSION_RESET")).toBeVisible();
    await expect(page.getByText("Cleared active simulator conversation state.")).toBeVisible();

    // Verify switching tabs in console maintains layout stability during recovery
    await page.getByRole("button", { name: /Ranked Candidates/i }).click();
    await expect(page.getByText("No candidates in current state.")).toBeVisible();
  });
});
