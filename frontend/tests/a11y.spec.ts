import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test.describe('Accessibility', () => {
  test('Overview page should not have any automatically detectable accessibility issues', async ({ page }) => {
    await page.goto('/overview');
    await page.locator('h1').first().waitFor();
    const accessibilityScanResults = await new AxeBuilder({ page }).analyze();
    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('Inventory page should not have any automatically detectable accessibility issues', async ({ page }) => {
    await page.goto('/data-console/inventory');
    await page.locator('h1').first().waitFor();
    const accessibilityScanResults = await new AxeBuilder({ page }).analyze();
    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('Graph evidence page should not have any automatically detectable accessibility issues', async ({ page }) => {
    await page.goto('/data-console/graph-evidence');
    await page.locator('h1').first().waitFor();
    const accessibilityScanResults = await new AxeBuilder({ page }).analyze();
    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('Sources page should not have any automatically detectable accessibility issues', async ({ page }) => {
    await page.goto('/data-console/sources');
    await page.locator('h1').first().waitFor();
    const accessibilityScanResults = await new AxeBuilder({ page }).analyze();
    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('Source Detail page should not have any automatically detectable accessibility issues', async ({ page }) => {
    await page.goto('/data-console/sources/src-sql-omc');
    await page.locator('h1').first().waitFor();
    const accessibilityScanResults = await new AxeBuilder({ page }).analyze();
    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('Data Browser page should not have any automatically detectable accessibility issues', async ({ page }) => {
    await page.goto('/data-console/browser');
    await page.locator('h1').first().waitFor();
    const accessibilityScanResults = await new AxeBuilder({ page }).analyze();
    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('Asset Browser page should not have any automatically detectable accessibility issues', async ({ page }) => {
    await page.goto('/data-console/browser/SQL_SERVER/sales-orders');
    await page.locator('h1').first().waitFor();
    const accessibilityScanResults = await new AxeBuilder({ page }).analyze();
    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('Record Detail page should not have any automatically detectable accessibility issues', async ({ page }) => {
    await page.goto('/data-console/browser/SQL_SERVER/sales-orders/records/SO-1001');
    await page.locator('h1').first().waitFor();
    const accessibilityScanResults = await new AxeBuilder({ page }).analyze();
    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('Graph Explorer page should not have any automatically detectable accessibility issues', async ({ page }) => {
    await page.goto('/data-console/graph');
    await page.locator('h1').first().waitFor();
    // Simulate entering a valid search to render the graph/table
    await page.fill('input[placeholder="Enter Exact Node ID..."]', 'node-123');
    await page.click('button:has-text("Search")');
    await page.click('button:has-text("Table")');
    await page.click('a:has-text("Inspect")');
    await page.locator('h2').filter({ hasText: 'Node: node-123' }).waitFor();
    const accessibilityScanResults = await new AxeBuilder({ page }).analyze();
    expect(accessibilityScanResults.violations).toEqual([]);

    // Switch to table and run a11y check again
    await page.click('button:has-text("Table")');
    await page.locator('table').first().waitFor();
    const tableA11yResults = await new AxeBuilder({ page }).analyze();
    expect(tableA11yResults.violations).toEqual([]);
  });

  test('404 page should not have any automatically detectable accessibility issues', async ({ page }) => {
    await page.goto('/does-not-exist');
    await page.locator('h1').first().waitFor();
    const accessibilityScanResults = await new AxeBuilder({ page }).analyze();
    expect(accessibilityScanResults.violations).toEqual([]);
  });
});
