import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const adapter = readFileSync(
  resolve(process.cwd(), 'flowdocs/core/static/main/js/product-analytics.js'),
  'utf8',
);
const umamiScriptUrl = 'https://analytics.ai-sahakar.net/script.js';
const websiteId = 'a947d503-2c2b-4192-8845-877e062efc38';
const identityAlias = `v1_${'a'.repeat(43)}`;
const approvedTestUrl = 'https://2026.ai-sahakar.net/__analytics-browser-test__';

async function loadApprovedTestDocument(page: Page, content = '') {
  await page.route(approvedTestUrl, route => route.fulfill({
    contentType: 'text/html',
    body: `<!doctype html><html><body>${content}</body></html>`,
  }));
  await page.goto(approvedTestUrl);
}

function consentedConfig(overrides: Record<string, unknown> = {}) {
  return {
    script_url: umamiScriptUrl,
    website_id: websiteId,
    allowed_domains: ['2026.ai-sahakar.net'],
    deployment_tier: 'stage',
    surface: 'classic',
    primary_surface: 'classic',
    ui_language: 'en',
    release_version: 'test-release',
    view_override_used: false,
    consent_status: 'granted',
    identity_ready: true,
    identity_alias: identityAlias,
    ...overrides,
  };
}

async function preventDeferredVendorLoad(page: Page) {
  await page.evaluate(() => {
    Object.defineProperty(window, 'requestIdleCallback', {
      configurable: true,
      value: () => 0,
    });
  });
}

async function installAdapter(page: Page, config: Record<string, unknown>, content = '') {
  await loadApprovedTestDocument(
    page,
    `${content}<script id="product-analytics-config" type="application/json">${JSON.stringify(config)}</script>`,
  );
  await preventDeferredVendorLoad(page);
  await page.addScriptTag({ content: adapter });
}

