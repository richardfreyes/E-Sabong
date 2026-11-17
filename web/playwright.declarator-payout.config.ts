import { defineConfig } from '@playwright/test';
// Desk payout projection and confirmation (see the spec). Local isolated stack or UAT; never production.
export default defineConfig({testDir:'./e2e/performance',testMatch:'declarator-payout.spec.ts',workers:1,retries:0,
  reporter:[['list']],use:{browserName:'chromium',headless:true,actionTimeout:15000,screenshot:'only-on-failure',trace:'off'},
});
