import { expect, test } from '@playwright/test';
import { mkdir, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { ADMIN_URL, PLAYER_URL, DEMO_PASSWORD, apiCall, apiLogin, newSession, tenantLogin } from './helpers';

test.use({ actionTimeout: 10000 });

test('Admin creates points, credits and transfers them to every role, with live server balances and history', async ({ page, browser, request }) => {
  test.setTimeout(150_000);
  const token = await apiLogin(request, 'admin');
  const run = Date.now().toString(36);
  const users: Record<string, { id: string; username: string }> = {};
  for (const role of ['master_agent', 'agent', 'player', 'declarator']) {
    const created = await apiCall(request, token, 'post', '/admin/users', {
      username: `pts-${role}-${run}`, full_name: `Points ${role}`, account_type: role,
      parent_id: role === 'agent' ? users['master_agent'].id : role === 'player' ? users['agent'].id : null,
      password: DEMO_PASSWORD,
    });
    expect(created.status(), await created.text()).toBe(201);
    users[role] = (await created.json()).user;
    // New accounts open at zero.
    expect((await (await apiCall(request, token, 'get', `/admin/points/accounts/${users[role].id}`)).json()).balance).toBe(0);
  }
  const initial = await (await apiCall(request, token, 'get', '/admin/points/summary')).json();
  const observer = await newSession(browser, ADMIN_URL, 'admin');
  const player = await newSession(browser, PLAYER_URL, users['player'].username);
  try {
    await tenantLogin(page, ADMIN_URL, 'admin');
    await expect(page).not.toHaveURL(/\/login/);
    await page.goto(`${ADMIN_URL}/points`);
    await observer.goto(`${ADMIN_URL}/points`);
    await player.goto(`${PLAYER_URL}/points`);
    await expect(page.locator('body')).not.toContainText(/demo (points|credits)/i);
    const wallet = (p: import('@playwright/test').Page) => p.locator('app-stat-card').filter({ hasText: 'Your balance' }).locator('.value');
    await expect(wallet(page)).toHaveText(initial.own_balance.toLocaleString('en-US'));

    // Create points in the Admin's wallet; a cancelled confirmation changes nothing.
    await page.getByRole('tab', { name: 'Create Points', exact: true }).click();
    await page.getByLabel('Amount', { exact: true }).fill('100000');
    await page.getByLabel('Reason (required)').fill('E2E wallet issuance');
    await page.getByRole('button', { name: 'Create Points', exact: true }).click();
    await expect(page.getByRole('dialog')).toContainText('Issued supply increases by 100,000');
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(wallet(page)).toHaveText(initial.own_balance.toLocaleString('en-US'));
    await page.getByRole('button', { name: 'Create Points', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Create points', exact: true }).click();
    let remaining = initial.own_balance + 100000;
    await expect(wallet(page)).toHaveText(remaining.toLocaleString('en-US'));
    await expect(wallet(observer)).toHaveText(remaining.toLocaleString('en-US'));
    await expect(page.getByRole('status', { name: 'Transaction receipt' })).toContainText('Issue completed');

    // Credit new points directly into the Declarator's wallet.
    await page.getByRole('radio', { name: 'Another account', exact: true }).click();
    await page.getByRole('combobox', { name: 'Recipient account', exact: true }).fill(users['declarator'].id);
    await page.getByRole('option', { name: new RegExp(users['declarator'].username) }).click();
    await page.getByLabel('Amount', { exact: true }).fill('1500');
    await page.getByLabel('Reason (required)').fill('E2E direct credit');
    await page.getByRole('button', { name: 'Create Points', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Create points', exact: true }).click();
    await expect(page.getByRole('status', { name: 'Transaction receipt' })).toContainText('Credit completed');
    await expect(wallet(page)).toHaveText(remaining.toLocaleString('en-US'));

    // Transfer existing points to every role.
    await page.getByRole('tab', { name: 'Transfer Points', exact: true }).click();
    for (const [role, amount] of [['master_agent', 20000], ['agent', 10000], ['player', 5000], ['declarator', 2000]] as const) {
      const account = users[role];
      await page.getByRole('combobox', { name: 'Recipient role', exact: true }).click();
      await page.getByRole('option', { name: new RegExp(`^${role.replace('_', ' ')}$`, 'i') }).click();
      await page.getByRole('combobox', { name: 'Recipient account', exact: true }).fill(account.id);
      await page.getByRole('option', { name: new RegExp(account.username) }).click();
      await expect(page.locator('.balances')).toContainText(account.id);
      await page.getByLabel('Amount', { exact: true }).fill(String(amount));
      await page.getByLabel('Remarks (optional)').fill(`E2E ${role} transfer`);
      await page.getByRole('button', { name: 'Transfer Points', exact: true }).click();
      await expect(page.getByRole('dialog')).toContainText('no points are created');
      await page.getByRole('dialog').getByRole('button', { name: 'Confirm transfer', exact: true }).click();
      remaining -= amount;
      await expect(wallet(page)).toHaveText(remaining.toLocaleString('en-US'));
      await expect(wallet(observer)).toHaveText(remaining.toLocaleString('en-US'));
      await expect(page.getByRole('status', { name: 'Transaction receipt' })).toContainText(account.username);
      const balance = (await (await apiCall(request, token, 'get', `/admin/points/accounts/${account.id}`)).json()).balance;
      expect(balance).toBe(role === 'declarator' ? 3500 : amount);
    }
    expect(remaining - initial.own_balance).toBe(63000);
    const final = await (await apiCall(request, token, 'get', '/admin/points/summary')).json();
    expect(final.total_issued).toBe(initial.total_issued + 101500);
    expect(final.total_distributed - initial.total_distributed).toBe(38500);
    await expect(player.locator('p.balance')).toHaveText('5,000');
    const integrity = await (await apiCall(request, token, 'get', '/admin/points/integrity')).json();
    expect(integrity.consistent).toBe(true);

    // History: filter, details and CSV export.
    const history = page.locator('app-points-history');
    await history.getByRole('combobox', { name: 'Type', exact: true }).click();
    await page.getByRole('option', { name: 'Credit', exact: true }).click();
    await expect(history.locator('app-data-table')).toContainText(users['declarator'].username);
    await history.getByText(users['declarator'].username).first().click();
    await expect(page.getByRole('dialog')).toContainText('E2E direct credit');
    await expect(page.getByRole('dialog')).toContainText('Asia/Manila');
    await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
    const download = page.waitForEvent('download');
    await history.getByRole('button', { name: 'Export CSV' }).click();
    const csv = await readFile(await (await download).path(), 'utf8');
    expect(csv.split('\n')[0]).toContain('reference');
    expect(csv).toContain('E2E direct credit');

    const output = resolve(__dirname, '../../docs/admin-credits');
    await mkdir(output, { recursive: true });
    for (const width of [320, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.locator('section[aria-label="Points operations"]').scrollIntoViewIfNeeded();
      await page.waitForTimeout(250);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await page.locator('.mat-drawer-content').evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      await page.screenshot({ path: resolve(output, `wallet-management-${width}.png`) });
    }
  } finally {
    await observer.context().close();
    await player.context().close();
  }
});
