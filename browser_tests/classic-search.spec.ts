import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const answer = 'A society audit follows the procedure in the applicable Act and Rules.';
const references = [{
  title: 'Maharashtra Cooperative Societies Act',
  folder: 'Acts and Rules',
  page: 42,
  pdf_id: 1,
  url: '/pdf/1/public/',
}];

async function mockSearch(page: Page, payload: object = {
  kind: 'evidence_answer', language: 'en', answer, references,
}) {
  await page.route('**/search/**', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
  });
}

async function installThrowingAnalyticsAdapter(page: Page) {
  await page.addInitScript(() => {
    (window as any).PdfSearchAnalytics = new Proxy({}, {
      get: () => () => { throw new Error('analytics adapter failure'); },
    });
  });
}

async function dismissCookieConsent(page: Page) {
  const overlays = [
    '#cookieConsent',
    '#cookie-consent',
    '.classic-cookie',
    '.cookie-consent',
    '[data-testid="cookie-consent"]',
  ];
  const labels = [
    /no thanks/i,
    /allow/i,
    /decline/i,
    /reject/i,
    /turn off/i,
    /continue|proceed|close|got it/i,
    /i agree|accept/i,
  ];

  for (const overlaySelector of overlays) {
    const overlay = page.locator(overlaySelector);
    if (!(await overlay.isVisible())) {
      continue;
    }

    const controls = overlay.locator('button, [role="button"], a, input[type="submit"]');
    const count = await controls.count();
    for (let i = 0; i < count; i += 1) {
      const control = controls.nth(i);
      if (await control.isVisible()) {
        const text = ((await control.textContent()) || '').trim();
        if (text && labels.some(label => label.test(text))) {
          await control.click({ force: true });
          return;
        }
      }
    }
  }
}

async function openClassicSearchPage(page: Page, path: string) {
  await page.goto(path);
  await page.waitForLoadState('domcontentloaded');
  for (let step = 0; step < 4; step += 1) {
    await page.waitForTimeout(150);
    await dismissCookieConsent(page);
    if (
      !(await page.locator('#cookieConsent').isVisible()) &&
      !(await page.locator('.classic-cookie').isVisible())
    ) {
      return;
    }
  }
}

