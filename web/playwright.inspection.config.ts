import { defineConfig, devices } from '@playwright/test';

const configuration = process.env['INSPECTION_CONFIGURATION'] || 'uat';
if (!['development', 'uat', 'production'].includes(configuration)) throw new Error('Invalid inspection build configuration');
const remote = process.env['INSPECTION_REMOTE_URL'];

export default defineConfig({
  testDir: './e2e',
  testMatch: /inspection-deterrents\.spec\.ts$/,
  timeout: 30_000,
  workers: 1,
  reporter: 'list',
  use: { ...devices['Desktop Chrome'], trace: 'off' },
  webServer: remote ? undefined : ['owner', 'admin', 'player'].map((project, index) => ({
    command: `npx ng serve ${project} --build-target ${project}:build:${configuration} --port ${4310 + index}`,
    url: `http://localhost:${4310 + index}${project === 'admin' ? '/admin/' : '/'}`,
    reuseExistingServer: false,
    timeout: 180_000,
  })),
});
