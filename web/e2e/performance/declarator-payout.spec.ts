import { test, expect, request, APIRequestContext, Browser, Page, Route } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { poolBoardOdds, exactCents } from '../../shared/services/pool-odds';

/**
 * Declarator desk payout: projected on the click, confirmed by the server, the same on every screen.
 *
 * Real browsers, production Angular assets and a real API. Targets a local isolated stack
 * (DECLARATOR_PAYOUT_ORIGIN, demo accounts) or UAT (DECLARATOR_UAT_FIXTURE, the synthetic fixture of
 * infrastructure/scripts/seed-declarator-uat.py). Never production. Only the event it creates is used
 * and it is cancelled at the end; tenant settings are not changed on UAT.
 *
 * Measured separately:
 *  - click → projected payout in the DOM (ms, and animation frames: 0–1 = shown at the next paint);
 *  - server answer (POST) or book.updated frame → confirmed payout in the DOM, per browser;
 * Checked: the projection equals the server's price for the same totals; the final confirmed price is
 * identical on desk A, desk B and the player screen and equals the API formula for the server pools;
 * failed, refused, lost and out-of-order answers end on the server's value; every frame carries this
 * tenant's id and this run's fight only.
 */
test('Declarator payout: projected on click, confirmed by the server, identical everywhere', async ({ browser }) => {
  const fixturePath = process.env['DECLARATOR_UAT_FIXTURE'];
  const fixture = fixturePath ? JSON.parse(readFileSync(fixturePath, 'utf8')) : null;
  const origin: string = fixture?.origin ?? process.env['DECLARATOR_PAYOUT_ORIGIN'] ?? 'http://127.0.0.1:4401';
  const host = new URL(origin).hostname;
  if (!(host.startsWith('uat.') || ['127.0.0.1', 'localhost'].includes(host)))
    throw Error('UAT or a local isolated stack only; production is never a target');
  const uat = host.startsWith('uat.');
  const out = path.resolve(process.env['DECLARATOR_PAYOUT_OUT'] || 'test-results/declarator-payout');
  mkdirSync(out, { recursive: true });
  test.setTimeout(30 * 60 * 1000);
  const password: string = fixture?.password ?? 'DemoPass#2026';
  const run = Date.now().toString(36);
  const failures: string[] = [];
  const check = (ok: boolean, what: string) => { if (!ok) failures.push(what); };

  // ---- API sessions ----
  async function actor(login: string, portal: 'admin' | 'player') {
    const ctx: APIRequestContext = await request.newContext({
      baseURL: origin,
      extraHTTPHeaders: { 'X-Requested-With': 'XMLHttpRequest', 'X-ESB-Client': '2', 'X-Portal': portal },
    });
    const r = await ctx.post('/api/v1/auth/login', { data: { username: login, password } });
    expect(r.status(), 'login ' + login).toBe(200);
    const token = (await r.json()).access_token as string;
    const call = async (method: string, url: string, data?: unknown, ok: number[] = [200, 201]) => {
      const res = await ctx.fetch('/api/v1' + url, { method, data, headers: { Authorization: `Bearer ${token}` } });
      const body = await res.json().catch(() => ({}));
      if (!ok.includes(res.status())) throw Error(`${method} ${url} -> ${res.status()} ${JSON.stringify(body).slice(0, 300)}`);
      return body;
    };
    return { login, call };
  }
  const A = fixture?.accounts;
  const admin = await actor(A?.admin.login ?? 'admin', 'admin');
  const judge = await actor(A?.judge.login ?? 'declarator', 'admin');
  const me = await judge.call('GET', '/auth/me');
  const tenantId: string = me.tenant.id;
  if (!uat) {
    await admin.call('PUT', '/admin/betting/settings', {
      enabled: true, starting_balance: '10000000.00', min_bet: '1.00', max_fight_stake: '10000000.00',
    });
  } else {
    const settings = await admin.call('GET', '/admin/betting/settings');
    expect(settings.enabled, 'UAT tenant betting must already be enabled; settings are never changed').toBe(true);
  }
  const playerLogins: string[] = [];
  if (uat) {
    for (const key of ['player1', 'player2']) {
      await admin.call('POST', '/admin/balance/account-grants', {
        target_user_id: A[key].id, amount: '5000.00', idempotency_key: `payout-${fixture.run}-${run}-${key}`,
        remarks: 'Synthetic payout verification',
      }, [201]);
      playerLogins.push(A[key].login);
    }
  } else {
    for (let i = 0; i < 2; i++) {
      const name = `pay-${run}-${i}`;
      await admin.call('POST', '/admin/users', { username: name, full_name: `Payout ${i}`, account_type: 'player', password }, [201]);
      playerLogins.push(name);
    }
  }
  const players = await Promise.all(playerLogins.map((l) => actor(l, 'player')));
  const event = await admin.call('POST', '/admin/events', {
    name: `Declarator payout ${uat ? fixture.run + ' ' : ''}${run}`, venue: 'Synthetic verification', starts_at: new Date().toISOString(),
  }, [201]);
  for (const op of ['publish', 'start']) await admin.call('POST', `/admin/events/${event.id}/${op}`);
  await admin.call('PUT', `/declarator/events/${event.id}/assignments/${me.id}`);
  const control = await judge.call('POST', `/declarator/events/${event.id}/betting/open`);
  const fight: string = control.match_id;
  expect(fight).toBeTruthy();
  const quick = await judge.call('GET', '/declarator/settings/quick-bets');
  const pick = (side: 'meron' | 'wala', want: number) => {
    const values = (quick[side] as string[]).map(Number);
    return values.includes(want) ? want : values[0];
  };
  const fiveK = pick('meron', 5000), tenK = pick('wala', 10000);
  const book = async () => judge.call('GET', `/declarator/matches/${fight}/book`);

  // ---- browsers ----
  const instrument = () => {
    const w = window as any;
    const m = (w.__m = { clicks: [] as any[], changes: [] as any[], answers: [] as any[], frames: [] as any[], rafs: 0, shifts: [] as any[], notes: [] as any[] });
    // Flicker: any layout shift, and every time the "confirming" note appears.
    try {
      new PerformanceObserver((list) => { for (const e of list.getEntries() as any[]) m.shifts.push({ t: e.startTime, value: e.value }); })
        .observe({ type: 'layout-shift', buffered: false });
    } catch { /* not supported */ }
    let noteShown = false;
    new MutationObserver(() => {
      const shown = (document.querySelector('[data-testid="payout-status"]')?.textContent ?? '').includes('confirming');
      if (shown && !noteShown) m.notes.push({ on: performance.now(), off: null });
      if (!shown && noteShown) m.notes[m.notes.length - 1].off = performance.now();
      noteShown = shown;
    }).observe(document, { subtree: true, childList: true, characterData: true });
    const tick = () => { m.rafs++; requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
    document.addEventListener('click', (e) => {
      const b = (e.target as Element)?.closest?.('button.dummy-amount') as HTMLElement | null;
      if (b) m.clicks.push({ t: performance.now(), f: m.rafs, side: b.dataset['side'], amount: Number(b.dataset['amount']) });
    }, true);
    let last = '';
    const read = () => {
      const pm = document.querySelector('[data-field="payout-meron"]'), pw = document.querySelector('[data-field="payout-wala"]');
      if (pm && pw) {
        const row = pm.closest('tr');
        return { kind: 'desk', meron: pm.textContent!.trim(), wala: pw.textContent!.trim(), projected: row?.getAttribute('data-projected') ?? null,
          tm: document.querySelector('[data-field="total-meron"]')?.textContent?.trim(), tw: document.querySelector('[data-field="total-wala"]')?.textContent?.trim() };
      }
      const pair = document.querySelector('[data-field="odds-pair"] b');
      return pair ? { kind: 'player', pair: pair.textContent!.trim() } : null;
    };
    const observe = () => {
      const v = read();
      if (!v) return;
      const k = JSON.stringify(v);
      if (k !== last) { last = k; m.changes.push({ t: performance.now(), f: m.rafs, ...v }); }
    };
    new MutationObserver(observe).observe(document, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['data-projected'] });
    const record = (url: string, sent: number, status: number, text: string, timing: string | null) => {
      if (!url.includes('/dummy-bets')) return;
      let j: any = null; try { j = JSON.parse(text); } catch { /* not JSON */ }
      m.answers.push({ sent, t: performance.now(), status, version: j?.version, odds: j?.odds ? { m: j.odds.meron_display, w: j.odds.wala_display } : null, timing });
    };
    const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (this: any, method: string, url: string) { this.__u = String(url); return (open as any).apply(this, arguments); } as any;
    XMLHttpRequest.prototype.send = function (this: any) {
      const sent = performance.now();
      this.addEventListener('loadend', () => record(this.__u ?? '', sent, this.status, this.responseText ?? '', this.getResponseHeader?.('server-timing') ?? null));
      return (send as any).apply(this, arguments);
    } as any;
    const fetch0 = w.fetch.bind(w);
    w.fetch = async (input: any, init?: any) => {
      const url = String(input?.url ?? input), sent = performance.now();
      try {
        const res: Response = await fetch0(input, init);
        if (url.includes('/dummy-bets')) res.clone().text().then((t) => record(url, sent, res.status, t, res.headers.get('server-timing')));
        return res;
      } catch (e) { record(url, sent, 0, '', null); throw e; }
    };
    const WS = w.WebSocket;
    w.WebSocket = class extends WS {
      constructor(...args: any[]) {
        super(...args);
        this.addEventListener('message', (ev: MessageEvent) => {
          try {
            const msg = JSON.parse(ev.data);
            if (msg.type === 'book.updated') m.frames.push({ t: performance.now(), version: msg.data.version, match_id: msg.data.match_id,
              tenant_id: msg.data.tenant_id, event_id: msg.data.event_id, bet_count: msg.data.bet_count,
              odds: { m: msg.data.odds?.meron_display, w: msg.data.odds?.wala_display }, pools: msg.data.pools, split: 'dummy_meron' in msg.data });
          } catch { /* not JSON */ }
        });
      }
    };
  };
  async function deskPage(b: Browser, label: string): Promise<Page> {
    const ctx = await b.newContext();
    await ctx.addInitScript(instrument);
    const page = await ctx.newPage();
    page.on('pageerror', (e) => failures.push(`${label} page error: ${e.message}`));
    await page.goto(origin + '/admin/login');
    await page.getByLabel('Username', { exact: true }).fill(judge.login);
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await expect(page).not.toHaveURL(/login/, { timeout: 30_000 });
    await page.goto(origin + '/admin/declarator');
    const picker = page.getByRole('combobox', { name: 'Event', exact: true }).first();
    await picker.click();
    await page.getByRole('option', { name: event.name, exact: true }).click();
    await expect(page.locator('[data-field="payout-meron"]')).toBeVisible({ timeout: 30_000 });
    return page;
  }
  const deskA = await deskPage(browser, 'desk A');
  const deskB = await deskPage(browser, 'desk B');
  const playerCtx = await browser.newContext();
  await playerCtx.addInitScript(instrument);
  const player = await playerCtx.newPage();
  await player.goto(origin + '/login');
  await player.getByLabel('Username', { exact: true }).fill(playerLogins[0]);
  await player.getByLabel('Password', { exact: true }).fill(password);
  await player.getByRole('button', { name: /sign in/i }).first().click();
  await expect(player).not.toHaveURL(/\/login/, { timeout: 30_000 });
  // The arena shows one event; pick this run's when others are live too.
  await player.goto(origin + '/');
  await player.locator('[data-field="odds-pair"]').first().waitFor({ timeout: 30_000 }).catch(() => null);
  const switcher = player.getByRole('button', { name: 'Switch event' }).first();
  if (await switcher.isVisible().catch(() => false)) {
    await switcher.click();
    await player.getByRole('menuitem').filter({ hasText: event.name }).first().click();
  }
  const playerDom = await player.locator('[data-field="odds-pair"]').first().waitFor({ timeout: 30_000 }).then(() => true, () => false);
  if (!playerDom) failures.push('player: the arena did not show the odds pair for this run\'s fight');

  // ---- helpers ----
  const metrics = (p: Page) => p.evaluate(() => structuredClone((window as any).__m));
  const deskShows = async (p: Page) => p.evaluate(() => ({
    meron: document.querySelector('[data-field="payout-meron"]')?.textContent?.trim(),
    wala: document.querySelector('[data-field="payout-wala"]')?.textContent?.trim(),
    projected: document.querySelector('[data-field="payout-meron"]')?.closest('tr')?.getAttribute('data-projected') ?? null,
  }));
  const pairOf = (m: string, w: string) => `${Math.round(Number(m) * 100)}–${Math.round(Number(w) * 100)}`;
  /** Every screen shows the server's confirmed price, which is the API formula for the server pools. */
  const playerPair = () => player.locator('[data-field="odds-pair"] b').first().textContent().then((t) => t?.trim());
  /** Wait until the desk has nothing in flight and desk A, desk B and the player all show the server's
   *  current price, not projected. A projection covers every unconfirmed click, so a desk that shows
   *  no projection is showing a server state that includes all of them. */
  async function allConfirmed(label: string) {
    let server: any = null, seen: unknown = null;
    const converged = async () => {
      server = await book();
      const want = { meron: server.odds.meron_display, wala: server.odds.wala_display, projected: null };
      const [a, b, p, queue, settled] = await Promise.all([deskShows(deskA), deskShows(deskB), playerDom ? playerPair() : null,
        deskA.getByTestId('ghost-queue').count(), deskA.evaluate(() => {
          // Every click on desk A has ended (added · refused · cancelled), by the desk's own tally.
          const text = document.querySelector('[data-testid="ghost-tally"]')?.textContent ?? '';
          const ended = (text.match(/\d+/g) ?? []).map(Number).reduce((x, y) => x + y, 0);
          return ended === (window as any).__m.clicks.length;
        })]);
      seen = { want, deskA: a, deskB: b, player: p, queue, settled };
      return settled && queue === 0 && JSON.stringify(a) === JSON.stringify(want) && JSON.stringify(b) === JSON.stringify(want)
        && (!playerDom || p === pairOf(want.meron, want.wala));
    };
    await expect.poll(converged, { timeout: 45_000, intervals: [100, 250, 500, 1000] }).toBe(true)
      .catch(() => failures.push(`${label}: screens did not converge on the server price ${JSON.stringify(seen)}`));
    const formula = poolBoardOdds(exactCents(server.pools.meron), exactCents(server.pools.wala));
    if (server.odds.source === 'pool')
      check(formula.meron === server.odds.meron_display && formula.wala === server.odds.wala_display,
        `${label}: server ${server.odds.meron_display}/${server.odds.wala_display} differs from formula ${formula.meron}/${formula.wala}`);
    return server;
  }
  const scenarios: Record<string, any> = {};
  const clickLatency = (m: any, from: number) => m.clicks.filter((c: any) => c.t >= from).map((c: any) => {
    const change = m.changes.find((x: any) => x.t >= c.t && x.projected);
    return change ? { ms: +(change.t - c.t).toFixed(2), frames: change.f - c.f, meron: change.meron, wala: change.wala } : null;
  });
  /** The server's confirmation of the last click reaches the desk as its POST answer or as the
   *  book.updated echo of that commit, whichever arrives first; measured from there to the first
   *  non-projected price on screen. */
  const confirmLatency = (m: any, from: number) => {
    const answers = m.answers.filter((a: any) => a.sent >= from && a.status === 200);
    if (!answers.length) return null;
    const newest = answers.reduce((a: any, b: any) => (b.version > a.version ? b : a));
    const echo = m.frames.find((f: any) => f.t >= from && f.match_id === fight && f.version >= newest.version);
    const evidence = Math.min(newest.t, echo?.t ?? Infinity);
    const confirmed = m.changes.find((x: any) => x.t >= evidence - 0.01 && !x.projected);
    return { answers: answers.length, request_ms: stats(answers.map((a: any) => +(a.t - a.sent).toFixed(1))),
      confirmed_by: echo && echo.t < newest.t ? 'websocket' : 'answer',
      evidence_to_confirmed_ms: confirmed ? +(confirmed.t - evidence).toFixed(2) : null,
      answer_minus_echo_ms: echo ? +(newest.t - echo.t).toFixed(2) : null };
  };
  /** Book frames → the matching price on screen. On the clicking desk only frames that arrived with no
   *  click of its own unconfirmed count: otherwise the desk rightly shows the frame plus its pending
   *  clicks (the projection), not the frame. */
  const frameLatency = (m: any, from: number, kind: 'desk' | 'player') => {
    const rows: number[] = [];
    const newer = (f: any) => m.frames.some((g: any) => g.match_id === fight && g.t <= f.t && g.version > f.version);
    const outstanding = (t: number) => m.clicks.filter((c: any) => c.t < t).length > m.answers.filter((a: any) => a.t < t && a.status === 200).length;
    const mine = m.frames.filter((f: any) => f.match_id === fight);
    const moved = (f: any) => { const i = mine.indexOf(f); return i === 0 || mine[i - 1].odds.m !== f.odds.m || mine[i - 1].odds.w !== f.odds.w; };
    // Stale (out-of-order) frames are rightly ignored, so they have no display time.
    for (const fr of mine.filter((f: any) => f.t >= from && moved(f) && !newer(f) && !(m.clicks.length && outstanding(f.t)))) {
      const hit = m.changes.find((x: any) => x.t >= fr.t - 0.001 && (kind === 'desk'
        ? x.meron === fr.odds.m && x.wala === fr.odds.w && !x.projected
        : x.pair === pairOf(fr.odds.m, fr.odds.w)));
      if (hit) rows.push(+(hit.t - fr.t).toFixed(2));
    }
    return rows;
  };
  const stats = (v: number[]) => {
    const s = [...v].sort((a, b) => a - b);
    const q = (p: number) => s.length ? s[Math.min(s.length - 1, Math.floor(s.length * p))] : null;
    return { n: s.length, median: q(0.5), p95: q(0.95), max: s.length ? s[s.length - 1] : null };
  };
  const now = (p: Page) => p.evaluate(() => performance.now());
  const click = (side: 'meron' | 'wala', amount: number) =>
    deskA.locator(`button.dummy-amount[data-side="${side}"][data-amount="${amount}"]`).click();

  // ---- 1. +5K Meron, 2. +10K Wala: projected at once, equal to the confirmed price ----
  for (const [name, side, amount] of [['meron_5k', 'meron', fiveK], ['wala_10k', 'wala', tenK]] as const) {
    const before = await book();
    const from = await now(deskA);
    await click(side, amount);
    const server = await allConfirmed(name);
    const m = await metrics(deskA);
    const lat = clickLatency(m, from);
    const projected = m.changes.find((x: any) => x.t >= from && x.projected);
    // What the server would price these totals at: the projection must equal it exactly.
    check(!!projected && projected.meron === server.odds.meron_display && projected.wala === server.odds.wala_display,
      `${name}: projected ${projected?.meron}/${projected?.wala} vs confirmed ${server.odds.meron_display}/${server.odds.wala_display}`);
    check(exactCents(server[`dummy_${side}`]) - exactCents(before[`dummy_${side}`]) === BigInt(amount * 100),
      `${name}: ghost total moved by exactly ${amount} (before ${before[`dummy_${side}`]}, after ${server[`dummy_${side}`]}, v${before.version} -> v${server.version})`);
    scenarios[name] = { click: lat, confirm: confirmLatency(m, from), server: server.odds };
  }

  // ---- 3. rapid clicks on both sides ----
  {
    const before = await book();
    const from = await now(deskA);
    const plan = Array.from({ length: 16 }, (_, i) => [i % 2 ? 'wala' : 'meron', i % 2 ? tenK : fiveK] as const);
    for (const [side, amount] of plan) await click(side, amount);
    const server = await allConfirmed('rapid');
    const added = (s: 'meron' | 'wala') => plan.filter((p) => p[0] === s).reduce((a, p) => a + p[1], 0);
    for (const s of ['meron', 'wala'] as const)
      check(exactCents(server[`dummy_${s}`]) - exactCents(before[`dummy_${s}`]) === BigInt(added(s) * 100), `rapid: ${s} ghost added exactly once per click`);
    const m = await metrics(deskA);
    const tally = await deskA.getByTestId('ghost-tally').textContent();
    const shifted = m.shifts.filter((x: any) => x.t >= from).reduce((a: number, x: any) => a + x.value, 0);
    check(shifted < 0.001, `rapid: the desk layout shifted by ${shifted} while clicking`);
    // The note may show while the server is slow, but never blinks: each showing lasts at least 1 s.
    const notes = m.notes.filter((n: any) => n.on >= from);
    const blinks = notes.filter((n: any) => n.off !== null && n.off - n.on < 1000);
    check(blinks.length === 0, `rapid: the confirming note blinked ${blinks.length} times`);
    scenarios['rapid_16_flicker'] = { layout_shift: shifted, confirming_note_shown: notes.length,
      note_visible_ms: notes.map((n: any) => n.off === null ? null : +(n.off - n.on).toFixed(0)), blinks: blinks.length };
    scenarios['rapid_16'] = { click: stats(clickLatency(m, from).filter(Boolean).map((x: any) => x.ms)),
      frames: stats(clickLatency(m, from).filter(Boolean).map((x: any) => x.frames)), confirm: confirmLatency(m, from), tally };
  }

  // ---- 4/5. auto ghost running, manual clicks and player bets at the same time ----
  {
    const fromA = await now(deskA), fromB = await now(deskB), fromP = await now(player);
    await judge.call('POST', `/declarator/events/${event.id}/ghost-auto/start`, { meron_amount: '700.00', wala_amount: '300.00', interval_seconds: 1 });
    let stop = false;
    const bettors = players.map(async (p, i) => {
      for (let n = 0; !stop; n++) {
        await p.call('POST', `/portal/matches/${fight}/bets`, { side: i ? 'wala' : 'meron', amount: `${10 + n % 5}.00`, request_id: crypto.randomUUID() }, [201, 409, 422]);
        await new Promise((r) => setTimeout(r, 700 + i * 250));
      }
    });
    const end = Date.now() + 20_000;
    while (Date.now() < end) {
      await click(Math.random() < 0.5 ? 'meron' : 'wala', Math.random() < 0.5 ? fiveK : tenK);
      await new Promise((r) => setTimeout(r, 250 + Math.random() * 500));
    }
    stop = true;
    await Promise.all(bettors);
    await judge.call('POST', `/declarator/events/${event.id}/ghost-auto/stop`);
    await new Promise((r) => setTimeout(r, 1500));
    const server = await allConfirmed('concurrent');
    const [mA, mB, mP] = await Promise.all([metrics(deskA), metrics(deskB), metrics(player)]);
    scenarios['concurrent_20s'] = {
      click: stats(clickLatency(mA, fromA).filter(Boolean).map((x: any) => x.ms)),
      frames: stats(clickLatency(mA, fromA).filter(Boolean).map((x: any) => x.frames)),
      frame_to_display_ms: { deskA: stats(frameLatency(mA, fromA, 'desk')), deskB: stats(frameLatency(mB, fromB, 'desk')), player: stats(frameLatency(mP, fromP, 'player')) },
      frames_received: { deskA: mA.frames.filter((f: any) => f.t >= fromA).length, deskB: mB.frames.filter((f: any) => f.t >= fromB).length, player: mP.frames.filter((f: any) => f.t >= fromP).length },
      bet_count: server.bet_count,
    };
  }

  // ---- 6. slow API: the projection shows at once, the server's answer replaces it ----
  {
    const slow = async (route: Route) => { await new Promise((r) => setTimeout(r, 2500)); await route.continue(); };
    await deskA.route('**/dummy-bets', slow);
    const from = await now(deskA);
    await click('meron', fiveK);
    await click('wala', tenK);
    await expect.poll(() => deskShows(deskA).then((s) => s.projected), { timeout: 2000 }).toBe('payout');
    const server = await allConfirmed('slow');
    await deskA.unroute('**/dummy-bets', slow);
    const m = await metrics(deskA);
    check(m.notes.some((n: any) => n.on >= from), 'slow: the confirming note appears when the server is slow');
    scenarios['slow_api_2500ms'] = { click: clickLatency(m, from), confirm: confirmLatency(m, from), server: server.odds };
  }

  // ---- 7. refused, failed and lost answers end on the server's value ----
  {
    const before = await book();
    const refuse = (route: Route) => route.fulfill({ status: 409, contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'betting_closed', message: 'Synthetic refusal' } }) });
    await deskA.route('**/dummy-bets', refuse);
    await click('meron', fiveK);
    await allConfirmed('refused');
    await deskA.unroute('**/dummy-bets', refuse);
    const down = (route: Route) => route.abort('failed');
    await deskA.route('**/dummy-bets', down);
    await click('wala', tenK);
    await allConfirmed('network failure');
    await deskA.unroute('**/dummy-bets', down);
    let afterRefusal = await book();
    check(afterRefusal.dummy_meron === before.dummy_meron && afterRefusal.dummy_wala === before.dummy_wala, 'refused/failed clicks added nothing');
    // Lost answer: the server applies the click, the browser never sees the answer, the desk resends
    // the same request id and the server answers with the book: counted once.
    let lost = true;
    const lose = async (route: Route) => {
      if (lost) { lost = false; await route.fetch().catch(() => null); await route.abort('connectionreset'); } else await route.continue();
    };
    await deskA.route('**/dummy-bets', lose);
    await click('meron', fiveK);
    const server = await allConfirmed('lost answer');
    await deskA.unroute('**/dummy-bets', lose);
    afterRefusal = server;
    check(exactCents(server.dummy_meron) - exactCents(before.dummy_meron) === BigInt(fiveK * 100), 'lost answer: the resent click counted exactly once');
    scenarios['failures'] = { refused_and_failed_added_nothing: true, lost_answer_counted_once: true };
  }

  // ---- 8. out-of-order answers: the newer state is never replaced by an older one ----
  {
    let n = 0;
    const reorder = async (route: Route) => {
      const mine = n++;
      const response = await route.fetch();
      await new Promise((r) => setTimeout(r, mine === 0 ? 2500 : 0)); // the first answer arrives last
      await route.fulfill({ response });
    };
    await deskA.route('**/dummy-bets', reorder);
    const from = await now(deskA);
    await click('meron', fiveK);
    await new Promise((r) => setTimeout(r, 400));
    await click('wala', tenK);
    const server = await allConfirmed('out of order');
    await deskA.unroute('**/dummy-bets', reorder);
    const m = await metrics(deskA);
    const answers = m.answers.filter((a: any) => a.sent >= from);
    check(answers.length === 2 && answers[0].version > answers[1].version, `out of order: answers arrived newest first (${answers.map((a: any) => a.version)})`);
    // After the newest answer the desk never showed an older price again.
    const newest = answers.reduce((a: any, b: any) => (b.version > a.version ? b : a), answers[0]);
    const shownAfter = m.changes.filter((x: any) => x.t > newest.t && !x.projected);
    check(shownAfter.every((x: any) => x.meron === server.odds.meron_display && x.wala === server.odds.wala_display),
      'out of order: an older answer replaced a newer price');
    scenarios['out_of_order'] = { versions_in_arrival_order: answers.map((a: any) => a.version), confirmed: server.odds };
  }

  // ---- 9/10. tenant isolation and frame shape on every screen ----
  const ordering: Record<string, unknown> = {};
  scenarios['frame_order'] = ordering;
  for (const [name, p] of [['desk A', deskA], ['desk B', deskB], ['player', player]] as const) {
    const m = await metrics(p);
    const foreign = m.frames.filter((f: any) => f.tenant_id && f.tenant_id !== tenantId);
    check(foreign.length === 0, `${name}: received ${foreign.length} frames of another tenant`);
    check(m.frames.every((f: any) => typeof f.version === 'number' && f.match_id && f.tenant_id && f.event_id), `${name}: frames carry version, fight, tenant and event ids`);
    check(m.frames.every((f: any) => !f.split), `${name}: a broadcast frame carried the ghost split`);
    // Frames from concurrent commits can arrive out of version order (two API workers publish them).
    // An older frame must change nothing on screen: on the desks no totals equal to a stale frame's
    // pools may appear after it arrived.
    const mine = m.frames.filter((f: any) => f.match_id === fight);
    const stale = mine.filter((f: any, i: number) => mine.slice(0, i).some((g: any) => g.version > f.version));
    const num = (t?: string) => Number((t ?? '').replace(/[^\d.]/g, ''));
    const shownStale = name === 'player' ? [] : stale.filter((f: any) => m.changes.some((x: any) => x.t >= f.t && x.t <= f.t + 200
      && num(x.tm) === Number(f.pools.meron) && num(x.tw) === Number(f.pools.wala)
      && !mine.some((g: any) => g.version > f.version && g.t <= x.t && g.pools.meron === f.pools.meron && g.pools.wala === f.pools.wala)));
    check(shownStale.length === 0, `${name}: ${shownStale.length} out-of-order frames were shown`);
    ordering[name] = { frames: mine.length, out_of_order: stale.length, shown: shownStale.length };
  }

  // ---- clean up: cancel the fight, end the event ----
  await judge.call('POST', `/declarator/matches/${fight}/betting/close`).catch(() => null);
  await judge.call('POST', `/declarator/matches/${fight}/declarations`, {
    request_id: crypto.randomUUID(), outcome: 'cancelled', base_revision: 0, reason: 'Synthetic payout verification',
  }).catch((e) => failures.push('cleanup cancel: ' + e));
  await judge.call('POST', `/declarator/events/${event.id}/end`).catch(() => null);

  const result = { origin: uat ? 'uat' : 'local', run, event: event.id, fight, player_dom: playerDom, scenarios, failures };
  writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  expect(failures).toEqual([]);
});
