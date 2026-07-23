import { test, expect } from '@playwright/test';

test.describe('E2E Navigation', () => {
  test('Navigates existing routes', async ({ page }) => {
    await page.goto('/overview');
    await expect(page.locator('h1').first()).toHaveText('Infrastructure Overview');
    
    await page.click('text=Inventory');
    await expect(page.locator('h1').first()).toHaveText('Data Inventory');
    
    await page.click('text=Graph evidence');
    await expect(page.locator('h1').first()).toHaveText('Customer graph evidence');
  });


  test('Keyboard navigation works', async ({ page }) => {
    await page.goto('/');
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
