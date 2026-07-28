import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import {
  expectNoVisibleMachineTokens,
  expectTechnicalEvidence,
} from './helpers/operator-language';

const username = 'ci-admin';
const password = 'ci-only-password-not-for-production';
const workbenchSections = [
  'overview',
  'sync',
  'generations',
  'restore',
  'maintenance',
  'jobs',
  'retention',
  'configuration',
] as const;

async function login(page: Page, next = '/dashboard/operations/') {
  await page.goto(`/login/?next=${encodeURIComponent(next)}`);
  const loginForm = page.locator('input[name="username"]').locator('xpath=ancestor::form');
  await loginForm.locator('input[name="username"]').fill(username);
  await loginForm.locator('input[name="password"]').fill(password);
  await Promise.all([
    page.waitForURL('**/dashboard/operations/**'),
    loginForm.locator('button[type="submit"]').click(),
  ]);
}

async function switchLanguage(page: Page, language: 'en' | 'mr') {
  const form = page.locator(`form:has(input[name="language"][value="${language}"])`);
  const button = form.locator('button[type="submit"]');
  if (!(await button.isVisible())) {
    await page.locator('.navbar-toggler').click();
    await expect(button).toBeVisible();
  }
  await button.click();
  await expect(page.locator('html')).toHaveAttribute('lang', language);
}

test.describe('Vault Operations Workbench', () => {
  test('passes authority, a11y, locale, no-JS, and zoom gates', async ({ browser, page }) => {
    test.setTimeout(90_000);
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
    await expectNoVisibleMachineTokens(page);
    const firstTechnicalCode = (
      await page.locator('details.operator-evidence code').first().textContent()
    )?.trim();
    if (firstTechnicalCode) await expectTechnicalEvidence(page, firstTechnicalCode);

    const results = await new AxeBuilder({ page }).analyze();
    expect(
      results.violations.filter(
        violation => violation.impact === 'critical' || violation.impact === 'serious',
      ),
    ).toEqual([]);

    for (const section of workbenchSections) {
      await page.goto(`/dashboard/operations/?section=${section}`);
      await expect(
        page.locator('.vault-section-nav a[aria-current="page"]'),
        `current navigation item for en/${section}`,
      ).toHaveAttribute('href', `?section=${section}`);
      await expectNoVisibleMachineTokens(page);
    }

    await switchLanguage(page, 'mr');
    await expect(page.getByRole('heading', { name: 'तिजोरी संचालन कार्यपटल' })).toBeVisible();
    for (const section of workbenchSections) {
      await page.goto(`/dashboard/operations/?section=${section}`);
      await expect(page.locator('html')).toHaveAttribute('lang', 'mr');
      await expect(
        page.locator('.vault-section-nav a[aria-current="page"]'),
        `current navigation item for mr/${section}`,
      ).toHaveAttribute('href', `?section=${section}`);
      await expectNoVisibleMachineTokens(page);
    }
    const marathiResults = await new AxeBuilder({ page }).analyze();
    expect(
      marathiResults.violations.filter(
        violation => violation.impact === 'critical' || violation.impact === 'serious',
      ),
    ).toEqual([]);
    await switchLanguage(page, 'en');

    await page.goto('/dashboard/operations/?section=retention');
    await expect(page.getByRole('heading', { name: 'Generation retirement' })).toBeVisible();
    await expect(page.getByText('It does not delete manifests', { exact: false })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create GC dry-run' })).toBeVisible();

    await page.goto('/dashboard/operations/?section=maintenance');
    await expect(page.getByRole('heading', { name: 'Documents & Indexes' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Choose the outcome you need' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Validate Files' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Repair Stored Indexes' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Reindex Needed' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Reindex Selected' })).toBeVisible();
    await expect(page.getByText('Unchanged during processing')).toBeVisible();
    await expect(page.getByText('Unchanged until explicit publication')).toBeVisible();
    const maintenanceAxe = await new AxeBuilder({ page }).analyze();
    expect(
      maintenanceAxe.violations.filter(
        violation => violation.impact === 'critical' || violation.impact === 'serious',
      ),
    ).toEqual([]);

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
    expect(await noJsPage.content()).toContain('All critical forms remain available');
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
