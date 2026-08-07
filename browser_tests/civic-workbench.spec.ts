import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const answer = 'A society audit follows the procedure set out in the applicable Act and Rules.';
const references = [{
  title: 'Maharashtra Cooperative Societies Act',
  folder: 'Acts and Rules',
  page: 42,
  excerpt: 'The committee shall arrange for the audit of the society accounts.',
  pdf_id: 1,
}];

async function mockSearch(page: Page, payload: object = {
  kind: 'evidence_answer', language: 'en', answer, references,
}) {
  await page.route('**/search/**', async route => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) });
  });
}

test.describe('Civic Knowledge Workbench', () => {
  test('empty state exposes a clear journey and keeps the composer ready', async ({ page }) => {
    await page.goto('/?view=workbench');
    const viewportWidth = page.viewportSize()?.width ?? 1440;
    await expect(page.locator('.knowledge-rail')).toBeVisible({ visible: viewportWidth >= 901 });
    await expect(page.locator('.evidence-rail')).toBeVisible({ visible: viewportWidth >= 1440 || viewportWidth <= 900 });
    await expect(page.locator('.empty-state h2')).toHaveText('Ask AI Sahakar');
    await expect(page.locator('.workspace-heading__support')).toContainText('official department documents');
    await expect(page.locator('.composer-controls')).toBeVisible();
    await expect(page.locator('.composer-submit svg')).toBeVisible();
    await expect(page.locator('#userQuery')).toHaveAttribute('placeholder', /what are the rules/);
    await expect(page.locator('#sendBtn')).toBeDisabled();
    await page.locator('#userQuery').fill('audit procedure');
    await expect(page.locator('#wordCounter')).toHaveText('2 of 30 words');
    await expect(page.locator('#sendBtn')).toBeEnabled();
    const inputBox = await page.locator('#userQuery').boundingBox();
    const buttonBox = await page.locator('#sendBtn').boundingBox();
    expect(inputBox).not.toBeNull();
    expect(buttonBox).not.toBeNull();
    if (viewportWidth > 430) {
      expect(Math.abs((inputBox?.y ?? 0) - (buttonBox?.y ?? 0))).toBeLessThanOrEqual(1);
    } else {
      expect(buttonBox?.width).toBeGreaterThanOrEqual((inputBox?.width ?? 0) - 1);
      expect(buttonBox?.y ?? 0).toBeGreaterThan(inputBox?.y ?? 0);
    }
  });

  test('a question produces a document-oriented answer and source evidence', async ({ page }) => {
    await mockSearch(page);
    await page.goto('/?view=workbench');
    await page.locator('#userQuery').fill('What is the society audit procedure?');
    await page.locator('#sendBtn').click();
    const response = page.locator('.conversation-entry--assistant').last();
    await expect(response).toHaveAttribute('data-response-kind', 'evidence_answer');
    await expect(response).toHaveAttribute('lang', 'en');
    await expect(response).toContainText(answer);
    await expect(page.locator('.answer-sources')).toContainText('Maharashtra Cooperative Societies Act');
    await expect(page.locator('.answer-sources .ref-card')).toContainText('Page 42');
    await expect(page.locator('[data-evidence-answer]')).not.toHaveAttribute('hidden');
  });

  test('keeps answer order and composer continuity across immediate sequential searches', async ({ page }) => {
    let requestCount = 0;
    await page.route('**/search/**', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      requestCount += 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          kind: 'evidence_answer',
          language: 'en',
          answer: requestCount === 1 ? 'First completed answer.' : 'Second completed answer.',
          references,
        }),
      });
    });
    await page.goto('/?view=workbench');
    await page.locator('#userQuery').fill('First question');
    await page.locator('#sendBtn').click();
    await expect(page.locator('.conversation-entry--assistant').last()).toContainText('First completed answer.');
    await page.locator('#userQuery').fill('Second question');
    await page.locator('#sendBtn').click();

    const responses = page.locator('.conversation-entry--assistant[data-response-kind]');
    await expect(responses).toHaveCount(2);
    await expect(responses.nth(0)).toContainText('First completed answer.');
    await expect(responses.nth(1)).toContainText('Second completed answer.');
    await expect(page.locator('#userQuery')).toBeVisible();
    await expect(page.locator('#userQuery')).toBeEditable();
  });

  test('announces request failures without removing the composer', async ({ page }) => {
    await page.route('**/search/**', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'search_unavailable', detail: 'Please try again later.' }),
      });
    });
    await page.goto('/?view=workbench');
    await page.locator('#userQuery').fill('Unavailable search');
    await page.locator('#sendBtn').click();

    await expect(page.locator('.search-error').last()).toHaveAttribute('role', 'alert');
    await expect(page.locator('#userQuery')).toBeVisible();
    await expect(page.locator('#userQuery')).toBeEditable();
  });

  test('rejects malformed successful responses without presenting false evidence', async ({ page }) => {
    await mockSearch(page, { language: 'en', answer, references });
    await page.goto('/?view=workbench');
    await page.locator('#userQuery').fill('Malformed response contract');
    await page.locator('#sendBtn').click();

    await expect(page.locator('.search-error').last()).toHaveAttribute('role', 'alert');
    await expect(page.locator('.conversation-entry--assistant[data-response-kind]')).toHaveCount(0);
    await expect(page.locator('#userQuery')).toBeVisible();
    await expect(page.locator('#userQuery')).toBeEditable();
  });

  test('new question cancels an in-flight response and keeps the reset conversation empty', async ({ page }) => {
    await page.route('**/search/**', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      await new Promise(resolve => setTimeout(resolve, 250));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ kind: 'evidence_answer', language: 'en', answer, references }),
      }).catch(() => undefined);
    });
    await page.goto('/?view=workbench');
    await page.locator('#userQuery').fill('Question that will be reset');
    await page.locator('#sendBtn').click();
    await expect(page.locator('.search-loading')).toBeVisible();
    await page.locator('[data-new-question]:visible').click();

    await page.waitForTimeout(350);
    await expect(page.locator('.empty-state h2')).toHaveText('Ask AI Sahakar');
    await expect(page.locator('.conversation-entry--user')).toHaveCount(0);
    await expect(page.locator('.conversation-entry--assistant[data-response-kind]')).toHaveCount(0);
    await page.locator('#userQuery').fill('Fresh question');
    await expect(page.locator('#sendBtn')).toBeEnabled();
  });

  test('renders a typed small-talk response without fabricating source evidence', async ({ page }) => {
    await mockSearch(page, {
      kind: 'small_talk',
      language: 'mr',
      answer: 'नमस्कार! मी तुम्हाला कशी मदत करू शकतो?',
      references: [],
    });
    await page.goto('/?view=workbench');
    await page.locator('#userQuery').fill('नमस्कार!');
    await page.locator('#sendBtn').click();

    const response = page.locator('.conversation-entry--assistant').last();
    await expect(response).toHaveAttribute('data-response-kind', 'small_talk');
    await expect(response).toHaveAttribute('lang', 'mr');
    await expect(response).toContainText('नमस्कार! मी तुम्हाला कशी मदत करू शकतो?');
    await expect(response.locator('.answer-sources .ref-card')).toHaveCount(0);
  });

  test('formats answer structure and shares the complete answer with source links', async ({ page }) => {
    const richAnswer = 'A short answer.\n\n1. **First requirement**\n2. Second requirement';
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: async (value: string) => { (window as unknown as { copied: string }).copied = value; } },
      });
      Object.defineProperty(navigator, 'share', {
        configurable: true,
        value: async (value: unknown) => { (window as unknown as { shared: unknown }).shared = value; },
      });
      Object.defineProperty(navigator, 'canShare', { configurable: true, value: () => true });
    });
    await mockSearch(page, {
      kind: 'evidence_answer', language: 'en', answer: richAnswer, references,
    });
    await page.goto('/?view=workbench');
    await page.locator('#userQuery').fill('What documents are required?');
    await page.locator('#sendBtn').click();
    await expect(page.locator('.answer-body strong')).toHaveText('First requirement');
    await expect(page.locator('.answer-body')).not.toContainText('**First requirement**');

    await page.locator('[data-copy-answer]').click();
    await expect(page.locator('[data-copy-answer]')).toHaveText('Copied');
    expect(await page.evaluate(() => (window as unknown as { copied: string }).copied)).toContain('First requirement');
    expect(await page.evaluate(() => (window as unknown as { copied: string }).copied)).not.toContain('**');

    await page.locator('[data-share-answer]').click();
    const shared = await page.evaluate(() => (window as unknown as { shared: {text: string} }).shared);
    expect(shared.text).toContain('First requirement');
    expect(shared.text).toContain('Maharashtra Cooperative Societies Act');
    expect(shared.text).toContain('/pdf/');
  });

  test('uses an explicit fallback share menu without a fixed recipient or fake source link', async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'share', { configurable: true, value: undefined });
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: async (value: string) => { (window as unknown as { copied: string }).copied = value; } },
      });
    });
    await mockSearch(page, {
      kind: 'evidence_answer', language: 'en', answer, references: [{ title: 'Unavailable source' }],
    });
    await page.goto('/?view=workbench');
    await page.locator('#userQuery').fill('What is the society audit procedure?');
    await page.locator('#sendBtn').click();
    const share = page.locator('[data-share-answer]');
    await share.click();
    await expect(page.locator('.share-menu')).toHaveAttribute('aria-label', 'Share options');
    await expect(page.locator('.share-menu a')).toHaveCount(3);
    for (const link of await page.locator('.share-menu a').all()) {
      await expect(link).not.toHaveAttribute('href', /phone=/);
    }
    await page.locator('.share-menu button').click();
    expect(await page.evaluate(() => (window as unknown as { copied: string }).copied)).not.toContain('/#');
    await page.keyboard.press('Escape');
    await expect(page.locator('.share-menu')).toHaveCount(0);
    await expect(share).toBeFocused();
  });

  test('footer partner disclosure is keyboard and touch discoverable without a layout jump', async ({ page }) => {
    await page.goto('/?view=workbench');
    await page.evaluate(() => document.fonts.ready);
    const footer = page.locator('.admin-footer__partners');
    await expect(footer).toBeVisible();
    const before = await footer.boundingBox();
    const beforeFlowHeight = await page.evaluate(() => document.documentElement.scrollHeight);
    await footer.locator('summary').click();
    await expect(footer.locator('.admin-footer__partners-menu')).toBeVisible();
    await expect(footer).toContainText('MediaNetwork');
    await expect(footer).toContainText('Nimble e-Solutions');
    const after = await footer.boundingBox();
    const afterFlowHeight = await page.evaluate(() => document.documentElement.scrollHeight);
    expect(after?.height).toBe(before?.height);
    expect(afterFlowHeight).toBe(beforeFlowHeight);
  });

  test('source drawer opens and restores focus after Escape', async ({ page }) => {
    await mockSearch(page);
    await page.goto('/?view=workbench');
    await page.locator('#userQuery').fill('Tell me about audits.');
    await page.locator('#sendBtn').click();
    const trigger = page.locator('[data-evidence-open]').last();
    await trigger.click();
    await expect(page.locator('.evidence-rail')).toHaveClass(/is-open/);
    await page.keyboard.press('Escape');
    await expect(page.locator('.evidence-rail')).not.toHaveClass(/is-open/);
    await expect(trigger).toBeFocused();
  });

  test('Marathi shell is complete and keeps the correct document language', async ({ page }) => {
    await page.goto('/?view=workbench');
    await page.getByRole('button', { name: 'मराठी' }).click();
    await page.waitForLoadState('networkidle');
    await expect(page.locator('html')).toHaveAttribute('lang', 'mr');
    await expect(page.locator('.empty-state h2')).toHaveText('सहकार AI ला विचारा');
    await expect(page.locator('#userQuery')).toHaveAttribute('placeholder', /उदाहरणार्थ/);
    await expect(page.locator('.knowledge-rail')).toContainText('सामान्य विषय');
  });

  test('has no horizontal overflow and no serious or critical axe violations at 320px', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/?view=workbench');
    const widths = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
    expect(widths.scroll).toBeLessThanOrEqual(widths.client + 1);
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations.filter(v => v.impact === 'critical' || v.impact === 'serious')).toEqual([]);
  });
});
