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
  test('receives a reviewed multi-file intake and can discard it safely', async ({ page }) => {
    await login(page);
    await page.locator('.admin-category-card__link').filter({ hasText: 'Codex Scale Category' }).click();

    const intake = page.locator('[data-pdf-intake]');
    await expect(intake.getByRole('heading', { name: 'Add Documents' })).toBeVisible();
    await intake.locator('[data-intake-input]').setInputFiles([
      {
        name: 'society-audit-order.pdf',
        mimeType: 'application/pdf',
        buffer: Buffer.from('%PDF-1.7\nCodex disposable intake fixture A\n'),
      },
      {
        name: 'committee-election-rules.pdf',
        mimeType: 'application/pdf',
        buffer: Buffer.from('%PDF-1.7\nCodex disposable intake fixture B\n'),
      },
    ]);

    const rows = intake.locator('[data-intake-item]');
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0).locator('[data-intake-state]')).toHaveText('Ready to upload');
    await rows.nth(1).locator('[data-intake-title]').fill('Committee Election Rules 2026');
    await expect(rows.nth(1).locator('[data-intake-title]')).toBeEditable();

    await intake.getByRole('button', { name: 'Receive Selected Files' }).click();
    await expect(rows.nth(0).locator('[data-intake-state]')).toHaveText('Received');
    await expect(rows.nth(1).locator('[data-intake-state]')).toHaveText('Received');
    await expect(intake.getByRole('button', { name: 'Process Ready Documents' })).toBeEnabled();

    await rows.nth(0).getByRole('button', { name: /Remove file/ }).click();
    await expect(rows).toHaveCount(1);
    await intake.getByRole('button', { name: 'Process Ready Documents' }).click();
    await expect(intake.getByRole('button', { name: 'Start New Intake' })).toBeVisible();
    await intake.getByRole('button', { name: 'Start New Intake' }).click();
    await expect(rows).toHaveCount(0);

    await intake.locator('[data-intake-input]').setInputFiles({
      name: 'fresh-intake.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('%PDF-1.7\nCodex disposable fresh intake fixture\n'),
    });
    await intake.getByRole('button', { name: 'Receive Selected Files' }).click();
    await expect(rows.locator('[data-intake-state]')).toHaveText('Received');
    await intake.getByRole('button', { name: 'Discard Draft' }).click();
    await expect(rows).toHaveCount(0);
    await expect(intake.locator('[data-intake-live]')).toHaveText('The draft intake was discarded.');

    const dimensions = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client + 1);
    await expectNoSeriousAxeViolations(page);
  });

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
    const unavailableRecord = page.locator('.document-record').filter({ hasText: 'Codex Unavailable PDF' });
    await expect(unavailableRecord.getByText('Document file is unavailable').first()).toBeVisible();
    await unavailableRecord.locator('summary[aria-label^="Manage document"]').click();
    const visibilityRecovery = unavailableRecord
      .locator('details.document-advanced')
      .filter({ hasText: 'Visibility & Recovery' });
    await visibilityRecovery.locator('summary').first().click();
    const editKeywords = page.getByRole('button', { name: 'Edit Keywords' });
    await editKeywords.hover();
    await expectNoSeriousAxeViolations(page);
    await editKeywords.focus();
    await expectNoSeriousAxeViolations(page);
    const unavailableEvidence = visibilityRecovery
      .locator('details.operator-evidence')
      .filter({ hasText: 'document_media_unavailable' });
    const unavailableCode = unavailableEvidence.getByText('document_media_unavailable', { exact: true });
    await expect(unavailableCode).toBeHidden();
    const technical = unavailableEvidence.locator('summary');
    await technical.click();
    await expect(unavailableCode).toBeVisible();
    await technical.click();
    await expect(unavailableCode).toBeHidden();

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
    const availableVisibility = availableRecord
      .locator('details.document-advanced')
      .filter({ hasText: 'Visibility & Recovery' });
    await availableVisibility.locator('summary').first().click();
    const quarantine = availableVisibility.getByText('Mark unavailable').first();
    await quarantine.hover();
    await expectNoSeriousAxeViolations(page);
    await quarantine.focus();
    await expectNoSeriousAxeViolations(page);
    await page.keyboard.press('Enter');
    const quarantineForm = availableVisibility.locator('form[action$="/unavailable/"]');
    await expect(quarantineForm.getByLabel(/Expected digest/)).toBeVisible();
    await expectNoSeriousAxeViolations(page);
    await expect(quarantineForm.locator('input[name="confirmation"]')).toHaveAttribute('lang', 'en');
    await expect(quarantineForm.locator('input[name="confirmation"]')).toHaveAttribute('dir', 'ltr');

    await page.setViewportSize({ width: 320, height: 720 });
    const quarantineDimensions = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(quarantineDimensions.scroll).toBeLessThanOrEqual(quarantineDimensions.client + 1);
    await expectNoSeriousAxeViolations(page);

    await switchLanguage(page, 'mr');
    await expect(page.locator('html')).toHaveAttribute('lang', 'mr');
    await expect(unavailableRecord.getByText('दस्तऐवज संचिका उपलब्ध नाही').first()).toBeVisible();
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

    const technicalOperations = page.locator('details.technical-operations');
    await technicalOperations.locator(':scope > summary').click();
    const indexGuidance = technicalOperations.locator('.operator-guidance');
    await expect(indexGuidance).toHaveCount(1);
    await expect(indexGuidance).toBeVisible();
    await expect(
      technicalOperations.getByRole('button', { name: 'Repair Stored Index' }),
    ).toBeDisabled();
    await expect(
      technicalOperations.getByRole('button', { name: 'Reprocess Needed' }),
    ).toBeDisabled();
    await expect(
      technicalOperations.getByRole('button', { name: 'Reprocess All' }),
    ).toBeDisabled();
    await expect(
      technicalOperations.getByRole('link', { name: 'Open search maintenance' }),
    ).toHaveAttribute(
      'href',
      '/dashboard/data-operations/advanced/#dataops-search-maintenance-heading',
    );
    const indexEvidence = indexGuidance.locator('details.operator-evidence');
    await expect(indexEvidence.locator('code')).toBeHidden();
    await expectNoVisibleMachineTokens(page);
    await indexEvidence.locator('summary').click();
    await expect(indexEvidence.locator('code')).toBeVisible();
    await indexEvidence.locator('summary').click();
    await expect(indexEvidence.locator('code')).toBeHidden();

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
    const dangerZone = firstRecord.locator('.document-advanced--danger');
    const permanentDelete = dangerZone.locator('button[aria-label^="Delete PDF permanently"]');
    await expect(dangerZone.getByText('Danger Zone', { exact: true })).toBeVisible();
    await expect(permanentDelete).toBeHidden();
    await dangerZone.locator('summary').click();
    await expect(permanentDelete).toBeVisible();
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
    await expect(
      page.getByText('No category selected: validate all eligible documents.'),
    ).toBeVisible();
    const previewValidation = page.getByRole('button', { name: 'Preview validation' });
    if (await previewValidation.count()) {
      const [previewResponse] = await Promise.all([
        page.waitForResponse(response => (
          response.request().method() === 'POST'
          && new URL(response.url()).pathname
            === '/dashboard/operations/api/v1/maintenance/plans/'
        )),
        previewValidation.click(),
      ]);
      expect(previewResponse.status()).toBe(302);
      expect(previewResponse.headers()['x-dataops-reason-code']).toBeUndefined();
      await expect(page.getByText('Selected preview', { exact: true })).toBeVisible();
    } else {
      await expect(
        page.getByText('Document maintenance is temporarily unavailable'),
      ).toBeVisible();
      await expect(
        page.getByRole('link', { name: 'Review maintenance worker status' }),
      ).toBeVisible();
    }

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
