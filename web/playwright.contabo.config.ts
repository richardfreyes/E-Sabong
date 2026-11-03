import { defineConfig, devices } from '@playwright/test';

/**
 * Contabo layout checks (e2e/contabo-gateway.spec.ts) against a running UAT environment behind
 * the shared gateway. No dev servers and no seeding: UAT already holds synthetic data.
 *
 * E2E_CONTABO_RESOLVE maps the UAT host to another address for a local rehearsal, e.g.
 * "uat.swcmain183.live=127.0.0.1"; E2E_CONTABO_INSECURE=1 accepts a rehearsal's local CA.
 */
const resolve = process.env['E2E_CONTABO_RESOLVE'];

export default defineConfig({
  testDir: './e2e',
  testMatch: /contabo-gateway\.spec\.ts$/,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    ...devices['Desktop Chrome'],
    ignoreHTTPSErrors: process.env['E2E_CONTABO_INSECURE'] === '1',
    trace: 'retain-on-failure',
    launchOptions: resolve
      ? { args: [`--host-resolver-rules=${resolve.split(',').map((r) => `MAP ${r.replace('=', ' ')}`).join(',')}`] }
      : {},
  },
});
