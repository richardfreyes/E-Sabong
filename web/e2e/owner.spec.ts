import { expect, test } from '@playwright/test';

import { OWNER } from './global-setup';
import { OWNER_URL, TENANT_SLUG, navTo } from './helpers';

test.describe('Platform owner portal', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${OWNER_URL}/login`);
    await page.getByLabel('Email').fill(OWNER.email);
    await page.getByLabel('Password', { exact: true }).fill(OWNER.password);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('heading', { name: 'Platform overview' })).toBeVisible();
  });

  test('shows KPIs and navigates to a tenant', async ({ page }) => {
    await expect(page.getByText('Active tenants')).toBeVisible();
    await navTo(page, 'Tenants');
    // This run's own tenant: every run adds a "Demo Arena", so a search for "demo" pages out the original.
    await page.getByPlaceholder('Search name or slug').fill(TENANT_SLUG);
    await page.getByRole('row', { name: new RegExp(`Demo Arena ${TENANT_SLUG}\\.localhost`) }).click();
    await expect(page.getByRole('heading', { name: 'Demo Arena' })).toBeVisible();

    await page.getByRole('tab', { name: 'Domains' }).click();
    await expect(page.getByText(`${TENANT_SLUG}.localhost`)).toBeVisible();
    await page.getByRole('tab', { name: 'Usage' }).click();
    await expect(page.getByRole('img', { name: /per day, last 30 days/ })).toBeVisible();
    await page.getByRole('tab', { name: 'Subscription' }).click();
    // demo-tenant subscribes to the zero-priced development plan; seeded catalogue plans are not for sale.
    await expect(page.getByRole('heading', { name: 'Development (not for sale)' })).toBeVisible();
  });

  test('rejects a wrong password', async ({ page, context }) => {
    await context.clearCookies();
    await page.goto(`${OWNER_URL}/login`);
    await page.getByLabel('Email').fill(OWNER.email);
    await page.getByLabel('Password', { exact: true }).fill('not-the-password');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('alert')).toContainText('Invalid email or password');
  });
});
