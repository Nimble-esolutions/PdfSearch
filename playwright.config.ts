import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './browser_tests',
  timeout: 30000,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:8000',
    headless: true,
    screenshot: 'only-on-failure',
    video: 'off',
    trace: 'off',
  },
  projects: [
    {
      name: 'desktop',
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'laptop',
      use: { viewport: { width: 1024, height: 800 } },
    },
    {
      name: 'tablet',
      use: { viewport: { width: 768, height: 1024 } },
    },
    {
      name: 'mobile',
      use: { viewport: { width: 390, height: 844 } },
    },
  ],
  reporter: [['list'], ['html', { outputFolder: 'playwright-report' }]],
});
