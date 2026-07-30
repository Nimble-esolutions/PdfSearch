import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { expectNoVisibleMachineTokens } from './helpers/operator-language';

async function login(page: Page) {
  await page.goto('/login/?next=/dashboard/');
  const form = page.locator('input[name="username"]').locator('xpath=ancestor::form');
  await form.locator('input[name="username"]').fill('ci-admin');
  await form.locator('input[name="password"]').fill('ci-only-password-not-for-production');
  await Promise.all([
    page.waitForURL('**/dashboard/'),
    form.locator('button[type="submit"]').click(),
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

test.describe('Operations Cockpit', () => {
  test('prioritizes work and separates local maintenance from Vault authority', async ({ page }) => {
    await login(page);
    await expect(page.getByRole('heading', { name: 'Operations Cockpit' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Needs attention' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Category Yard' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Active Work' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Vault posture' })).toBeVisible();

    await expect(page.locator('.cockpit-posture__code')).toHaveCount(0);
    await expect(page.locator('.cockpit-attention-item code')).toHaveCount(0);
    await expectNoVisibleMachineTokens(page);

    await expect(page.locator('.cockpit-metric')).toHaveCount(4);

    const maintenance = page.getByRole('link', { name: 'Maintain Documents & Search' }).first();
    await expect(maintenance).toHaveAttribute('href', /section=maintenance/);
    const vault = page.getByRole('link', { name: 'Open advanced Vault & Recovery' }).first();
    await expect(vault).toHaveAttribute('href', /\/dashboard\/operations\/$/);
    await expect(page.getByText('sync data to S3')).toHaveCount(0);
    await expect(page.locator('#job-drawer-toggle')).toHaveCount(0);

    const filters = page.getByRole('search', { name: 'Filter categories' });
    await expect(filters.getByLabel('Find category')).toBeVisible();
    await expect(filters.getByLabel('Search readiness')).toBeVisible();
    await expect(filters.getByLabel('Provenance')).toBeVisible();
    await expect(filters.getByLabel('Occupancy')).toBeVisible();
    await expect(filters.getByLabel('Sort')).toBeVisible();

    const dimensions = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client + 1);

    const results = await new AxeBuilder({ page }).analyze();
    expect(
      results.violations.filter(
        violation => violation.impact === 'critical' || violation.impact === 'serious',
      ),
    ).toEqual([]);

    await page.goto('/dashboard/?readiness=unavailable');
    await page.getByRole('link', { name: /Codex Smoke Category Renamed/ }).first().click();
    await expect(page.getByText('Document file is unavailable')).toBeVisible();
    await expect(page.getByText('document_media_unavailable')).toBeHidden();
    const technical = page.getByText('Technical details').first();
    await technical.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByText('document_media_unavailable')).toBeVisible();
    await page.keyboard.press('Enter');
    await expect(page.getByText('document_media_unavailable')).toBeHidden();

    const quarantine = page.getByText('Mark unavailable').first();
    await quarantine.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByLabel('Expected SHA-256').first()).toBeVisible();
    await expect(page.locator('input[name="confirmation"]').first()).toHaveAttribute('lang', 'en');
    await expect(page.locator('input[name="confirmation"]').first()).toHaveAttribute('dir', 'ltr');

    await page.setViewportSize({ width: 320, height: 720 });
    const quarantineDimensions = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(quarantineDimensions.scroll).toBeLessThanOrEqual(quarantineDimensions.client + 1);
    const quarantineResults = await new AxeBuilder({ page }).analyze();
    expect(
      quarantineResults.violations.filter(
        violation => violation.impact === 'critical' || violation.impact === 'serious',
      ),
    ).toEqual([]);

    await switchLanguage(page, 'mr');
    await expect(page.locator('html')).toHaveAttribute('lang', 'mr');
    await expect(page.getByText('दस्तऐवज संचिका उपलब्ध नाही')).toBeVisible();
    await expect(page.getByText('अपेक्षित संचिका आकार (बाइटमध्ये)').first()).toHaveCount(1);
    await expectNoVisibleMachineTokens(page);

    const marathiResults = await new AxeBuilder({ page }).analyze();
    expect(
      marathiResults.violations.filter(
        violation => violation.impact === 'critical' || violation.impact === 'serious',
      ),
    ).toEqual([]);
  });

});
