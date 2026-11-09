import { expect, test } from '@playwright/test';
import { ADMIN_URL, apiCall, apiLogin, navTo, tenantLogin } from './helpers';

test('amount-only balance operations post to the selected account and fit mobile screens', async ({ page, request }) => {
  const token = await apiLogin(request, 'admin');
  const accounts = await (await apiCall(request, token, 'get', '/admin/balance/accounts?account_type=master_agent&page_size=100')).json();
  const account = accounts.items[0];
  expect(account).toBeTruthy();
  const detail = async () => (await apiCall(request, token, 'get', `/admin/balance/accounts/${account.id}`)).json();
  const before = await detail();
  await tenantLogin(page, ADMIN_URL, 'admin');
  await expect(page).not.toHaveURL(/\/login/);
  await navTo(page, 'Balance Management');
  await page.getByTestId('account-search').fill(account.username);
  await page.getByRole('option', { name: new RegExp(`^${account.username} ·`) }).first().click();
  await expect(page.getByTestId('selected-account')).toContainText(account.username);
  await expect(page.locator('form input')).toHaveCount(3);
  await expect(page.getByLabel(/reason/i)).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Deposit', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Withdrawal Commission', exact: true })).toBeVisible();

  const post = async (prefix: string, amount: string, button: string) => {
    await page.getByTestId(`${prefix}-amount`).fill(amount);
    await page.getByTestId(`${prefix}-submit`).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText(account.username);
    await expect(dialog.locator('input, textarea')).toHaveCount(0);
    await dialog.getByRole('button', { name: button, exact: true }).click();
    await expect(page.getByTestId(`${prefix}-amount`)).toHaveValue('');
    await expect(page.getByTestId(`${prefix}-amount`).locator('xpath=ancestor::mat-form-field').locator('mat-error')).toHaveCount(0);
    await expect(page.getByTestId('selected-account')).toContainText(account.username);
  };
  await post('account-grant', '25', 'Deposit');
  await expect.poll(async () => Number((await detail()).account.balance)).toBe(Number(before.account.balance) + 25);
  const shownBalance = (value: number) => value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  await expect(page.locator('[data-field="balance"]')).toHaveText(shownBalance(Number(before.account.balance) + 25));
  await expect(page.getByTestId('cash-out-account-balance')).toHaveText(shownBalance(Number(before.account.balance) + 25));
  const history = page.getByTestId('withdrawal-history');
  await history.locator('mat-button-toggle').filter({ hasText: /^Deposit$/ }).click();
  await expect(history.locator('tbody')).toContainText(account.username);
  await expect(history.locator('tbody')).toContainText('25.00');
  await history.locator('mat-button-toggle').filter({ hasText: /^Withdrawal$/ }).click();
  await expect(page.getByTestId('deduct-submit')).toHaveCount(0);
  await expect(page.getByTestId('balance-actions').getByTestId('cash-out-submit')).toBeVisible();
  await expect(page.getByTestId('withdrawal-history').locator('form')).toHaveCount(0);
  await post('cash-out', '10', 'Confirm Withdrawal');
  await expect.poll(async () => Number((await detail()).account.balance)).toBe(Number(before.account.balance) + 15);
  await post('cash-out', '15', 'Confirm Withdrawal');
  await expect.poll(async () => Number((await detail()).account.balance)).toBe(Number(before.account.balance));

  // Exercise the real commission balance without manufacturing earnings.
  if (Number(before.available_earnings) > 0) {
    const amount = Math.min(Number(before.available_earnings), 1).toFixed(2);
    await post('earnings', amount, 'Withdrawal Commission');
    await expect.poll(async () => Number((await detail()).available_earnings)).toBe(Number(before.available_earnings) - Number(amount));
  } else {
    await page.getByTestId('earnings-amount').fill('1');
    await page.getByTestId('earnings-submit').click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.locator('.toast-error').filter({ hasText: 'available commission' }).first()).toBeVisible();
  }
  await expect(page.locator('[data-field="balance"]')).toHaveText(shownBalance(Number(before.account.balance)));
  await history.locator('mat-button-toggle').filter({ hasText: /^Withdrawal Commission$/ }).click();
  if (Number(before.available_earnings) > 0) await expect(history.locator('tbody')).toContainText(account.username);
  else await expect(history).toContainText('No commission withdrawals yet');
  await history.locator('mat-button-toggle').filter({ hasText: /^Withdrawal$/ }).click();
  await expect(history.locator('tbody')).toContainText(account.username);
  await expect(page.getByRole('heading', { name: 'Transaction history' })).toBeVisible();
  for (const width of [320, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole('heading', { name: 'Balance Management', exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: test.info().outputPath(`balance-management-ui-${width}.png`), fullPage: true, animations: 'disabled' });
  }
});

test('Loaders transfer funded allocation and withdraw without receiving administrator grant access', async ({ page, request }) => {
  const admin = await apiLogin(request, 'admin');
  const username = `loader${Date.now().toString(36)}`;
  const created = await apiCall(request, admin, 'post', '/admin/users', { username, full_name: 'Balance Loader', account_type: 'loader', parent_id: null, password: 'DemoPass#2026' });
  expect(created.status(), await created.text()).toBe(201);
  const { user: loader } = await created.json();
  const funded = await apiCall(request, admin, 'post', '/admin/balance/account-grants', { target_user_id: loader.id, amount: '100', remarks: 'Loader allocation', idempotency_key: `fund-${username}` });
  expect(funded.status(), await funded.text()).toBe(201);
  const accounts = await (await apiCall(request, admin, 'get', '/admin/balance/accounts?account_type=master_agent')).json();
  const account = accounts.items[0];
  await tenantLogin(page, ADMIN_URL, username);
  await expect(page).not.toHaveURL(/\/login/);
  await navTo(page, 'Balance Management');
  await page.getByTestId('account-search').fill(account.username);
  await page.getByRole('option', { name: new RegExp(`^${account.username} ·`) }).first().click();
  await expect(page.getByTestId('loader-grant-submit')).toHaveText('Transfer Balance');
  await expect(page.getByTestId('account-grant-submit')).toHaveCount(0);
  await expect(page.getByRole('tab', { name: /Adjustments/ })).toHaveCount(0);
  await expect(page.getByTestId('cash-out-submit')).toBeVisible();
  await expect(page.getByTestId('earnings-submit')).toBeVisible();
  await page.getByTestId('loader-grant-amount').fill('25');
  await page.getByTestId('loader-grant-submit').click();
  await page.getByRole('dialog').getByRole('button', { name: 'Transfer Balance', exact: true }).click();
  await expect(page.getByTestId('loader-grant-amount')).toHaveValue('');
  await expect(page.getByTestId('cash-out-admin-balance')).toHaveText('75.00');
  await page.getByTestId('cash-out-amount').fill('10');
  await page.getByTestId('cash-out-submit').click();
  await page.getByRole('dialog').getByRole('button', { name: 'Confirm Withdrawal' }).click();
  await expect(page.getByTestId('cash-out-amount')).toHaveValue('');
  await expect(page.getByTestId('cash-out-admin-balance')).toHaveText('85.00');
  await expect(page.getByTestId('withdrawal-history').locator('tbody')).toContainText(account.username);
  const token = await apiLogin(request, username);
  expect((await apiCall(request, token, 'post', '/admin/balance/account-grants', { target_user_id: account.id, amount: '10', idempotency_key: `deny-${username}` })).status()).toBe(403);
});
