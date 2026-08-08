import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const missingRoute = '/this-address-must-never-be-rendered/';

test.describe('Theme-consistent public recovery pages', () => {
  for (const theme of ['classic', 'workbench', 'maharashtra'] as const) {
    test(`404 uses the ${theme} shell without loading search or analytics clients`, async ({ page }) => {
      const response = await page.goto(`${missingRoute}?view=${theme}`);

      expect(response?.status()).toBe(404);
      await expect(page.locator('body')).toHaveClass(new RegExp(`public-error--${theme}`));
      await expect(page.getByRole('heading', { level: 1 })).toHaveText('This page is not available');
      await expect(page.locator('h1')).toHaveCount(1);
      await expect(page.locator('meta[name="robots"]')).toHaveAttribute(
        'content', 'noindex, nofollow, noarchive',
      );
      await expect(page.locator('link[rel="canonical"]')).toHaveCount(0);
      await expect(page.locator('#product-analytics-config')).toHaveCount(0);
      await expect(page.locator('script[src*="product-analytics.js"]')).toHaveCount(0);
      await expect(page.locator('script[src*="main/js/search.js"]')).toHaveCount(0);
      await expect(page.locator('script[src*="search-classic.js"]')).toHaveCount(0);
      await expect(page.locator('script[src*="search-maharashtra.js"]')).toHaveCount(0);
      await expect(page.locator('script[src]')).toHaveCount(0);
      await expect(page.locator('body')).not.toContainText('this-address-must-never-be-rendered');
      await expect(page.locator('.public-error__skip')).toHaveAttribute('href', '#publicErrorContent');
      await expect(page.getByRole('link', { name: 'Return to document search' })).toHaveAttribute(
        'href', `/?view=${theme}`,
      );
      await expect(page.locator('a[href="/login/"]')).toBeVisible();

      if (theme === 'classic') {
        await expect(page.locator('.classic-banner')).toBeVisible();
        await expect(page.locator('.workbench-header')).toHaveCount(0);
        await expect(page.locator('.maha-header')).toHaveCount(0);
      } else if (theme === 'workbench') {
        await expect(page.locator('.workbench-header')).toBeVisible();
        await expect(page.locator('.classic-banner')).toHaveCount(0);
        await expect(page.locator('.maha-header')).toHaveCount(0);
      } else {
        await expect(page.locator('.maha-header')).toBeVisible();
        await expect(page.locator('.classic-banner')).toHaveCount(0);
        await expect(page.locator('.workbench-header')).toHaveCount(0);
      }

      const widths = await page.evaluate(() => ({
        scroll: document.documentElement.scrollWidth,
        client: document.documentElement.clientWidth,
      }));
      expect(widths.scroll).toBeLessThanOrEqual(widths.client + 1);
    });
  }

  test('invalid view input fails closed to the Classic shell and is never reflected', async ({ page }) => {
    const response = await page.goto(`${missingRoute}?view=untrusted-template`);

    expect(response?.status()).toBe(404);
    await expect(page.locator('body')).toHaveClass(/public-error--classic/);
    await expect(page.locator('body')).not.toContainText('untrusted-template');
    await expect(page.getByRole('link', { name: 'Return to document search' })).toHaveAttribute('href', '/');
  });

  test('keeps every public header within a 320px viewport', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 680 });

    for (const theme of ['classic', 'workbench', 'maharashtra']) {
      await page.goto(`${missingRoute}?view=${theme}`);
      const widths = await page.evaluate(() => ({
        scroll: document.documentElement.scrollWidth,
        client: document.documentElement.clientWidth,
      }));
      expect(widths.scroll, `${theme} public error at 320px`).toBeLessThanOrEqual(widths.client + 1);
    }
  });

  test('404 has no serious or critical accessibility violations in every public theme', async ({ page }) => {
    for (const theme of ['classic', 'workbench', 'maharashtra']) {
      await page.goto(`${missingRoute}?view=${theme}`);
      const result = await new AxeBuilder({ page }).analyze();
      expect(
        result.violations.filter(item => ['critical', 'serious'].includes(item.impact ?? '')),
      ).toEqual([]);
    }
  });
});
