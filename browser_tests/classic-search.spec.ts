import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const answer = 'A society audit follows the procedure in the applicable Act and Rules.';
const references = [{
  title: 'Maharashtra Cooperative Societies Act',
  folder: 'Acts and Rules',
  page: 42,
  pdf_id: 1,
  url: '/public/pdf/1/',
}];

async function mockSearch(page: Page, payload: object = { kind: 'evidence_answer', answer, references }) {
  await page.route('**/search/**', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
  });
}

test.describe('Classic public search', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('cookieConsent', 'accepted'));
  });

  test('is the isolated default and preserves the training layout contract', async ({ page }) => {
    await page.goto('/');

    await expect(page.locator('body')).toHaveClass(/classic-search/);
    await expect(page.locator('.classic-utility')).toContainText('Admin Login');
    await expect(page.locator('.classic-banner h1')).toHaveText('AI Enabled Search');
    await expect(page.locator('.classic-message--welcome')).toContainText(
      'I am Sahakar AI. Click here to learn how to questions to get correct answers.',
    );
    await expect(page.locator('.classic-message--welcome a')).toHaveAttribute(
      'href',
      /1K4Z0RnRcQFXXDxxO10xFVjAbFRBXu7errbWbqtIK8qE/,
    );
    await expect(page.locator('.classic-utility a', { hasText: 'Locate Us' })).toHaveAttribute(
      'href',
      /1MoHzbONhTORm8fQ0IAZCWG82vtUgIJU/,
    );
    await expect(page.locator('.classic-icon-link--feedback')).toHaveAttribute(
      'href',
      /1FAIpQLSca6zWE0E5CIwoFfT6lzdGhaqaslLFpjaSu2mK654hAQVDlSg/,
    );
    await expect(page.locator('.classic-composer')).toBeVisible();
    await expect(page.locator('#sendBtn')).toBeDisabled();
    await expect(page.locator('.workbench')).toHaveCount(0);
    await expect(page.locator('link[href*="civic-workbench"]')).toHaveCount(0);
    await expect(page.locator('script[src*="main/js/search.js"]')).toHaveCount(0);
    await expect(page.locator('link[href*="bootstrap"]')).toHaveCount(0);
  });

  test('renders answers and protected source evidence without HTML injection', async ({ page }) => {
    await mockSearch(page, {
      answer: '<img src=x onerror=alert(1)>\n\nSafe Marathi: सहकारी संस्था',
      references,
    });
    await page.goto('/');
    await page.locator('#userQuery').fill('What is the society audit procedure?');
    await expect(page.locator('#wordCounter')).toHaveText('6/30 words');
    await page.locator('#sendBtn').click();

    const response = page.locator('.classic-message--assistant').last();
    await expect(response).toHaveAttribute('data-response-kind', 'evidence_answer');
    await expect(response).toContainText('<img src=x onerror=alert(1)>');
    await expect(response.locator('img')).toHaveCount(0);
    await expect(response).toContainText('सहकारी संस्था');
    await expect(response.locator('.classic-reference')).toHaveAttribute(
      'href',
      /\/public\/pdf\/1\/$/,
    );
    await expect(response).toContainText('Page 42');
  });

  test('renders a typed small-talk response without fabricating source evidence', async ({ page }) => {
    await mockSearch(page, {
      kind: 'small_talk',
      answer: 'Hello! How can I help you?',
      references: [],
    });
    await page.goto('/');
    await page.locator('#userQuery').fill('Hello!');
    await page.locator('#sendBtn').click();

    const response = page.locator('.classic-message--assistant').last();
    await expect(response).toHaveAttribute('data-response-kind', 'small_talk');
    await expect(response).toContainText('Hello! How can I help you?');
    await expect(response.locator('.classic-references')).toHaveCount(0);
  });

  test('keeps the 30-word contract and authored error state', async ({ page }) => {
    await page.goto('/');
    await page.locator('#userQuery').fill(Array.from({ length: 31 }, () => 'word').join(' '));

    await expect(page.locator('#wordCounter')).toHaveText('31/30 words');
    await expect(page.locator('#wordCounter')).toHaveClass(/is-over-limit/);
    await expect(page.locator('#sendBtn')).toBeDisabled();
  });

  test('Marathi session retains the explicit classic shareable view', async ({ page }) => {
    await page.goto('/?view=classic');
    await page.getByRole('button', { name: 'मराठी' }).click();
    await page.waitForLoadState('networkidle');

    await expect(page).toHaveURL(/\?view=classic$/);
    await expect(page.locator('html')).toHaveAttribute('lang', 'mr');
    await expect(page.locator('#userQuery')).toHaveAttribute('placeholder', /तुमचा प्रश्न/);
    await expect(page.locator('.classic-utility')).toContainText('English');
  });

  test('has no horizontal overflow or serious accessibility violations at 320px', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/');

    const widths = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(widths.scroll).toBeLessThanOrEqual(widths.client + 1);
    await expect(page.locator('.classic-composer__assistant')).toBeHidden();
    for (const selector of [
      '.classic-composer__field',
      '.classic-composer__submit',
      '.classic-icon-link--whatsapp',
      '.classic-icon-link--feedback',
    ]) {
      await expect(page.locator(selector)).toBeInViewport();
    }
    const result = await new AxeBuilder({ page }).analyze();
    expect(result.violations.filter(item => ['critical', 'serious'].includes(item.impact ?? ''))).toEqual([]);
  });
});
