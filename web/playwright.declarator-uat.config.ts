import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e/performance',
  testMatch: 'declarator-uat.spec.ts',
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    browserName: 'chromium',
    headless: true,
    actionTimeout: 15000,
    screenshot: 'only-on-failure',
    trace: 'off',
  },
});
