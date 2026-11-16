import { test, expect, request, APIRequestContext, Page } from '@playwright/test';
import { readFileSync, writeFileSync, appendFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';

/** Shared UAT policy remains enabled: OWNER official actions, Declarator ghost controls. */
test('live UAT Declarator workflow, authorization, settlement and reconnects', async ({
  browser,
}) => {
  const fixturePath = process.env['DECLARATOR_UAT_FIXTURE'];
  if (!fixturePath) throw Error('Private synthetic UAT fixture is required');
  const fixture = JSON.parse(readFileSync(fixturePath, 'utf8'));
  if (!new URL(fixture.origin).hostname.startsWith('uat.'))
    throw Error('Production is never a target of this test');
  const seconds = Number(process.env['DECLARATOR_UAT_SECONDS'] || 1200),
    out = path.resolve(process.env['DECLARATOR_UAT_OUT'] || 'test-results/declarator-uat');
  mkdirSync(out, { recursive: true });
  test.setTimeout((seconds + 300) * 1000);
  const failures: string[] = [],
    actions: Record<string, number[]> = {},
    actors: Session[] = [],
    contexts: any[] = [];
  let frames = 0,
    reconnects = 0,
    cycles = 0,
    event: any,
    activeFight: string | undefined,
    eventCompleted = false,
    started = Date.now();
  const safeError = (error: unknown) =>
    String(error)
      .split('\n')[0]
      .replace(/(Bearer |token=)[^\s&]+/gi, '$1[redacted]');
  const p95 = (values: number[]) =>
    [...values].sort((a, b) => a - b)[
      Math.min(values.length - 1, Math.floor(values.length * 0.95))
    ] || 0;
  class Session {
    token = '';
    context!: APIRequestContext;
    socket?: WebSocket;
    generation = 0;
    ending = false;
    ping?: ReturnType<typeof setInterval>;
    renewing?: Promise<void>;
    refreshed = Date.now();
    received = new Set<string>();
    recoveries = 0;
    constructor(
      readonly key: string,
      readonly platform = false,
    ) {}
    async login() {
      this.context = await request.newContext({
        baseURL: fixture.origin,
        extraHTTPHeaders: {
          'X-Requested-With': 'XMLHttpRequest',
          ...(this.platform
            ? {}
            : { 'X-Portal': this.key === 'admin' || this.key === 'judge' ? 'admin' : 'player' }),
        },
      });
      const r = await this.context.post(
        this.platform ? '/api/v1/platform/auth/login' : '/api/v1/auth/login',
        {
          data: this.platform
            ? { email: fixture.owner_email, password: fixture.password }
            : { username: fixture.accounts[this.key].login, password: fixture.password },
        },
      );
      expect(r.status(), await r.text()).toBe(200);
      this.token = (await r.json()).access_token;
      actors.push(this);
      return this;
    }
    async renew() {
      if (this.renewing) return this.renewing;
      this.renewing = (async () => {
        const r = await this.context.post(
          this.platform ? '/api/v1/platform/auth/refresh' : '/api/v1/auth/refresh',
          { data: {} },
        );
        expect(r.status(), await r.text()).toBe(200);
        this.token = (await r.json()).access_token;
        this.refreshed = Date.now();
      })();
      try {
        await this.renewing;
      } finally {
        this.renewing = undefined;
      }
    }
    async fetch(method: string, url: string, data?: unknown) {
      try {
        return await this.context.fetch('/api/v1' + url, {
          method,
          data,
          headers: { Authorization: 'Bearer ' + this.token },
        });
      } catch (error) {
        throw Error(method + ' ' + url + ': ' + safeError(error));
      }
    }
    async call(method: string, url: string, data?: unknown, expected = 200) {
      if (Date.now() - this.refreshed > 540000) await this.renew();
      const began = performance.now();
      let response = await this.fetch(method, url, data);
      if (response.status() === 401) {
        await this.renew();
        response = await this.fetch(method, url, data);
      }
      const key = method + ' ' + url.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, ':id');
      (actions[key] ??= []).push(performance.now() - began);
      expect(response.status(), await response.text()).toBe(expected);
      return response.json();
    }
    connect() {
      if (this.ending) return;
      this.generation++;
      const socket = new WebSocket(
        fixture.origin.replace('http', 'ws') + '/api/ws?token=' + encodeURIComponent(this.token),
      );
      this.socket = socket;
      socket.addEventListener('message', (message) => {
        frames++;
        const event = JSON.parse(String(message.data));
        if (event.type === 'fight.settled') {
          const key = event.data.match_id + ':' + event.data.revision;
          if (this.received.has(key)) failures.push('Duplicate settlement event ' + key);
          this.received.add(key);
        }
      });
      socket.addEventListener('close', () => {
        if (!this.ending) {
          reconnects++;
          void this.renew()
            .then(() => this.connect())
            .catch((error) => failures.push(safeError(error)));
        }
      });
      socket.addEventListener('error', () => failures.push('Player WebSocket transport error'));
      if (!this.ping)
        this.ping = setInterval(() => {
          if (this.socket?.readyState === WebSocket.OPEN) this.socket.send('{"type":"ping"}');
        }, 25000);
    }
    async close() {
      this.ending = true;
      if (this.ping) clearInterval(this.ping);
      this.socket?.close(1000);
      await this.context.dispose();
    }
  }
  const owner = await new Session('owner', true).login(),
    admin = await new Session('admin').login(),
    judge = await new Session('judge').login();
  const config = await owner.call('GET', '/platform/rental/settings');
  expect(config.shared_domain_enabled).toBe(true);
  const settings = await admin.call('GET', '/admin/betting/settings');
  expect(settings.enabled).toBe(true);
  const min = String(settings.min_bet || '1.00');
  const participants: Session[] = [];
  for (let i = 1; i <= 4; i++) {
    const key = 'player' + i;
    await admin.call(
      'POST',
      '/admin/balance/account-grants',
      {
        target_user_id: fixture.accounts[key].id,
        amount: '5000.00',
        idempotency_key: 'uat-' + fixture.run + '-' + key,
        remarks: 'Synthetic Declarator verification',
      },
      201,
    );
    const user = await new Session(key).login();
    participants.push(user);
    user.connect();
  }
  const before = await admin.call('GET', '/admin/betting/reconciliation');
  expect(before.balanced).toBe(true);
  event = await owner.call(
    'POST',
    '/platform/rental/events',
    {
      name: 'Declarator UAT verification ' + fixture.run,
      venue: 'Synthetic release verification',
      starts_at: new Date().toISOString(),
    },
    201,
  );
  for (const status of ['scheduled', 'live'])
    await owner.call('POST', `/platform/rental/events/${event.id}/status`, { status });
  await admin.call(
    'PUT',
    `/declarator/events/${event.id}/assignments/${fixture.accounts.judge.id}`,
  );
  async function pageFor(key: string, adminPage: boolean) {
    const ctx = await browser.newContext();
    contexts.push(ctx);
    const page = await ctx.newPage();
    page.on('pageerror', (e) => failures.push(e.message));
    page.on('websocket', (socket) => socket.on('framereceived', () => frames++));
    await page.goto(fixture.origin + (adminPage ? '/admin/login' : '/login'));
    await page.getByLabel('Username', { exact: true }).fill(fixture.accounts[key].login);
    await page.getByLabel('Password', { exact: true }).fill(fixture.password);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await expect(page).not.toHaveURL(/login/);
    return page;
  }
  const desk = await pageFor('judge', true);
  const selector = desk.getByRole('combobox', { name: 'Event', exact: true }).first();
  await selector.click();
  await desk.getByRole('option', { name: event.name, exact: true }).click();
  const player = await pageFor('player1', false);
  const cdp = await desk.context().newCDPSession(desk);
  await cdp.send('Performance.enable');
  const prefix = '/platform/rental';
  let sampleAt = 0;
  started = Date.now();
  try {
    while (Date.now() - started < seconds * 1000) {
      const fight = await owner.call(
        'POST',
        `${prefix}/events/${event.id}/fights`,
        { meron_name: 'Synthetic Meron', wala_name: 'Synthetic Wala' },
        201,
      );
      activeFight = fight.id;
      await player.goto(fixture.origin + '/matches/' + fight.id);
      await expect(player.getByRole('region', { name: 'Official result' })).toBeVisible();
      for (const action of ['start', 'open'])
        await owner.call('POST', `${prefix}/fights/${fight.id}/action`, { action });
      await expect(desk.getByTestId('betting-status')).toHaveText('OPEN');
      await expect(desk.getByRole('button', { name: 'Close bet', exact: true })).toBeDisabled();
      const refused = await judge.call(
        'POST',
        `/declarator/matches/${fight.id}/betting/close`,
        undefined,
        403,
      );
      expect(refused.error.code).toBe('owner_controlled');
      await desk.getByTestId('ghost-interval').fill('1');
      await desk.getByTestId('ghost-start').click();
      await expect(desk.getByTestId('ghost-status')).toHaveText('ACTIVE');
      const opened = Date.now();
      let tick = 0;
      while (Date.now() - opened < 20000 && Date.now() - started < seconds * 1000) {
        const actor = participants[tick % participants.length];
        await actor.call(
          'POST',
          `/portal/matches/${fight.id}/bets`,
          { side: tick % 2 ? 'meron' : 'wala', amount: min, request_id: crypto.randomUUID() },
          201,
        );
        if (tick % 2 === 0) await desk.locator('.dummy-amount[data-side="meron"]').first().click();
        tick++;
        await desk.waitForTimeout(250);
      }
      await desk.getByTestId('ghost-stop').click();
      await expect(desk.getByTestId('ghost-status')).toHaveText('INACTIVE');
      await owner.call('POST', `${prefix}/fights/${fight.id}/action`, { action: 'close' });
      await expect(desk.getByTestId('betting-status')).toHaveText('CLOSED');
      const outcome = ['meron', 'wala', 'draw', 'cancelled'][cycles % 4],
        generations = participants.map((p) => p.generation);
      if (outcome === 'cancelled')
        await owner.call('POST', `${prefix}/fights/${fight.id}/action`, {
          action: 'cancel',
          reason: 'Synthetic UAT verification',
        });
      else await owner.call('POST', `${prefix}/fights/${fight.id}/result`, { outcome });
      await expect(desk.getByTestId('settlement-summary')).not.toContainText('Awaiting result');
      if (outcome === 'cancelled')
        await expect(player.getByRole('region', { name: 'Official result' })).toContainText(
          'Cancelled',
        );
      else
        await expect(player.getByRole('region', { name: 'Official result' })).toContainText(
          new RegExp(outcome, 'i'),
        );
      const book = await judge.call('GET', `/declarator/matches/${fight.id}/book`);
      expect(book.state).toBe('settled');
      const key = fight.id + ':' + book.settlement.revision;
      for (const [index, actor] of participants.entries()) {
        if (actor.generation === generations[index] && actor.socket?.readyState === WebSocket.OPEN)
          await expect.poll(() => actor.received.has(key), { timeout: 5000 }).toBe(true);
        else {
          expect(book.state).toBe('settled');
          actor.recoveries++;
        }
      }
      activeFight = undefined;
      const reconciled = await admin.call('GET', '/admin/betting/reconciliation');
      expect(reconciled.balanced).toBe(true);
      expect(reconciled.checks.pool_mismatches).toBe(0);
      cycles++;
      if (Date.now() - sampleAt >= 60000) {
        await cdp.send('HeapProfiler.collectGarbage');
        const { metrics } = await cdp.send('Performance.getMetrics');
        const sample = {
          elapsedSeconds: (Date.now() - started) / 1000,
          cycles,
          frames,
          reconnects,
          metrics: Object.fromEntries(metrics.map((m) => [m.name, m.value])),
          actions: Object.fromEntries(
            Object.entries(actions).map(([name, values]) => [
              name,
              { count: values.length, p95: p95(values), max: Math.max(...values) },
            ]),
          ),
          failures,
          reconciled,
        };
        appendFileSync(path.join(out, 'checkpoints.jsonl'), JSON.stringify(sample) + '\n');
        console.log(
          JSON.stringify({
            uatCheckpoint: sample.elapsedSeconds,
            cycles,
            frames,
            reconnects,
            failures: failures.length,
          }),
        );
        sampleAt = Date.now();
      }
    }
    await owner.call('POST', `${prefix}/events/${event.id}/status`, { status: 'completed' });
    eventCompleted = true;
    const after = await admin.call('GET', '/admin/betting/reconciliation');
    expect(after.balanced).toBe(true);
    expect(failures).toEqual([]);
    const final = {
      passed: true,
      cycles,
      frames,
      reconnects,
      seconds: (Date.now() - started) / 1000,
      settlementDelivery: participants.map((p) => ({
        received: p.received.size,
        recoveries: p.recoveries,
      })),
      failures,
    };
    writeFileSync(path.join(out, 'result.json'), JSON.stringify(final, null, 2));
  } catch (error) {
    writeFileSync(
      path.join(out, 'result.json'),
      JSON.stringify({ passed: false, error: safeError(error), cycles, frames, failures }, null, 2),
    );
    throw Error(safeError(error));
  } finally {
    if (activeFight) {
      try {
        await owner.call('POST', `${prefix}/fights/${activeFight}/action`, {
          action: 'cancel',
          reason: 'Interrupted synthetic UAT verification',
        });
      } catch (error) {
        console.error('Synthetic fight cleanup requires review: ' + safeError(error));
      }
    }
    if (event && !eventCompleted) {
      try {
        await owner.call('POST', `${prefix}/events/${event.id}/status`, { status: 'completed' });
      } catch (error) {
        console.error('Synthetic event cleanup requires review: ' + safeError(error));
      }
    }
    for (const participant of actors) await participant.close();
    for (const context of contexts) await context.close();
  }
});
