import { expect, test, WebSocketRoute } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { PLAYER_URL, tenantLogin } from './helpers';

// Synthetic read responses exercise extreme values without changing anyone's wallet or book.
// Real cross-player synchronisation and settlement are covered in betting.spec.ts.
test('arena keeps its controls, video and entered amount stable across live frames at every target width', async ({ page }) => {
  let socket: WebSocketRoute | undefined;
  let matchId = '';
  const wallet = { balance: '1234567.89', version: 100, disclaimer: 'No cash value' };
  const pools = { meron: '1234567.89', wala: '9876543.21', draw: '123456.78' };
  const odds = { meron: '8.10', wala: '1.01', meron_display: '8.10', wala_display: '1.01' };
  await page.routeWebSocket(/\/api\/ws\?/, ws => { socket = ws; });
  await page.route('**/api/v1/portal/betting', async route => {
    const data = await (await route.fetch()).json();
    await route.fulfill({ json: { ...data, enabled: true, wallet } });
  });
  await page.route('**/api/v1/portal/matches/*/book', async route => {
    const data = await (await route.fetch()).json();
    matchId = data.book.match_id;
    const position = { stake: '1234567.89', pays: '9876543.21', result: null };
    await route.fulfill({ json: {
      ...data, wallet,
      book: { ...data.book, state: 'open', accepting: true, pools, odds, version: 100, final: false },
      mine: { meron: position, wala: position, draw: position, settled_payout: null, settled_net: null },
    } });
  });
  await page.route('**/api/v1/video/events/*/playback?audience=player', route => route.fulfill({ json: {
    status: 'ready', message: '', playback: { type: 'mp4', url: '/media/demo-loop.mp4' },
  } }));
  await tenantLogin(page, PLAYER_URL, 'player1');
  const panel = page.locator('pp-fight-betting-panel');
  await expect(panel.locator('.state')).toHaveText('Betting open');
  const input = panel.getByRole('textbox', { name: 'Bet amount' });
  await input.fill('123.45');
  const video = await page.locator('pp-video-stage video').elementHandle();
  const amount = await input.elementHandle();
  const button = await panel.locator('.bet.meron').elementHandle();
  let loads = 0;
  page.on('request', request => { if (request.url().includes('/video/events/')) loads++; });
  const output = resolve(__dirname, '../../docs/ui-ux/after');
  await mkdir(output, { recursive: true });
  for (const width of [320, 375, 390, 430, 768, 1024, 1280, 1440, 1920]) {
    await page.setViewportSize({ width, height: 1000 });
    await panel.scrollIntoViewIfNeeded();
    await page.waitForTimeout(250);
    for (let i = 0; i < 5; i++) {
      socket!.send(JSON.stringify({ type: 'book.updated', data: {
        match_id: matchId, state: 'open', accepting: true, pools: { ...pools, meron: '1234568.89' }, odds,
        final: false, draw_multiplier: '8.00', commission_rate: '0.1000', bet_count: 7,
        outcome: null, version: 101 + width * 10 + i,
      } }));
    }
    await expect(panel.locator('.meron [data-field="pool"]')).toHaveText('1,234,568.89');
    await expect(input).toHaveValue('123.45');
    const dimensions = await page.evaluate(() => {
      const content = document.querySelector('.mat-drawer-content')!;
      return { page: document.documentElement.scrollWidth, viewport: innerWidth, content: content.scrollWidth, available: content.clientWidth };
    });
    expect(dimensions.page, `page overflow at ${width}`).toBeLessThanOrEqual(dimensions.viewport);
    expect(dimensions.content, `content overflow at ${width}`).toBeLessThanOrEqual(dimensions.available + 1);
    const overflowing = await panel.locator('[data-field], .bar').evaluateAll(elements => elements.filter(el => el.scrollWidth > el.clientWidth + 1).map(el => el.textContent));
    expect(overflowing, `clipped betting figures at ${width}`).toEqual([]);
    const rows = await panel.locator('.bet').evaluateAll(cards => cards.map(card => [...card.querySelectorAll('.name, .pool, .rows, .bar')].map(el => Math.round(el.getBoundingClientRect().top))));
    expect(rows[0], `Meron/Wala alignment at ${width}`).toEqual(rows[1]);
    if ([320, 768, 1440].includes(width)) await page.screenshot({ path: resolve(output, `player-long-values-${width}.png`) });
  }
  expect(await video!.evaluate(el => el === document.querySelector('pp-video-stage video'))).toBe(true);
  expect(await amount!.evaluate(el => el === document.querySelector('#pp-bet-amount'))).toBe(true);
  expect(await button!.evaluate(el => el === document.querySelector('.bet.meron'))).toBe(true);
  expect(loads).toBe(0);
  // Actual decoded video frames and elapsed playback, not merely a retained DOM node.
  await expect.poll(() => page.locator('video').evaluate((el: HTMLVideoElement) => el.videoWidth)).toBeGreaterThan(0);
  await expect.poll(() => page.locator('video').evaluate((el: HTMLVideoElement) => el.currentTime)).toBeGreaterThan(0);
});
