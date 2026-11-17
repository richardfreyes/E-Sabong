import { test, expect, request, APIRequestContext, Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';

/**
 * Declarator desk request volume and manual ghost click latency, measured in a real browser against
 * production Angular assets and a real API (local isolated stacks only; no route interception).
 *
 * - Request volume per endpoint for DECLARATOR_REQ_IDLE seconds with auto ghost betting running and
 *   three players betting, plus the peak number of API requests pending in the browser.
 * - 8/16/32-click bursts: click → request start and click → answer per click, in click order.
 * - Stop and Close during a burst (click → request start → answer), and Declare right after Close.
 * - Every burst is accounted for: the desk's tally (added · refused · cancelled) against the book.
 */
test('Declarator desk request volume and ghost click latency', async ({ browser }) => {
  const origin = process.env['DECLARATOR_REQ_ORIGIN'] || 'http://127.0.0.1:4411';
  if (!['127.0.0.1', 'localhost'].includes(new URL(origin).hostname))
    throw Error('Local isolated stacks only');
  const idle = Number(process.env['DECLARATOR_REQ_IDLE'] || 60);
  const out = path.resolve(process.env['DECLARATOR_REQ_OUT'] || 'test-results/declarator-requests');
  mkdirSync(out, { recursive: true });
  test.setTimeout((idle + 600) * 1000);
  const password = 'DemoPass#2026';

  async function actor(username: string, portal: string) {
    const ctx: APIRequestContext = await request.newContext({
      baseURL: origin,
      extraHTTPHeaders: {
        'X-Requested-With': 'XMLHttpRequest',
        'X-ESB-Client': '2',
        'X-Portal': portal,
      },
    });
    const r = await ctx.post('/api/v1/auth/login', { data: { username, password } });
    expect(r.status(), await r.text()).toBe(200);
    const token = (await r.json()).access_token as string;
    const call = async (method: string, url: string, data?: unknown) => {
      const res = await ctx.fetch('/api/v1' + url, {
        method,
        data,
        headers: { Authorization: `Bearer ${token}` },
      });
      return { status: res.status(), body: await res.json().catch(() => ({})) };
    };
    return { ctx, call };
  }
  const admin = await actor('admin', 'admin');
  const judge = await actor('declarator', 'admin');
  await admin.call('PUT', '/admin/betting/settings', {
    enabled: true,
    starting_balance: '10000000.00',
    min_bet: '1.00',
    max_fight_stake: '10000000.00',
  });
  const run = Date.now().toString(36);
  const event = (
    await admin.call('POST', '/admin/events', {
      name: `Requests ${run}`,
      venue: 'bench',
      starts_at: new Date().toISOString(),
    })
  ).body;
  for (const op of ['publish', 'start'])
    expect((await admin.call('POST', `/admin/events/${event.id}/${op}`)).status).toBe(200);
  const me = (await judge.call('GET', '/auth/me')).body;
  expect(
    (await admin.call('PUT', `/declarator/events/${event.id}/assignments/${me.id}`)).status,
  ).toBe(200);
  const players = [];
  for (let i = 0; i < 3; i++) {
    const name = `req-${run}-${i}`;
    expect(
      (
        await admin.call('POST', '/admin/users', {
          username: name,
          full_name: `Req ${i}`,
          account_type: 'player',
          password,
        })
      ).status,
    ).toBe(201);
    players.push(await actor(name, 'player'));
  }
  let fight = '';
  let stopPlayers = false;
  const playerLoops = players.map(async (p, i) => {
    while (!stopPlayers) {
      await new Promise((r) => setTimeout(r, 2500 + i * 300));
      if (fight)
        await p.call('POST', `/portal/matches/${fight}/bets`, {
          side: i % 2 ? 'wala' : 'meron',
          amount: `${10 + i}.00`,
          request_id: crypto.randomUUID(),
        });
    }
  });

  // ---- the desk ----
  const ctx = await browser.newContext();
  const desk: Page = await ctx.newPage();
  const errors: string[] = [];
  desk.on('pageerror', (e) => errors.push(e.message));
  type Req = { key: string; method: string; start: number; end?: number; status?: number };
  const requests: Req[] = [];
  const open = new Map<object, Req>();
  let pending = 0,
    peakPending = 0;
  const key = (url: string) =>
    new URL(url).pathname.replace(/^\/api\/v1/, '').replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, ':id');
  desk.on('request', (r) => {
    if (!r.url().includes('/api/v1/')) return;
    const row: Req = { key: key(r.url()), method: r.method(), start: performance.now() };
    requests.push(row);
    open.set(r, row);
    pending++;
    peakPending = Math.max(peakPending, pending);
  });
  const done = async (r: import('@playwright/test').Request) => {
    const row = open.get(r);
    if (!row) return;
    open.delete(r);
    pending--;
    row.end = performance.now();
    row.status = (await r.response().catch(() => null))?.status();
  };
  desk.on('requestfinished', done);
  desk.on('requestfailed', done);
  await desk.goto(origin + '/admin/login');
  await desk.getByLabel('Username', { exact: true }).fill('declarator');
  await desk.getByLabel('Password', { exact: true }).fill(password);
  await desk.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(desk).not.toHaveURL(/login/);
  await desk.goto(origin + '/admin/declarator');
  const picker = desk.getByRole('combobox', { name: 'Event', exact: true }).first();
  await picker.click();
  await desk.getByRole('option', { name: event.name, exact: true }).click();
  await expect(desk.getByTestId('betting-control')).toBeVisible();
  const quick = (await judge.call('GET', '/declarator/settings/quick-bets')).body;
  const amount = Number(quick.meron[0]);

  const result: Record<string, unknown> = { origin, idle_seconds: idle };
  const declares: unknown[] = [];
  result['declares'] = declares;
  const openFight = async () => {
    await desk.getByRole('button', { name: 'Open bet', exact: true }).click();
    await expect(desk.getByTestId('betting-status')).toHaveText('OPEN', { timeout: 30000 });
    fight = (await judge.call('GET', `/declarator/events/${event.id}/monitor?compact=true`)).body
      .betting.match_id as string;
  };
  const settle = async () => {
    if ((await desk.getByTestId('betting-status').textContent())?.includes('OPEN')) {
      await desk.getByRole('button', { name: 'Close bet', exact: true }).click();
      await expect(desk.getByTestId('betting-status')).toHaveText('CLOSED', { timeout: 30000 });
    }
    const began = performance.now();
    const from = requests.length;
    await desk.getByRole('button', { name: 'Declare Meron', exact: true }).click();
    const confirmed = performance.now();
    await desk
      .getByRole('dialog')
      .getByRole('button', { name: 'Confirm declaration', exact: true })
      .click();
    // Checked every animation frame (expect() polls with a 100/250/500/1000 ms backoff, too coarse here).
    await desk.waitForFunction(
      () => {
        const b = document.querySelector('button.open-betting') as HTMLButtonElement | null;
        return !!b && !b.disabled;
      },
      null,
      { polling: 'raf', timeout: 30000 },
    );
    fight = '';
    const ready = performance.now();
    const untilReady = requests.length - from;
    // Reads the declaration still causes after the desk is ready (they compete with the next action).
    await desk.waitForTimeout(2000);
    declares.push({
      total_ms: Math.round(ready - began),
      after_confirm_ms: Math.round(ready - confirmed),
      requests_until_ready: untilReady,
      requests_within_2s: requests.length - from,
      requests: requests
        .slice(from)
        .map(
          (r) =>
            `${r.method} ${r.key} +${Math.round(r.start - confirmed)}..${r.end ? Math.round(r.end - confirmed) : '?'}`,
        ),
    });
    return Math.round(ready - began);
  };
  const count = (from: number, to: number) => {
    const rows = requests.filter((r) => r.start >= from && r.start < to);
    const by: Record<string, number> = {};
    for (const r of rows) by[`${r.method} ${r.key}`] = (by[`${r.method} ${r.key}`] ?? 0) + 1;
    return {
      total: rows.length,
      per_minute: Math.round((rows.length * 60000) / (to - from)),
      by_endpoint: by,
    };
  };
  const pct = (v: number[], p: number) =>
    v.length
      ? [...v].sort((a, b) => a - b)[Math.min(v.length - 1, Math.floor(v.length * p))]
      : null;
  const stats = (v: number[]) => ({
    n: v.length,
    p50: pct(v, 0.5),
    p95: pct(v, 0.95),
    max: pct(v, 1),
  });

  // ---- idle with auto ghost betting and players ----
  await openFight();
  await desk.getByTestId('ghost-interval').fill('1');
  await desk.getByTestId('ghost-start').click();
  await expect(desk.getByTestId('ghost-status')).toHaveText('ACTIVE');
  await desk.waitForTimeout(2000);
  const idleFrom = performance.now();
  peakPending = pending;
  await desk.waitForTimeout(idle * 1000);
  result['idle'] = { ...count(idleFrom, performance.now()), peak_pending: peakPending };
  await desk.getByTestId('ghost-stop').click();
  await expect(desk.getByTestId('ghost-status')).toHaveText('INACTIVE');
  await settle();

  // ---- bursts ----
  const button = desk.locator('.dummy-amount[data-side="meron"]').first();
  async function burst(size: number, ending: 'none' | 'stop' | 'close') {
    await openFight();
    if (ending === 'stop') {
      await desk.getByTestId('ghost-start').click();
      await expect(desk.getByTestId('ghost-status')).toHaveText('ACTIVE');
    }
    const before = (await judge.call('GET', `/declarator/matches/${fight}/book`)).body;
    const from = requests.length;
    peakPending = pending;
    const clicks: number[] = [];
    for (let i = 0; i < size; i++) {
      clicks.push(performance.now());
      await button.click({ delay: 0, timeout: 5000 });
    }
    let control: Record<string, number | null> | undefined;
    if (ending !== 'none') {
      const path = ending === 'stop' ? '/ghost-auto/stop' : '/betting/close';
      const clicked = performance.now();
      if (ending === 'stop') await desk.getByTestId('ghost-stop').click({ timeout: 30000 });
      else
        await desk
          .getByRole('button', { name: 'Close bet', exact: true })
          .click({ timeout: 30000 });
      await expect
        .poll(() => requests.slice(from).find((r) => r.key.endsWith(path))?.end ?? 0, {
          timeout: 30000,
        })
        .toBeGreaterThan(0);
      const req = requests.slice(from).find((r) => r.key.endsWith(path))!;
      control = {
        click_to_request_ms: Math.round(req.start - clicked),
        click_to_answer_ms: Math.round(req.end! - clicked),
        server_ms: Math.round(req.end! - req.start),
      };
    }
    await expect.poll(() => desk.getByTestId('ghost-queue').count(), { timeout: 60000 }).toBe(0);
    const sent = requests.slice(from).filter((r) => r.key.endsWith('/dummy-bets'));
    await expect.poll(() => sent.every((r) => r.end !== undefined), { timeout: 30000 }).toBe(true);
    const tally = ((await desk.getByTestId('ghost-tally').textContent()) ?? '').match(
      /(\d+) added · (\d+) refused · (\d+) cancelled/,
    );
    const [added, refused, cancelled] = tally ? tally.slice(1).map(Number) : [-1, -1, -1];
    const after = (await judge.call('GET', `/declarator/matches/${fight}/book`)).body;
    const ghostAuto = (await judge.call('GET', `/declarator/events/${event.id}/ghost-auto`)).body;
    const auto = ending === 'stop' ? Number(ghostAuto.added_meron) : 0;
    const delta =
      Number(after.dummy_meron) -
      Number(before.dummy_meron) -
      (auto - (ending === 'stop' ? Number(before.dummy_meron) : 0));
    const accounted =
      added + refused + cancelled === size &&
      Math.round(delta) === Math.round(added * amount) &&
      sent.length === added + refused;
    const others = requests.slice(from).filter((r) => !r.key.endsWith('/dummy-bets'));
    const row = {
      size,
      ending,
      sent: sent.length,
      added,
      refused,
      cancelled,
      accounted,
      click_to_request_ms: stats(sent.map((r, i) => Math.round(r.start - clicks[i]))),
      click_to_answer_ms: stats(sent.map((r, i) => Math.round(r.end! - clicks[i]))),
      request_ms: stats(sent.map((r) => Math.round(r.end! - r.start))),
      burst_ms: Math.round(Math.max(...sent.map((r) => r.end!)) - clicks[0]),
      other_requests: others.length,
      other_by_endpoint: count(clicks[0], performance.now()).by_endpoint,
      peak_pending: peakPending,
      control,
    };
    if (!accounted)
      errors.push(
        `burst ${size}/${ending} not accounted: ${JSON.stringify({ added, refused, cancelled, delta, sent: sent.length })}`,
      );
    const declare_ms = await settle();
    return { ...row, declare_ms };
  }
  const bursts = [];
  for (const size of [8, 16, 32]) bursts.push(await burst(size, 'none'));
  bursts.push(await burst(16, 'stop'));
  bursts.push(await burst(16, 'close'));
  bursts.push(await burst(32, 'close'));
  result['bursts'] = bursts;

  stopPlayers = true;
  await Promise.all(playerLoops);
  const reconciliation = (await admin.call('GET', '/admin/betting/reconciliation')).body;
  result['reconciliation_balanced'] = reconciliation.balanced;
  result['errors'] = errors;
  writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 1));
  await ctx.close();
  expect(errors).toEqual([]);
  expect(reconciliation.balanced).toBe(true);
});
