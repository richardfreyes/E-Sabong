import { APIRequestContext, BrowserContext, Page, expect, request as playwrightRequest, test } from '@playwright/test';

import { ADMIN_URL, DEMO_PASSWORD, PLAYER_URL, apiCall, apiLogin, openArena, tenantLogin, unique } from './helpers';

/**
 * Declarator betting switch: Open -> Close -> Declare Meron, Wala, or Draw -> Open the next fight.
 * The Declarator works in the browser; a player in a separate browser follows every change over
 * the WebSocket without reloading. Setup goes through the real API; the Declarator's result is final (no approval step).
 * Balances and stakes are simulated Arena Points with no cash value.
 */
let api: APIRequestContext;
let admin: string;
let eventId: string;
let eventName: string;
let firstFight: string;
let deskUserId: string;
let deskUser: string;
let player: string;
let playerToken: string;
let previous: { enabled: boolean } | null = null;
let desk: Page;
let arena: Page;
const contexts: BrowserContext[] = [];

const call = (token: string, method: 'get' | 'post' | 'put', path: string, data?: unknown) => apiCall(api, token, method, path, data);
const control = () => desk.getByTestId('betting-control');
const deskStatus = () => desk.getByTestId('betting-status');
const deskFight = () => desk.getByTestId('betting-fight');
const panel = () => arena.locator('pp-fight-betting-panel');
const playerState = () => panel().locator('.state');

async function expectOpen(fight: number): Promise<void> {
  await expect(deskStatus()).toHaveText('OPEN', { timeout: 15_000 });
  await expect(deskFight()).toHaveText(`Fight #${fight}`);
  await expect(arena.locator('pp-fight-header .num')).toHaveText(`#${fight}`, { timeout: 15_000 });
  await expect(playerState()).toHaveText('Betting Open', { timeout: 15_000 });
  for (const side of ['meron', 'wala', 'draw']) await expect(panel().locator(`button.bet.${side}`)).toBeEnabled();
}

async function expectClosed(fight: number): Promise<void> {
  await expect(deskStatus()).toHaveText('CLOSED', { timeout: 15_000 });
  await expect(deskFight()).toHaveText(`Fight #${fight}`);
  await expect(arena.locator('pp-fight-header .num')).toHaveText(`#${fight}`, { timeout: 15_000 });
  await expect(playerState()).toHaveText('Betting Closed', { timeout: 15_000 });
  // The full interface stays on screen; only submission is disabled.
  for (const side of ['meron', 'wala', 'draw']) await expect(panel().locator(`button.bet.${side}`)).toBeDisabled();
}

async function click(name: 'Open Betting' | 'Close Betting'): Promise<void> {
  const button = control().getByRole('button', { name });
  await expect(button).toBeEnabled({ timeout: 15_000 });
  await button.click();
}

