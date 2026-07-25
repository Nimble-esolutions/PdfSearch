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

async function mockSearch(page: Page, payload: object = { answer, references }) {
  await page.route('**/search/**', async route => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) });
  });
}

test.describe('Civic Knowledge Workbench', () => {
  test('empty state exposes a clear journey and keeps the composer ready', async ({ page }) => {
    await page.goto('/');
    const viewportWidth = page.viewportSize()?.width ?? 1440;
    await expect(page.locator('.knowledge-rail')).toBeVisible({ visible: viewportWidth >= 901 });
    await expect(page.locator('.evidence-rail')).toBeVisible({ visible: viewportWidth >= 1440 || viewportWidth <= 900 });
    await expect(page.locator('.empty-state h2')).toHaveText('Ask AI Sahakar');
    await expect(page.locator('#userQuery')).toHaveAttribute('placeholder', /what are the rules/);
    await expect(page.locator('#sendBtn')).toBeDisabled();
    await page.locator('#userQuery').fill('audit procedure');
    await expect(page.locator('#wordCounter')).toHaveText('2 of 30 words');
    await expect(page.locator('#sendBtn')).toBeEnabled();
  });

  test('a question produces a document-oriented answer and source evidence', async ({ page }) => {
    await mockSearch(page);
    await page.goto('/');
    await page.locator('#userQuery').fill('What is the society audit procedure?');
    await page.locator('#sendBtn').click();
    await expect(page.locator('.conversation-entry--assistant').last()).toContainText(answer);
    await expect(page.locator('.answer-sources')).toContainText('Maharashtra Cooperative Societies Act');
    await expect(page.locator('.answer-sources .ref-card')).toContainText('Page 42');
    await expect(page.locator('[data-evidence-answer]')).not.toHaveAttribute('hidden');
  });

  test('source drawer opens and restores focus after Escape', async ({ page }) => {
    await mockSearch(page);
    await page.goto('/');
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
    await page.goto('/');
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
    await page.goto('/');
    const widths = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
    expect(widths.scroll).toBeLessThanOrEqual(widths.client + 1);
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations.filter(v => v.impact === 'critical' || v.impact === 'serious')).toEqual([]);
  });
});
