import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const username = 'ci-admin';
const password = 'ci-only-password-not-for-production';

async function login(page: Page, next = '/dashboard/operations/') {
  await page.goto(`/login/?next=${encodeURIComponent(next)}`);
  await page.locator('input[name="username"]').fill(username);
  await page.locator('input[name="password"]').fill(password);
  await page.locator('form button[type="submit"]').first().click();
  await page.waitForURL('**/dashboard/operations/**');
}

test.describe('Vault Operations Workbench', () => {
  test('renders independent authority evidence without CDN requests', async ({ page }) => {
    const externalRequests: string[] = [];
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.hostname === 'cdn.jsdelivr.net') externalRequests.push(request.url());
    });
    await login(page);
    await expect(page.getByRole('heading', { name: 'Vault Operations Workbench' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Authority comparison' })).toBeVisible();
    await expect(page.locator('[data-summary="runtime"]')).toBeVisible();
    await expect(page.locator('[data-summary="remote"]')).toBeVisible();
    await expect(page.locator('link[href*="vendor/bootstrap/5.3.0"]')).toHaveCount(1);
    expect(externalRequests).toEqual([]);
  });

  test('has no serious or critical accessibility violations', async ({ page }) => {
    await login(page);
    const results = await new AxeBuilder({ page }).analyze();
    expect(
      results.violations.filter(
        violation => violation.impact === 'critical' || violation.impact === 'serious',
      ),
    ).toEqual([]);
  });

  test('keeps critical forms in document order without JavaScript', async ({ browser }) => {
    const context = await browser.newContext({ javaScriptEnabled: false });
    const page = await context.newPage();
    await login(page, '/dashboard/operations/?section=sync');
    await page.goto('/dashboard/operations/?section=sync');
    const form = page.locator('form[action$="/sync/run/"]');
    await expect(form).toBeVisible();
    await expect(form.locator('input[name="idempotency_key"]')).toHaveCount(1);
    await expect(form.locator('input[name="state_version"]')).toHaveCount(1);
    await expect(page.locator('noscript')).toContainText('All critical forms remain available');
    await context.close();
  });

  test('renders reviewed Marathi labels', async ({ page }) => {
    await login(page);
    await page.getByRole('button', { name: 'मराठी' }).click();
    await page.waitForLoadState('networkidle');
    await expect(page.locator('html')).toHaveAttribute('lang', 'mr');
    await expect(page.getByRole('heading', { name: 'तिजोरी संचालन कार्यपटल' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'अधिकृत स्थिती तुलना' })).toBeVisible();
  });

  test('does not overflow at the 320px CSS viewport used for 200 percent zoom', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 720 });
    await login(page);
    const dimensions = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client + 1);
  });
});