test.describe.serial('Declarator opens and closes betting', () => {
  test.beforeAll(async ({ browser }) => {
    test.setTimeout(120_000);
    api = await playwrightRequest.newContext();
    admin = await apiLogin(api, 'admin');
    previous = await (await call(admin, 'get', '/admin/betting/settings')).json();
    const settings = await call(admin, 'put', '/admin/betting/settings', { enabled: true, starting_balance: '10000.00' });
    expect(settings.status(), await settings.text()).toBe(200);

    const run = Date.now().toString(36);
    deskUser = `e2e-switch-${run}`;
    const created = await call(admin, 'post', '/admin/users', { username: deskUser, full_name: 'E2E Betting Switch', account_type: 'declarator', password: DEMO_PASSWORD });
    expect(created.status(), await created.text()).toBe(201);
    deskUserId = (await created.json()).user.id;
    player = `e2e-bettor-${run}`;
    const bettor = await call(admin, 'post', '/admin/users', { username: player, full_name: player, account_type: 'player', parent_id: null, password: DEMO_PASSWORD });
    expect(bettor.status(), await bettor.text()).toBe(201);
    playerToken = await apiLogin(api, player);

    eventName = unique('E2E Betting Switch');
    const event = await call(admin, 'post', '/admin/events', { name: eventName, venue: 'Switch Arena', starts_at: '2030-07-01T18:00:00+08:00' });
    expect(event.status(), await event.text()).toBe(201);
    eventId = (await event.json()).id;
    const fight = await call(admin, 'post', `/admin/events/${eventId}/matches`, { meron_name: 'Ruby', wala_name: 'Onyx' });
    expect(fight.status(), await fight.text()).toBe(201);
    firstFight = (await fight.json()).id;
    for (const path of [`/admin/events/${eventId}/publish`, `/admin/events/${eventId}/start`]) {
      expect((await call(admin, 'post', path)).status()).toBe(200);
    }
    expect((await call(admin, 'put', `/declarator/events/${eventId}/assignments/${deskUserId}`)).status()).toBe(200);

    const deskContext = await browser.newContext();
    contexts.push(deskContext);
    desk = await deskContext.newPage();
    await tenantLogin(desk, ADMIN_URL, deskUser);
    await expect(desk).toHaveURL(/\/admin\/declarator/);
    await expect(desk.getByRole('combobox', { name: 'Event' }).first()).toContainText(eventName);

    const arenaContext = await browser.newContext();
    contexts.push(arenaContext);
    arena = await arenaContext.newPage();
    await tenantLogin(arena, PLAYER_URL, player);
    await expect(arena).not.toHaveURL(/\/login/);
    await openArena(arena, eventName);
  });

  test.afterAll(async () => {
    // Cancelling the event refunds any open stakes; the run's Declarator is suspended (reversible, audited).
    if (eventId) await call(admin, 'post', `/admin/events/${eventId}/cancel`, { reason: 'E2E betting switch cleanup' });
    if (deskUserId) await call(admin, 'post', `/admin/users/${deskUserId}/suspend`);
    if (previous) await call(admin, 'put', '/admin/betting/settings', { enabled: previous.enabled });
    for (const c of contexts) await c.close();
    await api?.dispose();
  });

  test('open then close, and betting cannot reopen until a winner is declared', async () => {
    test.setTimeout(90_000);
    await expectClosed(1);
    await expect(control()).toContainText('Ready · opening betting starts this fight');
    // The complete board is on screen while closed: balance, amount, pools, odds, returns, DRAW.
    await expect(panel().locator('[data-field="balance"]')).toBeVisible();
    await expect(panel().locator('#pp-bet-amount')).toBeVisible();
    for (const field of ['pool', 'rate', 'bet', 'pays']) await expect(panel().locator(`button.bet.wala [data-field="${field}"]`)).toBeVisible();
    for (const field of ['draw-pool', 'draw-rate', 'draw-pays']) await expect(panel().locator(`[data-field="${field}"]`)).toBeVisible();
    await expect(arena.locator('pp-winning-history-board')).toBeVisible();

    await click('Open Betting');
    await expectOpen(1);
    await panel().locator('button.bet.meron').click();
    await expect(panel()).toContainText('Bet accepted · MERON 100.00');

    await click('Close Betting');
    await expectClosed(1);
    // Pools and the player's own stake stay visible after the close.
    await expect(panel().locator('button.bet.meron [data-field="pool"]')).toHaveText('100.00');
    await expect(panel().locator('button.bet.meron [data-field="bet"]')).toHaveText('100.00');
    // A stale page (or any direct request) is refused by the backend.
    const stale = await call(playerToken, 'post', `/portal/matches/${firstFight}/bets`, { side: 'wala', amount: '50.00', request_id: crypto.randomUUID() });
    expect(stale.status()).toBe(409);
    expect((await stale.json()).error.code).toBe('betting_closed');

    await expect(control().getByRole('button', { name: 'Open Betting' })).toBeDisabled();
    await expect(desk.getByRole('button', { name: /MERON\s*WINS/ })).toBeEnabled();
    await expect(desk.getByRole('button', { name: /WALA\s*WINS/ })).toBeEnabled();
    await expect(desk.getByRole('button', { name: 'DRAW', exact: true })).toBeEnabled();
    const refused = await call(await apiLogin(api, deskUser), 'post', `/declarator/matches/${firstFight}/betting/open`);
    expect(refused.status()).toBe(409);
    expect((await refused.json()).error.code).toBe('result_required');
  });

  test('declared result is final and prepares the next fight, which the Declarator opens', async () => {
    test.setTimeout(90_000);
    await desk.getByRole('button', { name: /MERON\s*WINS/ }).click();
    await desk.getByRole('dialog').getByRole('button', { name: 'Confirm declaration' }).click();
    await expect(desk.getByRole('status').filter({ hasText: 'Fight #1: Meron wins is the official result.' })).toBeVisible();

    // Fight #2 is prepared automatically; no one adds it by hand.
    await expectClosed(2);
    await expect(control()).toContainText('Ready · opening betting starts this fight');
    // There is no entry form: the Declarator only opens and closes betting and declares the result.
    await expect(desk.getByTestId('fight-entries')).toHaveCount(0);
    await expect(desk.getByRole('row', { name: /#1/ }).first()).toContainText('Meron wins');
    // A finalized fight can never take bets again.
    const reopen = await call(await apiLogin(api, deskUser), 'post', `/declarator/matches/${firstFight}/betting/open`);
    expect(reopen.status()).toBe(409);
    expect((await reopen.json()).error.code).toBe('result_finalized');

    await click('Open Betting');
    await expectOpen(2);
    await panel().locator('button.bet.wala').click();
    await expect(panel()).toContainText('Bet accepted · WALA 100.00');
    await click('Close Betting');
    await expectClosed(2);
  });
});
