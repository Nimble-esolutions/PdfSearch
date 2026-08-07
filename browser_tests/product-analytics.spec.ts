import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const adapter = readFileSync(
  resolve(process.cwd(), 'flowdocs/core/static/main/js/product-analytics.js'),
  'utf8',
);

test.describe('Privacy-bounded product analytics adapter', () => {
  test('accepts only the event schema and strips browser content from transport', async ({ page }) => {
    const config = {
      script_url: 'data:text/javascript,window.__trackerLoaded=true',
      website_id: 'a947d503-2c2b-4192-8845-877e062efc38',
      allowed_domains: [''],
      deployment_tier: 'stage',
      surface: 'classic',
      primary_surface: 'classic',
      ui_language: 'en',
      release_version: 'test-release',
      view_override_used: false,
    };
    await page.setContent(
      `<script id="product-analytics-config" type="application/json">${JSON.stringify(config)}</script>`,
    );
    await page.addScriptTag({ content: adapter });

    const result = await page.evaluate(() => {
      const captured: Array<{name: string, data: Record<string, unknown>}> = [];
      (window as any).umami = {
        track: (name: string, data: Record<string, unknown>) => captured.push({ name, data }),
      };
      const analytics = (window as any).PdfSearchAnalytics;
      const accepted = analytics.track('search_submitted', {
        view: 'classic',
        question_language: 'en',
        word_count_bucket: '6-10',
      });
      const rejectedContent = analytics.track('search_submitted', {
        view: 'classic',
        question_language: 'en',
        word_count_bucket: '6-10',
        question: 'SENTINEL PRIVATE QUESTION',
      });
      const rejectedEvent = analytics.track('capture_everything', {
        answer: 'SENTINEL PRIVATE ANSWER',
      });
      const transport = analytics.beforeSend('event', {
        website: 'a947d503-2c2b-4192-8845-877e062efc38',
        name: captured[0].name,
        data: captured[0].data,
        url: '/?view=workbench&question=SENTINEL',
        title: 'SENTINEL DOCUMENT TITLE',
        referrer: 'https://example.test/private',
      });
      const rejectedIdentity = analytics.beforeSend('identify', {
        website: 'a947d503-2c2b-4192-8845-877e062efc38',
        name: 'identify',
        data: { user: 'SENTINEL USER' },
      });
      const languages = {
        english: analytics.questionLanguage('What are the current rules?', 'mr'),
        marathi: analytics.questionLanguage('सध्याचे नियम काय आहेत?', 'en'),
        fallback: analytics.questionLanguage('1234', 'mr'),
      };
      return { accepted, rejectedContent, rejectedEvent, rejectedIdentity, captured, transport, languages };
    });

    expect(result.accepted).toBe(true);
    expect(result.rejectedContent).toBe(false);
    expect(result.rejectedEvent).toBe(false);
    expect(result.rejectedIdentity).toBe(false);
    expect(result.captured).toHaveLength(1);
    expect(JSON.stringify(result)).not.toContain('SENTINEL PRIVATE');
    expect(result.transport).toMatchObject({
      name: 'search_submitted',
      url: '/product-events/classic',
      language: 'en',
    });
    expect(result.transport).not.toHaveProperty('title');
    expect(result.transport).not.toHaveProperty('referrer');
    expect(result.languages).toEqual({ english: 'en', marathi: 'mr', fallback: 'mr' });
  });

  test('fails open when the vendor object is unavailable', async ({ page }) => {
    const config = {
      script_url: 'data:text/javascript,window.__trackerLoaded=true',
      website_id: 'a947d503-2c2b-4192-8845-877e062efc38',
      allowed_domains: [''],
      deployment_tier: 'stage',
      surface: 'workbench',
      primary_surface: 'classic',
      ui_language: 'mr',
      release_version: '',
      view_override_used: false,
    };
    await page.setContent(
      `<button id="search">Search</button><script id="product-analytics-config" type="application/json">${JSON.stringify(config)}</script>`,
    );
    await page.addScriptTag({ content: adapter });

    const result = await page.evaluate(() => {
      let clicked = false;
      document.getElementById('search')?.addEventListener('click', () => { clicked = true; });
      const tracked = (window as any).PdfSearchAnalytics.track('search_feedback_opened', {
        view: 'workbench',
      });
      document.getElementById('search')?.click();
      return { tracked, clicked };
    });

    expect(result).toEqual({ tracked: true, clicked: true });
  });

  test('honours Do Not Track before loading or accepting events', async ({ page }) => {
    const config = {
      script_url: 'data:text/javascript,window.__trackerLoaded=true',
      website_id: 'a947d503-2c2b-4192-8845-877e062efc38',
      allowed_domains: [''],
      deployment_tier: 'stage',
      surface: 'classic',
      primary_surface: 'classic',
      ui_language: 'en',
      release_version: '',
      view_override_used: false,
    };
    await page.setContent(
      `<script id="product-analytics-config" type="application/json">${JSON.stringify(config)}</script>`,
    );
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'doNotTrack', { value: '1', configurable: true });
      (window as any).__capturedAnalytics = [];
      (window as any).umami = {
        track: (name: string, data: Record<string, unknown>) => {
          (window as any).__capturedAnalytics.push({ name, data });
        },
      };
    });
    await page.addScriptTag({ content: adapter });

    const result = await page.evaluate(() => ({
      tracked: (window as any).PdfSearchAnalytics.track('search_feedback_opened', {
        view: 'classic',
      }),
      trackerLoaded: Boolean((window as any).__trackerLoaded),
      remoteScripts: document.querySelectorAll('script[data-website-id]').length,
      captured: (window as any).__capturedAnalytics.length,
    }));

    expect(result).toEqual({ tracked: false, trackerLoaded: false, remoteScripts: 0, captured: 0 });
  });

  test('honours Global Privacy Control before loading or accepting events', async ({ page }) => {
    const config = {
      script_url: 'data:text/javascript,window.__trackerLoaded=true',
      website_id: 'a947d503-2c2b-4192-8845-877e062efc38',
      allowed_domains: [''],
      deployment_tier: 'stage',
      surface: 'workbench',
      primary_surface: 'classic',
      ui_language: 'en',
      release_version: '',
      view_override_used: false,
    };
    await page.setContent(
      `<script id="product-analytics-config" type="application/json">${JSON.stringify(config)}</script>`,
    );
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'globalPrivacyControl', { value: true, configurable: true });
      (window as any).__capturedAnalytics = [];
      (window as any).umami = {
        track: (name: string, data: Record<string, unknown>) => {
          (window as any).__capturedAnalytics.push({ name, data });
        },
      };
    });
    await page.addScriptTag({ content: adapter });

    const result = await page.evaluate(() => ({
      tracked: (window as any).PdfSearchAnalytics.track('search_feedback_opened', {
        view: 'workbench',
      }),
      trackerLoaded: Boolean((window as any).__trackerLoaded),
      remoteScripts: document.querySelectorAll('script[data-website-id]').length,
      captured: (window as any).__capturedAnalytics.length,
    }));

    expect(result).toEqual({ tracked: false, trackerLoaded: false, remoteScripts: 0, captured: 0 });
  });

  test('rejects collection when the current hostname is not allowlisted', async ({ page }) => {
    const config = {
      script_url: 'data:text/javascript,window.__trackerLoaded=true',
      website_id: 'a947d503-2c2b-4192-8845-877e062efc38',
      allowed_domains: ['2026.ai-sahakar.net'],
      deployment_tier: 'stage',
      surface: 'classic',
      primary_surface: 'classic',
      ui_language: 'en',
      release_version: '',
      view_override_used: false,
    };
    await page.setContent(
      `<script id="product-analytics-config" type="application/json">${JSON.stringify(config)}</script>`,
    );
    await page.evaluate(() => {
      (window as any).__capturedAnalytics = [];
      (window as any).umami = {
        track: (name: string, data: Record<string, unknown>) => {
          (window as any).__capturedAnalytics.push({ name, data });
        },
      };
    });
    await page.addScriptTag({ content: adapter });

    const result = await page.evaluate(() => ({
      tracked: (window as any).PdfSearchAnalytics.track('search_feedback_opened', {
        view: 'classic',
      }),
      trackerLoaded: Boolean((window as any).__trackerLoaded),
      remoteScripts: document.querySelectorAll('script[data-website-id]').length,
      captured: (window as any).__capturedAnalytics.length,
    }));

    expect(result).toEqual({ tracked: false, trackerLoaded: false, remoteScripts: 0, captured: 0 });
  });
});
