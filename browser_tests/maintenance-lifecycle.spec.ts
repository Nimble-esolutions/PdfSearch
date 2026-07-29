import { test, expect, type Page } from '@playwright/test';

const phase = process.env.MAINTENANCE_E2E_PHASE || 'queue';

async function login(page: Page, next = '/dashboard/operations/?section=maintenance') {
  await page.goto(`/login/?next=${encodeURIComponent(next)}`);
  const form = page.locator('input[name="username"]').locator('xpath=ancestor::form');
  await form.locator('input[name="username"]').fill('ci-admin');
  await form.locator('input[name="password"]').fill('ci-only-password-not-for-production');
  await Promise.all([
    page.waitForURL('**/dashboard/operations/**'),
    form.locator('button[type="submit"]').click(),
  ]);
}

async function waitForJob(page: Page, status: RegExp) {
  await expect.poll(async () => {
    const response = await page.request.get(
      '/dashboard/operations/api/v1/state/',
    );
    const payload = await response.json();
    return payload.data.maintenance.jobs[0]?.status || '';
  }, { timeout: 120_000, intervals: [500, 1000, 2000] }).toMatch(status);
  await page.reload();
}

async function previewAndQueue(
  page: Page,
  previewButton: string,
  typedConfirmation?: string,
) {
  await page.locator('fieldset.maintenance-scope input[type="checkbox"]').first().check();
  await page.locator('select[name="filter_indexed"]').selectOption('true');
  await page.locator('input[name="filter_category"]').fill('audit');
  await page.locator('input[name="filter_subject"]').fill('cooperation');
  await page.locator('input[name="filter_keywords"]').fill('audit,लेखापरीक्षण');
  const today = new Date().toISOString().slice(0, 10);
  await page.locator('input[name="filter_uploaded_after"]').fill(today);
  await page.locator('input[name="filter_uploaded_before"]').fill(today);
  await page.getByRole('button', { name: previewButton }).click();
  await expect(page.getByText('Selected preview', { exact: true })).toBeVisible();
  const selected = page.locator('.maintenance-selected-plan');
  await expect(
    selected.locator('div').filter({ hasText: /^Documents2$/ }).first(),
  ).toBeVisible();
  await expect(
    selected.locator('div').filter({ hasText: /^Folders1$/ }).first(),
  ).toBeVisible();
  if (typedConfirmation) {
    await page.locator('input[name="typed_confirmation"]').first().fill(typedConfirmation);
  }
  await page.getByRole('button', { name: 'Confirm and queue local maintenance' }).first().click();
}

async function assertExpectedSearch(page: Page, query: string, language: string) {
  const result = await page.evaluate(async ({ query, language }) => {
    const csrf = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
    const response = await fetch('/search/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-CSRFToken': csrf,
      },
      body: new URLSearchParams({ query, language }).toString(),
    });
    return { status: response.status, body: await response.json() };
  }, { query, language });
  expect(result.status).toBe(200);
  expect(
    result.body.references.some(
      (reference: { title?: string }) =>
        reference.title === 'Bilingual audit evidence',
    ),
  ).toBe(true);
}

async function waitForActivationSettlement(page: Page) {
  await expect.poll(async () => {
    try {
      const response = await page.request.get(
        '/dashboard/operations/api/v1/state/',
      );
      if (!response.ok()) return 'state-unavailable';
      const envelope = await response.json();
      const pending = envelope.data?.pending_activation;
      return pending
        ? `${pending.state}:${pending.public_id}`
        : 'settled';
    } catch {
      return 'state-unavailable';
    }
  }, { timeout: 120_000 }).toBe('settled');
}

async function waitForRuntimeIdentity(
  page: Page,
  generationId: string,
  manifestDigest: string,
) {
  await expect.poll(async () => {
    try {
      const response = await page.request.get('/readyz');
      if (!response.ok()) return { generationId: '', manifestDigest: '' };
      const payload = await response.json();
      return {
        generationId: payload.runtime_generation_id || '',
        manifestDigest: payload.runtime_manifest_digest || '',
      };
    } catch {
      return { generationId: '', manifestDigest: '' };
    }
  }, { timeout: 120_000, intervals: [500, 1000, 2000] }).toEqual({
    generationId,
    manifestDigest,
  });
}

