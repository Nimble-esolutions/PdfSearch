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
  test('passes authority, a11y, locale, no-JS, and zoom gates', async ({ browser, page }) => {
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

    const results = await new AxeBuilder({ page }).analyze();
    expect(
      results.violations.filter(
        violation => violation.impact === 'critical' || violation.impact === 'serious',
      ),
    ).toEqual([]);

    await page.getByRole('button', { name: 'मराठी' }).click();
    await page.waitForLoadState('networkidle');
    await expect(page.locator('html')).toHaveAttribute('lang', 'mr');
    await expect(page.getByRole('heading', { name: 'तिजोरी संचालन कार्यपटल' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'अधिकृत स्थिती तुलना' })).toBeVisible();
    await page.getByRole('button', { name: 'English' }).click();
    await page.waitForLoadState('networkidle');

    const storageState = await page.context().storageState();
    const origin = new URL(page.url()).origin;
    const noJsContext = await browser.newContext({
      javaScriptEnabled: false,
      storageState,
    });
    const noJsPage = await noJsContext.newPage();
    await noJsPage.goto(`${origin}/dashboard/operations/?section=sync`);
    const form = noJsPage.locator('form[action$="/sync/run/"]');
    await expect(form).toBeVisible();
    await expect(form.locator('input[name="idempotency_key"]')).toHaveCount(1);
    await expect(form.locator('input[name="state_version"]')).toHaveCount(1);
    await expect(noJsPage.locator('noscript')).toContainText('All critical forms remain available');
    await noJsContext.close();

    await page.setViewportSize({ width: 320, height: 720 });
    await page.goto('/dashboard/operations/');
    const dimensions = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client + 1);
  });
});
