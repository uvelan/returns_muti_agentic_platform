import { test, expect } from '@playwright/test';

test.describe('E2E Navigation', () => {
  test('Navigates existing routes', async ({ page }) => {
    await page.goto('/overview');
    await expect(page.locator('h1').first()).toHaveText('Infrastructure Overview');

    await page.click('text=Inventory');
    await expect(page.locator('h1').first()).toHaveText('Data Inventory');

    await page.click('text=Graph evidence');
    await expect(page.locator('h1').first()).toHaveText('Customer graph evidence');

    await page.click('text=Data Sources');
    await expect(page.locator('h1').first()).toHaveText('Data Sources');

    await page.click('text=Data Browser');
    await expect(page.locator('h1').first()).toHaveText('Governed Data Browser');
  });

  test('Navigates Data Console dynamic routes', async ({ page }) => {
    // Navigate from sources list to source detail
    await page.goto('/data-console/sources');
    await page.click('text=OMC SQL Server');
    await expect(page.locator('h1').first()).toHaveText('OMC SQL Server');
    await expect(page.locator('text=src-sql-omc').first()).toBeVisible();

    // Navigate from browser landing to asset browser
    await page.goto('/data-console/browser');
    await page.click('text=SalesOrders');
    await expect(page.locator('h1').first()).toHaveText('dbo.SalesOrders');

    // Navigate from asset browser to record detail
    await page.click('text=Details');
    await expect(page.locator('h1').first()).toContainText('Record: row-0');

    // Navigate to Graph Explorer and execute a search
    await page.goto('/data-console/graph');
    await expect(page.locator('h1').first()).toHaveText('Graph Explorer');
    await page.fill('input[placeholder="Enter Exact Node ID..."]', 'node-123');
    await page.click('button:has-text("Search")');
    // Test the GraphTable alternative view
    await page.click('button:has-text("Table")');
    await expect(page.locator('text=No nodes available')).not.toBeVisible();

    // Click on Inspect in the table
    await page.click('a:has-text("Inspect")');
    // It should navigate to the node detail panel
    await expect(page.locator('h2').filter({ hasText: 'Node: ' })).toBeVisible();
  });


  test('Keyboard navigation works', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('a[aria-label="Return Platform overview"]')).toBeVisible();
    // Press tab to hit "Skip to main content"
    await page.keyboard.press('Tab');
    await expect(page.locator('text=Skip to main content')).toBeFocused();
    // Press tab to hit the home link
    await page.keyboard.press('Tab');
    await expect(page.locator('a[aria-label="Return Platform overview"]')).toBeFocused();
  });

  test('Route error state', async ({ page }) => {
    await page.goto('/does-not-exist');
    await expect(page.locator('h1')).toHaveText('404');
    await expect(page.locator('text=The requested console module could not be found.')).toBeVisible();
  });
});
