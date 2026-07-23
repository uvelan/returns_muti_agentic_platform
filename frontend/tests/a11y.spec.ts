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

  test('404 page should not have any automatically detectable accessibility issues', async ({ page }) => {
    await page.goto('/does-not-exist');
    const accessibilityScanResults = await new AxeBuilder({ page }).analyze();
    expect(accessibilityScanResults.violations).toEqual([]);
  });
});
