import { expect, test } from '@playwright/test';

import { OWNER } from './global-setup';
import { OWNER_URL, navTo } from './helpers';

test.describe('Platform owner portal', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${OWNER_URL}/login`);
    await page.getByLabel('Email').fill(OWNER.email);
    await page.getByLabel('Password').fill(OWNER.password);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('heading', { name: 'Platform overview' })).toBeVisible();
  });

  test('shows KPIs and navigates to a tenant', async ({ page }) => {
    await expect(page.getByText('Active tenants')).toBeVisible();
    await navTo(page, 'Tenants');
    await page.getByPlaceholder('Search name or slug').fill('demo');
    await page.getByRole('row', { name: /Demo Arena/ }).click();
    await expect(page.getByRole('heading', { name: 'Demo Arena' })).toBeVisible();

    await page.getByRole('tab', { name: 'Domains' }).click();
    await expect(page.getByText('demo.localhost')).toBeVisible();
    await page.getByRole('tab', { name: 'Usage' }).click();
    await expect(page.getByRole('img', { name: /per day, last 30 days/ })).toBeVisible();
    await page.getByRole('tab', { name: 'Subscription' }).click();
    await expect(page.getByRole('heading', { name: 'Professional' })).toBeVisible();
  });

  test('rejects a wrong password', async ({ page, context }) => {
    await context.clearCookies();
    await page.goto(`${OWNER_URL}/login`);
    await page.getByLabel('Email').fill(OWNER.email);
    await page.getByLabel('Password').fill('not-the-password');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('alert')).toContainText('Invalid email or password');
  });
});
