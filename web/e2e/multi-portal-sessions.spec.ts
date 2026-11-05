import { BrowserContext, Page, Route, expect, test } from '@playwright/test';
import { OWNER } from './global-setup';
import { ADMIN_URL, API, OWNER_URL, PLAYER_URL, TENANT, TENANT_SLUG, apiLogin, tenantLogin } from './helpers';

/**
 * Admin (/admin/) and Player (/) share one origin, so they share cookies. Each portal must keep
 * its own session: signing in, reloading, expiring or signing out in one tab must never change
 * who is signed in to another portal. All tabs below live in ONE browser context (one cookie jar),
 * exactly like tabs of the same browser.
 */

const ME = /\/api\/v1\/(platform\/)?auth\/me$/;

/**
 * Hard-reloads a tab and returns the identity the portal restored from its own session (the
 * /auth/me answer it booted with), or null when it found no session for this portal.
 */
async function reloadAs(page: Page): Promise<string | null> {
  let resolve!: (who: string | null) => void;
  const restored = new Promise<string | null>((r) => (resolve = r));
  const handler = async (route: Route) => {
    const response = await route.fetch();
    const body = response.status() === 200 ? await response.json() : null;
    resolve(body ? (body.username ?? body.email ?? null) : null);
    await route.fulfill({ response });
  };
  await page.route(ME, handler);
  try {
    await page.reload({ waitUntil: 'load' });
    // A tab without a session for its portal never asks /me; give a restore time to happen.
    return await Promise.race([restored, page.waitForTimeout(6_000).then(() => null)]);
  } finally {
    await page.unroute(ME, handler);
  }
}

/** Every 403 any tab receives from the API; a correct session layout produces none. */
function watchForbidden(context: BrowserContext): string[] {
  const seen: string[] = [];
  context.on('response', (r) => {
    if (r.status() === 403 && new URL(r.url()).pathname.startsWith('/api/')) seen.push(`${r.request().method()} ${new URL(r.url()).pathname}`);
  });
  return seen;
}

/** Opens a portal in a new tab of the same browser (dev servers take the tenant from ?tenant=). */
async function openTab(context: BrowserContext, base: string): Promise<Page> {
  const page = await context.newPage();
  await page.goto(`${base.replace(/\/$/, '')}/?tenant=${encodeURIComponent(TENANT_SLUG)}`);
  return page;
}

async function signOut(page: Page): Promise<void> {
  const item = page.getByRole('menuitem', { name: 'Sign out' });
  // Right after a reload the shell may still be binding its handlers: retry until the menu opens.
  await expect(async () => {
    await page.keyboard.press('Escape'); // close any overlay a previous attempt opened
    await page.getByRole('button', { name: 'Account menu' }).click();
    await expect(item).toBeVisible({ timeout: 1_000 });
  }).toPass({ timeout: 15_000 });
  await item.click();
  await expect(page).toHaveURL(/\/login/);
}

async function adminTab(context: BrowserContext): Promise<Page> {
  const page = await context.newPage();
  await tenantLogin(page, ADMIN_URL, 'admin');
  await expect(page.getByRole('heading', { name: /^Hello, / })).toBeVisible();
  return page;
}

async function playerTab(context: BrowserContext, username = 'player1'): Promise<Page> {
  const page = await context.newPage();
  await tenantLogin(page, PLAYER_URL, username);
  await expect(page).not.toHaveURL(/\/login/);
  await expect(page.getByRole('button', { name: 'Account menu' })).toBeVisible();
  return page;
}

