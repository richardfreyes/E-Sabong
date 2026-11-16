import { defineConfig } from '@playwright/test';
// Desk request volume and ghost click latency against a local isolated stack (see the spec).
export default defineConfig({testDir:'./e2e/performance',testMatch:'declarator-requests.spec.ts',workers:1,retries:0,
  reporter:[['list']],use:{browserName:'chromium',headless:true,actionTimeout:15000,screenshot:'only-on-failure',trace:'off'},
  webServer:{command:'node e2e/performance/real-serve.cjs',url:`http://127.0.0.1:${process.env['DECLARATOR_WEB_PORT']||4411}/admin/`,reuseExistingServer:true},
});
