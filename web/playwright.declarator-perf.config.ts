import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e/performance', workers: 1, retries: 0,
  reporter: [['list']], use: {browserName:'chromium',headless:true},
  webServer: {command:'node e2e/performance/serve.cjs',url:'http://127.0.0.1:4301/admin/',reuseExistingServer:true},
});