test.describe('independent portal sessions in one browser', () => {
  test('admin and player stay signed in independently across reloads, concurrent reloads and sign-out', async ({ browser }) => {
    const context = await browser.newContext();
    const forbidden = watchForbidden(context);
    const admin = await adminTab(context);
    const player = await playerTab(context);

    // The reported bug: after the player signs in, reloading the admin tab became the player.
    expect(await reloadAs(admin)).toBe('admin');
    await expect(admin).toHaveURL(/\/admin\/?$/);
    await expect(admin.getByRole('heading', { name: /^Hello, / })).toBeVisible();
    expect(await reloadAs(player)).toBe('player1');
    await expect(player).not.toHaveURL(/\/admin|\/login/);

    // Hard reload of both portals at the same moment.
    expect(await Promise.all([reloadAs(admin), reloadAs(player)])).toEqual(['admin', 'player1']);

    // Each portal's API calls carry that portal's credentials.
    const adminMe = admin.waitForResponse((r) => r.url().endsWith('/api/v1/auth/me'));
    await admin.reload();
    expect((await (await adminMe).request().allHeaders())['x-portal']).toBe('admin');

    // Signing out of the player portal leaves the admin session alone, and vice versa.
    await signOut(player);
    expect(await reloadAs(admin)).toBe('admin');
    await tenantLogin(player, PLAYER_URL, 'player1');
    await expect(player).not.toHaveURL(/\/login/);
    await signOut(admin);
    expect(await reloadAs(player)).toBe('player1');
    expect(await reloadAs(admin)).toBeNull();
    await expect(admin).toHaveURL(/\/admin\/login/);

    expect(forbidden).toEqual([]);
    await context.close();
  });

  test('two admin tabs reloading at the same instant both stay signed in, and sign out together', async ({ browser }) => {
    const context = await browser.newContext();
    const forbidden = watchForbidden(context);
    const first = await adminTab(context);
    const second = await openTab(context, ADMIN_URL);
    await expect(second.getByRole('heading', { name: /^Hello, / })).toBeVisible();
    const player = await playerTab(context);
    for (let round = 0; round < 3; round++) {
      expect(await Promise.all([reloadAs(first), reloadAs(second), reloadAs(player)])).toEqual(['admin', 'admin', 'player1']);
    }
    // Tabs of one portal share its session: signing out in one signs the other out at once,
    // while the player portal in the same browser stays signed in.
    await signOut(first);
    await expect(second).toHaveURL(/\/admin\/login/);
    expect(await reloadAs(player)).toBe('player1');
    expect(forbidden).toEqual([]);
    await context.close();
  });

  test('an ended session only signs out its own portal', async ({ browser }) => {
    const context = await browser.newContext();
    const admin = await adminTab(context);
    const player = await playerTab(context);

    await context.clearCookies({ name: 'esb_rt_admin' });
    expect(await reloadAs(admin)).toBeNull();
    await expect(admin).toHaveURL(/\/admin\/login/);
    expect(await reloadAs(player)).toBe('player1');

    await tenantLogin(admin, ADMIN_URL, 'admin');
    await expect(admin.getByRole('heading', { name: /^Hello, / })).toBeVisible();
    await context.clearCookies({ name: 'esb_rt_player' });
    expect(await reloadAs(player)).toBeNull();
    await expect(player).toHaveURL(/\/login/);
    expect(await reloadAs(admin)).toBe('admin');
    await context.close();
  });

  test('a player account is refused by the admin portal without touching the player session', async ({ browser, request }) => {
    const context = await browser.newContext();
    const player = await playerTab(context);
    const admin = await context.newPage();
    await tenantLogin(admin, ADMIN_URL, 'player1');
    await expect(admin.getByText('This account has no administration access')).toBeVisible();
    expect(await reloadAs(admin)).toBeNull();
    await expect(admin).toHaveURL(/\/admin\/login/);
    expect(await reloadAs(player)).toBe('player1');

    // The API still refuses administration endpoints to the player's own credentials.
    const token = await apiLogin(request, 'player1');
    const denied = await request.get(`${API}/admin/users`, { headers: { ...TENANT, Authorization: `Bearer ${token}` } });
    expect(denied.status()).toBe(403);
    await context.close();
  });

  test('owner, admin and player sessions coexist in one browser', async ({ browser }) => {
    const context = await browser.newContext();
    const forbidden = watchForbidden(context);
    const owner = await context.newPage();
    await owner.goto(`${OWNER_URL}/login`);
    await owner.getByLabel('Email').fill(OWNER.email);
    await owner.getByLabel('Password', { exact: true }).fill(OWNER.password);
    await owner.getByRole('button', { name: 'Sign in', exact: true }).click();
    await expect(owner.getByRole('heading', { name: 'Platform overview' })).toBeVisible();
    const admin = await adminTab(context);
    const player = await playerTab(context);

    expect(await Promise.all([reloadAs(owner), reloadAs(admin), reloadAs(player)])).toEqual([OWNER.email, 'admin', 'player1']);
    await signOut(player);
    expect(await Promise.all([reloadAs(owner), reloadAs(admin)])).toEqual([OWNER.email, 'admin']);
    expect(forbidden).toEqual([]);
    await context.close();
  });
});
