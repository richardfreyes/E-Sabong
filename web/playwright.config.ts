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
    ['owner', process.env['E2E_OWNER_URL'] || 'http://localhost:4200'],
    ['admin', process.env['E2E_ADMIN_URL'] || 'http://localhost:4201/admin/'],
    ['player', process.env['E2E_PLAYER_URL'] || 'http://localhost:4202'],
  ].map(([project, url]) => ({
    command: `npx ng serve ${project} --port ${new URL(url).port}${process.env['E2E_PROXY_CONFIG'] ? ` --proxy-config ${process.env['E2E_PROXY_CONFIG']}` : ''}`,
    url, reuseExistingServer: true, timeout: 180_000,
  })),
});
