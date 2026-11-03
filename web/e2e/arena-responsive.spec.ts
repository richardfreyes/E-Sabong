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
      // Only MERON carries a stake, so its "Your side" tag must not shift its figures against WALA.
      mine: { meron: position, wala: { stake: '0.00', pays: '0.00', result: null }, draw: position, settled_payout: null, settled_net: null },
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
  // Evidence screenshots go to test-results by default; set UI_EVIDENCE_DIR to write them into docs.
  const output = process.env['UI_EVIDENCE_DIR'] ? resolve(process.env['UI_EVIDENCE_DIR']) : resolve(__dirname, '../test-results/arena');
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
    // Each figure sits at the same place inside MERON and WALA (offsets from the card's own top),
    // whether the cards are side by side or stacked.
    const cards = await panel.locator('.bet').evaluateAll(list => list.map(card => {
      const box = card.getBoundingClientRect();
      return { top: Math.round(box.top), left: Math.round(box.left), offsets: [...card.querySelectorAll('.name, .pool, .rows, .bar')].map(el => Math.round(el.getBoundingClientRect().top - box.top)) };
    }));
    expect(cards[0].offsets, `Meron/Wala alignment at ${width}`).toEqual(cards[1].offsets);
    if (width < 600) {
      expect(cards[1].top, `cards stack on phones at ${width}`).toBeGreaterThan(cards[0].top);
      expect(cards[1].left).toBe(cards[0].left);
    } else {
      expect(cards[1].top, `Meron and Wala side by side at ${width}`).toBe(cards[0].top);
      expect(cards[1].left).toBeGreaterThan(cards[0].left);
    }
    expect(cards[2].top, `Draw follows Meron and Wala at ${width}`).toBeGreaterThan(cards[1].top);
    if ([320, 768, 1440].includes(width)) await page.screenshot({ path: resolve(output, `player-long-values-${width}.png`) });
  }
  expect(await video!.evaluate(el => el === document.querySelector('pp-video-stage video'))).toBe(true);
  expect(await amount!.evaluate(el => el === document.querySelector('#pp-bet-amount'))).toBe(true);
  expect(await button!.evaluate(el => el === document.querySelector('.bet.meron'))).toBe(true);
  expect(loads).toBe(0);

  // Red MERON, blue WALA, green DRAW: border, solid action and legible label, open and closed.
  // Read colours only after transitions settle: mid-transition values are interpolated (oklab).
  const settle = () => panel.locator('.bet, .bet .bar').evaluateAll(els => Promise.all(els.flatMap(el => el.getAnimations().map(a => a.finished))));
  const palette = async () => { await settle(); return panel.locator('.bet').evaluateAll(list => list.map(card => {
    const bar = card.querySelector('.bar')!;
    const style = getComputedStyle(card), barStyle = getComputedStyle(bar);
    return { side: card.classList[1], border: style.borderTopColor, fill: barStyle.backgroundColor, ink: barStyle.color, disabled: (card as HTMLButtonElement).disabled };
  })); };
  // Computed colours arrive as rgb(0–255) or, for color-mix(in srgb), color(srgb 0–1).
  const rgb = (c: string) => {
    const n = c.match(/-?[\d.]+/g)!.slice(0, 3).map(Number);
    if (c.startsWith('color(srgb')) return n.map(v => v * 255);
    if (!c.startsWith('rgb')) throw new Error(`unexpected colour format ${c}`);
    return n;
  };
  const lum = (c: string) => { const [r, g, b] = rgb(c).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const contrast = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const open = await palette();
  expect(open.map(c => [c.side, c.fill])).toEqual([['meron', 'rgb(220, 38, 38)'], ['wala', 'rgb(37, 99, 235)'], ['draw', 'rgb(34, 197, 94)']]);
  expect(open[0].border, 'the player\'s own side gets the full accent border').toBe('rgb(239, 68, 68)');
  const dominant = ['meron', 'wala', 'draw'].map((_, i) => rgb(open[i].border).indexOf(Math.max(...rgb(open[i].border))));
  expect(dominant, 'borders read as red, blue, green').toEqual([0, 2, 1]);
  const surface = 'rgb(20, 22, 29)';
  for (const c of open) {
    expect(contrast(c.ink, c.fill), `${c.side} action label contrast`).toBeGreaterThanOrEqual(4.5);
    expect(contrast(c.border, surface), `${c.side} border contrast`).toBeGreaterThanOrEqual(3);
  }
  socket!.send(JSON.stringify({ type: 'book.updated', data: {
    match_id: matchId, state: 'closed', accepting: false, pools, odds, final: true, draw_multiplier: '8.00', commission_rate: '0.1000',
    bet_count: 7, outcome: null, version: 999_999,
  } }));
  await expect(panel.locator('.state')).toHaveText('Betting closed');
  await expect(panel.locator('#pp-bet-help')).toContainText('Betting is closed for this fight');
  const closed = await palette();
  for (const c of closed) {
    expect(c.disabled).toBe(true);
    expect(c.fill, `${c.side} keeps a tint of its colour when unavailable`).not.toBe('rgba(0, 0, 0, 0)');
    expect(contrast(c.ink, c.fill), `${c.side} unavailable label contrast`).toBeGreaterThanOrEqual(4.5);
  }
  const [r, , b] = rgb(closed[0].fill); expect(r).toBeGreaterThan(b);   // still reads as red
  const [r2, , b2] = rgb(closed[1].fill); expect(b2).toBeGreaterThan(r2); // still reads as blue
  await expect(input).toHaveValue('123.45');
  // Actual decoded video frames and elapsed playback, not merely a retained DOM node.
  await expect.poll(() => page.locator('video').evaluate((el: HTMLVideoElement) => el.videoWidth)).toBeGreaterThan(0);
  await expect.poll(() => page.locator('video').evaluate((el: HTMLVideoElement) => el.currentTime)).toBeGreaterThan(0);
});
