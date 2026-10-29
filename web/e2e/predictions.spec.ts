import { APIRequestContext, BrowserContext, Page, expect, request as playwrightRequest, test } from '@playwright/test';

import { PLAYER_URL, apiCall, apiLogin, openArena, tenantLogin, unique } from './helpers';

/**
 * Predictions live in PostgreSQL: one player, two separate browser sessions (separate contexts,
 * separate sign-ins, no shared storage) must see the same pick, the same changes, and the same
 * official outcomes. Fight lifecycle is driven through the API by the operator.
 */
const PLAYER = 'player2';
let api: APIRequestContext;
let admin: string;
let eventId: string;
let eventName: string;
let fights: { id: string; match_number: number }[] = [];
let a: { context: BrowserContext; page: Page };
let b: { context: BrowserContext; page: Page };
let balance: number;

async function session(browser: import('@playwright/test').Browser) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await tenantLogin(page, PLAYER_URL, PLAYER);
  await expect(page).not.toHaveURL(/\/login/);
  await openArena(page, eventName);
  return { context, page };
}

const pickStatus = (page: Page) => page.locator('.pick-status');

/** The history row for one fight, read from the player's "My predictions" page. */
async function historyRow(page: Page, fight: number) {
  await page.goto(`${PLAYER_URL}/predictions`);
  await page.getByPlaceholder('Search fight #, corner or event').fill(`#${fight}`);
  // The search is debounced: wait for the server-filtered list before reading the row.
  const rows = page.getByRole('row').filter({ hasText: eventName });
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText(`#${fight}`);
  return rows;
}

async function points(): Promise<number> {
  return (await (await apiCall(api, await apiLogin(api, PLAYER), 'get', '/portal/points')).json()).balance;
}

test.describe.serial('Server-side predictions across sessions', () => {
  test.beforeAll(async ({ browser }) => {
    test.setTimeout(120_000);
    api = await playwrightRequest.newContext();
    admin = await apiLogin(api, 'admin');
    eventName = unique('E2E Prediction Derby');
    const event = await (await apiCall(api, admin, 'post', '/admin/events', { name: eventName, venue: 'Prediction Arena', starts_at: '2030-07-01T18:00:00+08:00' })).json();
    eventId = event.id;
    for (const [meron, wala] of [['Garnet', 'Sapphire'], ['Ruby', 'Lapis']]) {
      const r = await apiCall(api, admin, 'post', `/admin/events/${event.id}/matches`, { meron_name: meron, wala_name: wala });
      expect(r.status(), await r.text()).toBe(201);
      fights.push(await r.json());
    }
    for (const path of [`/admin/events/${event.id}/publish`, `/admin/events/${event.id}/start`]) {
      expect((await apiCall(api, admin, 'post', path)).status()).toBe(200);
    }
    balance = await points();
    a = await session(browser);
    b = await session(browser);
  });

  test.afterAll(async () => {
    // Both fights are decided by now: close the event so runs do not pile up in "Live" lists.
    if (eventId) await apiCall(api, admin, 'post', `/admin/events/${eventId}/complete`);
    await a?.context.close();
    await b?.context.close();
    await api?.dispose();
  });

  test('a pick made in one session appears in another, and changes sync live', async () => {
    await expect(a.page.locator('h1.fight-no')).toContainText('#1');
    await a.page.getByRole('radio', { name: 'WALA', exact: true }).check();
    await expect(pickStatus(a.page)).toContainText('Your pick WALA');

    // A separate sign-in with no shared browser storage loads the same pick from the server.
    await b.page.reload();
    await openArena(b.page, eventName);
    await expect(pickStatus(b.page)).toContainText('Your pick WALA');

    // Changing it in session B reaches session A over the WebSocket, without a reload.
    await b.page.getByRole('radio', { name: 'DRAW', exact: true }).check();
    await expect(pickStatus(b.page)).toContainText('Your pick DRAW');
    await expect(pickStatus(a.page)).toContainText('Your pick DRAW', { timeout: 15_000 });

    // Still exactly one prediction for this fight.
    const mine = await (await apiCall(api, await apiLogin(api, PLAYER), 'get', `/portal/predictions?match_id=${fights[0].id}`)).json();
    expect(mine.total).toBe(1);
    expect(mine.items[0]).toMatchObject({ side: 'draw', changes: 1, status: 'pending', open: true });
  });

  test('the window closes and the official result decides the pick in every session', async () => {
    expect((await apiCall(api, admin, 'post', `/admin/matches/${fights[0].id}/start`)).status()).toBe(200);
    expect((await apiCall(api, admin, 'post', `/admin/matches/${fights[0].id}/result`, { outcome: 'draw' })).status()).toBe(201);

    // No late changes, whatever the client does.
    const late = await apiCall(api, await apiLogin(api, PLAYER), 'put', `/portal/matches/${fights[0].id}/prediction`, { side: 'meron' });
    expect(late.status()).toBe(409);

    for (const { page } of [a, b]) {
      const row = await historyRow(page, 1);
      await expect(row).toContainText('DRAW');
      await expect(row).toContainText('WIN', { timeout: 15_000 });
    }
    // History filters run on the server.
    await a.page.getByRole('combobox', { name: 'Outcome' }).click();
    await a.page.getByRole('option', { name: 'LOSE' }).click();
    await expect(a.page.getByRole('row').filter({ hasText: eventName })).toHaveCount(0);
  });

  test('a cancelled fight voids the pick and no points move', async () => {
    await a.page.goto(PLAYER_URL);
    await openArena(a.page, eventName);
    await expect(a.page.locator('h1.fight-no')).toContainText('#2');
    await a.page.getByRole('radio', { name: 'MERON', exact: true }).check();
    await expect(pickStatus(a.page)).toContainText('Your pick MERON');
    expect((await apiCall(api, admin, 'post', `/admin/matches/${fights[1].id}/cancel`, { reason: 'Weigh-in failed' })).status()).toBe(200);

    await expect(pickStatus(a.page)).toContainText('VOID', { timeout: 15_000 });
    const row = await historyRow(b.page, 2);
    await expect(row).toContainText('MERON');
    await expect(row).toContainText('VOID');
    await expect(row).toContainText('Cancelled');
    expect(await points()).toBe(balance);

    // Other players never see these rows.
    const other = await (await apiCall(api, await apiLogin(api, 'player3'), 'get', `/portal/predictions?q=${encodeURIComponent(eventName)}`)).json();
    expect(other.total).toBe(0);
  });
});