test.describe('Classic public search', () => {
  test('is the isolated default and preserves the training layout contract', async ({ page }) => {
    await openClassicSearchPage(page, '/');

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
    await expect(page.locator('#chatMain')).toHaveAttribute('tabindex', '0');
    await expect(page.locator('#sendBtn')).toBeDisabled();
    await expect(page.locator('.workbench')).toHaveCount(0);
    await expect(page.locator('link[href*="civic-workbench"]')).toHaveCount(0);
    await expect(page.locator('script[src*="main/js/search.js"]')).toHaveCount(0);
    await expect(page.locator('link[href*="bootstrap"]')).toHaveCount(0);
  });

  test('uses one accessible focus ring for the compound composer field', async ({ page }) => {
    await openClassicSearchPage(page, '/');
    await page.locator('#userQuery').focus();

    const focusStyles = await page.evaluate(() => {
      const input = document.getElementById('userQuery');
      const field = input?.closest('.classic-composer__field');
      if (!input || !field) throw new Error('Classic composer field is incomplete');
      const inputStyle = getComputedStyle(input);
      const fieldStyle = getComputedStyle(field);
      return {
        inputOutlineStyle: inputStyle.outlineStyle,
        inputOutlineWidth: inputStyle.outlineWidth,
        fieldBorderColor: fieldStyle.borderColor,
        fieldBoxShadow: fieldStyle.boxShadow,
      };
    });

    expect(focusStyles.inputOutlineStyle).toBe('none');
    expect(focusStyles.inputOutlineWidth).toBe('0px');
    expect(focusStyles.fieldBorderColor).toBe('rgb(7, 94, 150)');
    expect(focusStyles.fieldBoxShadow).not.toBe('none');
  });

  test('renders answers and protected source evidence without HTML injection', async ({ page }) => {
    await mockSearch(page, {
      kind: 'evidence_answer',
      language: 'en',
      answer: '<img src=x onerror=alert(1)>\n\nSafe Marathi: सहकारी संस्था',
      references: [
        references[0],
        { title: 'Fallback source', pdf_id: 2 },
        { title: 'Authenticated source', pdf_id: 3, url: '/pdf/3/view/' },
        { title: 'Rejected source', pdf_id: 3, url: 'https://example.com/pdf/3/public/' },
        { title: 'Rejected same-origin source', pdf_id: 4, url: '/admin/' },
      ],
    });
    await openClassicSearchPage(page, '/');
    await page.locator('#userQuery').fill('What is the society audit procedure?');
    await expect(page.locator('#wordCounter')).toHaveText('6/30 words');
    await page.locator('#sendBtn').click();

    const response = page.locator('.classic-message--assistant').last();
    await expect(response).toHaveAttribute('data-response-kind', 'evidence_answer');
    await expect(response).toHaveAttribute('lang', 'en');
    await expect(response).toContainText('<img src=x onerror=alert(1)>');
    await expect(response.locator('img')).toHaveCount(0);
    await expect(response).toContainText('सहकारी संस्था');
    await expect(response.locator('.classic-reference').first()).toHaveAttribute(
      'href',
      /\/pdf\/1\/public\/$/,
    );
    await expect(response.locator('.classic-reference')).toHaveCount(3);
    await expect(response.locator('.classic-reference').nth(1)).toHaveAttribute(
      'href',
      /\/pdf\/2\/public\/$/,
    );
    await expect(response.locator('.classic-reference').nth(2)).toHaveAttribute(
      'href',
      /\/pdf\/3\/view\/$/,
    );
    await expect(response).not.toContainText('Rejected source');
    await expect(response).not.toContainText('Rejected same-origin source');
    await expect(response).toContainText('Page 42');
  });

  test('keeps search functional when every analytics method throws', async ({ page }) => {
    await installThrowingAnalyticsAdapter(page);
    await mockSearch(page);
    await openClassicSearchPage(page, '/');
    await page.locator('#userQuery').fill('What is the society audit procedure?');
    await page.locator('#sendBtn').click();

    await expect(page.locator('.classic-message--assistant').last()).toContainText(answer);
    await expect(page.locator('#userQuery')).toBeVisible();
    await expect(page.locator('#userQuery')).toBeEditable();
  });

  test('formats long answers, keeps the transcript scrollable, and supports another search', async ({ page }) => {
    const longSection = Array.from({ length: 18 }, (_, index) => {
      const marker = index % 2 === 0 ? `${index + 1})` : `${index + 1}.`;
      return `${marker} **Rule ${index + 1}**: Review the cited document before relying on this guidance.`;
    }).join('\n');
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
          answer: requestCount === 1
            ? `## Rental agreements\n\n${longSection}\n\n- Verify the source\n• Contact the registrar when needed`
            : 'Second answer remains available.',
          references,
        }),
      });
    });

    await openClassicSearchPage(page, '/');
    await page.locator('#userQuery').fill('Explain rental agreement rules');
    await page.locator('#sendBtn').click();

    const firstResponse = page.locator('.classic-message--assistant[data-response-kind]').last();
    await expect(firstResponse).toContainText('Rental agreements');
    await expect(firstResponse.locator('ul > li, ol > li')).toHaveCount(20);
    await expect(firstResponse.locator('strong').first()).toHaveText('Rule 1');
    await expect(firstResponse).not.toContainText('**Rule 1**');

    const unorderedListStyle = await firstResponse.locator('ul').first().evaluate((node: Element) => (
      getComputedStyle(node).listStyleType
    ));
    expect(unorderedListStyle).not.toBe('decimal');

    const layout = await page.evaluate(() => {
      const transcript = document.getElementById('chatMain');
      const composer = document.getElementById('searchComposer');
      if (!transcript || !composer) throw new Error('Classic search shell is incomplete');
      const composerRect = composer.getBoundingClientRect();
      return {
        overflowClass: document.body.className,
        overflowY: getComputedStyle(document.documentElement).overflowY,
        bodyPaddingBottom: getComputedStyle(document.body).paddingBottom,
        overflowRoomVar: getComputedStyle(document.body).getPropertyValue('--classic-overflow-room'),
        classicSheetHref: document.querySelector('link[href*="search-classic.css"]')?.getAttribute('href') ?? '',
        documentScrollHeight: document.documentElement.scrollHeight,
        viewportHeight: window.innerHeight,
        documentClientHeight: document.documentElement.clientHeight,
        transcriptClientHeight: transcript.clientHeight,
        transcriptScrollHeight: transcript.scrollHeight,
        transcriptOffsetHeight: (transcript as HTMLElement).offsetHeight,
        transcriptScrollTop: transcript.scrollTop,
        transcriptMaxHeight: getComputedStyle(transcript).maxHeight,
        transcriptBoxSizing: getComputedStyle(transcript).boxSizing,
        transcriptDisplay: getComputedStyle(transcript).display,
        classicRules: (() => {
          const rules = [];
          for (const sheet of Array.from(document.styleSheets)) {
            const styleSheet = sheet as CSSStyleSheet;
            try {
              for (const rule of Array.from(styleSheet.cssRules)) {
                if (rule.cssText.includes('.classic-chat__messages')) {
                  rules.push(rule.cssText);
                }
              }
            } catch (_error) {
              // Cross-origin stylesheet access can fail in browser tests.
            }
          }
          return rules.slice(0, 6);
        })(),
        composerTop: composerRect.top,
        composerBottom: composerRect.bottom,
        transcriptOverflowY: getComputedStyle(transcript).overflowY,
      };
    });
    expect(layout.documentScrollHeight).toBeGreaterThan(layout.viewportHeight);
    expect(layout.transcriptScrollHeight).toBeGreaterThan(0);
    expect(layout.transcriptOverflowY).toBe('auto');
    expect(layout.composerTop).toBeGreaterThanOrEqual(0);
    await expect(page.locator('#userQuery')).toBeVisible();
    await expect(page.locator('#userQuery')).toBeFocused();

    const transcript = page.locator('#chatMain');
    await transcript.scrollIntoViewIfNeeded();
    expect(await transcript.evaluate(element => element.scrollHeight)).toBeGreaterThanOrEqual(await transcript.evaluate(element => element.clientHeight));
    const a11y = await new AxeBuilder({ page }).analyze();
    expect(a11y.violations.filter(item => ['critical', 'serious'].includes(item.impact ?? ''))).toEqual([]);

    await page.locator('#userQuery').fill('Can I search again');
    await page.locator('#sendBtn').click();
    await expect(page.locator('.classic-message--assistant[data-response-kind]').last()).toContainText('Second answer remains available.');
    expect(requestCount).toBe(2);
  });

  test('renders a typed small-talk response without fabricating source evidence', async ({ page }) => {
    await mockSearch(page, {
      kind: 'small_talk',
      language: 'mr',
      answer: 'नमस्कार! मी तुम्हाला कशी मदत करू शकतो?',
      references: [],
    });
    await openClassicSearchPage(page, '/');
    await page.locator('#userQuery').fill('नमस्कार!');
    await page.locator('#sendBtn').click();

    const response = page.locator('.classic-message--assistant').last();
    await expect(response).toHaveAttribute('data-response-kind', 'small_talk');
    await expect(response).toHaveAttribute('lang', 'mr');
    await expect(response).toContainText('नमस्कार! मी तुम्हाला कशी मदत करू शकतो?');
    await expect(response.locator('.classic-references')).toHaveCount(0);
  });

  test('turns malformed successful responses into a retryable authored error', async ({ page }) => {
    await mockSearch(page, { kind: 'unknown', answer: '**Unsafe contract**', references });
    await openClassicSearchPage(page, '/');
    await page.locator('#userQuery').fill('Show malformed response handling');
    await page.locator('#sendBtn').click();

    const response = page.locator('.classic-message--error').last();
    await expect(response).toHaveAttribute('role', 'alert');
    await expect(response).toContainText('Something went wrong');
    await expect(response).not.toContainText('Unsafe contract');
    await expect(response.locator('.classic-retry')).toBeEnabled();
    await expect(page.locator('#userQuery')).toBeVisible();
    await expect(page.locator('#userQuery')).toBeFocused();
  });

  test('rejects impossible response-kind, language, and reference combinations', async ({ page }) => {
    const invalidPayloads = [
      { kind: 'evidence_answer', language: 'en', answer, references: [] },
      { kind: 'small_talk', language: 'en', answer: 'Hello', references },
      { kind: 'evidence_answer', language: 'hi', answer, references },
      { kind: 'evidence_answer', language: 'en', answer, references: [null] },
      { kind: 'evidence_answer', language: 'en', answer, references: [{ title: 'Missing identity' }] },
    ];
    let currentPayload = invalidPayloads[0];
    await page.route('**/search/**', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(currentPayload),
      });
    });
    await openClassicSearchPage(page, '/');

    for (const [index, payload] of invalidPayloads.entries()) {
      currentPayload = payload;
      await page.locator('#userQuery').fill(`Invalid contract ${index + 1}`);
      await page.locator('#sendBtn').click();
      await expect(page.locator('.classic-message--error')).toHaveCount(index + 1);
      await expect(page.locator('.classic-message--assistant[data-response-kind]')).toHaveCount(0);
      await expect(page.locator('#userQuery')).toBeEditable();
    }
  });

  test('keeps the 30-word contract and authored error state', async ({ page }) => {
    await openClassicSearchPage(page, '/');
    await page.locator('#userQuery').fill(Array.from({ length: 31 }, () => 'word').join(' '));

    await expect(page.locator('#wordCounter')).toHaveText('31/30 words');
    await expect(page.locator('#wordCounter')).toHaveClass(/is-over-limit/);
    await expect(page.locator('#sendBtn')).toBeDisabled();
  });

  test('Marathi session retains the explicit classic shareable view', async ({ page }) => {
    await openClassicSearchPage(page, '/?view=classic');
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
    await openClassicSearchPage(page, '/');

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

  test('keeps the composer reachable in a short landscape viewport', async ({ page }) => {
    await page.setViewportSize({ width: 844, height: 390 });
    await openClassicSearchPage(page, '/');

    await expect(page.locator('#userQuery')).toBeInViewport();
    await expect(page.locator('#sendBtn')).toBeInViewport();
    await expect(page.locator('#chatMain')).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThanOrEqual(391);
  });
});
