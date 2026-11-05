import { BrowserContext, Page, expect, test } from '@playwright/test';

/**
 * Contabo layout (docs/deployment-contabo.md) against a running UAT environment behind the
 * shared gateway: the UAT access gate, and Owner (/owner/), Admin (/admin/) and Player (/)
 * on ONE origin keeping separate sessions. Skipped unless E2E_CONTABO_URL is set; run with
 * playwright.contabo.config.ts (no dev servers, no seeding: UAT holds synthetic data).
 *
 *   E2E_CONTABO_URL=https://uat.swcmain183.live E2E_CONTABO_ACCESS_TOKEN=… \
 *   E2E_CONTABO_OWNER_EMAIL=… E2E_CONTABO_OWNER_PASSWORD=… E2E_CONTABO_TENANT_PASSWORD=… \
 *   npx playwright test -c playwright.contabo.config.ts
 */

const BASE = (process.env['E2E_CONTABO_URL'] || '').replace(/\/$/, '');
const TOKEN = process.env['E2E_CONTABO_ACCESS_TOKEN'] || '';
const OWNER_EMAIL = process.env['E2E_CONTABO_OWNER_EMAIL'] || '';
const OWNER_PASSWORD = process.env['E2E_CONTABO_OWNER_PASSWORD'] || '';
const TENANT_PASSWORD = process.env['E2E_CONTABO_TENANT_PASSWORD'] || '';
const ME = /\/api\/v1\/(platform\/)?auth\/me$/;

test.skip(!BASE || !TOKEN, 'set E2E_CONTABO_URL and E2E_CONTABO_ACCESS_TOKEN to run the Contabo gateway checks');

/** Every request URL and Referer the context sends, to prove the token never travels in one. */
function recordUrls(context: BrowserContext): string[] {
  const seen: string[] = [];
  context.on('request', (r) => {
    seen.push(r.url());
    const referer = r.headers()['referer'];
    if (referer) seen.push(referer);
  });
  return seen;
}

/** A context that has passed the UAT gate (cookie set by the real /__uat/access sign-in). */
async function gatedContext(browser: import('@playwright/test').Browser): Promise<BrowserContext> {
  const context = await browser.newContext({ httpCredentials: { username: 'uat', password: TOKEN } });
  const page = await context.newPage();
  const response = await page.goto(`${BASE}/__uat/access`);
  // The HTTP redirect lands on /; the player app may then route on to /login on its own.
  expect(response?.url()).toBe(`${BASE}/`);
  expect((await context.cookies()).map((c) => c.name)).toContain('esb_uat_access');
  await page.close();
  return context;
}

/**
 * Hard-reloads a tab and returns the identity its portal restored (the first /auth/me answer),
 * or null when the portal found no session. Observed in the browser, so it also works when
 * only the browser can resolve the UAT host (local rehearsal with E2E_CONTABO_RESOLVE).
 */
async function reloadAs(page: Page): Promise<string | null> {
  let who: string | null = null;
  // Read the body as soon as the response arrives: Chromium may discard it shortly after.
  const me = page
    .waitForResponse(
      async (r) => {
        if (!ME.test(new URL(r.url()).pathname)) return false;
        if (r.status() === 200) {
          const body = await r.json().catch(() => null);
          who = body ? (body.username ?? body.email ?? null) : null;
        }
        return true;
      },
      { timeout: 6_000 },
    )
    .catch(() => null);
  await page.reload({ waitUntil: 'load' });
  await me;
  return who;
}

/** Reloads tabs one after another (concurrent reloads are covered by multi-portal-sessions.spec.ts). */
async function reloadAll(...pages: Page[]): Promise<(string | null)[]> {
  const out: (string | null)[] = [];
  for (const page of pages) out.push(await reloadAs(page));
  return out;
}

