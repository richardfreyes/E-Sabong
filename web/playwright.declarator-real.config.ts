import { defineConfig } from '@playwright/test';
export default defineConfig({testDir:'./e2e/performance',testMatch:'declarator-real.spec.ts',workers:1,retries:0,
  reporter:[['list']],use:{browserName:'chromium',headless:true,actionTimeout:15000,screenshot:'only-on-failure',trace:'off'},
  webServer:{command:'node e2e/performance/real-serve.cjs',url:`http://127.0.0.1:${process.env['DECLARATOR_WEB_PORT']||4401}/admin/`,reuseExistingServer:true},
});
