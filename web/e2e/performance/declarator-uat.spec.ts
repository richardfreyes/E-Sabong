import { test, expect, request, APIRequestContext, Page } from '@playwright/test';
import { readFileSync, writeFileSync, appendFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';

/**
 * Live UAT Declarator verification for independent tenants (rc.5+). Never targets production.
 *
 * Tenant A is the synthetic fixture inside the UAT root workspace; tenant B is a synthetic tenant
 * provisioned here. Each tenant's Admin runs its own event, its own Declarator opens/closes betting
 * and declares, and its own players bet. Every bet, wallet and WebSocket frame is checked.
 * The user's own events are never touched: only events this run creates are used or closed.
 */
test('live UAT Declarator workflow, tenant isolation, settlement and Stop latency', async ({
  browser,
}) => {
  const fixturePath = process.env['DECLARATOR_UAT_FIXTURE'];
  if (!fixturePath) throw Error('Private synthetic UAT fixture is required');
  const fixture = JSON.parse(readFileSync(fixturePath, 'utf8'));
  if (!new URL(fixture.origin).hostname.startsWith('uat.'))
    throw Error('Production is never a target of this test');
  const cyclesWanted = Number(process.env['DECLARATOR_UAT_CYCLES'] || 12);
  const out = path.resolve(process.env['DECLARATOR_UAT_OUT'] || 'test-results/declarator-uat');
  mkdirSync(out, { recursive: true });
  const statePath = path.join(out, 'tenant-b.json');
  test.setTimeout(60 * 60 * 1000);
  const attempt = Date.now().toString(36);
  const failures: string[] = [];
  const actions: Record<string, number[]> = {};
  const latency: Record<string, number[]> = {};
  const actors: Session[] = [];
  const contexts: any[] = [];
  const created: { admin: Session; event: string; fight?: string }[] = [];
  let cycles = 0;
  let finalWritten = false; // keep the full result even when its final assertion fails
  const p95 = (v: number[]) =>
    [...v].sort((a, b) => a - b)[Math.min(v.length - 1, Math.floor(v.length * 0.95))] || 0;
  const safe = (e: unknown) =>
    String(e)
      .split('\n')
      .filter((l) => !/password|token|Bearer/i.test(l))
      .slice(0, 6)
      .join(' | ');
  const cents = (v: number) => Math.round((v + Number.EPSILON) * 100) / 100;
  const log = (o: unknown) => {
    appendFileSync(path.join(out, 'log.jsonl'), JSON.stringify(o) + '\n');
    console.log(JSON.stringify(o));
  };

  class Session {
    token = '';
    context!: APIRequestContext;
    socket?: WebSocket;
    generation = 0;
    ending = false;
    ping?: ReturnType<typeof setInterval>;
    refreshed = Date.now();
    renewing?: Promise<void>;
    settled = new Set<string>();
    frames: string[] = [];
    constructor(
      readonly name: string,
      readonly login: string,
      readonly portal: 'admin' | 'player' | 'platform',
      readonly tenant: 'A' | 'B' | 'platform',
      public password: string,
      public id = '',
    ) {}
    async start() {
      this.context = await request.newContext({
        baseURL: fixture.origin,
        extraHTTPHeaders: {
          'X-Requested-With': 'XMLHttpRequest',
          'X-ESB-Client': '2', // as the web app sends; older desks are refused (426)
          ...(this.portal === 'platform' ? {} : { 'X-Portal': this.portal }),
        },
      });
      const r = await this.context.post(
        this.portal === 'platform' ? '/api/v1/platform/auth/login' : '/api/v1/auth/login',
        {
          data:
            this.portal === 'platform'
              ? { email: this.login, password: this.password }
              : { username: this.login, password: this.password },
        },
      );
      expect(r.status(), 'login ' + this.name).toBe(200);
      this.token = (await r.json()).access_token;
      this.refreshed = Date.now();
      actors.push(this);
      return this;
    }
    async renew() {
      if (this.renewing) return this.renewing;
      this.renewing = (async () => {
        const r = await this.context.post(
          this.portal === 'platform' ? '/api/v1/platform/auth/refresh' : '/api/v1/auth/refresh',
          { data: {} },
        );
        expect(r.status(), 'refresh ' + this.name).toBe(200);
        this.token = (await r.json()).access_token;
        this.refreshed = Date.now();
      })();
      try {
        await this.renewing;
      } finally {
        this.renewing = undefined;
      }
    }
    async raw(method: string, url: string, data?: unknown) {
      if (Date.now() - this.refreshed > 540000) await this.renew();
      const began = performance.now();
      let r = await this.context.fetch('/api/v1' + url, {
        method,
        data,
        headers: { Authorization: 'Bearer ' + this.token },
      });
      if (r.status() === 401) {
        await this.renew();
        r = await this.context.fetch('/api/v1' + url, {
          method,
          data,
          headers: { Authorization: 'Bearer ' + this.token },
        });
      }
      const key =
        method + ' ' + url.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, ':id').replace(/\?.*/, '');
      (latency[key] ??= []).push(performance.now() - began);
      return r;
    }
    async call(method: string, url: string, data?: unknown, expected: number | number[] = 200) {
      const r = await this.raw(method, url, data);
      const ok = Array.isArray(expected) ? expected : [expected];
      if (!ok.includes(r.status()))
        throw Error(
          `${this.name} ${method} ${url.replace(/\?.*/, '')} -> ${r.status()} ${(await r.text()).slice(0, 300)}`,
        );
      const text = await r.text();
      return text ? JSON.parse(text) : null;
    }
    connect() {
      if (this.ending) return;
      this.generation++;
      const ws = new WebSocket(
        fixture.origin.replace('http', 'ws') + '/api/ws?token=' + encodeURIComponent(this.token),
      );
      this.socket = ws;
      ws.addEventListener('message', (m) => {
        const text = String(m.data);
        this.frames.push(text);
        const ev = JSON.parse(text);
        if (ev.type === 'fight.settled') {
          const key = ev.data.match_id + ':' + ev.data.revision;
          if (this.settled.has(key)) failures.push(`${this.name}: duplicate settlement ${key}`);
          this.settled.add(key);
        }
      });
      ws.addEventListener('close', () => {
        if (!this.ending)
          void this.renew()
            .then(() => this.connect())
            .catch((e) => failures.push(safe(e)));
      });
      if (!this.ping)
        this.ping = setInterval(() => {
          if (this.socket?.readyState === WebSocket.OPEN) this.socket.send('{"type":"ping"}');
        }, 25000);
    }
    async stop() {
      this.ending = true;
      if (this.ping) clearInterval(this.ping);
      this.socket?.close(1000);
      await this.context.dispose();
    }
  }

  const statement = async (p: Session) =>
    Number((await p.call('GET', '/portal/betting/statement')).balance);
  const reconcile = async (admin: Session, label: string) => {
    const r = await admin.call('GET', '/admin/betting/reconciliation');
    if (!r.balanced || r.checks.pool_mismatches !== 0 || r.checks.wallet_mismatches !== 0)
      failures.push(`${label}: reconciliation not balanced ${JSON.stringify(r.checks)}`);
    return r;
  };
  async function timed(name: string, work: () => Promise<void>) {
    const began = performance.now();
    await work();
    (actions[name] ??= []).push(performance.now() - began);
  }

  /** Check every player bet on a settled fight against the settlement rules, and return totals. */
  async function verifyBets(players: Session[], mid: string, book: any) {
    const outcome = book.settlement.outcome as string;
    const price = { meron: Number(book.odds.meron_display), wala: Number(book.odds.wala_display) };
    let staked = 0,
      returned = 0,
      claims = 0,
      count = 0;
    for (const p of players) {
      const bets = (await p.call('GET', `/portal/bets?match_id=${mid}&page_size=100`)).items;
      for (const b of bets) {
        count++;
        const amount = Number(b.amount),
          payout = Number(b.payout);
        staked += amount;
        returned += payout;
        let want: [string, number];
        if (outcome === 'meron' || outcome === 'wala') {
          if (b.side === outcome) {
            const pays = cents(
              amount * (b.acceptance_priced ? Number(b.multiplier) : price[outcome]),
            );
            claims += pays;
            want = ['won', pays];
          } else want = ['lost', 0];
        } else if (outcome === 'draw')
          want =
            b.side === 'draw'
              ? ['won', cents(amount * Number(b.multiplier))]
              : ['refunded', amount];
        else want = ['refunded', amount];
        if (b.status !== want[0] || Math.abs(payout - want[1]) > 0.001) {
          // A winning side is capped at the pot; that is the only allowed deviation.
          const capped =
            (outcome === 'meron' || outcome === 'wala') &&
            Number(book.settlement.paid_to_winners) < claims - 0.001;
          if (!capped)
            failures.push(
              `bet ${b.id} ${b.side} ${outcome}: got ${b.status}/${payout}, want ${want[0]}/${want[1]}`,
            );
        }
      }
    }
    const pot = Number(book.pools.meron) + Number(book.pools.wala);
    const commission = Number(book.settlement.commission);
    const expectedCommission =
      outcome === 'meron' || outcome === 'wala'
        ? cents(pot - Number(book.settlement.paid_to_winners))
        : 0;
    if (Math.abs(commission - expectedCommission) > 0.001)
      failures.push(`fight ${mid}: commission ${commission}, want ${expectedCommission}`);
    return { count, staked: cents(staked), returned: cents(returned) };
  }

  // ---- tenant A (fixture) ----
  const A = fixture.accounts;
  const owner = await new Session(
    'owner',
    fixture.owner_email,
    'platform',
    'platform',
    fixture.password,
  ).start();
  const adminA = await new Session(
    'adminA',
    A.admin.login,
    'admin',
    'A',
    fixture.password,
    A.admin.id,
  ).start();
  const judgeA = await new Session(
    'judgeA',
    A.judge.login,
    'admin',
    'A',
    fixture.password,
    A.judge.id,
  ).start();
  const settingsA = await adminA.call('GET', '/admin/betting/settings');
  expect(
    settingsA.enabled,
    'tenant A betting must already be enabled; settings are never changed',
  ).toBe(true);
  const min = Number(settingsA.min_bet || 1);
  const reconA0 = await reconcile(adminA, 'A before');
  const playersA: Session[] = [];
  for (let i = 1; i <= 4; i++) {
    const key = 'player' + i;
    await adminA.call(
      'POST',
      '/admin/balance/account-grants',
      {
        target_user_id: A[key].id,
        amount: '5000.00',
        idempotency_key: `uat-${fixture.run}-${attempt}-${key}`,
        remarks: 'Synthetic Declarator verification',
      },
      201,
    );
    const p = await new Session(
      'A-' + key,
      A[key].login,
      'player',
      'A',
      fixture.password,
      A[key].id,
    ).start();
    playersA.push(p);
    p.connect();
  }

  // ---- tenant B (provisioned here; recorded at once so cleanup can always find it) ----
  const plans = await (
    await request.newContext({ baseURL: fixture.origin })
  ).get('/api/v1/public/plans');
  const planList = (await plans.json()) as any[];
  const plan = planList.find((p) => (p.code ?? p.slug) === 'development') ?? planList[0];
  const slug = ('perf-' + fixture.run + attempt).slice(0, 30).toLowerCase();
  const tenantB = await owner.call(
    'POST',
    '/platform/tenants',
    {
      name: 'Synthetic isolation ' + fixture.run,
      slug,
      plan_id: plan.id,
      admin_email: `perf-${fixture.run}-${attempt}@example.com`,
      admin_name: 'Synthetic B admin',
      with_trial: true,
    },
    201,
  );
  const stateB: any = {
    run: fixture.run,
    attempt,
    tenant_id: tenantB.tenant.id,
    slug,
    accounts: {},
  };
  const saveState = () =>
    writeFileSync(statePath, JSON.stringify(stateB, null, 2), { mode: 0o600 });
  saveState();
  let adminB = await new Session(
    'adminB',
    tenantB.login_identifier,
    'admin',
    'B',
    tenantB.temporary_password,
  ).start();
  await adminB.call('POST', '/auth/me/password', {
    current_password: tenantB.temporary_password,
    new_password: fixture.password,
  });
  await adminB.stop();
  adminB = await new Session(
    'adminB',
    tenantB.login_identifier,
    'admin',
    'B',
    fixture.password,
  ).start();
  stateB.accounts.admin = { username: tenantB.admin_username };
  const settingsB = await adminB.call('PUT', '/admin/betting/settings', {
    enabled: true,
    starting_balance: '1000.00',
  });
  const minB = Number(settingsB.min_bet || 1);
  const makeB = async (key: string, type: 'declarator' | 'player') => {
    const { user: u } = await adminB.call(
      'POST',
      '/admin/users',
      {
        username: `perf-${fixture.run}-b${key}`,
        full_name: 'Synthetic B ' + key,
        account_type: type,
        password: fixture.password,
      },
      201,
    );
    stateB.accounts[key] = { id: u.id, username: u.username };
    saveState();
    return u;
  };
  const judgeBUser = await makeB('judge', 'declarator');
  const pB1 = await makeB('player1', 'player'),
    pB2 = await makeB('player2', 'player');
  const judgeB = await new Session(
    'judgeB',
    `${judgeBUser.username}@${slug}`,
    'admin',
    'B',
    fixture.password,
    judgeBUser.id,
  ).start();
  const playersB: Session[] = [];
  for (const u of [pB1, pB2]) {
    const p = await new Session(
      'B-' + u.username,
      `${u.username}@${slug}`,
      'player',
      'B',
      fixture.password,
      u.id,
    ).start();
    playersB.push(p);
    p.connect();
  }
  const reconB0 = await reconcile(adminB, 'B before');
  log({ setup: 'tenants ready', tenantB: slug, plan: plan.code });

  // ---- events: each Admin runs its own ----
  const newEvent = async (admin: Session, judge: Session, label: string) => {
    const e = await admin.call(
      'POST',
      '/admin/events',
      {
        name: `Declarator UAT ${fixture.run} ${attempt} ${label}`,
        venue: 'Synthetic verification',
        starts_at: new Date().toISOString(),
      },
      201,
    );
    created.push({ admin, event: e.id });
    for (const op of ['publish', 'start']) await admin.call('POST', `/admin/events/${e.id}/${op}`);
    await admin.call('PUT', `/declarator/events/${e.id}/assignments/${judge.id}`);
    return e;
  };
  const eventA = await newEvent(adminA, judgeA, 'A');
  const eventB = await newEvent(adminB, judgeB, 'B');

  // ---- Declarator desk (tenant A), never reloaded ----
  const ctx = await browser.newContext();
  contexts.push(ctx);
  const desk: Page = await ctx.newPage();
  desk.on('pageerror', (e) => failures.push('desk: ' + e.message));
  await desk.goto(fixture.origin + '/admin/login');
  await desk.getByLabel('Username', { exact: true }).fill(A.judge.login);
  await desk.getByLabel('Password', { exact: true }).fill(fixture.password);
  await desk.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(desk).not.toHaveURL(/login/);
  const picker = desk.getByRole('combobox', { name: 'Event', exact: true }).first();
  await picker.click();
  await desk.getByRole('option', { name: eventA.name, exact: true }).click();
  await expect(desk.getByTestId('betting-control')).toBeVisible();
  const cdp = await ctx.newCDPSession(desk);
  await cdp.send('Performance.enable');
  const quick = await judgeA.call('GET', '/declarator/settings/quick-bets');
  const quickMeron = Number(quick.meron[0]),
    quickWala = Number(quick.wala[0]);

  // A newly activated event appears in the open desk without a reload.
  await timed('new-event-visible', async () => {
    const second = await newEvent(adminA, judgeA, 'A2');
    await expect
      .poll(
        async () => {
          await picker.click();
          const seen = await desk.getByRole('option', { name: second.name, exact: true }).count();
          await desk.keyboard.press('Escape');
          return seen;
        },
        { timeout: 15000, intervals: [500, 1000, 2000] },
      )
      .toBe(1);
  });
  await expect(desk.getByTestId('betting-control')).toBeVisible();

  const sample = async (label: string) => {
    await cdp.send('HeapProfiler.collectGarbage');
    const { metrics } = await cdp.send('Performance.getMetrics');
    const m = Object.fromEntries(metrics.map((x) => [x.name, x.value]));
    const row = {
      label,
      cycles,
      heapMB: cents(m['JSHeapUsedSize'] / 1e6),
      nodes: m['Nodes'],
      listeners: m['JSEventListeners'],
      taskSeconds: cents(m['TaskDuration']),
      failures: failures.length,
    };
    appendFileSync(path.join(out, 'checkpoints.jsonl'), JSON.stringify(row) + '\n');
    return row;
  };
  await sample('start');
  const ledger = { bets: 0, staked: 0, returned: 0 };
  const playerStart = new Map<Session, number>();
  for (const p of [...playersA, ...playersB]) playerStart.set(p, await statement(p));
  const idsA = new Set<string>([eventA.id, fixture.tenant_id]);
  const idsB = new Set<string>([eventB.id, tenantB.tenant.id]);

  async function settleDelivered(players: Session[], key: string, mid: string) {
    for (const p of players) {
      if (p.socket?.readyState === WebSocket.OPEN)
        await expect.poll(() => p.settled.has(key), { timeout: 8000 }).toBe(true);
      else {
        const book = await judgeA.call('GET', `/declarator/matches/${mid}/book`);
        expect(book.state).toBe('settled');
      }
    }
  }

  try {
    // ---- tenant A: full desk cycles ----
    const outcomes = ['meron', 'wala', 'draw', 'cancelled'];
    const labels: Record<string, string> = {
      meron: 'Meron',
      wala: 'Wala',
      draw: 'Draw',
      cancelled: 'Cancel',
    };
    while (cycles < cyclesWanted) {
      const outcome = outcomes[cycles % 4];
      // An Admin closes (cycle 6) or opens (cycle 7) betting from the admin API; the open desk
      // must follow without a reload. Results stay with the Declarator: an Admin result on a
      // Declarator-verified fight is refused (409 verification_required) by design.
      const adminCloses = cycles === 5;
      const adminOpens = cycles === 6;
      const target = adminOpens
        ? ((await judgeA.call('GET', `/declarator/events/${eventA.id}/monitor`)).betting
            ?.match_id as string | undefined)
        : undefined;
      await timed(adminOpens && target ? 'admin-open-to-desk' : 'open', async () => {
        if (adminOpens && target) {
          // An Admin starts the prepared fight, then opens betting (opening an unstarted fight is
          // accepted by the admin API but leaves the desk unable to act: reported separately).
          await adminA.call('POST', `/admin/matches/${target}/start`, undefined, [200, 409, 422]);
          await adminA.call('POST', `/admin/matches/${target}/betting/open`, {});
        } else await desk.getByRole('button', { name: 'Open bet', exact: true }).click();
        await expect(desk.getByTestId('betting-status')).toHaveText('OPEN', { timeout: 15000 });
      });
      if (adminOpens && !target) failures.push('cycle 7: no prepared fight for the Admin to open');
      const mid = (await judgeA.call('GET', `/declarator/events/${eventA.id}/monitor`)).betting
        .match_id as string;
      created[0].fight = mid;
      idsA.add(mid);
      await desk.getByTestId('ghost-interval').fill('1');
      await timed('auto-start', async () => {
        await desk.getByTestId('ghost-start').click();
        await expect(desk.getByTestId('ghost-status')).toHaveText('ACTIVE');
      });
      const framesBefore = playersA[0].frames.length;
      let manualMeron = 0,
        manualWala = 0,
        restarted = false;
      const began = Date.now();
      for (let tick = 0; Date.now() - began < 15000; tick++) {
        const p = playersA[tick % playersA.length];
        const side = ['meron', 'wala', 'draw'][tick % 3];
        await p.call(
          'POST',
          `/portal/matches/${mid}/bets`,
          { side, amount: min.toFixed(2), request_id: crypto.randomUUID() },
          201,
        );
        const ghostSide = tick % 2 ? 'wala' : 'meron';
        await timed('manual-ghost', async () => {
          await desk.locator(`.dummy-amount[data-side="${ghostSide}"]`).first().click();
        });
        if (ghostSide === 'meron') manualMeron++;
        else manualWala++;
        if (cycles % 2 === 1 && !restarted && Date.now() - began > 7000) {
          await timed('auto-stop', async () => {
            await desk.getByTestId('ghost-stop').click();
            await expect(desk.getByTestId('ghost-status')).toHaveText('INACTIVE', {
              timeout: 15000,
            });
          });
          await timed('auto-restart', async () => {
            await desk.getByTestId('ghost-start').click();
            await expect(desk.getByTestId('ghost-status')).toHaveText('ACTIVE');
          });
          restarted = true;
        }
        await desk.waitForTimeout(1000);
      }
      await timed(adminCloses ? 'admin-close-to-desk' : 'close', async () => {
        if (adminCloses) await adminA.call('POST', `/admin/matches/${mid}/betting/close`);
        else await desk.getByRole('button', { name: 'Close bet', exact: true }).click();
        await expect(desk.getByTestId('betting-status')).toHaveText('CLOSED', { timeout: 15000 });
      });
      // Live totals: every manual and automatic ghost amount is in the book, exactly once.
      const closed = await judgeA.call('GET', `/declarator/matches/${mid}/book`);
      const auto = await judgeA.call('GET', `/declarator/events/${eventA.id}/ghost-auto`);
      if (auto.active) failures.push(`cycle ${cycles}: auto ghost still active after close`);
      const ghostMeron = Number(closed.dummy_meron),
        ghostWala = Number(closed.dummy_wala);
      const autoMeron = Number(auto.added_meron),
        autoWala = Number(auto.added_wala);
      const wantMeron = manualMeron * quickMeron,
        wantWala = manualWala * quickWala;
      // The auto job reports its current run; a restart begins a new job, so compare as a lower bound.
      if (ghostMeron < wantMeron + autoMeron - 0.001 || ghostWala < wantWala + autoWala - 0.001)
        failures.push(
          `cycle ${cycles}: ghost totals ${ghostMeron}/${ghostWala} below manual ${wantMeron}/${wantWala} + auto ${autoMeron}/${autoWala}`,
        );
      const bookFrames = playersA[0].frames
        .slice(framesBefore)
        .filter((f) => f.includes('"book.updated"') && f.includes(mid)).length;
      if (bookFrames === 0) failures.push(`cycle ${cycles}: players received no live book updates`);
      await timed('declare', async () => {
        await desk.getByRole('button', { name: `Declare ${labels[outcome]}`, exact: true }).click();
        const dialog = desk.getByRole('dialog');
        if (outcome === 'cancelled')
          await dialog.getByLabel('Cancellation reason').fill('Synthetic UAT cancellation');
        await dialog
          .getByRole('button', {
            name: outcome === 'cancelled' ? 'Cancel fight' : 'Confirm declaration',
            exact: true,
          })
          .click();
        await expect(desk.getByTestId('settlement-summary')).not.toContainText('Awaiting result', {
          timeout: 15000,
        });
        await expect(desk.getByRole('button', { name: 'Open bet', exact: true })).toBeEnabled({
          timeout: 15000,
        });
      });
      const book = await judgeA.call('GET', `/declarator/matches/${mid}/book`);
      expect(book.state).toBe('settled');
      expect(book.settlement.outcome).toBe(outcome);
      // One settlement per fight: revision 1 for a result, none for a cancellation.
      const wantRevision = outcome === 'cancelled' ? null : 1;
      if (book.settlement.revision !== wantRevision)
        failures.push(`fight ${mid}: settlement revision ${book.settlement.revision}`);
      await settleDelivered(playersA, mid + ':' + book.settlement.revision, mid);
      const totals = await verifyBets(playersA, mid, book);
      ledger.bets += totals.count;
      ledger.staked = cents(ledger.staked + totals.staked);
      ledger.returned = cents(ledger.returned + totals.returned);
      // Fight history and audit trail.
      const records = await judgeA.call(
        'GET',
        `/declarator/records?event_id=${eventA.id}&page_size=100`,
      );
      if (!records.items.some((r: any) => r.match.id === mid))
        failures.push(`fight ${mid}: missing from history`);
      await reconcile(adminA, `A cycle ${cycles}`);
      created[0].fight = undefined;
      cycles++;
      log({
        cycle: cycles,
        outcome,
        adminCloses,
        adminOpens,
        bets: totals.count,
        bookFrames,
        ghost: [ghostMeron, ghostWala],
        ...(await sample('cycle-' + cycles)),
      });
    }

    // ---- Stop/Close under bursts of manual ghost clicks (queue: FIFO dispatch, bounded concurrency, GHOST_CONCURRENCY in flight) ----
    // Each phase is timed separately: click -> request sent -> response -> desk shows the state.
    const bursts: any[] = [];
    const timedRequest = async (
      match: string,
      click: () => Promise<void>,
      shown: () => Promise<void>,
    ) => {
      let sent = 0,
        answered = 0;
      const onRequest = (r: any) => {
        if (r.url().includes(match) && !sent) sent = Date.now();
      };
      const onDone = (r: any) => {
        if (r.url().includes(match) && !answered) answered = Date.now();
      };
      desk.on('request', onRequest);
      desk.on('requestfinished', onDone);
      const clicked = Date.now();
      await click();
      const dispatched = Date.now();
      await shown();
      const visible = Date.now();
      desk.off('request', onRequest);
      desk.off('requestfinished', onDone);
      return {
        clickToSentMs: sent ? sent - clicked : null,
        actionabilityMs: dispatched - clicked,
        sentToAnsweredMs: sent && answered ? answered - sent : null,
        answeredToVisibleMs: answered ? visible - answered : null,
        totalMs: visible - clicked,
      };
    };
    for (const [size, ending] of [
      [8, 'stop'],
      [16, 'stop'],
      [32, 'stop'],
      [8, 'stop'],
      [16, 'stop'],
      [32, 'close'],
    ] as const) {
      await desk.getByRole('button', { name: 'Open bet', exact: true }).click();
      await expect(desk.getByTestId('betting-status')).toHaveText('OPEN', { timeout: 15000 });
      const mid = (await judgeA.call('GET', `/declarator/events/${eventA.id}/monitor`)).betting
        .match_id as string;
      created[0].fight = mid;
      idsA.add(mid);
      await desk.getByTestId('ghost-interval').fill('1');
      await desk.getByTestId('ghost-start').click();
      await expect(desk.getByTestId('ghost-status')).toHaveText('ACTIVE');
      const before = await judgeA.call('GET', `/declarator/matches/${mid}/book`);
      const button = desk.locator('.dummy-amount[data-side="meron"]').first();
      for (let i = 0; i < size; i++) await button.click({ delay: 0, timeout: 5000 });
      const queueText =
        (await desk
          .getByTestId('ghost-queue')
          .textContent()
          .catch(() => '')) ?? '';
      const timing =
        ending === 'stop'
          ? await timedRequest(
              '/ghost-auto/stop',
              () => desk.getByTestId('ghost-stop').click({ timeout: 30000 }),
              () =>
                expect(desk.getByTestId('ghost-status')).toHaveText('INACTIVE', { timeout: 30000 }),
            )
          : await timedRequest(
              '/betting/close',
              () =>
                desk
                  .getByRole('button', { name: 'Close bet', exact: true })
                  .click({ timeout: 30000 }),
              () =>
                expect(desk.getByTestId('betting-status')).toHaveText('CLOSED', { timeout: 30000 }),
            );
      // Wait until every sent click is decided, then account for every click. The desk counts each
      // exactly once: added, refused (by the server) or cancelled (never sent).
      await expect.poll(() => desk.getByTestId('ghost-queue').count(), { timeout: 30000 }).toBe(0);
      const tally = ((await desk.getByTestId('ghost-tally').textContent()) ?? '').match(
        /(\d+) added · (\d+) refused · (\d+) cancelled/,
      );
      const [added, refused, cancelled] = tally ? tally.slice(1).map(Number) : [-1, -1, -1];
      if (ending === 'stop') {
        await timed('burst-close', async () => {
          await desk.getByRole('button', { name: 'Close bet', exact: true }).click();
          await expect(desk.getByTestId('betting-status')).toHaveText('CLOSED', { timeout: 30000 });
        });
      }
      const after = await judgeA.call('GET', `/declarator/matches/${mid}/book`);
      const manual =
        Number(after.dummy_meron) -
        Number(before.dummy_meron) -
        (Number(after.dummy_wala) - Number(before.dummy_wala));
      const accepted = Math.round(manual / quickMeron);
      if (Math.abs(manual - accepted * quickMeron) > 0.001)
        failures.push(
          `burst ${size}: manual ghost total ${manual} is not a whole number of clicks`,
        );
      if (added !== accepted)
        failures.push(
          `burst ${size}/${ending}: desk says ${added} added, server book has ${accepted}`,
        );
      if (added + refused + cancelled !== size)
        failures.push(
          `burst ${size}/${ending}: ${added} added + ${refused} refused + ${cancelled} cancelled != ${size} clicks`,
        );
      // After Close the server accepts nothing more, from the desk or the API.
      const late = await judgeA.raw('POST', `/declarator/matches/${mid}/dummy-bets`, {
        side: 'meron',
        amount: quickMeron.toFixed(2),
        request_id: crypto.randomUUID(),
      });
      if (late.status() !== 409)
        failures.push(`burst ${size}: ghost bet after close -> ${late.status()}`);
      const latePlayer = await playersA[0].raw('POST', `/portal/matches/${mid}/bets`, {
        side: 'meron',
        amount: min.toFixed(2),
        request_id: crypto.randomUUID(),
      });
      if (latePlayer.status() !== 409)
        failures.push(`burst ${size}: player bet after close -> ${latePlayer.status()}`);
      await desk.waitForTimeout(2000);
      const settledBook = await judgeA.call('GET', `/declarator/matches/${mid}/book`);
      if (settledBook.dummy_meron !== after.dummy_meron || settledBook.version !== after.version)
        failures.push(`burst ${size}: book changed after close`);
      (actions[`${ending}-under-burst`] ??= []).push(timing.totalMs);
      bursts.push({
        size,
        ending,
        queueAtStop: queueText,
        accepted,
        refused,
        cancelled,
        ...timing,
      });
      log({ burst: bursts[bursts.length - 1] });
      await desk.getByRole('button', { name: 'Declare Cancel', exact: true }).click();
      await desk
        .getByRole('dialog')
        .getByLabel('Cancellation reason')
        .fill('Synthetic Stop latency check');
      await desk
        .getByRole('dialog')
        .getByRole('button', { name: 'Cancel fight', exact: true })
        .click();
      await expect(desk.getByRole('button', { name: 'Open bet', exact: true })).toBeEnabled({
        timeout: 30000,
      });
      created[0].fight = undefined;
    }

    // ---- an outdated desk is refused before any work; this desk notices a new deployment ----
    const unversioned = await judgeA.context.fetch('/api/v1/declarator/events', {
      headers: { Authorization: 'Bearer ' + judgeA.token, 'X-ESB-Client': '' },
    });
    if (unversioned.status() !== 426)
      failures.push(`unversioned desk call -> ${unversioned.status()}`);
    await desk.route('**/admin/index.html', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: '<script src="main-NEWBUILD99.js" type="module"></script>',
      }),
    );
    await expect(desk.getByTestId('update-available')).toBeVisible({ timeout: 90000 });
    await desk.unroute('**/admin/index.html');

    // ---- tenant B: its own Declarator runs its own fights (API) ----
    const playerAMid = new Map<Session, number>();
    for (const p of playersA) playerAMid.set(p, await statement(p));
    const bOutcomes = ['meron', 'cancelled'];
    const fightsB: string[] = [];
    for (const outcome of bOutcomes) {
      const control = await judgeB.call('POST', `/declarator/events/${eventB.id}/betting/open`);
      const mid = control.match_id as string;
      fightsB.push(mid);
      idsB.add(mid);
      created[1].fight = mid;
      await judgeB.call('POST', `/declarator/events/${eventB.id}/ghost-auto/start`, {
        meron_amount: '1000.00',
        wala_amount: '1000.00',
        interval_seconds: 1,
      });
      for (let i = 0; i < 6; i++) {
        const p = playersB[i % 2];
        await p.call(
          'POST',
          `/portal/matches/${mid}/bets`,
          { side: i % 2 ? 'wala' : 'meron', amount: '10.00', request_id: crypto.randomUUID() },
          201,
        );
        await judgeB.call('POST', `/declarator/matches/${mid}/dummy-bets`, {
          side: 'wala',
          amount: '500.00',
          request_id: crypto.randomUUID(),
        });
      }
      await judgeB.call('POST', `/declarator/events/${eventB.id}/ghost-auto/stop`);
      await judgeB.call('POST', `/declarator/matches/${mid}/betting/close`);
      await judgeB.call('POST', `/declarator/matches/${mid}/declarations`, {
        request_id: crypto.randomUUID(),
        outcome,
        base_revision: 0,
        reason: outcome === 'cancelled' ? 'Synthetic UAT cancellation' : null,
      });
      const book = await judgeB.call('GET', `/declarator/matches/${mid}/book`);
      expect(book.state).toBe('settled');
      for (const p of playersB)
        await expect
          .poll(() => p.settled.has(mid + ':' + book.settlement.revision), { timeout: 8000 })
          .toBe(true);
      await verifyBets(playersB, mid, book);
      await reconcile(adminB, 'B ' + outcome);
      created[1].fight = undefined;
    }
    // Tenant B activity did not move any tenant A balance.
    for (const p of playersA)
      if ((await statement(p)) !== playerAMid.get(p))
        failures.push(`${p.name}: balance moved during tenant B fights`);

    // ---- isolation: every cross-tenant call is refused, nothing leaks over sockets ----
    const mA = [...idsA].find((x) => x !== eventA.id && x !== fixture.tenant_id)!;
    const mB = fightsB[0];
    const refused = [403, 404];
    const probes: [Session, string, string, unknown][] = [
      [judgeA, 'GET', `/declarator/events/${eventB.id}/monitor`, undefined],
      [judgeA, 'GET', `/declarator/matches/${mB}/book`, undefined],
      [judgeA, 'POST', `/declarator/matches/${mB}/betting/open`, undefined],
      [judgeA, 'POST', `/declarator/matches/${mB}/betting/close`, undefined],
      [
        judgeA,
        'POST',
        `/declarator/matches/${mB}/dummy-bets`,
        { side: 'meron', amount: '1.00', request_id: crypto.randomUUID() },
      ],
      [
        judgeA,
        'POST',
        `/declarator/matches/${mB}/declarations`,
        { request_id: crypto.randomUUID(), outcome: 'meron', base_revision: 0 },
      ],
      [
        judgeA,
        'POST',
        `/declarator/events/${eventB.id}/ghost-auto/start`,
        { meron_amount: '1.00', wala_amount: '1.00', interval_seconds: 5 },
      ],
      [
        playersA[0],
        'POST',
        `/portal/matches/${mB}/bets`,
        // A valid amount for the caller's own tenant: a below-minimum stake is refused first (422).
        { side: 'meron', amount: min.toFixed(2), request_id: crypto.randomUUID() },
      ],
      [playersA[0], 'GET', `/portal/matches/${mB}/book`, undefined],
      [adminA, 'GET', `/admin/events/${eventB.id}`, undefined],
      [adminA, 'POST', `/admin/matches/${mB}/result`, { outcome: 'wala' }],
      [
        adminA,
        'POST',
        '/admin/balance/account-grants',
        { target_user_id: pB1.id, amount: '1.00', idempotency_key: crypto.randomUUID() },
      ],
      [judgeB, 'GET', `/declarator/events/${eventA.id}/monitor`, undefined],
      [
        judgeB,
        'POST',
        `/declarator/matches/${mA}/dummy-bets`,
        { side: 'meron', amount: '1.00', request_id: crypto.randomUUID() },
      ],
      [
        playersB[0],
        'POST',
        `/portal/matches/${mA}/bets`,
        { side: 'meron', amount: minB.toFixed(2), request_id: crypto.randomUUID() },
      ],
      [adminB, 'GET', `/admin/events/${eventA.id}`, undefined],
    ];
    const isolation: string[] = [];
    for (const [who, method, url, data] of probes) {
      const r = await who.raw(method, url, data);
      const row = `${who.name} ${method} ${url.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, ':id')} -> ${r.status()}`;
      isolation.push(row);
      if (!refused.includes(r.status())) failures.push('isolation: ' + row);
    }
    const listedA = await (await playersA[0].raw('GET', '/portal/events')).text();
    const listedB = await (await playersB[0].raw('GET', '/portal/events')).text();
    if (listedA.includes(eventB.id))
      failures.push('isolation: tenant A player lists tenant B event');
    if (listedB.includes(eventA.id))
      failures.push('isolation: tenant B player lists tenant A event');
    let leaked = 0;
    for (const p of playersA)
      for (const f of p.frames) for (const id of idsB) if (f.includes(id)) leaked++;
    for (const p of playersB)
      for (const f of p.frames) for (const id of idsA) if (f.includes(id)) leaked++;
    if (leaked)
      failures.push(`isolation: ${leaked} WebSocket frames carried the other tenant's ids`);

    // ---- wallets: each tenant A player's balance moved by exactly its stakes and returns ----
    let walletDelta = 0;
    for (const p of playersA) walletDelta += (await statement(p)) - (playerStart.get(p) ?? 0);
    const expectedDelta = cents(ledger.returned - ledger.staked);
    // Grants were credited before the start balance was read; the burst/Stop fight had no player bets.
    if (Math.abs(cents(walletDelta) - expectedDelta) > 0.001)
      failures.push(`tenant A wallets moved ${cents(walletDelta)}, bets say ${expectedDelta}`);
    const reconA1 = await reconcile(adminA, 'A after');
    const reconB1 = await reconcile(adminB, 'B after');
    const audit = await adminA.call(
      'GET',
      `/admin/audit-logs?since=${encodeURIComponent(new Date(Date.now() - 3 * 3600e3).toISOString())}&page_size=100`,
    );
    const auditActions = new Set((audit.items as any[]).map((a) => a.action));
    for (const want of ['betting.opened', 'ghost.auto_stopped'])
      if (![...auditActions].some((a) => a === want))
        failures.push(`audit: no ${want} entry for this run`);

    const final = {
      passed: failures.length === 0,
      cycles,
      ledger,
      walletDelta: cents(walletDelta),
      isolation,
      leakedFrames: leaked,
      reconciliation: {
        A: { before: reconA0, after: reconA1 },
        B: { before: reconB0, after: reconB1 },
      },
      actions: Object.fromEntries(
        Object.entries(actions).map(([k, v]) => [
          k,
          { count: v.length, p95: Math.round(p95(v)), max: Math.round(Math.max(...v)) },
        ]),
      ),
      latency: Object.fromEntries(
        Object.entries(latency).map(([k, v]) => [
          k,
          { count: v.length, p95: Math.round(p95(v)), max: Math.round(Math.max(...v)) },
        ]),
      ),
      bursts,
      browser: await sample('final'),
      failures,
    };
    writeFileSync(path.join(out, 'result.json'), JSON.stringify(final, null, 2));
    finalWritten = true;
    expect(failures).toEqual([]);
  } catch (error) {
    writeFileSync(
      path.join(out, finalWritten ? 'error.json' : 'result.json'),
      JSON.stringify({ passed: false, error: safe(error), cycles, failures }, null, 2),
    );
    throw Error(safe(error));
  } finally {
    // Void any unfinished synthetic fight, then end every event this run created.
    for (const c of created) {
      if (!c.fight) continue;
      const judge = c.admin === adminA ? judgeA : judgeB;
      try {
        await judge.raw('POST', `/declarator/matches/${c.fight}/betting/close`);
        // The Declarator voids it: an Admin result is refused on a Declarator-verified fight.
        await judge.call('POST', `/declarator/matches/${c.fight}/declarations`, {
          request_id: crypto.randomUUID(),
          outcome: 'cancelled',
          base_revision: 0,
          reason: 'Interrupted synthetic UAT verification',
        });
      } catch (e) {
        console.error('Synthetic fight cleanup requires review: ' + safe(e));
      }
    }
    for (const c of created) {
      try {
        const r = await c.admin.raw('POST', `/admin/events/${c.event}/complete`);
        if (![200, 422, 409].includes(r.status()))
          console.error('Synthetic event cleanup status ' + r.status());
      } catch (e) {
        console.error('Synthetic event cleanup requires review: ' + safe(e));
      }
    }
    for (const a of actors) await a.stop().catch(() => undefined);
    for (const c of contexts) await c.close().catch(() => undefined);
  }
});
