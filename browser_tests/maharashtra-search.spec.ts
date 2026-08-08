import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const references = [{
  title: 'Maharashtra Cooperative Societies Act',
  folder: 'Acts and Rules',
  page: 42,
  pdf_id: 1,
  url: '/pdf/1/public/',
}];

async function mockSearch(page: Page, payload: object) {
  await page.route('**/search/**', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
  });
}

test.describe('Maharashtra Service public search', () => {
  test('loads as an isolated, official-service theme with every required public link', async ({ page }) => {
    await page.goto('/?view=maharashtra');

    await expect(page.locator('body')).toHaveClass(/maha-service/);
    await expect(page.locator('.maha-header')).toBeVisible();
    await expect(page.locator('.maha-identity__seal img')).toHaveAttribute(
      'src',
      /maharashtra-theme-registrar-seal\.svg$/,
    );
    await expect(page.locator('.maha-identity__emblem')).toHaveAttribute(
      'src',
      /maharashtra-theme-national-emblem\.svg$/,
    );
    await expect(page.locator('link[href*="search-maharashtra.css"]')).toHaveCount(1);
    await expect(page.locator('script[src*="search-maharashtra.js"]')).toHaveCount(1);
    await expect(page.locator('link[href*="classic-search"], link[href*="civic-workbench"]')).toHaveCount(0);
    await expect(page.locator('.classic-search, .workbench')).toHaveCount(0);
    await expect(page.getByText('Suggested Questions')).toHaveCount(0);
    await expect(page.getByText('Civic Knowledge Desk')).toHaveCount(0);
    await expect(page.getByText('Ask about Maharashtra cooperative law')).toHaveCount(0);

    await expect(page.locator('[data-help-link]').first()).toHaveAttribute(
      'href',
      /1K4Z0RnRcQFXXDxxO10xFVjAbFRBXu7errbWbqtIK8qE/,
    );
    await expect(page.locator('[data-whatsapp-link]')).toHaveAttribute('href', /^https:\/\/wa\.me\//);
    await expect(page.locator('[data-feedback-link]')).toHaveAttribute(
      'href',
      /1FAIpQLSca6zWE0E5CIwoFfT6lzdGhaqaslLFpjaSu2mK654hAQVDlSg/,
    );
    await expect(page.getByRole('link', { name: 'Locate us' }).first()).toHaveAttribute(
      'href',
      /1MoHzbONhTORm8fQ0IAZCWG82vtUgIJU/,
    );
    await expect(page.locator('img[src*="whatsapp"], img[src*="feedback"]')).toHaveCount(0);
  });

  test('renders structured evidence safely and accepts a second question', async ({ page }) => {
    let requestCount = 0;
    await page.route('**/search/**', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      requestCount += 1;
      const responseAnswer = requestCount === 1
        ? `## Audit procedure

12. **Read the Act**
14. Verify the Rules

- Keep records

<img src=x onerror=alert(1)>

[Unsafe](https://example.com/private)`
        : 'The second answer remains available.';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          kind: 'evidence_answer',
          language: 'en',
          answer: responseAnswer,
          references: [
            ...references,
            { title: 'Generated protected source', pdf_id: 2 },
            { title: 'Rejected external source', pdf_id: 3, url: 'https://example.com/pdf/3/public/' },
            { title: 'Rejected local route', pdf_id: 4, url: '/admin/' },
          ],
        }),
      });
    });

    await page.goto('/?view=maharashtra');
    await page.locator('#userQuery').fill('Explain the society audit procedure');
    await page.locator('#sendBtn').click();

    const firstAnswer = page.locator('.maha-message--assistant[data-response-kind]').first();
    await expect(firstAnswer).toHaveAttribute('lang', 'en');
    await expect(firstAnswer.getByRole('heading', { name: 'Audit procedure' })).toBeVisible();
    await expect(firstAnswer.locator('ol > li')).toHaveCount(2);
    await expect(firstAnswer.locator('ol > li').first()).toHaveAttribute('value', '12');
    await expect(firstAnswer.locator('ol > li').nth(1)).toHaveAttribute('value', '14');
    await expect(firstAnswer.locator('ul > li')).toHaveCount(1);
    await expect(firstAnswer.locator('strong').first()).toHaveText('Read the Act');
    await expect(firstAnswer).toContainText('<img src=x onerror=alert(1)>');
    await expect(firstAnswer.locator('img')).toHaveCount(0);
    await expect(firstAnswer.getByRole('link', { name: 'Unsafe' })).toHaveCount(0);
    await expect(firstAnswer).toContainText('Unsafe (https://example.com/private)');
    await expect(firstAnswer.locator('.maha-source')).toHaveCount(2);
    await expect(firstAnswer.locator('.maha-source').first()).toHaveAttribute('href', /\/pdf\/1\/public\/$/);
    await expect(firstAnswer.locator('.maha-source').nth(1)).toHaveAttribute('href', /\/pdf\/2\/public\/$/);
    await expect(firstAnswer).not.toContainText('Rejected external source');
    await expect(firstAnswer).not.toContainText('Rejected local route');

    await page.locator('#userQuery').fill('Ask a second question');
    await page.locator('#sendBtn').click();
    await expect(page.locator('.maha-message--assistant[data-response-kind]')).toHaveCount(2);
    await expect(page.locator('.maha-message--assistant[data-response-kind]').last()).toContainText(
      'The second answer remains available.',
    );
    await expect(page.locator('#userQuery')).toBeVisible();
    await expect(page.locator('#userQuery')).toBeEditable();
  });

  test('keeps long answers, the composer, the footer, and document scrolling reachable', async ({ page }) => {
    const longAnswer = Array.from(
      { length: 24 },
      (_, index) => `${index + 1}. **Rule ${index + 1}**: Review the cited official document.`,
    ).join('\n');
    await mockSearch(page, {
      kind: 'evidence_answer',
      language: 'en',
      answer: `## Detailed guidance\n\n${longAnswer}`,
      references,
    });

    await page.goto('/?view=maharashtra');
    await page.locator('#userQuery').fill('Explain all applicable audit rules');
    await page.locator('#sendBtn').click();
    await expect(page.locator('.maha-answer-body ol > li')).toHaveCount(24);

    const layout = await page.evaluate(() => ({
      documentHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
      bodyOverflow: getComputedStyle(document.body).overflowY,
      transcriptOverflow: getComputedStyle(document.querySelector('#chatMain') as Element).overflowY,
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
    }));
    expect(layout.documentHeight).toBeGreaterThan(layout.viewportHeight);
    expect(layout.bodyOverflow).not.toBe('hidden');
    expect(layout.transcriptOverflow).toBe('visible');
    expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth + 1);

    await page.locator('#searchComposer').scrollIntoViewIfNeeded();
    await expect(page.locator('#searchComposer')).toBeInViewport();
    await page.locator('.maha-footer').scrollIntoViewIfNeeded();
    await expect(page.locator('.maha-footer')).toBeInViewport();
  });

  test('preserves the Marathi session and has no accessibility defects', async ({ page }) => {
    await mockSearch(page, {
      kind: 'evidence_answer',
      language: 'mr',
      answer: '## लेखापरीक्षण प्रक्रिया\n\n1. संस्थेची कागदपत्रे तपासा.\n2. अधिकृत नियमांचा संदर्भ घ्या.',
      references,
    });
    await page.goto('/?view=maharashtra');
    await page.context().addCookies([{
      name: 'django_language',
      value: 'mr',
      domain: new URL(page.url()).hostname,
      path: '/',
    }]);
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('lang', 'mr');
    await expect(page.locator('#searchComposer input[name="language"]')).toHaveValue('mr');
    await expect(page.getByRole('link', { name: 'आम्हाला शोधा' }).first()).toBeVisible();
    await page.locator('#userQuery').fill('संस्थेचे लेखापरीक्षण कसे करावे?');
    await page.locator('#sendBtn').click();

    const answer = page.locator('.maha-message--assistant[data-response-kind]').last();
    await expect(answer).toHaveAttribute('lang', 'mr');
    await expect(answer).toContainText('संस्थेची कागदपत्रे तपासा');
    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations).toEqual([]);
  });

  test('fits a 320px budget-phone viewport without clipping controls', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 740 });
    await page.goto('/?view=maharashtra');

    const layout = await page.evaluate(() => ({
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
      textareaFontSize: Number.parseFloat(getComputedStyle(document.querySelector('#userQuery') as Element).fontSize),
      submitHeight: (document.querySelector('#sendBtn') as HTMLElement).getBoundingClientRect().height,
    }));
    expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth + 1);
    expect(layout.textareaFontSize).toBeGreaterThanOrEqual(16);
    expect(layout.submitHeight).toBeGreaterThanOrEqual(44);
    await expect(page.locator('#userQuery')).toBeVisible();
    await expect(page.locator('.maha-service-actions')).toBeVisible();
  });

  test('explains recoverable failures without removing the input', async ({ page }) => {
    await page.route('**/search/**', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'search_unavailable' }),
      });
    });
    await page.goto('/?view=maharashtra');
    await page.locator('#userQuery').fill('Find the applicable audit rule');
    await page.locator('#sendBtn').click();

    await expect(page.locator('.maha-message--error')).toHaveAttribute('role', 'alert');
    await expect(page.locator('.maha-message--error')).toContainText('Search unavailable');
    await expect(page.locator('.maha-retry')).toBeVisible();
    await expect(page.locator('#userQuery')).toBeVisible();
    await expect(page.locator('#userQuery')).toBeEditable();
  });
});
