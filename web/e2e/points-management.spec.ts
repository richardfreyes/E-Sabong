import { expect, test } from '@playwright/test';
import { ADMIN_URL, PLAYER_URL, apiCall, apiLogin, tenantLogin } from './helpers';

test('legacy points are an isolated read-only archive at every responsive width', async ({ page, request }) => {
  const token = await apiLogin(request, 'admin');
  const before = await (await apiCall(request, token, 'get', '/admin/points/summary')).json();
  for (const path of ['/points/allocations', '/points/transfers', '/points/adjustments', '/points/transactions/00000000-0000-0000-0000-000000000001/refund']) {
    const response = await apiCall(request, token, 'post', `/admin${path}`, { amount: 100 });
    expect([404, 405]).toContain(response.status());
  }
  await tenantLogin(page, ADMIN_URL, 'admin');
  await expect(page).not.toHaveURL(/\/login/);
  await page.goto(`${ADMIN_URL}/points`);
  await expect(page.getByRole('heading', { name: 'Legacy points archive' })).toBeVisible();
  await expect(page.locator('app-admin-points form')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Create Points|Transfer Points|Adjust|Refund transfer/i })).toHaveCount(0);
  await expect(page.getByText('Read-only historical balances and transactions.', { exact: false })).toBeVisible();
  for (const width of [320, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath(`balance-${width}.png`), animations: 'disabled', fullPage: true });
  }
  const after = await (await apiCall(request, token, 'get', '/admin/points/summary')).json();
  expect(after).toEqual(before);
  expect((await (await apiCall(request, token, 'get', '/admin/points/integrity')).json()).consistent).toBe(true);
});

test('player header, gameplay and statement share one real balance', async ({ page, request }) => {
  const admin = await apiLogin(request, 'admin');
  const settings = await (await apiCall(request, admin, 'get', '/admin/betting/settings')).json();
  expect((await apiCall(request, admin, 'put', '/admin/betting/settings', { enabled: true, starting_balance: '5000.00' })).status()).toBe(200);
  try {
  const token = await apiLogin(request, 'player1');
  const config = await (await apiCall(request, token, 'get', '/portal/betting')).json();
  const expected = Number(config.wallet.balance).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  await tenantLogin(page, PLAYER_URL, 'player1');
  await expect(page).not.toHaveURL(/\/login/);
  const header = page.locator('[data-field="header-balance"]');
  await expect(header).toHaveCount(1);
  await expect(page.locator('header .balance')).toHaveCount(1);
  await expect(header.locator('.pp-num')).toHaveText(expected);
  await expect(page.locator('pp-fight-betting-panel [data-field="balance"]')).toHaveText(expected);
  await page.goto(`${PLAYER_URL}/points`); // legacy bookmarks redirect to the current balance
  await expect(page).toHaveURL(/\/arena-points$/);
  await expect(page.locator('p.balance')).toHaveText(expected);
  await expect(header.locator('.pp-num')).toHaveText(expected);
  for (const width of [320, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    const text = await page.locator('body').innerText();
    expect(text).not.toMatch(/\b(?:cr|pts|credits|PHP)\b|₱/i);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath(`balance-${width}.png`), animations: 'disabled', fullPage: true });
  }
  expect((await apiCall(request, token, 'get', '/portal/points')).status()).toBe(404);
  expect((await apiCall(request, token, 'get', '/portal/points/history')).status()).toBe(404);
  } finally {
    await apiCall(request, admin, 'put', '/admin/betting/settings', { enabled: settings.enabled, starting_balance: settings.starting_balance });
  }
});
