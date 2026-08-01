import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { expectNoVisibleMachineTokens } from './helpers/operator-language';

async function login(page: Page) {
  await page.goto('/login/?next=/dashboard/operations/');
  const form = page.locator('input[name="username"]').locator('xpath=ancestor::form');
  await form.locator('input[name="username"]').fill('ci-admin');
  await form.locator('input[name="password"]').fill('ci-only-password-not-for-production');
  await Promise.all([
    page.waitForURL('**/dashboard/operations/'),
    form.locator('button[type="submit"]').click(),
  ]);
}

test.describe('Data Operations', () => {
  test('keeps routine recovery simple and approval-gated', async ({ browser, page }) => {
    await login(page);
    await expect(page.getByRole('heading', { name: 'Data operations' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Refresh data' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Back up data' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Restore data' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Storage and automation' })).toBeVisible();
    await expect(page.locator('form[action$="/refresh/"] input[name="idempotency_key"]')).toHaveCount(1);
    await expect(page.locator('details.dataops-history')).toBeVisible();
    await expectNoVisibleMachineTokens(page);
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations.filter(v => v.impact === 'critical' || v.impact === 'serious')).toEqual([]);

    const storageState = await page.context().storageState();
    const origin = new URL(page.url()).origin;
    const noJsContext = await browser.newContext({ javaScriptEnabled: false, storageState });
    const noJsPage = await noJsContext.newPage();
    await noJsPage.goto(`${origin}/dashboard/operations/`);
    await expect(noJsPage.getByRole('heading', { name: 'Data operations' })).toBeVisible();
    await noJsContext.close();

    await page.setViewportSize({ width: 320, height: 720 });
    const dimensions = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client + 1);
  });
});
