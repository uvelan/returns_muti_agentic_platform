# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: e2e.spec.ts >> E2E Navigation >> Navigates Data Console dynamic routes
- Location: tests\e2e.spec.ts:21:3

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: page.click: Test timeout of 30000ms exceeded.
Call log:
  - waiting for locator('text=OMC SQL Server')

```

# Test source

```ts
  1  | import { test, expect } from '@playwright/test';
  2  | 
  3  | test.describe('E2E Navigation', () => {
  4  |   test('Navigates existing routes', async ({ page }) => {
  5  |     await page.goto('/overview');
  6  |     await expect(page.locator('h1').first()).toHaveText('Infrastructure Overview');
  7  | 
  8  |     await page.click('text=Inventory');
  9  |     await expect(page.locator('h1').first()).toHaveText('Data Inventory');
  10 | 
  11 |     await page.click('text=Graph evidence');
  12 |     await expect(page.locator('h1').first()).toHaveText('Customer graph evidence');
  13 | 
  14 |     await page.click('text=Data Sources');
  15 |     await expect(page.locator('h1').first()).toHaveText('Data Sources');
  16 | 
  17 |     await page.click('text=Data Browser');
  18 |     await expect(page.locator('h1').first()).toHaveText('Governed Data Browser');
  19 |   });
  20 | 
  21 |   test('Navigates Data Console dynamic routes', async ({ page }) => {
  22 |     // Navigate from sources list to source detail
  23 |     await page.goto('/data-console/sources');
> 24 |     await page.click('text=OMC SQL Server');
     |                ^ Error: page.click: Test timeout of 30000ms exceeded.
  25 |     await expect(page.locator('h1').first()).toHaveText('OMC SQL Server');
  26 |     await expect(page.locator('text=src-sql-omc').first()).toBeVisible();
  27 | 
  28 |     // Navigate from browser landing to asset browser
  29 |     await page.goto('/data-console/browser');
  30 |     await page.click('text=SalesOrders');
  31 |     await expect(page.locator('h1').first()).toHaveText('dbo.SalesOrders');
  32 | 
  33 |     // Navigate from asset browser to record detail
  34 |     await page.click('text=Details');
  35 |     await expect(page.locator('h1').first()).toContainText('Record: row-0');
  36 | 
  37 |     // Navigate to Graph Explorer and execute a search
  38 |     await page.goto('/data-console/graph');
  39 |     await expect(page.locator('h1').first()).toHaveText('Graph Explorer');
  40 |     await page.fill('input[placeholder="Enter Exact Node ID..."]', 'node-123');
  41 |     await page.click('button:has-text("Search")');
  42 |     // Test the GraphTable alternative view
  43 |     await page.click('button:has-text("Table")');
  44 |     await expect(page.locator('text=No nodes available')).not.toBeVisible();
  45 | 
  46 |     // Click on Inspect in the table
  47 |     await page.click('a:has-text("Inspect")');
  48 |     // It should navigate to the node detail panel
  49 |     await expect(page.locator('h2').filter({ hasText: 'Node: ' })).toBeVisible();
  50 |   });
  51 | 
  52 | 
  53 |   test('Keyboard navigation works', async ({ page }) => {
  54 |     await page.goto('/');
  55 |     await expect(page.locator('a[aria-label="Return Platform overview"]')).toBeVisible();
  56 |     // Press tab to hit "Skip to main content"
  57 |     await page.keyboard.press('Tab');
  58 |     await expect(page.locator('text=Skip to main content')).toBeFocused();
  59 |     // Press tab to hit the home link
  60 |     await page.keyboard.press('Tab');
  61 |     await expect(page.locator('a[aria-label="Return Platform overview"]')).toBeFocused();
  62 |   });
  63 | 
  64 |   test('Route error state', async ({ page }) => {
  65 |     await page.goto('/does-not-exist');
  66 |     await expect(page.locator('h1')).toHaveText('404');
  67 |     await expect(page.locator('text=The requested console module could not be found.')).toBeVisible();
  68 |   });
  69 | });
  70 | 
```