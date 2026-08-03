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
  test('keeps the v3 recovery workflow simple and approval-gated', async ({ browser, page }) => {
    await login(page);
    await expect(page.getByRole('heading', { name: 'Data protection', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Check health' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Back up data' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Recovery points' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Search maintenance' })).toBeVisible();

    const workbenchForms = page.locator('.dataops-workbench form');
    expect(await workbenchForms.count()).toBeGreaterThan(0);
    expect(await workbenchForms.evaluateAll(forms => forms.map(form => ({
      action: form.getAttribute('action'),
      method: form.getAttribute('method')?.toLowerCase(),
    })))).toEqual(expect.arrayContaining([
      { action: '/dashboard/data-operations/actions/', method: 'post' },
    ]));
    expect(await workbenchForms.evaluateAll(forms => forms.every(form => (
      form.getAttribute('action') === '/dashboard/data-operations/actions/'
      && form.getAttribute('method')?.toLowerCase() === 'post'
    )))).toBe(true);

    const evidence = page.locator('details.dataops-history');
    await expect(evidence).toBeVisible();
    await expect(evidence.locator('summary')).toHaveText(/Activity and technical evidence/);

    await expect(page.locator('input[name*="profile"], select[name*="profile"], textarea[name*="profile"]')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /clone|rebind/i })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: /storage profiles|clone|rebind/i })).toHaveCount(0);
    await expectNoVisibleMachineTokens(page);
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations.filter(v => v.impact === 'critical' || v.impact === 'serious')).toEqual([]);

    const storageState = await page.context().storageState();
    const origin = new URL(page.url()).origin;
    const noJsContext = await browser.newContext({ javaScriptEnabled: false, storageState });
    const noJsPage = await noJsContext.newPage();
    await noJsPage.goto(`${origin}/dashboard/operations/`);
    await expect(noJsPage.getByRole('heading', { name: 'Data protection', exact: true })).toBeVisible();
    await expect(noJsPage.getByRole('heading', { name: 'Recovery points' })).toBeVisible();
    await expect(noJsPage.locator('form[action="/dashboard/data-operations/actions/"]')).not.toHaveCount(0);
    await noJsContext.close();

    await page.setViewportSize({ width: 320, height: 720 });
    const dimensions = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client + 1);
  });
});
