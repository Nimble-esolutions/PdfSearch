import { test, expect, type Page } from '@playwright/test';

type ProfileOptions = {
  reducedMotion?: boolean;
  saveData?: boolean;
  effectiveType?: string;
  deviceMemory?: number;
  hardwareConcurrency?: number;
};

async function setProfile(page: Page, options: ProfileOptions = {}) {
  await page.addInitScript((profile: ProfileOptions) => {
    const connection = {
      saveData: profile.saveData ?? false,
      effectiveType: profile.effectiveType ?? '4g',
      addEventListener: () => {},
    };
    Object.defineProperty(navigator, 'connection', { configurable: true, value: connection });
    Object.defineProperty(navigator, 'deviceMemory', {
      configurable: true,
      value: profile.deviceMemory ?? 8,
    });
    Object.defineProperty(navigator, 'hardwareConcurrency', {
      configurable: true,
      value: profile.hardwareConcurrency ?? 8,
    });
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = (query: string) => {
      if (query === '(prefers-reduced-motion: reduce)') {
        return {
          matches: profile.reducedMotion ?? false,
          media: query,
          onchange: null,
          addListener: () => {},
          removeListener: () => {},
          addEventListener: () => {},
          removeEventListener: () => {},
          dispatchEvent: () => false,
        } as MediaQueryList;
      }
      return originalMatchMedia(query);
    };
  }, options);
}

async function mockAnswer(page: Page, answer = 'A concise answer from the official source.') {
  await page.route('**/search/**', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ answer, references: [] }),
    });
  });
}

test.describe('Search Desk motion profiles', () => {
  test('uses full mode for a capable connection and renders a short answer progressively', async ({ page }) => {
    await setProfile(page);
    await mockAnswer(page);
    await page.goto('/');

    await expect(page.locator('body')).toHaveAttribute('data-motion-mode', 'full');
    await page.locator('#userQuery').fill('What does Section 10 cover?');
    await page.locator('#sendBtn').click();
    await expect(page.locator('.gpt-msg').last()).toBeVisible();
    await expect(page.locator('.gpt-msg').last()).toHaveAttribute('aria-live', 'off');
    await expect(page.locator('.gpt-msg .visually-hidden')).toHaveText('A concise answer from the official source.', { timeout: 10000 });
  });

  test('selects light mode for Save-Data and low-capability devices', async ({ page }) => {
    await setProfile(page, { saveData: true, effectiveType: '2g', deviceMemory: 2, hardwareConcurrency: 2 });
    await page.goto('/');
    await expect(page.locator('body')).toHaveAttribute('data-motion-mode', 'light');
    await expect(page.locator('#optional-deva-font')).toHaveAttribute('media', 'not all');
  });

  test('selects reduced mode when the user requests reduced motion', async ({ page }) => {
    await setProfile(page, { reducedMotion: true });
    await page.goto('/');
    await expect(page.locator('body')).toHaveAttribute('data-motion-mode', 'reduced');
  });

  test('renders long answers immediately and keeps one complete announcement', async ({ page }) => {
    await setProfile(page);
    const longAnswer = 'Long answer '.repeat(100);
    await mockAnswer(page, longAnswer);
    await page.goto('/');
    await page.locator('#userQuery').fill('Explain cooperative society elections.');
    await page.locator('#sendBtn').click();
    const answer = page.locator('.gpt-msg').last();
    await expect(answer.locator('.visually-hidden')).toHaveText(longAnswer, { timeout: 10000 });
    await expect(answer).toHaveClass(/answer-complete/);
    await expect(answer.locator('[aria-live="polite"]')).toHaveCount(1);
  });

  test('prompt chips keep keyboard focus and populate the real composer', async ({ page }) => {
    await setProfile(page, { reducedMotion: true });
    await page.goto('/');
    const prompt = page.locator('.prompt-chip').first();
    await prompt.focus();
    await prompt.press('Enter');
    await expect(page.locator('#userQuery')).toHaveValue(await prompt.textContent() ?? '');
    await expect(page.locator('#userQuery')).toBeFocused();
  });
});
