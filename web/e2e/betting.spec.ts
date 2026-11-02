import { APIRequestContext, BrowserContext, Locator, Page, expect, request as playwrightRequest, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';

import { DEMO_PASSWORD, PLAYER_URL, apiCall, apiLogin, openArena, tenantLogin } from './helpers';

const FIXTURE = 'E2E Betting Fixture';

/**
 * Simulated betting with play credits, across two players in separate browser sessions: both see
 * the same all-player pools and live multipliers, each sees only their own BET, RETURNS and play credits.
 * The operator drives the fight through the API. Runs share one live fixture event and add a fight
 * to it, so they do not count against the plan's monthly event limit. Play balances cannot be
 * topped up, so each run uses two fresh players. Betting is switched back to its previous state afterwards so the other
 * suites keep the predictions UI.
 */
let api: APIRequestContext;
let admin: string;
let eventId: string;
let eventName: string;
let fightId: string;
let previous: { enabled: boolean } | null = null;
let ana: { context: BrowserContext; page: Page; start: number };
let ben: { context: BrowserContext; page: Page; start: number };

async function session(browser: import('@playwright/test').Browser, username: string) {
  const created = await apiCall(api, admin, 'post', '/admin/users', {
    username, full_name: username, account_type: 'player', parent_id: null, password: DEMO_PASSWORD,
  });
  expect(created.status(), await created.text()).toBe(201);
  const token = await apiLogin(api, username);
  const start = Number((await (await apiCall(api, token, 'get', '/portal/betting')).json()).wallet.balance);
  const context = await browser.newContext();
  const page = await context.newPage();
  await tenantLogin(page, PLAYER_URL, username);
  await expect(page).not.toHaveURL(/\/login/);
  await openArena(page, eventName);
  return { context, page, start };
}

// Play credits: grouped, two decimals, never a currency symbol.
const money = (n: number) => n.toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const panel = (page: Page) => page.locator('pp-fight-betting-panel');
/** The MERON/WALA card is itself the bet button. */
const card = (page: Page, side: 'meron' | 'wala'): Locator => panel(page).locator(`button.bet.${side}`);
const field = (page: Page, side: 'meron' | 'wala', name: 'pool' | 'rate' | 'bet' | 'pays') => card(page, side).locator(`[data-field="${name}"]`);
const state = (page: Page) => panel(page).locator('.state');
const balance = (page: Page) => panel(page).locator('[data-field="balance"]');
const chip = (page: Page, label: string) => panel(page).getByRole('button', { name: label, exact: true });
async function captureBetting(page: Page, state: string): Promise<void> {
  const output = resolve(__dirname, '../../docs/ui-ux/after');
  await mkdir(output, { recursive: true });
  await panel(page).scrollIntoViewIfNeeded();
  await page.screenshot({ path: resolve(output, `player-betting-${state}.png`) });
}

test.describe.serial('Simulated betting across players', () => {
  test.beforeAll(async ({ browser }) => {
    test.setTimeout(120_000);
    api = await playwrightRequest.newContext();
    admin = await apiLogin(api, 'admin');
    previous = await (await apiCall(api, admin, 'get', '/admin/betting/settings')).json();
    const settings = await apiCall(api, admin, 'put', '/admin/betting/settings', { enabled: true, starting_balance: '10000.00', commission_rate: '0.1000', draw_multiplier: '8.00' });
    expect(settings.status(), await settings.text()).toBe(200);
    eventName = FIXTURE;
    const live = await (await apiCall(api, admin, 'get', `/admin/events?status=live&q=${encodeURIComponent(FIXTURE)}&page_size=5`)).json();
    let event = live.items.find((e: { name: string }) => e.name === FIXTURE);
    const setup: string[] = [];
    if (!event) {
      // First run only: later runs reuse this event.
      event = await (await apiCall(api, admin, 'post', '/admin/events', { name: FIXTURE, venue: 'Betting Arena', starts_at: '2030-08-01T18:00:00+08:00' })).json();
      setup.push(`/admin/events/${event.id}/publish`, `/admin/events/${event.id}/start`);
    }
    eventId = event.id;
    const fight = await apiCall(api, admin, 'post', `/admin/events/${eventId}/matches`, { meron_name: 'Garnet', wala_name: 'Sapphire' });
    expect(fight.status(), await fight.text()).toBe(201);
    fightId = (await fight.json()).id;
    for (const path of [...setup, `/admin/matches/${fightId}/betting/open`]) {
      const r = await apiCall(api, admin, 'post', path);
      expect(r.status(), await r.text()).toBe(200);
    }
    const run = Date.now().toString(36);
    ana = await session(browser, `ana${run}`);
    ben = await session(browser, `ben${run}`);
  });

  test.afterAll(async () => {
    // A fight left undecided by a failed run is cancelled (refunding its stakes); the fixture event stays live.
    const left = await apiCall(api, admin, 'get', `/admin/matches/${fightId}`);
    if (fightId && left.ok() && ['scheduled', 'in_progress'].includes((await left.json()).status)) {
      await apiCall(api, admin, 'post', `/admin/matches/${fightId}/cancel`, { reason: 'E2E cleanup' });
    }
    if (previous) await apiCall(api, admin, 'put', '/admin/betting/settings', { enabled: previous.enabled });
    await ana?.context.close();
    await ben?.context.close();
    await api?.dispose();
  });

  test('a bet updates every player’s pools and odds live and only the bettor’s BET, PAYS and balance', async () => {
    const video = await ana.page.locator('pp-video-stage video').elementHandle();
    const input = await panel(ana.page).getByRole('textbox', { name: 'Bet amount' }).elementHandle();
    const meron = await card(ana.page, 'meron').elementHandle();
    for (const { page } of [ana, ben]) {
      await expect(page.locator('pp-fight-header')).toContainText(eventName);
      await expect(state(page)).toHaveText('Betting open');
      await expect(field(page, 'meron', 'pool')).toHaveText('0.00');
      await expect(field(page, 'meron', 'rate')).toHaveText('—');
      await expect(panel(page).locator('.split-labels')).toContainText('—');
    }
    await chip(ana.page, '500.00 credits').click();
    await expect(chip(ana.page, '500.00 credits')).toHaveAttribute('aria-pressed', 'true');
    await card(ana.page, 'meron').click();
    await expect(panel(ana.page)).toContainText('Bet accepted · MERON 500.00 credits');
    await expect(field(ana.page, 'meron', 'bet')).toHaveText('500.00');
    await expect(balance(ana.page)).toHaveText(money(ana.start - 500));

    // The other player sees the new pool without reloading, but not Ana's stake or balance.
    await expect(field(ben.page, 'meron', 'pool')).toHaveText('500.00', { timeout: 15_000 });
    await expect(field(ben.page, 'meron', 'bet')).toHaveText('0.00');
    await expect(balance(ben.page)).toHaveText(money(ben.start));

    // Ben takes the other side with cents: 500 vs 300.50 -> 800.50 x 0.9 = 720.45 to share.
    await panel(ben.page).getByRole('textbox', { name: 'Bet amount' }).fill('300.50');
    await card(ben.page, 'wala').click();
    for (const { page } of [ana, ben]) {
      await expect(field(page, 'meron', 'rate')).toHaveText('1.44×est', { timeout: 15_000 }); // 720.45 / 500
      await expect(field(page, 'wala', 'rate')).toHaveText('2.40×est'); // 720.45 / 300.50
      await expect(panel(page).locator('.split-labels')).toContainText('62.5%');
    }
    // Each player's projected return follows the odds the other's bet changed.
    await expect(field(ana.page, 'meron', 'pays')).toHaveText('720.45');
    await expect(field(ben.page, 'wala', 'pays')).toHaveText('720.45');
    await expect(field(ben.page, 'meron', 'pays')).toHaveText('0.00');
    await expect(chip(ana.page, '500.00 credits')).toHaveAttribute('aria-pressed', 'true');
    await expect(panel(ana.page).getByRole('textbox', { name: 'Bet amount' })).toHaveValue('500');
    expect(await video?.evaluate(el => el === document.querySelector('pp-video-stage video'))).toBe(true);
    expect(await input?.evaluate(el => el === document.querySelector('#pp-bet-amount'))).toBe(true);
    expect(await meron?.evaluate(el => el === document.querySelector('button.bet.meron'))).toBe(true);
    await captureBetting(ana.page, 'open');
  });

  test('closing keeps every figure and disables the actions; the result settles each wallet', async () => {
    expect((await apiCall(api, admin, 'post', `/admin/matches/${fightId}/betting/close`)).status()).toBe(200);
    for (const { page } of [ana, ben]) {
      await expect(state(page)).toHaveText('Betting closed', { timeout: 15_000 });
      await expect(card(page, 'meron')).toBeDisabled();
      await expect(card(page, 'wala')).toBeDisabled();
      await expect(panel(page).locator('button.draw')).toBeDisabled();
      await expect(panel(page).getByRole('button', { name: 'All in' })).toBeDisabled();
      await expect(field(page, 'meron', 'pool')).toHaveText('500.00');
      await expect(field(page, 'meron', 'rate')).toHaveText('1.44×'); // final
    }
    await expect(field(ana.page, 'meron', 'pays')).toHaveText('720.45');
    await captureBetting(ana.page, 'closed');
    expect((await apiCall(api, admin, 'post', `/admin/matches/${fightId}/start`)).status()).toBe(200);
    expect((await apiCall(api, admin, 'post', `/admin/matches/${fightId}/result`, { outcome: 'meron' })).status()).toBe(201);

    await expect(state(ana.page)).toHaveText('Settled · MERON', { timeout: 15_000 });
    await expect(panel(ana.page)).toContainText('Returned 720.45 credits');
    await expect(field(ana.page, 'meron', 'pays')).toHaveText('720.45Won');
    await expect(balance(ana.page)).toHaveText(money(ana.start + 220.45));
    await expect(field(ben.page, 'wala', 'pays')).toHaveText('0.00Lost', { timeout: 15_000 });
    await expect(balance(ben.page)).toHaveText(money(ben.start - 300.5));
    await captureBetting(ana.page, 'settled');
  });
});