/** Status codes of API calls made from inside the page (same origin, same cookie jar). */
async function statuses(page: Page, calls: { path: string; method?: string; token?: string; body?: unknown }[]): Promise<number[]> {
  return page.evaluate(async (list) => {
    const out: number[] = [];
    for (const c of list) {
      const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' };
      if (c.token) headers['Authorization'] = `Bearer ${c.token}`;
      const r = await fetch(c.path, { method: c.method ?? 'GET', headers, body: c.body ? JSON.stringify(c.body) : undefined });
      out.push(r.status);
    }
    return out;
  }, calls);
}

async function tenantSignIn(context: BrowserContext, path: '/admin' | '', username: string): Promise<Page> {
  const page = await context.newPage();
  await page.goto(`${BASE}${path}/login`);
  await page.getByLabel('Username', { exact: true }).fill(username);
  await page.getByLabel('Password', { exact: true }).fill(TENANT_PASSWORD);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/);
  await expect(page.getByRole('button', { name: 'Account menu' })).toBeVisible();
  return page;
}

async function ownerSignIn(context: BrowserContext): Promise<Page> {
  const page = await context.newPage();
  await page.goto(`${BASE}/owner/login`);
  await page.getByLabel('Email').fill(OWNER_EMAIL);
  await page.getByLabel('Password', { exact: true }).fill(OWNER_PASSWORD);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Platform overview' })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`^${BASE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/owner/`));
  return page;
}

test.describe('UAT access gate', () => {
  test('refuses browsers without access, including an old ?key= link', async ({ browser }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    for (const path of ['/', '/admin/', '/owner/', '/api/health']) {
      const response = await page.goto(`${BASE}${path}`);
      expect(response?.status(), path).toBe(403);
    }
    await expect(page.getByText('UAT access is restricted.')).toBeVisible();
    const legacy = await page.goto(`${BASE}/__uat/access?key=${TOKEN}`);
    expect(legacy?.status()).toBe(401);
    expect((await context.cookies()).map((c) => c.name)).not.toContain('esb_uat_access');
    await context.close();
  });

  test('signs in without the token ever appearing in a URL or Referer', async ({ browser }) => {
    const context = await browser.newContext({ httpCredentials: { username: 'uat', password: TOKEN } });
    const urls = recordUrls(context);
    const page = await context.newPage();
    const response = await page.goto(`${BASE}/__uat/access`);
    expect(response?.url()).toBe(`${BASE}/`);
    expect(response?.status()).toBe(200);
    await expect(page.locator('app-root')).toBeAttached();
    // Navigate around so later requests (and their Referer headers) are recorded too.
    await page.goto(`${BASE}/admin/`);
    await page.goto(`${BASE}/owner/`);
    const gate = (await context.cookies()).find((c) => c.name === 'esb_uat_access');
    expect(gate?.httpOnly).toBe(true);
    expect(gate?.secure).toBe(true);
    expect(gate?.sameSite).toBe('Lax');
    expect(urls.length).toBeGreaterThan(5);
    expect(urls.filter((u) => u.includes(TOKEN))).toEqual([]);
    // The page itself cannot read the gate cookie.
    expect(await page.evaluate(() => document.cookie)).not.toContain('esb_uat_access');
    await context.close();
  });

  test('a wrong password is refused and sets no cookie', async ({ browser }) => {
    const context = await browser.newContext({ httpCredentials: { username: 'uat', password: `${TOKEN}x` } });
    const page = await context.newPage();
    const response = await page.goto(`${BASE}/__uat/access`);
    expect(response?.status()).toBe(401);
    expect((await context.cookies()).map((c) => c.name)).not.toContain('esb_uat_access');
    await context.close();
  });
});