test.describe('disposable maintenance lifecycle', () => {
  test.setTimeout(180_000);
  test.skip(({ browserName }) => browserName !== 'chromium');

  test(`executes ${phase} phase`, async ({ page }) => {
    if (phase === 'queue') {
      await login(page);
      await previewAndQueue(page, 'Preview Repair Stored Indexes');
      await waitForJob(page, /completed/);
      return;
    }

    if (phase === 'fail') {
      await login(page);
      await previewAndQueue(
        page,
        'Preview Reindex Selected',
        'REINDEX SELECTED',
      );
      await waitForJob(page, /(partial|failed|retryable)/);
      await expect(page.getByRole('button', { name: 'Retry from checkpoints' })).toBeVisible();
      return;
    }

    if (phase === 'retry') {
      await login(page);
      await page.getByRole('button', { name: 'Retry from checkpoints' }).click();
      await waitForJob(page, /completed/);
      await page
        .locator('.vault-panel:has(#maintenance-jobs-heading) article')
        .first()
        .getByRole('button', { name: 'Prepare for activation' })
        .click();
      await expect(page.getByText('Local maintenance candidate prepared')).toBeVisible();
      return;
    }

    if (phase === 'vault-activate') {
      const generationId = process.env.VAULT_E2E_TARGET_GENERATION_ID || '';
      const manifestDigest = process.env.VAULT_E2E_TARGET_MANIFEST_DIGEST || '';
      expect(generationId, 'VAULT_E2E_TARGET_GENERATION_ID is required').not.toBe('');
      expect(
        manifestDigest,
        'VAULT_E2E_TARGET_MANIFEST_DIGEST is required',
      ).toMatch(/^[0-9a-f]{64}$/);

      await login(page, '/dashboard/operations/?section=restore');
      const activationForm = page
        .locator('.vault-record', {
          has: page.getByRole('heading', {
            name: generationId,
            exact: true,
          }),
        })
        .locator('form:has(button:has-text("Prepare staging activation"))');
      await expect(activationForm).toHaveCount(1);
      await activationForm
        .getByRole('button', { name: 'Prepare staging activation' })
        .click();
      await page.locator('input[name="confirmation_phrase"]').fill(
        (await page.getByLabel('Required confirmation phrase').textContent())?.trim() || '',
      );
      await page.getByRole('button', { name: /confirm/i }).click();

      await waitForRuntimeIdentity(page, generationId, manifestDigest);
      await login(page, '/dashboard/operations/?section=restore');
      await waitForActivationSettlement(page);
      await page.reload();

      for (const [query, language] of [
        ['cooperative audit evidence', 'en'],
        ['सहकारी लेखापरीक्षण पुरावा', 'mr'],
      ]) {
        await assertExpectedSearch(page, query, language);
      }
      return;
    }

    await login(page, '/dashboard/operations/?section=restore');
    const activationForm = page.locator('form:has(button:has-text("Prepare staging activation"))').last();
    await activationForm.getByRole('button', { name: 'Prepare staging activation' }).click();
    await page.locator('input[name="confirmation_phrase"]').fill(
      (await page.getByLabel('Required confirmation phrase').textContent())?.trim() || '',
    );
    await page.getByRole('button', { name: /confirm/i }).click();
    await expect.poll(async () => {
      try {
        const response = await page.request.get('/readyz');
        return response.ok() ? (await response.json()).runtime_generation_id : '';
      } catch {
        return '';
      }
    }, { timeout: 120_000 }).toMatch(/^lm-/);
    await login(page, '/dashboard/operations/?section=restore');
    await waitForActivationSettlement(page);
    await page.reload();

    for (const [query, language] of [
      ['cooperative audit evidence', 'en'],
      ['सहकारी लेखापरीक्षण पुरावा', 'mr'],
    ]) {
      await assertExpectedSearch(page, query, language);
    }

    await page.goto('/dashboard/operations/?section=restore');
    await page.getByRole('button', { name: 'Review signed rollback' }).click();
    await page.locator('input[name="confirmation_phrase"]').fill(
      (await page.getByLabel('Required confirmation phrase').textContent())?.trim() || '',
    );
    await page.getByRole('button', { name: /confirm/i }).click();
    await expect.poll(async () => {
      try {
        const response = await page.request.get('/readyz');
        return response.ok() ? (await response.json()).runtime_generation_id : '';
      } catch {
        return '';
      }
    }, { timeout: 120_000 }).toBe('maintenance-e2e-parent');
    await login(page, '/dashboard/operations/?section=restore');
    await waitForActivationSettlement(page);
    await page.reload();
    for (const [query, language] of [
      ['cooperative audit evidence', 'en'],
      ['सहकारी लेखापरीक्षण पुरावा', 'mr'],
    ]) {
      await assertExpectedSearch(page, query, language);
    }
  });
});
