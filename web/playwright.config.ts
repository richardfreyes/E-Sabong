import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end tests run against the three dev servers (ports 4200-4202) proxied to
 * the API on :8000, using the `demo` tenant created by `python -m app.cli demo-tenant`.
 * See docs/testing.md for the full setup.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env['CI'] ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'e2e-report' }]],
  globalSetup: './e2e/global-setup.ts',
  use: {
    ...devices['Desktop Chrome'],
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    { command: 'npx ng serve owner', url: 'http://localhost:4200', reuseExistingServer: true, timeout: 180_000 },
    { command: 'npx ng serve admin', url: 'http://localhost:4201/admin/', reuseExistingServer: true, timeout: 180_000 },
    { command: 'npx ng serve player', url: 'http://localhost:4202', reuseExistingServer: true, timeout: 180_000 },
  ],
});
