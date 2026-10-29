import { APIRequestContext, BrowserContext, Page, expect, request as playwrightRequest, test } from '@playwright/test';

import { ADMIN_URL, DEMO_PASSWORD, PLAYER_URL, TENANT, apiCall, apiLogin, openArena, tenantLogin, unique } from './helpers';

/**
 * Declarator -> confirmation -> independent approval -> official result -> Player outcomes.
 * Setup (accounts, event, fight lifecycle) goes through the real API; every declaration,
 * approval and pick goes through the browser UI.
 */
type Side = 'MERON' | 'WALA' | 'DRAW';
type Outcome = 'WIN' | 'LOSE' | 'VOID';

let api: APIRequestContext;
let adminToken: string;
let eventId: string;
let eventName: string;
let deskUser: string;
let deskUserId: string;
let fights: { id: string; match_number: number }[] = [];
let admin: Page;
let desk: Page;
const players: { username: string; context: BrowserContext; page: Page; balance: number }[] = [];

const login = (username: string, password = DEMO_PASSWORD, headers: Record<string, string> = TENANT) => apiLogin(api, username, password, headers);
const call = (token: string, method: 'get' | 'post' | 'put', path: string, data?: unknown, headers: Record<string, string> = TENANT) =>
  apiCall(api, token, method, path, data, headers);

async function balance(username: string): Promise<number> {
  return (await (await call(await login(username), 'get', '/portal/points')).json()).balance;
}

async function startFight(n: number): Promise<void> {
  const r = await call(adminToken, 'post', `/admin/matches/${fights[n - 1].id}/start`);
  expect(r.status(), await r.text()).toBe(200);
}

/** Each player marks a side on the live fight from the arena page. */
async function pick(n: number, sides: Side[]): Promise<void> {
  for (const [i, side] of sides.entries()) {
    const { page } = players[i];
    await expect(page.locator('h1.fight-no')).toContainText(`#${n}`, { timeout: 15_000 });
    const radio = page.getByRole('radio', { name: side, exact: true });
    await expect(radio).toBeEnabled();
    await radio.check();
    await expect(page.locator('.pick-status')).toContainText(`Your pick ${side}`);
    await expect(page.locator('.pick-status')).toContainText('Awaiting official result');
  }
}

/** The Declarator confirms the result; it must not be official until approved. */
async function declare(n: number, button: RegExp, question: string, reason?: string): Promise<void> {
  const fight = desk.getByRole('region', { name: 'Current fight' });
  await expect(fight).toContainText(`#${n}`, { timeout: 15_000 });
  const control = desk.getByRole('button', { name: button });
  await expect(control).toBeEnabled({ timeout: 15_000 });
  await control.click();
  const dialog = desk.getByRole('dialog');
  await expect(dialog).toContainText(question);
  if (reason) {
    await dialog.getByLabel('Cancellation reason').fill(reason);
    await dialog.getByRole('button', { name: 'Cancel fight' }).click();
  } else {
    await dialog.getByRole('button', { name: 'Confirm declaration' }).click();
  }
  await expect(desk.getByRole('status').filter({ hasText: `Fight #${n}: declaration submitted. Awaiting approval.` })).toBeVisible();
  await expect(fight).toContainText('Awaiting approval');
  // A Declarator can never approve their own declaration.
  await expect(desk.getByRole('button', { name: 'Review current declaration' })).toHaveCount(0);
}

/** A tenant administrator independently confirms the pending declaration. */
async function approve(n: number): Promise<void> {
  const fight = admin.getByRole('region', { name: 'Current fight' });
  await expect(fight).toContainText(`#${n}`, { timeout: 15_000 });
  await expect(fight).toContainText('Awaiting approval', { timeout: 15_000 });
  await admin.getByRole('button', { name: 'Review current declaration' }).click();
  const history = admin.getByRole('dialog');
  await history.getByRole('button', { name: 'Confirm official result' }).click();
  await admin.getByRole('dialog').last().getByRole('button', { name: 'Confirm Finalization' }).click();
  await expect(fight).toContainText('Finalized', { timeout: 15_000 });
  await admin.keyboard.press('Escape');
}

