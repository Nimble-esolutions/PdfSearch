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
  test('prioritizes work and separates routine recovery from approval-gated actions', async ({ page }) => {
    await login(page);
    await expect(page.getByRole('heading', { name: 'Operations Cockpit' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Needs attention' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Category Yard' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Active Work' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Data protection' })).toBeVisible();

    await expect(page.locator('.cockpit-posture__code')).toHaveCount(0);
    await expect(page.locator('.cockpit-attention-item code')).toHaveCount(0);
    await expectNoVisibleMachineTokens(page);

    await expect(page.locator('.cockpit-metric')).toHaveCount(4);

    const maintenance = page.getByRole('link', { name: 'Maintain Documents & Search' }).first();
    await expect(maintenance).toHaveAttribute('href', /section=maintenance/);
    const dataops = page.getByRole('link', { name: 'Open Data Operations' }).first();
    await expect(dataops).toHaveAttribute('href', /\/dashboard\/operations\/$/);
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
    const unavailableRecord = page.locator('.document-record').filter({ hasText: 'Codex Unavailable PDF' });
    await unavailableRecord.locator('summary[aria-label^="Manage document"]').click();
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

    const bindRecovery = unavailableRecord.getByText('Bind recovery evidence').first();
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

    const availableRecord = page.locator('.document-record').filter({ hasText: 'Codex Smoke PDF' });
    await availableRecord.locator('summary[aria-label^="Manage document"]').click();
    const quarantine = availableRecord.getByText('Mark unavailable').first();
    await quarantine.hover();
    await expectNoSeriousAxeViolations(page);
    await quarantine.focus();
    await expectNoSeriousAxeViolations(page);
    await page.keyboard.press('Enter');
    await expect(page.getByLabel(/Expected digest/).first()).toBeVisible();
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

  test('keeps a 105-document category bounded and responsive', async ({ page }, testInfo) => {
    await login(page);
    await page.locator('.admin-category-card__link').filter({ hasText: 'Codex Scale Category' }).click();

    await expect(page.locator('.document-record')).toHaveCount(25);
    await expect(page.getByText('Page 1 of 5')).toBeVisible();
    await expect(page.getByText('VeryLongUnbrokenDocumentTitle')).toBeVisible();
    await expect(page.getByText('मराठी सहकारी संस्था दस्तऐवज')).toBeVisible();
    await expect(page.locator('.document-action-panel:visible')).toHaveCount(0);

    const configuredWidth = page.viewportSize()?.width || 1440;
    const widths = testInfo.project.name === 'mobile' ? [320, 390] : [configuredWidth];
    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      const dimensions = await page.evaluate(() => {
        const list = document.querySelector('.document-list') as HTMLElement;
        return {
          pageScroll: document.documentElement.scrollWidth,
          pageClient: document.documentElement.clientWidth,
          listScroll: list.scrollWidth,
          listClient: list.clientWidth,
        };
      });
      expect(dimensions.pageScroll).toBeLessThanOrEqual(dimensions.pageClient + 1);
      expect(dimensions.listScroll).toBeLessThanOrEqual(dimensions.listClient + 1);
    }

    const firstRecord = page.locator('.document-record').first();
    const view = firstRecord.getByRole('link', { name: 'View' });
    const manage = firstRecord.locator('summary[aria-label^="Manage document"]');
    for (const control of [view, manage]) {
      const box = await control.boundingBox();
      expect(box?.height || 0).toBeGreaterThanOrEqual(44);
    }
    await manage.focus();
    await page.keyboard.press('Enter');
    await expect(firstRecord.locator('.document-action-panel')).toBeVisible();
    await expect(firstRecord.getByRole('button', { name: /Rename PDF/ })).toBeVisible();
    await expect(firstRecord.getByRole('button', { name: /Delete PDF/ })).toBeVisible();
    await expectNoSeriousAxeViolations(page);
  });

  test('keeps maintenance scope usable with stage-scale categories', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', '320px scope check runs once');
    await login(page);
    await page.goto('/dashboard/data-operations/advanced/');

    await expect(page.getByRole('heading', { name: 'Search maintenance', level: 2 })).toHaveCount(1);
    const scope = page.locator('[data-maintenance-scope]');
    const items = scope.locator('[data-maintenance-scope-item]');
    expect(await items.count()).toBeGreaterThanOrEqual(46);

    const filter = scope.getByLabel('Find categories');
    await filter.fill('Codex Scope Category 01');
    await scope.getByRole('button', { name: 'Select visible' }).click();
    await expect(scope.locator('input[type="checkbox"]:checked')).toHaveCount(1);
    await scope.getByRole('button', { name: 'Clear selection' }).click();
    await expect(scope.locator('input[type="checkbox"]:checked')).toHaveCount(0);

    await page.setViewportSize({ width: 320, height: 844 });
    const firstVisible = scope.locator('[data-maintenance-scope-item]:visible').first();
    const checkboxBox = await firstVisible.locator('input[type="checkbox"]').boundingBox();
    const labelBox = await firstVisible.boundingBox();
    expect(checkboxBox?.width || 0).toBeGreaterThanOrEqual(16);
    expect(checkboxBox?.width || 0).toBeLessThanOrEqual(20);
    expect(labelBox?.height || 0).toBeGreaterThanOrEqual(44);
    const dimensions = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client + 1);
    await expectNoSeriousAxeViolations(page);
  });

});