test.describe('Privacy-bounded product analytics adapter', () => {
  test('accepts only the event schema and strips browser content from transport', async ({ page }) => {
    await installAdapter(page, consentedConfig());

    const result = await page.evaluate(({ configuredWebsiteId, configuredIdentityAlias }) => {
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
        website: configuredWebsiteId,
        name: captured[0].name,
        data: captured[0].data,
        url: '/?view=workbench&question=SENTINEL',
        title: 'SENTINEL DOCUMENT TITLE',
        referrer: 'https://example.test/private',
      });
      const identity = analytics.beforeSend('identify', {
        website: configuredWebsiteId,
        id: configuredIdentityAlias,
      });
      const rejectedIdentity = analytics.beforeSend('identify', {
        website: configuredWebsiteId,
        id: 'SENTINEL USER',
      });
      const languages = {
        english: analytics.questionLanguage('What are the current rules?', 'mr'),
        marathi: analytics.questionLanguage('सध्याचे नियम काय आहेत?', 'en'),
        fallback: analytics.questionLanguage('1234', 'mr'),
      };
      return {
        accepted, rejectedContent, rejectedEvent, rejectedIdentity, captured, transport, identity, languages,
      };
    }, { configuredWebsiteId: websiteId, configuredIdentityAlias: identityAlias });

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
    expect(result.identity).toEqual({
      website: websiteId,
      hostname: '2026.ai-sahakar.net',
      id: identityAlias,
    });
    expect(result.languages).toEqual({ english: 'en', marathi: 'mr', fallback: 'mr' });
  });

  test('loads the approved tracker only after consent and persists only the derived alias', async ({ page }) => {
    const requests: string[] = [];
    await page.route(umamiScriptUrl, async route => {
      requests.push(route.request().url());
      await route.fulfill({
        contentType: 'application/javascript',
        body: `window.__umamiEvents=[];window.umami={identify:(id)=>window.__umamiIdentity=id,track:(name,data)=>window.__umamiEvents.push({name,data})};`,
      });
    });
    await loadApprovedTestDocument(
      page,
      `<script id="product-analytics-config" type="application/json">${JSON.stringify(consentedConfig())}</script>`,
    );
    await page.addScriptTag({ content: adapter });

    await expect.poll(async () => page.evaluate(() => (window as any).PdfSearchAnalytics.getStatus().state))
      .toBe('ready');
    const result = await page.evaluate(() => ({
      identity: (window as any).__umamiIdentity,
      events: (window as any).__umamiEvents,
      script: document.querySelector<HTMLScriptElement>('script[data-website-id]')?.src,
      autoTrack: document.querySelector<HTMLScriptElement>('script[data-website-id]')?.dataset.autoTrack,
      performance: document.querySelector<HTMLScriptElement>('script[data-website-id]')?.dataset.performance,
    }));

    expect(requests).toEqual([umamiScriptUrl]);
    expect(result.identity).toBe(identityAlias);
    expect(result.events).toContainEqual(expect.objectContaining({ name: 'search_viewed' }));
    expect(result.script).toBe(umamiScriptUrl);
    expect(result.autoTrack).toBe('false');
    expect(result.performance).toBe('false');
  });

  test('fails open for search interactions when the vendor object is unavailable', async ({ page }) => {
    await installAdapter(page, consentedConfig({
      surface: 'workbench', primary_surface: 'classic', ui_language: 'mr', release_version: '',
    }), '<button id="search">Search</button>');

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

  test('allows explicit consent to override Do Not Track without exposing page content', async ({ page }) => {
    await loadApprovedTestDocument(
      page,
      `<script id="product-analytics-config" type="application/json">${JSON.stringify(consentedConfig())}</script>`,
    );
    await preventDeferredVendorLoad(page);
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'doNotTrack', { value: '1', configurable: true });
    });
    await page.addScriptTag({ content: adapter });

    const result = await page.evaluate(() => ({
      tracked: (window as any).PdfSearchAnalytics.track('search_feedback_opened', { view: 'classic' }),
      status: (window as any).PdfSearchAnalytics.getStatus(),
      remoteScripts: document.querySelectorAll('script[data-website-id]').length,
    }));

    expect(result).toEqual({
      tracked: true,
      status: expect.objectContaining({ state: 'pending', consent: 'granted', dnt_overridden_by_consent: true }),
      remoteScripts: 0,
    });
  });

  test('honours Global Privacy Control before loading or accepting events', async ({ page }) => {
    await loadApprovedTestDocument(
      page,
      `<script id="product-analytics-config" type="application/json">${JSON.stringify(consentedConfig())}</script>`,
    );
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'globalPrivacyControl', { value: true, configurable: true });
    });
    await page.addScriptTag({ content: adapter });

    const result = await page.evaluate(() => ({
      tracked: (window as any).PdfSearchAnalytics.track('search_feedback_opened', { view: 'classic' }),
      status: (window as any).PdfSearchAnalytics.getStatus(),
      remoteScripts: document.querySelectorAll('script[data-website-id]').length,
    }));

    expect(result).toEqual({
      tracked: false,
      status: expect.objectContaining({ state: 'blocked_gpc', consent: 'granted' }),
      remoteScripts: 0,
    });
  });

  test('rejects collection when the current hostname is not allowlisted', async ({ page }) => {
    await installAdapter(page, consentedConfig({ allowed_domains: ['other.ai-sahakar.net'] }));

    const result = await page.evaluate(() => ({
      tracked: (window as any).PdfSearchAnalytics.track('search_feedback_opened', { view: 'classic' }),
      status: (window as any).PdfSearchAnalytics.getStatus(),
      remoteScripts: document.querySelectorAll('script[data-website-id]').length,
    }));

    expect(result).toEqual({
      tracked: false,
      status: expect.objectContaining({ state: 'host_unapproved', consent: 'granted' }),
      remoteScripts: 0,
    });
  });
});
