import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { API, TENANT_SLUG, DEMO_PASSWORD, apiCall, apiLogin } from './helpers';

test('neutral controller, room broadcasts and settlement work over live HTTP and browser WebSocket', async ({ page, request }) => {
  const admin = await apiLogin(request, 'admin');
  const saved = await (await apiCall(request, admin, 'get', '/admin/betting/settings')).json();
  const run = Date.now().toString(36);
  let arenaId = '';
  try {
    expect((await apiCall(request, admin, 'put', '/admin/betting/settings', { enabled: true, starting_balance: '100.00' })).status()).toBe(200);
    const players: string[] = [];
    for (const side of ['a', 'b']) {
      const username = `arcade-${side}-${run}`;
      const created = await apiCall(request, admin, 'post', '/admin/users', {
        username, full_name: `Arcade Team ${side}`, account_type: 'player', password: DEMO_PASSWORD,
      });
      expect(created.status(), await created.text()).toBe(201);
      const token = await apiLogin(request, username);
      players.push(token);
      expect((await apiCall(request, token, 'get', '/portal/betting')).status()).toBe(200);
    }
    const arena = await apiCall(request, admin, 'post', '/simulations/arenas', { name: `Arcade ${run}` });
    expect(arena.status(), await arena.text()).toBe(201);
    arenaId = (await arena.json()).id;
    expect((await apiCall(request, admin, 'patch', `/simulations/arenas/${arenaId}`, { enabled: true })).status()).toBe(200);
    const created = await apiCall(request, admin, 'post', `/simulations/arenas/${arenaId}/matches`, {
      number: 1, team_a: 'Alpha', team_b: 'Beta', simulation_fee_rate: '0.05',
    });
    expect(created.status(), await created.text()).toBe(201);
    let match = await created.json();
    const socketUrl = new URL('/api/ws/simulations', API.replace(/^http/, 'ws'));
    socketUrl.searchParams.set('token', players[0]);
    socketUrl.searchParams.set('tenant', TENANT_SLUG);
    await page.evaluate(({ url, id }) => new Promise<void>((resolve, reject) => {
      const socket = new WebSocket(url);
      const state = window as any;
      state.neutralSocket = socket;
      state.neutralFrames = [];
      socket.onerror = () => reject(new Error('Simulation WebSocket failed'));
      socket.onclose = event => reject(new Error(`Simulation socket closed: ${event.code}`));
      socket.onmessage = event => {
        const frame = JSON.parse(event.data);
        state.neutralFrames.push(frame);
        if (frame.type === 'ERROR') reject(new Error(frame.message || frame.code));
        if (frame.type === 'CONNECTED') socket.send(JSON.stringify({ type: 'SUBSCRIBE', arenaId: id }));
        if (frame.type === 'SUBSCRIBED') resolve();
      };
    }), { url: socketUrl.toString(), id: arenaId });
    const seen = (kind: string) => page.evaluate(type => (window as any).neutralFrames.filter((f: any) => f.type === type), kind);
    const transition = async (target: string, extra: Record<string, unknown> = {}) => {
      match = await (await apiCall(request, admin, 'get', `/simulations/matches/${match.id}`)).json();
      const body = { request_id: randomUUID(), expected_version: match.version, new_state: target, ...extra };
      const r = await apiCall(request, admin, 'post', `/simulations/matches/${match.id}/transition`, body);
      expect(r.status(), await r.text()).toBe(200);
      const result = await r.json();
      match = result.match;
      return { result, body };
    };
    await transition('WAGERING_OPEN');
    await expect.poll(async () => (await seen('MATCH_STATE_CHANGED')).some((f: any) => f.data.newState === 'WAGERING_OPEN')).toBe(true);
    for (let i = 0; i < 2; i++) {
      const wager = await apiCall(request, players[i], 'post', `/simulations/matches/${match.id}/wagers`, {
        request_id: randomUUID(), side: i ? 'SIDE_B' : 'SIDE_A', amount: '10.00',
      });
      expect(wager.status(), await wager.text()).toBe(201);
    }
    await expect.poll(async () => (await seen('ODDS_UPDATED')).some((f: any) => f.data.totalSideA === '10.00' && f.data.totalSideB === '10.00')).toBe(true);
    await transition('FINAL_CALL', { final_call_seconds: 2 });
    await expect.poll(async () => (await seen('COUNTDOWN_TICK')).length).toBeGreaterThan(0);
    const late = await apiCall(request, players[0], 'post', `/simulations/matches/${match.id}/wagers`, {
      request_id: randomUUID(), side: 'SIDE_A', amount: '1.00',
    });
    expect(late.status()).toBe(400);
    await transition('WAGERING_CLOSED');
    await transition('SIMULATION_RUNNING');
    await transition('RESULT_DECLARED', { outcome: 'SIDE_A' });
    const { result, body } = await transition('SETTLED');
    expect(result.settlement.fee_amount).toBe('1.00');
    await expect.poll(async () => (await seen('MATCH_STATE_CHANGED')).some((f: any) => f.data.newState === 'SETTLED')).toBe(true);
    const replay = await apiCall(request, admin, 'post', `/simulations/matches/${match.id}/transition`, body);
    expect((await replay.json()).settlement.id).toBe(result.settlement.id);
    for (const [i, expected] of ['109.00', '90.00'].entries()) {
      const config = await (await apiCall(request, players[i], 'get', '/portal/betting')).json();
      expect(config.wallet.balance).toBe(expected);
    }
    expect((await (await apiCall(request, admin, 'get', '/admin/betting/reconciliation')).json()).balanced).toBe(true);
  } finally {
    await page.evaluate(() => (window as any).neutralSocket?.close()).catch(() => undefined);
    if (arenaId) await apiCall(request, admin, 'patch', `/simulations/arenas/${arenaId}`, { enabled: false });
    await apiCall(request, admin, 'put', '/admin/betting/settings', { enabled: saved.enabled, starting_balance: saved.starting_balance });
  }
});
