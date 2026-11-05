import { expect, request, test } from '@playwright/test';

import { ADMIN_URL, API, DEMO_PASSWORD, PLAYER_URL, TENANT, apiCall, apiLogin, navTo, tenantLogin, unique } from './helpers';

/**
 * Failure paths across portals: invalid input, network loss and recovery, duplicate
 * submissions, session revocation, tenant isolation and the non-monetary wording of play
 * credits. Uses the real API; network failures are injected in the browser only.
 */
test.describe('Resilience and negative paths', () => {
  test('invalid sign-in input is refused with a clear message and no session', async ({ page }) => {
    await page.goto(`${PLAYER_URL}/login?tenant=${encodeURIComponent(TENANT['X-Tenant-Slug'])}`);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await expect(page).toHaveURL(/\/login/); // empty form: client-side validation, nothing sent
    await tenantLogin(page, PLAYER_URL, 'player1', 'not-the-password');
    await expect(page.getByRole('alert')).toContainText(/Invalid username or password/);
    await expect(page).toHaveURL(/\/login/);
  });

  test('a failed dashboard request shows an error with retry, then recovers without a reload', async ({ page }) => {
    await tenantLogin(page, ADMIN_URL, 'admin');
    await expect(page.getByRole('heading', { name: /Hello/ })).toBeVisible();
    await page.route('**/api/v1/admin/dashboard*', (route) => route.abort('internetdisconnected'));
    await navTo(page, 'Events');
    await navTo(page, 'Dashboard');
    const retry = page.getByRole('button', { name: 'Try again' });
    await expect(retry).toBeVisible();
    await expect(page.locator('app-stat-card')).toHaveCount(0); // no placeholder figures on failure
    await page.unroute('**/api/v1/admin/dashboard*');
    await retry.click();
    await expect(page.locator('app-stat-card').first()).toBeVisible();
    await expect(retry).toHaveCount(0);
  });

  test('double-clicking a create button sends one request', async ({ page }) => {
    await tenantLogin(page, ADMIN_URL, 'admin');
    await navTo(page, 'Events');
    let creates = 0;
    page.on('request', (r) => { if (r.method() === 'POST' && /\/api\/v1\/admin\/events$/.test(r.url())) creates++; });
    // Slow the response so the second click lands while the first is still in flight.
    await page.route('**/api/v1/admin/events', async (route) => {
      if (route.request().method() === 'POST') await new Promise((r) => setTimeout(r, 800));
      await route.continue();
    });
    await page.getByRole('button', { name: 'New event' }).click();
    const dialog = page.getByRole('dialog');
    const name = unique('Once');
    await dialog.getByLabel('Name', { exact: true }).fill(name);
    await dialog.getByLabel('Venue').fill('Test Arena');
    await dialog.getByLabel('Starts').fill('2030-06-01T18:00');
    await dialog.getByRole('button', { name: 'Create draft' }).dblclick();
    await expect(page.getByRole('heading', { name })).toBeVisible();
    expect(creates).toBe(1);
  });

  test('a revoked session sends the player back to sign-in instead of failing silently', async ({ page }) => {
    const api = await request.newContext();
    const admin = await apiLogin(api, 'admin');
    const username = `sess${Date.now().toString(36)}`;
    const created = await apiCall(api, admin, 'post', '/admin/users',
      { username, full_name: 'Session Test', account_type: 'player', password: DEMO_PASSWORD });
    expect(created.status(), await created.text()).toBe(201);

    await tenantLogin(page, PLAYER_URL, username);
    await expect(page).not.toHaveURL(/\/login/);
    // Changing the password elsewhere revokes every other session of this account.
    const other = await apiLogin(api, username);
    const changed = await apiCall(api, other, 'post', '/auth/me/password',
      { current_password: DEMO_PASSWORD, new_password: `${DEMO_PASSWORD}x` });
    expect(changed.status(), await changed.text()).toBe(200);

    // Any authenticated request now fails; background refreshes may notice before this click does.
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Events', exact: true })
      .click({ timeout: 3_000 }).catch(() => undefined);
    await expect(page).toHaveURL(/\/login\?.*reason=expired/, { timeout: 15_000 });
    await expect(page.getByRole('status')).toContainText('Your session ended');
    await api.dispose();
  });

  test('a token from one workspace is refused by another', async () => {
    const api = await request.newContext();
    const token = await apiLogin(api, 'player1');
    const own = await apiCall(api, token, 'get', '/auth/me');
    expect(own.status()).toBe(200);
    const other = await api.get(`${API}/auth/me`, { headers: { 'X-Tenant-Slug': 'demo', Authorization: `Bearer ${token}` } });
    if (TENANT['X-Tenant-Slug'] !== 'demo') expect(other.status()).toBe(401);
    await api.dispose();
  });

  test('play credits never appear as money in the player portal', async ({ page }) => {
    await tenantLogin(page, PLAYER_URL, 'player1');
    await expect(page).not.toHaveURL(/\/login/);
    await page.waitForLoadState('networkidle');
    const text = await page.locator('body').innerText();
    expect(text).not.toMatch(/₱|PHP|peso|cash balance|deposit|withdraw|payout/i);
  });

  test('phone layouts keep navigation reachable and never scroll sideways', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 320, height: 720 } });
    const page = await context.newPage();
    await tenantLogin(page, PLAYER_URL, 'player1');
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.getByRole('link', { name: /Events/ }).first()).toBeVisible(); // phone rail
    for (const path of ['/', '/events', '/history', '/results', '/points']) {
      await page.goto(`${PLAYER_URL}${path}`);
      await page.waitForLoadState('networkidle');
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, `player ${path} at 320px`).toBeLessThanOrEqual(0);
    }
    // Separate context: localhost cookies are shared across ports, so the player's session would follow.
    const adminContext = await browser.newContext({ viewport: { width: 320, height: 720 } });
    const admin = await adminContext.newPage();
    await tenantLogin(admin, ADMIN_URL, 'admin');
    await admin.getByRole('button', { name: 'Open navigation' }).click();
    await expect(admin.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Events', exact: true })).toBeVisible();
    expect(await admin.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    await adminContext.close();
    await context.close();
  });
});
