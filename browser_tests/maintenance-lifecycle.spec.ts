import { test, expect, type Page } from '@playwright/test';

const phase = process.env.MAINTENANCE_E2E_PHASE || 'queue';

async function login(page: Page, next = '/dashboard/data-operations/advanced/') {
  await page.goto(`/login/?next=${encodeURIComponent(next)}`);
  const form = page.locator('input[name="username"]').locator('xpath=ancestor::form');
  await form.locator('input[name="username"]').fill('ci-admin');
  await form.locator('input[name="password"]').fill('ci-only-password-not-for-production');
  const destination = next.startsWith('/dashboard/data-operations/')
    ? '**/dashboard/data-operations/**'
    : '**/dashboard/operations/**';
  await Promise.all([
    page.waitForURL(destination),
    form.locator('button[type="submit"]').click(),
  ]);
}

async function waitForJob(page: Page, status: RegExp) {
  await expect.poll(async () => {
    const response = await page.request.get(
      '/dashboard/operations/api/v1/state/',
    );
    const payload = await response.json();
    const job = payload.data.maintenance.jobs[0];
    const observed = job?.status || '';
    if (
      ['completed', 'failed', 'partial', 'cancelled'].includes(observed)
      && !status.test(observed)
    ) {
      throw new Error(
        `maintenance job reached unexpected terminal state: ${JSON.stringify(job)}`,
      );
    }
    return observed;
  }, { timeout: 120_000, intervals: [500, 1000, 2000] }).toMatch(status);
  await page.reload();
}

async function previewAndQueue(
  page: Page,
  previewButton: string,
  typedConfirmation?: string,
) {
  await page
    .getByRole('group', { name: 'Categories' })
    .getByRole('checkbox')
    .first()
    .check();
  await page.getByText('Optional document filters', { exact: true }).click();
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
    selected.locator('div').filter({ hasText: /^Categories1$/ }).first(),
  ).toBeVisible();
  if (typedConfirmation) {
    await page.locator('input[name="typed_confirmation"]').first().fill(typedConfirmation);
  }
  await page.getByRole('button', { name: 'Confirm and queue' }).first().click();
}

async function submitHiddenForm(
  page: Page,
  action: string,
  fields: Record<string, string>,
) {
  await Promise.all([
    page.waitForURL(`**${action}`),
    page.evaluate(({ action, fields }) => {
      const form = document.createElement('form');
      form.method = 'post';
      form.action = action;
      const csrf = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
      for (const [name, value] of Object.entries({ ...fields, csrfmiddlewaretoken: csrf })) {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = name;
        input.value = value;
        form.appendChild(input);
      }
      document.body.appendChild(form);
      form.submit();
    }, { action, fields }),
  ]);
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
      await previewAndQueue(page, 'Preview index repair');
      await waitForJob(page, /completed/);
      return;
    }

    if (phase === 'fail') {
      await login(page);
      await previewAndQueue(
        page,
        'Preview selected reindex',
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
        .getByRole('heading', { name: 'Maintenance jobs', exact: true })
        .locator('xpath=ancestor::section')
        .locator('article')
        .first()
        .getByRole('button', { name: 'Prepare search update' })
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

      await login(page, '/dashboard/data-operations/advanced/');
      const stateResponse = await page.request.get('/dashboard/operations/api/v1/state/');
      const stateEnvelope = await stateResponse.json();
      const workspace = stateEnvelope.data.workspaces.find(
        (item: { generation_id?: string }) => item.generation_id === generationId,
      );
      expect(workspace?.public_id, 'verified restore workspace is required').toBeTruthy();
      await submitHiddenForm(
        page,
        '/dashboard/operations/api/v1/confirmations/issue/',
        {
          idempotency_key: `vault-activate-${generationId}`,
          action: 'activate_workspace',
          target: workspace.public_id,
        },
      );
      await page.locator('input[name="confirmation_phrase"]').fill(
        (await page.getByLabel('Required confirmation phrase').textContent())?.trim() || '',
      );
      await page.route('**/activations/*/schedule/', async (route) => {
        await route.continue({
          headers: {
            ...route.request().headers(),
            accept: 'application/json',
          },
        });
      });
      const [scheduleResponse] = await Promise.all([
        page.waitForResponse(
          (response) =>
            response.request().method() === 'POST'
            && response.url().includes('/activations/')
            && response.url().endsWith('/schedule/'),
        ),
        page.getByRole('button', { name: /confirm/i }).click(),
      ]);
      const scheduleEnvelope = await scheduleResponse.json();
      expect(
        scheduleResponse.status(),
        `Vault activation response: ${JSON.stringify(scheduleEnvelope)}`,
      ).toBe(202);
      await page.unroute('**/activations/*/schedule/');

      await waitForRuntimeIdentity(page, generationId, manifestDigest);
      await login(page, '/dashboard/data-operations/advanced/');
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

    await login(page, '/dashboard/data-operations/advanced/');
    await page.getByRole('button', { name: 'Review search update activation' }).click();
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
    await login(page, '/dashboard/data-operations/advanced/');
    await waitForActivationSettlement(page);
    await page.reload();

    for (const [query, language] of [
      ['cooperative audit evidence', 'en'],
      ['सहकारी लेखापरीक्षण पुरावा', 'mr'],
    ]) {
      await assertExpectedSearch(page, query, language);
    }

    await page.goto('/dashboard/data-operations/advanced/');
    await submitHiddenForm(
      page,
      '/dashboard/operations/api/v1/activations/rollback/confirm/',
      { idempotency_key: 'maintenance-e2e-signed-rollback' },
    );
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
    await login(page, '/dashboard/data-operations/advanced/');
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
