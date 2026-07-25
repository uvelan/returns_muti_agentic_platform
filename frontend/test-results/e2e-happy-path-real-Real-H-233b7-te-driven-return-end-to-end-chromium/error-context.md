# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: e2e\happy-path-real.spec.ts >> Real Happy-Path Return >> Completes an associate-driven return end-to-end
- Location: tests\e2e\happy-path-real.spec.ts:4:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText('2. Confirm and lock discovery')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByText('2. Confirm and lock discovery')

```

```yaml
- link "Skip to main content":
  - /url: "#main-content"
- complementary "Primary navigation":
  - link "Return Platform overview":
    - /url: /
    - text: Return Platform Data Console
  - navigation "Sidebar":
    - heading "Associate" [level=3]
    - link "Returns Assistant":
      - /url: /associate/returns
    - heading "Customer" [level=3]
    - link "My Returns":
      - /url: /customer/returns
    - heading "Support" [level=3]
    - link "Support Returns":
      - /url: /support/returns
    - link "Review Queue":
      - /url: /support/review-queue
    - link "Operations":
      - /url: /support/operations
    - heading "AI Gateway" [level=3]
    - link "AI Requests":
      - /url: /ai-gateway/requests
    - link "AI Simulator":
      - /url: /ai-gateway/simulator
    - link "Interceptions":
      - /url: /ai-gateway/interceptions
    - heading "Explore" [level=3]
    - link "Overview":
      - /url: /overview
    - link "Model & Schema":
      - /url: /data-console/schema
    - link "Inventory":
      - /url: /data-console/inventory
    - link "Graph Evidence":
      - /url: /data-console/graph-evidence
    - link "Data Sources":
      - /url: /data-console/sources
    - link "Data Browser":
      - /url: /data-console/browser
    - link "Graph Explorer":
      - /url: /data-console/graph
    - heading "Data Operations" [level=3]
    - link "Graph Sync":
      - /url: /data-console/graph-sync
    - link "Imports":
      - /url: /data-console/imports
    - link "Exports":
      - /url: /data-console/exports
    - link "Jobs & Activity":
      - /url: /data-console/jobs
    - heading "Sandbox & AI" [level=3]
    - link "AI Studio":
      - /url: /data-console/ai-studio
    - link "Workspaces":
      - /url: /data-console/workspaces
    - link "Scenarios (What-If)":
      - /url: /data-console/scenarios
    - heading "Governance" [level=3]
    - link "Feedback Learning":
      - /url: /data-console/feedback-learning
    - link "Audit":
      - /url: /data-console/audit
    - link "Governance":
      - /url: /data-console/governance
    - link "Settings":
      - /url: /data-console/settings
    - link "Hardening":
      - /url: /data-console/hardening
    - heading "System" [level=3]
    - link "Dependencies":
      - /url: /system/dependencies
    - link "Seed Data":
      - /url: /seed-data
- main:
  - navigation "Breadcrumb":
    - list:
      - listitem:
        - link "associate":
          - /url: /associate
      - listitem: Returns Assistant
  - heading "Returns Assistant" [level=1]
  - paragraph: Start with one strong anchor. The assistant discovers candidates, asks for confirmation, seals the order line, collects only missing return details, and hands the request to Return Support.
  - heading "An error occurred" [level=3]
  - paragraph: "Associate discovery failed: ValidationError"
  - heading "1. Minimal evidence" [level=2]
  - text: Evidence type
  - combobox "Evidence type":
    - option "Order number" [selected]
    - option "Customer ID"
    - option "Phone"
    - option "Email"
    - option "Tracking number"
    - option "SKU / product"
  - text: Order number
  - textbox "Order number": ORD-10001
  - button "Discover orders":
    - img
    - text: Discover orders
  - heading "How it works" [level=2]
  - list:
    - listitem: Enter one strong anchor.
    - listitem: Graph-first discovery returns candidate orders and lines.
    - listitem: The associate confirms the exact line; the context is digest-locked.
    - listitem: The assistant collects reason, quantity, packages, shipping path, and notes.
    - listitem: Return Support creates and follows the ticket; SQL and Neo4j remain source-aligned.
```

# Test source

```ts
  1  | import { test, expect } from '@playwright/test';
  2  | 
  3  | test.describe('Real Happy-Path Return', () => {
  4  |   test('Completes an associate-driven return end-to-end', async ({ page }) => {
  5  |     // 1. Start the associate flow
  6  |     await page.goto('/associate/returns');
  7  |     console.log(await page.content());
  8  |     await expect(page.locator('h1')).toHaveText('Returns Assistant', { timeout: 15000 });
  9  | 
  10 |     // Make sure we are not in fixture mode if there is a banner (we assume none in prod/e2e if real)
  11 | 
  12 |     // 2. Discover Orders (Anchor: ORD-10001)
  13 |     // The default anchor is ORD-10001, but let's explicitly fill it to be safe
  14 |     const orderInput = page.getByRole('textbox', { name: 'Order number' });
  15 |     await expect(orderInput).toBeVisible();
  16 |     await orderInput.fill('ORD-10001');
  17 |     await page.getByRole('button', { name: 'Discover orders' }).click();
  18 | 
  19 |     // 3. Confirm and lock discovery
> 20 |     await expect(page.getByText('2. Confirm and lock discovery')).toBeVisible();
     |                                                                   ^ Error: expect(locator).toBeVisible() failed
  21 |     // Default candidate and order line should be selected. Click confirm.
  22 |     await page.getByRole('button', { name: 'Confirm and lock' }).click();
  23 | 
  24 |     // 4. Complete return details
  25 |     await expect(page.getByText('3. Complete return details')).toBeVisible();
  26 |     await expect(page.getByText('Discovery locked')).toBeVisible();
  27 |     
  28 |     // Select Reason "DAMAGED" (default, but let's ensure it's selected or click it)
  29 |     await page.getByLabel('Reason').selectOption('DAMAGED');
  30 |     await page.getByLabel('Return quantity').fill('1');
  31 |     await page.getByLabel('Package count').fill('1');
  32 | 
  33 |     await page.getByRole('button', { name: 'Create Return Support request' }).click();
  34 | 
  35 |     // 5. Navigate to return timeline
  36 |     await expect(page.getByText('Return submitted')).toBeVisible();
  37 |     await page.getByRole('link', { name: 'Open live return timeline' }).click();
  38 | 
  39 |     // 6. Verify Return state goes to COMPLETED
  40 |     // We are on /customer/returns/:sessionId now
  41 |     await expect(page.getByText('Return ORD-10001')).toBeVisible();
  42 |     
  43 |     // The Temporal workflow will transition the status. Wait up to 30 seconds for COMPLETED.
  44 |     await expect(page.getByText('COMPLETED', { exact: true })).toBeVisible({ timeout: 30000 });
  45 | 
  46 |     // The progress should be 100%
  47 |     await expect(page.getByText('100%')).toBeVisible();
  48 |   });
  49 | });
  50 | 
```