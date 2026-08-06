import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const policyPages = [
  { path: '/privacy/', title: 'Privacy Policy', navigation: 'Privacy' },
  { path: '/terms/', title: 'Terms of Service', navigation: 'Terms' },
  { path: '/data-policy/', title: 'Data Policy', navigation: 'Data Policy' },
  { path: '/cookies/', title: 'Cookie Policy', navigation: 'Cookies' },
  { path: '/disclaimer/', title: 'Disclaimer', navigation: 'Disclaimer' },
];

test.describe('Theme-aware public policy pages', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('cookieConsent', 'accepted'));
  });

  for (const theme of ['classic', 'workbench'] as const) {
    test(`${theme} treatment remains consistent across every policy page`, async ({ page }) => {
      for (const policy of policyPages) {
        await page.goto(`${policy.path}?view=${theme}`);

        await expect(page.locator('body')).toHaveClass(new RegExp(`public-legal--${theme}`));
        await expect(page.getByRole('heading', { level: 1 })).toHaveText(policy.title);
        await expect(page.locator('h1')).toHaveCount(1);
        await expect(page.locator('article.legal-document')).toHaveAttribute('lang', 'en');
        await expect(page.locator('link[rel="canonical"]')).toHaveAttribute(
          'href',
          `https://ai-sahakar.net${policy.path}`,
        );
        await expect(page.locator('link[href*="public-legal.css"]')).toHaveCount(1);
        await expect(page.locator('link[href*="bootstrap"]')).toHaveCount(0);
        await expect(page.locator('link[href*="main/css/style.css"]')).toHaveCount(0);
        await expect(page.getByRole('link', { name: 'Return to search' })).toHaveAttribute(
          'href',
          `/?view=${theme}`,
        );
        await expect(page.locator('.policy-nav a[aria-current="page"]')).toHaveText(policy.navigation);

        if (theme === 'classic') {
          await expect(page.locator('.classic-banner')).toBeVisible();
          await expect(page.locator('.workbench-header')).toHaveCount(0);
        } else {
          await expect(page.locator('.workbench-header')).toBeVisible();
          await expect(page.locator('[data-about-open]')).toHaveCount(0);
          await expect(page.locator('.classic-banner')).toHaveCount(0);
        }

        const widths = await page.evaluate(() => ({
          scroll: document.documentElement.scrollWidth,
          client: document.documentElement.clientWidth,
        }));
        expect(widths.scroll).toBeLessThanOrEqual(widths.client + 1);
      }
    });
  }

  test('preserves only an allowlisted preview across navigation and language switching', async ({ page }) => {
    await page.goto('/privacy/?view=workbench');

    await expect(page.locator('.policy-nav a', { hasText: 'Terms' })).toHaveAttribute(
      'href',
      '/terms/?view=workbench',
    );
    await expect(page.locator('form[action="/i18n/setlang/"] input[name="next"]')).toHaveValue(
      '/privacy/?view=workbench',
    );

    await page.goto('/privacy/?view=untrusted-template');
    await expect(page.locator('body')).not.toContainText('untrusted-template');
    await expect(page.locator('form[action="/i18n/setlang/"] input[name="next"]')).toHaveValue(
      '/privacy/',
    );
  });

  test('has no serious or critical accessibility violations in either treatment', async ({ page }) => {
    for (const theme of ['classic', 'workbench']) {
      await page.goto(`/privacy/?view=${theme}`);
      const result = await new AxeBuilder({ page }).analyze();
      expect(
        result.violations.filter(item => ['critical', 'serious'].includes(item.impact ?? '')),
      ).toEqual([]);
    }
  });
});
