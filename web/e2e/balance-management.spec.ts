import { expect, test } from '@playwright/test';
import { ADMIN_URL, DEMO_PASSWORD, apiCall, apiLogin, navTo, newSession, tenantLogin } from './helpers';

/** Balance of every player, as the API reports it to the administrator. */
async function balances(request: Parameters<typeof apiLogin>[0], token: string): Promise<Record<string, number>> {
  const page = await (await apiCall(request, token, 'get', '/admin/balance/accounts?account_type=player&page_size=100')).json();
  return Object.fromEntries(page.items.map((a: { username: string; balance: string }) => [a.username, Number(a.balance)]));
}

test('administrators add balance to one player, corrections need a second administrator, agents stay read-only', async ({ page, browser, request }) => {
  const admin = await apiLogin(request, 'admin');
  const settings = await (await apiCall(request, admin, 'get', '/admin/betting/settings')).json();
  expect((await apiCall(request, admin, 'put', '/admin/betting/settings', { enabled: true, starting_balance: '0.00' })).status()).toBe(200);
  // A second administrator reviews corrections (maker-checker).
  const checkerName = `checker${Date.now().toString(36)}`;
  const created = await apiCall(request, admin, 'post', '/admin/users',
    { username: checkerName, full_name: 'Second Admin', account_type: 'admin', parent_id: null, password: DEMO_PASSWORD });
  expect(created.status(), await created.text()).toBe(201);
  try {
    for (const name of ['player1', 'player2', 'player3']) await apiCall(request, await apiLogin(request, name), 'get', '/portal/betting');
    const before = await balances(request, admin);

    await tenantLogin(page, ADMIN_URL, 'admin');
    await expect(page).not.toHaveURL(/\/login/);
    await navTo(page, 'Balance Management');
    await expect(page.getByRole('heading', { name: 'Balance Management' })).toBeVisible();
    await expect(page.getByTestId('no-cash-value')).toContainText('no cash value');
    await expect(page.getByRole('button', { name: 'Grant to every player' })).toHaveCount(0);

    await page.getByTestId('account-search').fill('player1');
    await page.getByRole('option', { name: /player1/ }).click();
    await expect(page.getByTestId('selected-account')).toContainText('player1');
    await page.getByTestId('account-grant-amount').fill('25');
    await page.getByRole('button', { name: 'Add to player1' }).click();
    await expect(page.getByRole('heading', { name: 'Add balance to player1?' })).toBeVisible();
    await page.getByRole('dialog').getByRole('button', { name: 'Add balance' }).click();
    await expect(page.locator('.toast-success', { hasText: /added to player1/ }).first()).toBeVisible();
    const afterSpecific = await balances(request, admin);
    for (const [name, value] of Object.entries(before)) expect(afterSpecific[name]).toBe(name === 'player1' ? value + 25 : value);

    await page.getByRole('tab', { name: 'Transactions' }).click();
    await expect(page.getByText('Account grant').first()).toBeVisible();
    await page.getByRole('tab', { name: 'Accounts' }).click();
    await page.getByTestId('correction-basis').click();
    await page.getByRole('option', { name: /Account grant \+25\.00/ }).first().click();
    await page.getByTestId('correction-reason').fill('Granted to player1 in error');
    await page.getByRole('button', { name: 'Submit for review' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Submit' }).click();
    await expect(page.locator('.toast-success', { hasText: 'submitted for review' }).first()).toBeVisible();
    expect((await balances(request, admin))['player1']).toBe(afterSpecific['player1']); // nothing posts while pending

    // The proposer cannot approve their own correction.
    await page.getByRole('tab', { name: /Adjustments/ }).click();
    await expect(page.getByText('Needs another admin').first()).toBeVisible();

    // ---- a second administrator approves it ----
    const checker = await newSession(browser, ADMIN_URL, checkerName);
    await navTo(checker, 'Balance Management');
    await checker.getByRole('tab', { name: /Adjustments/ }).click();
    await checker.getByRole('button', { name: 'Approve' }).first().click();
    await checker.getByRole('dialog').getByRole('button', { name: 'Approve' }).click();
    await expect(checker.locator('.toast-success', { hasText: 'applied' }).first()).toBeVisible();
    expect((await balances(request, admin))['player1']).toBe(before['player1']);
    await checker.context().close();

    const recon = await (await apiCall(request, admin, 'get', '/admin/betting/reconciliation')).json();
    expect(recon.balanced).toBe(true);

    // ---- responsive layout ----
    await page.getByRole('tab', { name: 'Accounts' }).click();
    for (const width of [320, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: test.info().outputPath(`balance-management-${width}.png`), animations: 'disabled', fullPage: true });
    }

    // ---- an agent sees only assigned players and has no write action ----
    const agentPage = await newSession(browser, ADMIN_URL, 'agent1');
    await navTo(agentPage, 'Balance Management');
    await expect(agentPage.getByText('assigned to you', { exact: false })).toBeVisible();
    await expect(agentPage.getByRole('button', { name: /Grant to every player|Add to|Reset every player|Submit for review/ })).toHaveCount(0);
    await expect(agentPage.getByRole('tab', { name: /Adjustments/ })).toHaveCount(0);
    await agentPage.context().close();
    const agent = await apiLogin(request, 'agent1');
    const seen = await (await apiCall(request, agent, 'get', '/admin/balance/accounts?page_size=100')).json();
    expect(seen.items.map((a: { username: string }) => a.username).sort()).toEqual(['player1', 'player2', 'player3']);
    const refused = await apiCall(request, agent, 'post', '/admin/balance/grants', { amount: '5.00', idempotency_key: `e2e-${Date.now()}` });
    expect(refused.status()).toBe(403);
  } finally {
    await apiCall(request, admin, 'put', '/admin/betting/settings', { enabled: settings.enabled, starting_balance: settings.starting_balance });
  }
});
