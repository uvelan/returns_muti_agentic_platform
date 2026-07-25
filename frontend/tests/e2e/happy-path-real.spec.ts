import { test, expect } from '@playwright/test';

test.describe('Real Happy-Path Return', () => {
  test('Completes an associate-driven return end-to-end', async ({ page }) => {
    // 1. Start the associate flow
    await page.goto('/associate/returns');
    console.log(await page.content());
    await expect(page.locator('h1')).toHaveText('Returns Assistant', { timeout: 15000 });

    // Make sure we are not in fixture mode if there is a banner (we assume none in prod/e2e if real)

    // 2. Discover Orders (Anchor: ORD-10001)
    // The default anchor is ORD-10001, but let's explicitly fill it to be safe
    const orderInput = page.getByRole('textbox', { name: 'Order number' });
    await expect(orderInput).toBeVisible();
    await orderInput.fill('ORD-10001');
    await page.getByRole('button', { name: 'Discover orders' }).click();

    // 3. Confirm and lock discovery
    await expect(page.getByText('2. Confirm and lock discovery')).toBeVisible({ timeout: 15000 });
    // Default candidate and order line should be selected. Click confirm.
    await page.getByRole('button', { name: 'Confirm and lock' }).click();

    // 4. Complete return details
    await expect(page.getByText('3. Complete return details')).toBeVisible();
    await expect(page.getByText('Discovery locked')).toBeVisible();
    
    // Select Reason "DAMAGED" (default, but let's ensure it's selected or click it)
    await page.getByLabel('Reason').selectOption('DAMAGED');
    await page.getByLabel('Return quantity').fill('1');
    await page.getByLabel('Package count').fill('1');

    await page.getByRole('button', { name: 'Create Return Support request' }).click();

    // 5. Navigate to return timeline
    await expect(page.getByText('Return submitted')).toBeVisible();
    await page.getByRole('link', { name: 'Open live return timeline' }).click();

    // 6. Verify Return state goes to COMPLETED
    // We are on /customer/returns/:sessionId now
    await expect(page.getByText('Return ORD-10001')).toBeVisible();
    
    // The Temporal workflow will transition the status. Wait up to 30 seconds for COMPLETED.
    await expect(page.getByText('COMPLETED', { exact: true })).toBeVisible({ timeout: 30000 });

    // The progress should be 100%
    await expect(page.getByText('100%')).toBeVisible();
  });
});