/** Each player's outcome for fight n, read from the fight's page in the player's own browser. */
async function expectOutcomes(n: number, expected: Outcome[], officialText: string): Promise<void> {
  for (const [i, outcome] of expected.entries()) {
    const detail = await players[i].context.newPage();
    await detail.goto(`${PLAYER_URL}/matches/${fights[n - 1].id}`);
    const official = detail.getByRole('region', { name: 'Official result' });
    await expect(official).toContainText(officialText, { timeout: 15_000 });
    await expect(official.locator('.pick')).toContainText(`· ${outcome}`);
    await detail.close();
  }
}

test.describe.serial('Declarator official result workflow', () => {
  test.beforeAll(async ({ browser }) => {
    test.setTimeout(120_000);
    api = await playwrightRequest.newContext();
    adminToken = await login('admin');
    deskUser = `e2e-desk-${Date.now().toString(36)}`;
    const user = await call(adminToken, 'post', '/admin/users', {
      username: deskUser, full_name: 'E2E Declarator', account_type: 'declarator', password: DEMO_PASSWORD,
    });
    expect(user.status(), await user.text()).toBe(201);
    deskUserId = (await user.json()).user.id;

    eventName = unique('E2E Declared Derby');
    const event = await call(adminToken, 'post', '/admin/events', { name: eventName, venue: 'Declarator Arena', starts_at: '2030-06-01T18:00:00+08:00' });
    expect(event.status(), await event.text()).toBe(201);
    eventId = (await event.json()).id;
    for (const [meron, wala] of [['Red Comet', 'Blue Ridge'], ['Crimson Spur', 'Cobalt Wing'], ['Ember', 'Tide'], ['Blaze', 'Frost']]) {
      const m = await call(adminToken, 'post', `/admin/events/${eventId}/matches`, { meron_name: meron, wala_name: wala });
      expect(m.status(), await m.text()).toBe(201);
      fights.push(await m.json());
    }
    for (const path of [`/admin/events/${eventId}/publish`, `/admin/events/${eventId}/start`]) {
      expect((await call(adminToken, 'post', path)).status()).toBe(200);
    }
    expect((await call(adminToken, 'put', `/declarator/events/${eventId}/assignments/${deskUserId}`)).status()).toBe(200);
    await startFight(1);

    // Three players, each in their own browser, watching this event's arena.
    for (const username of ['player1', 'player2', 'player3']) {
      const context = await browser.newContext();
      const page = await context.newPage();
      await tenantLogin(page, PLAYER_URL, username);
      await expect(page).not.toHaveURL(/\/login/);
      await openArena(page, eventName);
      players.push({ username, context, page, balance: await balance(username) });
    }

    desk = await (await browser.newContext()).newPage();
    await tenantLogin(desk, ADMIN_URL, deskUser);
    await expect(desk).toHaveURL(/\/admin\/declarator/);
    await expect(desk.getByRole('heading', { name: 'Declarator dashboard' })).toBeVisible();

    admin = await (await browser.newContext()).newPage();
    await tenantLogin(admin, ADMIN_URL, 'admin');
    await expect(admin).not.toHaveURL(/\/login/);
    await admin.goto(`${ADMIN_URL}/declarator`);
    await admin.getByRole('combobox', { name: 'Event' }).first().click();
    await admin.getByRole('option', { name: eventName }).click();
  });

  test.afterAll(async () => {
    // Each run creates a Declarator with a known demo password: suspend it (reversible, audited)
    // rather than leave a usable credential behind. Its declaration history is kept.
    if (deskUserId) await call(adminToken, 'post', `/admin/users/${deskUserId}/suspend`);
    // Every fight is decided by now: close the event so test runs do not pile up in "Live" lists.
    if (eventId) await call(adminToken, 'post', `/admin/events/${eventId}/complete`);
    for (const p of players) await p.context.close();
    await desk?.context().close();
    await admin?.context().close();
    await api?.dispose();
  });

  test('A: MERON wins', async () => {
    test.setTimeout(90_000);
    // The only assigned event is selected automatically (the second "Event" select filters records).
    await expect(desk.getByRole('combobox', { name: 'Event' }).first()).toContainText(eventName);
    await pick(1, ['MERON', 'WALA', 'DRAW']);
    await declare(1, /MERON\s*WINS/, 'Are you sure you want to declare MERON as the winner of Fight #1?');
    // Pending proposals are not official: players still see the fight as undecided.
    await expect(players[0].page.locator('.pick-status')).toContainText('Awaiting official result');
    await approve(1);
    await expectOutcomes(1, ['WIN', 'LOSE', 'LOSE'], 'Meron wins');
    // Synced to the Declarator's own desk over the WebSocket.
    await expect(desk.getByRole('region', { name: 'Current fight' })).toContainText('Finalized', { timeout: 15_000 });
  });

  test('B: WALA wins', async () => {
    test.setTimeout(90_000);
    await startFight(2);
    await pick(2, ['WALA', 'MERON', 'DRAW']);
    await declare(2, /WALA\s*WINS/, 'Are you sure you want to declare WALA as the winner of Fight #2?');
    await approve(2);
    await expectOutcomes(2, ['WIN', 'LOSE', 'LOSE'], 'Wala wins');
  });

  test('C: DRAW', async () => {
    test.setTimeout(90_000);
    await startFight(3);
    await pick(3, ['DRAW', 'MERON', 'WALA']);
    await declare(3, /^DRAW$/, 'Are you sure you want to declare Fight #3 a DRAW?');
    await approve(3);
    await expectOutcomes(3, ['WIN', 'LOSE', 'LOSE'], 'Draw');
  });

  test('D: cancelled fight voids every pick and moves no points', async () => {
    test.setTimeout(90_000);
    const reason = 'Gamecock injured before release';
    await startFight(4);
    await pick(4, ['MERON', 'WALA', 'DRAW']);
    await declare(4, /Cancel fight/, 'Are you sure you want to cancel Fight #4?', reason);
    await approve(4);
    await expectOutcomes(4, ['VOID', 'VOID', 'VOID'], `Cancelled: ${reason}`);
    // Each player's own arena shows the void outcome on the cancelled fight still on stage.
    for (const { page } of players) await expect(page.locator('.pick-status')).toContainText('VOID', { timeout: 15_000 });

    // Recorded in the Declarator's history with its reason and a CANCELLED result.
    const row = desk.getByRole('row', { name: /#4/ });
    await expect(row).toContainText('Cancelled');
    await expect(row).toContainText(reason);
    const records = await (await call(await login(deskUser), 'get', `/declarator/records?event_id=${eventId}`)).json();
    const outcomes = Object.fromEntries(records.items.map((r: { match: { match_number: number }; official_winner: string }) => [r.match.match_number, r.official_winner]));
    expect(outcomes).toEqual({ 1: 'meron', 2: 'wala', 3: 'draw', 4: 'cancelled' });

    // Results never move demonstration points, in either direction.
    for (const p of players) expect(await balance(p.username)).toBe(p.balance);
  });

  test('E: access control', async ({ page }) => {
    const deskToken = await login(deskUser);
    const playerToken = await login('player1');

    // The Declarator sees exactly the events it is assigned to.
    const events = await (await call(deskToken, 'get', '/declarator/events')).json();
    expect(events.map((e: { id: string }) => e.id)).toEqual([eventId]);
    const unassigned = (await (await call(adminToken, 'get', '/admin/events?page_size=50')).json()).items.find((e: { id: string; status: string }) => e.id !== eventId && e.status === 'live');
    expect((await call(deskToken, 'get', `/declarator/events/${unassigned.id}/monitor`)).status()).toBe(404);

    // Players cannot declare, and cannot open the administration portal.
    const live = await call(playerToken, 'post', `/declarator/matches/${fights[0].id}/declarations`, { request_id: crypto.randomUUID(), outcome: 'wala', base_revision: 0 });
    expect(live.status()).toBe(403);
    await tenantLogin(page, ADMIN_URL, 'player1');
    await expect(page.getByRole('alert')).toContainText('no administration access');

    // The Declarator's token is useless for another tenant.
    const foreign = await call(deskToken, 'get', '/declarator/events', undefined, { 'X-Tenant-Slug': 'cli-check' });
    expect([401, 403, 404]).toContain(foreign.status());

    // No administrator or owner functions.
    expect((await call(deskToken, 'get', '/admin/users')).status()).toBe(403);
    expect((await call(deskToken, 'post', '/admin/events', { name: 'Nope', venue: 'X', starts_at: '2030-06-01T18:00:00+08:00' })).status()).toBe(403);
    expect((await call(deskToken, 'post', `/admin/matches/${fights[0].id}/start`)).status()).toBe(403);
    expect([401, 403]).toContain((await call(deskToken, 'get', '/platform/tenants', undefined, {} as typeof TENANT)).status());
  });
});
