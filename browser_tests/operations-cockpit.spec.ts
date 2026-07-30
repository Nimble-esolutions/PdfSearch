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

async function expectNoSeriousAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.filter(
      violation => violation.impact === 'critical' || violation.impact === 'serious',
    ),
  ).toEqual([]);
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

    await expectNoSeriousAxeViolations(page);

    await page.goto('/dashboard/?readiness=unavailable');
    await page.getByRole('link', { name: /Codex Smoke Category Renamed/ }).first().click();
    await expect(page.getByText('Document file is unavailable')).toBeVisible();
    const editKeywords = page.getByRole('button', { name: 'Edit Keywords' });
    await editKeywords.hover();
    await expectNoSeriousAxeViolations(page);
    await editKeywords.focus();
    await expectNoSeriousAxeViolations(page);
    await expect(page.getByText('document_media_unavailable')).toBeHidden();
    const technical = page.getByText('Technical details').first();
    await technical.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByText('document_media_unavailable')).toBeVisible();
    await page.keyboard.press('Enter');
    await expect(page.getByText('document_media_unavailable')).toBeHidden();

    const bindRecovery = page.getByText('Bind recovery evidence').first();
    await bindRecovery.click();
    const bindingForm = page.locator('form[action$="/recovery-evidence/"]');
    await bindingForm.evaluate((form: HTMLFormElement) => {
      form.noValidate = true;
    });
    await bindingForm.locator('input[name="expected_sha256"]').fill('not-a-digest');
    await bindingForm.locator('input[name="expected_size"]').fill('19');
    await bindingForm.locator('input[name="case_reference"]').fill('SAFE-RECOVERY');
    await bindingForm.locator('input[name="confirmation"]').fill('BIND RECOVERY EVIDENCE');
    await Promise.all([
      page.waitForURL('**/dashboard/folder/**'),
      bindingForm.locator('button[type="submit"]').click(),
    ]);
    await expect(page.getByText('Review the highlighted fields.').last()).toBeVisible();
    await expect(page.locator('a[href^="#expected_sha256_"]').last()).toBeVisible();
    await expect(page.locator('a[href^="#binding_reason_"]').last()).toBeVisible();
    const bindingAfterRedirect = page.locator('form[action$="/recovery-evidence/"]');
    await expect(bindingAfterRedirect.locator('input[name="case_reference"]')).toHaveValue(
      'SAFE-RECOVERY',
    );
    await expect(bindingAfterRedirect.locator('input[name="expected_sha256"]')).toBeFocused();
    await expect(page.getByText('Restore is unavailable until approved').first()).toBeVisible();

    const quarantine = page.getByText('Mark unavailable').first();
    await quarantine.hover();
    await expectNoSeriousAxeViolations(page);
    await quarantine.focus();
    await expectNoSeriousAxeViolations(page);
    await page.keyboard.press('Enter');
    await expect(page.getByLabel('Expected SHA-256').first()).toBeVisible();
    await expectNoSeriousAxeViolations(page);
    await expect(page.locator('input[name="confirmation"]').first()).toHaveAttribute('lang', 'en');
    await expect(page.locator('input[name="confirmation"]').first()).toHaveAttribute('dir', 'ltr');

    await page.setViewportSize({ width: 320, height: 720 });
    const quarantineDimensions = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(quarantineDimensions.scroll).toBeLessThanOrEqual(quarantineDimensions.client + 1);
    await expectNoSeriousAxeViolations(page);

    await switchLanguage(page, 'mr');
    await expect(page.locator('html')).toHaveAttribute('lang', 'mr');
    await expect(page.getByText('दस्तऐवज संचिका उपलब्ध नाही')).toBeVisible();
    await expect(page.getByText('अपेक्षित संचिका आकार (बाइटमध्ये)').first()).toHaveCount(1);
    await expectNoVisibleMachineTokens(page);

    await expectNoSeriousAxeViolations(page);
  });

});