test.describe('Owner, Admin and Player on one origin', () => {
  test.skip(!OWNER_EMAIL || !OWNER_PASSWORD || !TENANT_PASSWORD, 'set the E2E_CONTABO_* credentials');

  test('each portal keeps its own session through reloads and sign-out', async ({ browser }) => {
    const context = await gatedContext(browser);
    const forbidden: string[] = [];
    context.on('response', (r) => {
      if (r.status() === 403 && new URL(r.url()).pathname.startsWith('/api/')) forbidden.push(new URL(r.url()).pathname);
    });
    const owner = await ownerSignIn(context);
    const admin = await tenantSignIn(context, '/admin', 'admin');
    const player = await tenantSignIn(context, '', 'player1');

    expect(await reloadAll(owner, admin, player)).toEqual([OWNER_EMAIL, 'admin', 'player1']);

    // Signing out of the player portal ends only that session.
    const signOut = player.getByRole('menuitem', { name: 'Sign out' });
    // Right after a reload the shell may still be binding its handlers: retry until the menu opens.
    await expect(async () => {
      await player.keyboard.press('Escape');
      await player.getByRole('button', { name: 'Account menu' }).click();
      await expect(signOut).toBeVisible({ timeout: 1_000 });
    }).toPass({ timeout: 15_000 });
    await signOut.click();
    await expect(player).toHaveURL(/\/login/);
    expect(await reloadAll(owner, admin, player)).toEqual([OWNER_EMAIL, 'admin', null]);
    expect(forbidden).toEqual([]);
    await context.close();
  });

  test('owner deep links survive a reload under /owner/, and /owner redirects', async ({ browser }) => {
    const context = await gatedContext(browser);
    const owner = await ownerSignIn(context);
    await owner.goto(`${BASE}/owner/tenants`);
    await expect(owner.getByRole('heading', { name: 'Tenants' })).toBeVisible();
    await owner.reload();
    await expect(owner).toHaveURL(`${BASE}/owner/tenants`);
    await expect(owner.getByRole('heading', { name: 'Tenants' })).toBeVisible();
    const bare = await owner.goto(`${BASE}/owner`);
    expect(bare?.url()).toBe(`${BASE}/owner/`);
    const redirect = await bare?.request().redirectedFrom()?.response();
    expect(redirect?.status()).toBe(308);
    await context.close();
  });

  test('tenant and owner tokens are refused by the other API', async ({ browser }) => {
    const context = await gatedContext(browser);
    const page = await context.newPage();
    await page.goto(`${BASE}/owner/login`);
    const tokens = await page.evaluate(
      async ({ tenantPassword, ownerEmail, ownerPassword }) => {
        const post = async (path: string, body: unknown) =>
          (
            await (
              await fetch(path, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                body: JSON.stringify(body),
              })
            ).json()
          ).access_token as string;
        return {
          tenant: await post('/api/v1/auth/login', { username: 'admin', password: tenantPassword }),
          platform: await post('/api/v1/platform/auth/login', { email: ownerEmail, password: ownerPassword }),
        };
      },
      { tenantPassword: TENANT_PASSWORD, ownerEmail: OWNER_EMAIL, ownerPassword: OWNER_PASSWORD },
    );
    expect(tokens.tenant && tokens.platform).toBeTruthy();
    expect(
      await statuses(page, [
        { path: '/api/v1/platform/tenants', token: tokens.tenant },
        { path: '/api/v1/auth/me', token: tokens.platform },
        { path: '/api/v1/platform/tenants', token: tokens.platform },
        { path: '/api/v1/auth/me', token: tokens.tenant },
      ]),
    ).toEqual([401, 401, 200, 200]);
    // Refresh cookies are path-scoped per portal: the platform one never reaches tenant auth paths.
    expect((await context.cookies()).find((c) => c.name === 'esb_prt')?.path).toBe('/api/v1/platform/auth');
    expect((await context.cookies(`${BASE}/api/v1/auth/refresh`)).map((c) => c.name)).not.toContain('esb_prt');
    expect((await context.cookies(`${BASE}/api/v1/platform/auth/refresh`)).map((c) => c.name)).toContain('esb_prt');
    await context.close();
  });
});
