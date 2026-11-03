/** Node with native TypeScript stripping (tested v26.12.1); real HTTP/WS and independent DB audit via staging.py.
 * Private fixture and runtime JSON stay outside Git. No production targets or cleanup writes.
 */
import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { cpus, totalmem, platform, release } from 'node:os';
import { performance, monitorEventLoopDelay } from 'node:perf_hooks';

type Actor = { id: string; token: string };
type Tenant = { id: string; slug: string; arenaId: string; admin: Actor; players: Actor[] };
type Match = { id: string; version: number; state: string };
type Fixture = { base: string; runId: string; tenants: Tenant[] };
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const flags = new Map<string, string>();
for (let i = 2; i < process.argv.length; i += 2) flags.set(process.argv[i], process.argv[i + 1] ?? '');
if (flags.has('--help') || !flags.has('--state')) {
  console.log('node scripts/benchmarks/concurrency-settlement.ts --state /tmp/private-run --out docs/benchmarks/run [--pilot true] [--scenario all|boundary|idempotency|precision|websocket] [--ws-update-rounds 20] [--ws-update-interval-ms 100] [--ws-update-concurrency 1]');
  process.exit(flags.has('--help') ? 0 : 2);
}
const state = resolve(flags.get('--state')!);
const out = resolve(flags.get('--out') || `docs/benchmarks/${new Date().toISOString().replace(/[:.]/g, '-')}`);
await mkdir(out, { recursive: true });
if ((await readdir(out)).length) throw new Error('Use a fresh empty output directory; benchmark evidence is never overwritten');
const fixture: Fixture = JSON.parse(await readFile(resolve(state, 'fixture.json'), 'utf8'));
const config = JSON.parse(await readFile(resolve(state, 'runtime.json'), 'utf8'));
if (!/^esabong_bench_[a-z0-9_]+$/.test(fixture.runId) || config.database !== fixture.runId ||
    !['127.0.0.1', 'localhost', '[::1]'].includes(new URL(fixture.base).hostname)) throw new Error('Only the isolated local benchmark fixture is allowed');
const pilot = flags.get('--pilot') === 'true';
const target = { burst: pilot ? 25 : 500, settlements: 20, wagers: pilot ? 50 : 5000,
  sockets: pilot ? 50 : 1000, tenants: 10, wagerConcurrency: pilot ? 4 : 32, socketConcurrency: pilot ? 10 : 25,
  wsUpdateRounds: Number(flags.get('--ws-update-rounds') || 0),
  wsUpdateIntervalMs: Number(flags.get('--ws-update-interval-ms') || 100),
  wsUpdateConcurrency: Number(flags.get('--ws-update-concurrency') || 1) };
if (!Number.isInteger(target.wsUpdateRounds) || target.wsUpdateRounds < 0 || target.wsUpdateRounds > 100 ||
    !Number.isFinite(target.wsUpdateIntervalMs) || target.wsUpdateIntervalMs < 50 || target.wsUpdateIntervalMs > 10000)
  throw new Error('Socket rounds must be 0..100 and interval must be 50..10000 milliseconds');
if (!Number.isInteger(target.wsUpdateConcurrency) || target.wsUpdateConcurrency < 1 || target.wsUpdateConcurrency > 16)
  throw new Error('Socket update concurrency must be 1..16 simultaneous wagers per room');
if (fixture.tenants.length < target.tenants || fixture.tenants[0].players.length < Math.max(target.burst, target.wagers)) throw new Error('Insufficient fixture size');
const selected = flags.get('--scenario') || 'all';
if (!['all','boundary','idempotency','precision','websocket'].includes(selected)) throw new Error('Unknown scenario');
const python = resolve(root, 'api/.venv/bin/python');
const helper = resolve(root, 'scripts/benchmarks/staging.py');
const monitorLog = createWriteStream(resolve(out, 'monitor-errors.log'));
const monitor = spawn(python, [helper, 'monitor', '--state', state, '--out', resolve(out, 'database-telemetry.jsonl')], { cwd: resolve(root, 'api'), stdio: ['ignore', 'ignore', 'pipe'] });
monitor.stderr.pipe(monitorLog);
const loop = monitorEventLoopDelay({ resolution: 10 }); loop.enable();
const nodeLog = createWriteStream(resolve(out, 'generator-telemetry.jsonl'));
let activePhase = 'initial', inFlight = 0;
const phaseMetrics: Record<string, { durations: number[]; statuses: Record<string, number>; codes: Record<string, number>; transportErrors: number; attempts: number }> = {};
const ticks = setInterval(() => nodeLog.write(JSON.stringify({ epoch_ms: Date.now(), phase: activePhase, in_flight: inFlight,
  memory: process.memoryUsage(), cpu: process.cpuUsage(), event_loop_utilization: performance.eventLoopUtilization(),
  event_loop_p99_ms: loop.percentile(99) / 1e6 }) + '\n'), 1000);
ticks.unref();
let interrupted = false;
process.on('SIGINT', () => { interrupted = true; });
const report: any = { run_id: fixture.runId, pilot, started_at: new Date().toISOString(), target,
  target_base: fixture.base, worker_count: config.workers,
  host: { platform: platform(), release: release(), cpus: cpus().length, total_memory_bytes: totalmem(), node: process.version },
  scenarios: {}, limitations: [
    'Generator, API and database run on one shared development host; results are not a production capacity SLA.',
    'JWT issuance, password hashing and synthetic fixture grants are outside the timed workload.',
    'Final call already rejects new wagers. A supplementary open-to-close contest tests admitted requests.',
    'Settlement uses POST /simulations/matches/:id/transition with new_state=SETTLED; there is no /settle alias.',
    'Database lock samples are sampled waits, not a complete wait-time histogram; rollback totals include expected rejections and monitor traffic.',
    'Socket timing starts at a benchmark-only append to a private per-worker trace at the after-commit broadcaster hand-off, before any ODDS_UPDATED coalescing and Redis publish; application wire payloads are not altered by the benchmark.',
    'The socket gate is p95 <50ms commit-to-client, with full retention and delivery; a coalesced odds frame delivers every earlier wanted version of its match. Maximum latency and the former all-deliveries <50ms gate are reported separately.',
  ] };
function quantiles(values: number[]) {
  const ordered = [...values].sort((a,b) => a-b);
  const pct = (p: number) => ordered.length ? ordered[Math.min(ordered.length-1, Math.ceil(ordered.length*p)-1)] : null;
  return { samples: ordered.length, p50_ms: pct(.5), p95_ms: pct(.95), p99_ms: pct(.99), max_ms: ordered.at(-1) ?? null };
}
function metrics() { return phaseMetrics[activePhase] ||= { durations: [], statuses: {}, codes: {}, transportErrors: 0, attempts: 0 }; }
async function request(t: Tenant, actor: Actor, method: string, path: string, body?: any, retry = false): Promise<any> {
  for (let attempt = 0; attempt < 7; attempt++) {
    if (interrupted) throw new Error('Interrupted');
    const stat = metrics(); stat.attempts++; inFlight++;
    const began = performance.now();
    let result: any;
    try {
      const response = await fetch(`${fixture.base}/api/v1/simulations${path}`, { method,
        headers: { Authorization: `Bearer ${actor.token}`, 'X-Tenant-Slug': t.slug, 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(180_000) });
      const data = await response.json();
      stat.statuses[response.status] = (stat.statuses[response.status] || 0) + 1;
      if (data.error?.code) stat.codes[data.error.code] = (stat.codes[data.error.code] || 0) + 1;
      result = { status: response.status, data, elapsed_ms: performance.now() - began };
    } catch (error) {
      stat.transportErrors++;
      result = { status: 0, data: { error: { code: (error as Error).name } }, elapsed_ms: performance.now() - began };
    } finally { stat.durations.push(performance.now() - began); inFlight--; }
    if (retry && attempt < 6 && (result.status === 0 || result.data.error?.code === 'serialization_retry')) {
      await new Promise(r => setTimeout(r, Math.min(50*2**attempt, 1000)));
      continue;
    }
    return result;
  }
}
function okay(r: any, expected = 200) { if (r.status !== expected) throw new Error(`HTTP ${r.status}: ${r.data.error?.code || 'unexpected response'}`); return r.data; }
async function current(t: Tenant, match: Match) { return okay(await request(t, t.admin, 'GET', `/matches/${match.id}`)) as Match; }
async function transition(t: Tenant, match: Match, next: string, extras: any = {}, fixed?: any) {
  const body = fixed || { request_id: randomUUID(), expected_version: match.version, new_state: next, ...extras };
  const r = await request(t, t.admin, 'POST', `/matches/${match.id}/transition`, body, true);
  return okay(r);
}
const nextNumbers = new Map<string,number>();
async function fresh(t: Tenant): Promise<Match> {
  if (!nextNumbers.has(t.arenaId)) {
    let highest=0, page=1;
    while (true) {
      const listing=okay(await request(t,t.admin,'GET',`/arenas/${t.arenaId}/matches?page_size=100&page=${page}`));
      for(const row of listing.items) highest=Math.max(highest,row.number);
      if(page*100>=listing.total) break;
      page++;
    }
    nextNumbers.set(t.arenaId,highest);
  }
  const number=nextNumbers.get(t.arenaId)!+1; nextNumbers.set(t.arenaId,number);
  return okay(await request(t,t.admin,'POST',`/arenas/${t.arenaId}/matches`,{
    number,team_a:'Synthetic Alpha',team_b:'Synthetic Beta',simulation_fee_rate:'0.05',
  }),201);
}
async function finish(t: Tenant, match: Match, outcome = 'CANCELLED') {
  match = await current(t, match);
  if (match.state === 'pending') match = (await transition(t, match, 'WAGERING_OPEN')).match;
  if (['wagering_open','final_call'].includes(match.state)) match = (await transition(t, match, 'WAGERING_CLOSED')).match;
  if (match.state === 'wagering_closed') match = (await transition(t, match, 'SIMULATION_RUNNING')).match;
  if (match.state === 'simulation_running') match = (await transition(t, match, 'RESULT_DECLARED', { outcome })).match;
  return await transition(t, match, 'SETTLED');
}
async function wager(t: Tenant, player: Actor, match: Match, amount: string, side: string, retry = false) {
  return await request(t, player, 'POST', `/matches/${match.id}/wagers`, { request_id: randomUUID(), amount, side }, retry);
}
async function parallel<T,R>(items: T[], concurrency: number, action: (item: T, index: number) => Promise<R>): Promise<R[]> {
  const results: R[] = new Array(items.length); let next = 0;
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (!interrupted) { const i = next++; if (i >= items.length) break; results[i] = await action(items[i], i); }
  }));
  return results;
}
async function helperCall(command: string, extras: string[] = []) {
  return await new Promise<any>((resolveCall, reject) => {
    const child = spawn(python, [helper, command, '--state', state, ...extras], { cwd: resolve(root, 'api'), stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = ''; child.stdout.on('data', d => stdout += d); child.stderr.on('data', d => stderr += d);
    child.on('exit', code => {
      if (code) { writeFile(resolve(out, `${command}-error.log`), stderr).catch(() => undefined); reject(new Error(`Database helper ${command} failed; see private/local error log`)); }
      else { try { resolveCall(JSON.parse(stdout.trim().split('\n').at(-1)!)); } catch { reject(new Error('Invalid helper output')); } }
    });
  });
}
async function audit(label: string) { return await helperCall('audit', ['--out', out, '--label', label]); }
async function checkpoint() { await writeFile(resolve(out, 'report.partial.json'), JSON.stringify(report, null, 2)); }
async function scenario(name: string, action: () => Promise<any>) {
  if (selected !== 'all' && selected !== name) return;
  activePhase = name;
  const began = performance.now(), startEpoch = Date.now();
  console.log(JSON.stringify({ phase: name, event: 'start' }));
  let result: any;
  try { result = await action(); }
  catch (error) { result = { pass: false, error: (error as Error).message }; }
  const stat = metrics();
  const elapsed = (performance.now()-began)/1000;
  report.scenarios[name] = { ...result, started_epoch_ms: startEpoch, ended_epoch_ms: Date.now(), seconds: elapsed,
    http: { attempts: stat.attempts, statuses: stat.statuses, error_codes: stat.codes, transport_errors: stat.transportErrors,
      attempt_rps: stat.attempts/elapsed, latency: quantiles(stat.durations) } };
  await checkpoint();
  console.log(JSON.stringify({ phase: name, event: 'complete', pass: result.pass, seconds: elapsed }));
}
const primary = fixture.tenants[0];
try {
  let ready = false;
  for (let attempt = 0; attempt < 30; attempt++) {
    try { ready = (await fetch(`${fixture.base}/api/ready`, { signal: AbortSignal.timeout(2000) })).ok; } catch {}
    if (ready) break;
    await new Promise(r => setTimeout(r, 1000));
  }
  if (!ready) throw new Error('Staging API is not ready');
  await scenario('boundary', async () => {
    let match = await fresh(primary);
    match = (await transition(primary, match, 'WAGERING_OPEN')).match;
    match = (await transition(primary, match, 'FINAL_CALL', { final_call_seconds: 30 })).match;
    let release!: () => void; const barrier = new Promise<void>(r => release = r);
    const dispatch: number[] = [];
    const jobs = primary.players.slice(0, target.burst).map(player => (async () => { await barrier; dispatch.push(performance.now()); return wager(primary, player, match, '0.01', 'SIDE_A'); })());
    const closing = (async () => { await barrier; return transition(primary, match, 'WAGERING_CLOSED'); })();
    release();
    const responses = await Promise.all(jobs); await closing;
    const rejected = responses.filter(r => r.status === 400 && r.data.error?.code === 'wagering_closed').length;
    const check = await helperCall('probe', ['--match', match.id]);
    await finish(primary, match);
    // Supplement the requested (already closed-to-new-wagers) final-call race with real ingress.
    let contested = await fresh(primary);
    contested = (await transition(primary, contested, 'WAGERING_OPEN')).match;
    let go!: () => void; const gate = new Promise<void>(r => go = r);
    const entering = primary.players.slice(0, target.burst).map(player => (async () => { await gate; return wager(primary, player, contested, '0.01', 'SIDE_A'); })());
    const closeJob = (async () => {
      await gate;
      for (let i=0;i<12;i++) {
        const snapshot = await current(primary, contested);
        const r = await request(primary, primary.admin, 'POST', `/matches/${contested.id}/transition`, {
          request_id: randomUUID(), expected_version: snapshot.version, new_state: 'WAGERING_CLOSED',
        }, true);
        if (r.status === 200) return { attempts: i+1, snapshot: await helperCall('probe', ['--match', contested.id]) };
        if (r.status !== 409) throw new Error(`Close failed with HTTP ${r.status}`);
      }
      throw new Error('Close could not acquire a fresh version within 12 attempts');
    })();
    go();
    const contestedResponses = await Promise.all(entering);
    const closed = await closeJob;
    const after = await helperCall('probe', ['--match', contested.id]);
    const late = await parallel(primary.players.slice(0, 20), 20, p => wager(primary, p, contested, '0.01', 'SIDE_A'));
    await finish(primary, contested);
    const auditResult = await audit('boundary');
    const cleanRejections = responses.filter(r => [400,409].includes(r.status)).length;
    const statusCounts = (rows:any[]) => rows.reduce((counts:Record<string,number>,r:any)=>{
      counts[r.status]=(counts[r.status]||0)+1; return counts;
    },{});
    return { pass: cleanRejections === target.burst && check.insert_rows === 0 && check.update_rows === 0 && check.stakes === 0 &&
      contestedResponses.every(r => [201,400,409].includes(r.status)) && closed.snapshot.stakes === after.stakes &&
      late.every(r => [400,409].includes(r.status)) && auditResult.pass,
      final_call: { requested: target.burst, rejected, clean_rejections:cleanRejections, statuses:statusCounts(responses),
        dispatch_spread_ms: Math.max(...dispatch)-Math.min(...dispatch), db_guard: check },
      contested_open_to_close: { accepted: contestedResponses.filter(r=>r.status===201).length,
        rejected: contestedResponses.filter(r=>[400,409].includes(r.status)).length, other: contestedResponses.filter(r=>![201,400,409].includes(r.status)).length,
        statuses:statusCounts(contestedResponses),
        close_attempts: closed.attempts, stakes_at_close_ack: closed.snapshot.stakes, stakes_after_drain: after.stakes,
        late_rejections: late.filter(r=>r.status===400).length, late_sentinels: late.length }, audit: auditResult };
  });
  await scenario('idempotency', async () => {
    let match = await fresh(primary); match = (await transition(primary, match, 'WAGERING_OPEN')).match;
    okay(await wager(primary, primary.players[0], match, '12.34', 'SIDE_A'), 201);
    okay(await wager(primary, primary.players[1], match, '56.78', 'SIDE_B'), 201);
    match = await current(primary, match);
    for (const next of ['WAGERING_CLOSED','SIMULATION_RUNNING']) match = (await transition(primary, match, next)).match;
    match = (await transition(primary, match, 'RESULT_DECLARED', { outcome: 'SIDE_A' })).match;
    const start = performance.now();
    const results = await Promise.all(Array.from({ length: target.settlements }, () => request(primary, primary.admin, 'POST', `/matches/${match.id}/transition`, {
      request_id: randomUUID(), expected_version: match.version, new_state: 'SETTLED',
    })));
    const ids = new Set(results.map(r=>r.data.settlement?.id).filter(Boolean));
    const commits = results.filter(r=>r.status===200 && r.data.replayed===false).length;
    const replays = results.filter(r=>r.status===200 && r.data.replayed===true).length;
    const batchSeconds=(performance.now()-start)/1000;
    const audited = await audit('idempotency');
    return { pass: ids.size===1 && commits===1 && replays===19 && audited.pass,
      requests: target.settlements, new_settlements: commits, receipt_replays: replays, unique_receipts: ids.size,
      batch_seconds: batchSeconds, audit: audited };
  });
  await scenario('precision', async () => {
    let seed = 0x52e14f0b;
    const next = () => (seed = (Math.imul(seed,1664525) + 1013904223) >>> 0);
    const stakes = primary.players.slice(0,target.wagers).map((player,i) => {
      let cents = 1+next()%99999; if (cents%100===0) cents++;
      return { player, side: i%2 ? 'SIDE_B':'SIDE_A', amount: `${Math.floor(cents/100)}.${(cents%100).toString().padStart(2,'0')}` };
    });
    const outcomes: any[] = [];
    for (const outcome of ['SIDE_A','SIDE_B','DRAW','CANCELLED']) {
      let match = await fresh(primary); match = (await transition(primary, match, 'WAGERING_OPEN')).match;
      const start = performance.now(); let completed=0;
      const results = await parallel(stakes, target.wagerConcurrency, async line => {
        const r = await wager(primary, line.player, match, line.amount, line.side, true);
        if (++completed%1000===0) console.log(JSON.stringify({ phase: 'precision', outcome, completed, target: target.wagers }));
        return r;
      });
      const wagerSeconds = (performance.now()-start)/1000;
      const successes = results.filter(r=>r.status===201).length;
      const settleStart = performance.now();
      const result = await finish(primary, match, outcome);
      const settlementSeconds = (performance.now()-settleStart)/1000;
      const audited = await audit(`precision-${outcome.toLowerCase()}`);
      const round = audited.settlements.find((s:any)=>s.match_id===match.id);
      outcomes.push({ outcome, requested_wagers: target.wagers, accepted_wagers: successes, wager_seconds: wagerSeconds,
        unique_wager_tps: successes/wagerSeconds, settlement_seconds: settlementSeconds,
        receipt_id: result.settlement.id, audit: round, global_wallet_mismatches: audited.wallet_mismatches,
        pass: successes===target.wagers && round?.stakes===target.wagers && round?.distinct_players===target.wagers && audited.pass });
      await writeFile(resolve(out,'precision-progress.json'), JSON.stringify(outcomes,null,2));
    }
    return { pass: outcomes.every(o=>o.pass), deterministic_seed: '0x52e14f0b', outcomes };
  });
  await scenario('websocket', async () => {
    const tenants = fixture.tenants.slice(0,target.tenants);
    const matches = await parallel(tenants, 10, t => fresh(t));
    const sockets: WebSocket[] = [];
    const expectedRooms = tenants.map(t=>`tenant:${t.id}:arena:${t.arenaId}`);
    type Delivery = {key:string; room:string; type:string; match:string; version:number; client:number; epoch_ms:number};
    const records: Delivery[] = [];
    const wanted = new Map<string,number>();
    const groups = new Map<string,string>();
    const seenKeys = new Map<string,number>();
    const joinLatency: number[] = [];
    let failures=0, closed=0, leaks=0, duplicateFrames=0, observing=true;
    const closeCodes: Record<string,number> = {};
    const retention: {epoch_ms:number;open:number}[] = [];
    let retentionTimer: ReturnType<typeof setInterval> | undefined;
    const socketJobs = Array.from({length: target.sockets}, (_,i)=>({ client:i, tenantIndex:i%tenants.length, playerIndex: Math.floor(i/tenants.length) }));
    const started = performance.now();
    try {
      const joins = await parallel(socketJobs, target.socketConcurrency, job => new Promise<boolean>(resolveJoin => {
        const t = tenants[job.tenantIndex], player = t.players[job.playerIndex], expected = expectedRooms[job.tenantIndex];
        const url = new URL('/api/ws/simulations', fixture.base.replace(/^http/,'ws'));
        url.searchParams.set('tenant', t.slug); url.searchParams.set('token', player.token);
        const began = performance.now(), ws = new WebSocket(url); sockets.push(ws);
        let done=false, subscribed=false;
        const timeout = setTimeout(()=>{ failures++; done=true; ws.close(); resolveJoin(false); }, 45_000);
        const end = (ok:boolean) => { if (!done) { done=true; clearTimeout(timeout); if(!ok) failures++; resolveJoin(ok); } };
        ws.addEventListener('message', event => {
          let frame: any; try { frame=JSON.parse(String(event.data)); } catch { failures++; return; }
          if(frame.type==='CONNECTED') ws.send(JSON.stringify({type:'SUBSCRIBE',arenaId:t.arenaId}));
          if(frame.type==='ERROR') end(false);
          if(frame.type==='SUBSCRIBED') { subscribed=true; joinLatency.push(performance.now()-began); end(frame.room===expected); }
          if(['MATCH_STATE_CHANGED','ODDS_UPDATED'].includes(frame.type)) {
            if(frame.room!==expected) leaks++;
            const key = `${frame.room}|${frame.type}|${frame.data.matchId}|${frame.data.version}`;
            const perClient = `${job.tenantIndex}:${job.playerIndex}|${key}`;
            if(seenKeys.has(perClient)) duplicateFrames++;
            seenKeys.set(perClient,1);
            records.push({key,room:frame.room,type:frame.type,match:frame.data.matchId,version:frame.data.version,
              client:job.client,epoch_ms:performance.timeOrigin+performance.now()});
          }
        });
        ws.addEventListener('error', ()=>end(false));
        ws.addEventListener('close', event=>{
          if(observing) closeCodes[event.code]=(closeCodes[event.code]||0)+1;
          if(observing && subscribed) closed++; end(false);
        });
      }));
      const openAtBarrier = sockets.filter(s=>s.readyState===WebSocket.OPEN).length;
      const sampleRetention = ()=>retention.push({epoch_ms:Date.now(),open:sockets.filter(s=>s.readyState===WebSocket.OPEN).length});
      sampleRetention(); retentionTimer=setInterval(sampleRetention,1000); retentionTimer.unref();
      console.log(JSON.stringify({phase:'websocket', subscribed:joins.filter(Boolean).length, open:openAtBarrier, target:target.sockets}));
      await new Promise(r=>setTimeout(r, 10000));
      await parallel(tenants, 10, async (t,i)=>{
        let match = matches[i];
        wanted.set(`${expectedRooms[i]}|MATCH_STATE_CHANGED|${match.id}|${match.version+1}`,target.sockets/tenants.length);
        groups.set(`${expectedRooms[i]}|MATCH_STATE_CHANGED|${match.id}|${match.version+1}`,'baseline');
        match = (await transition(t,match,'WAGERING_OPEN')).match;
        wanted.set(`${expectedRooms[i]}|ODDS_UPDATED|${match.id}|${match.version+1}`,target.sockets/tenants.length);
        groups.set(`${expectedRooms[i]}|ODDS_UPDATED|${match.id}|${match.version+1}`,'baseline');
        okay(await wager(t,t.players[0],match,'1.23','SIDE_A',true),201);
        match=await current(t,match);
        wanted.set(`${expectedRooms[i]}|ODDS_UPDATED|${match.id}|${match.version+1}`,target.sockets/tenants.length);
        groups.set(`${expectedRooms[i]}|ODDS_UPDATED|${match.id}|${match.version+1}`,'baseline');
        okay(await wager(t,t.players[1],match,'4.56','SIDE_B',true),201);
        matches[i]=await current(t,match);
      });
      // Coalesced ODDS_UPDATED packets carry the newest version of a burst, so a wanted odds version is
      // delivered to a client by its first odds frame for that match at or after that version. State
      // changes are never coalesced and must arrive exactly. Each (client, wanted version) counts once.
      const satisfy = () => {
        // Linear pass: per (client, match) a cursor walks that match's wanted odds versions in
        // ascending order, so one frame satisfies every not-yet-delivered version at or below it.
        const odds=new Map<string,{key:string;version:number}[]>(), exact=new Set<string>();
        for(const key of wanted.keys()) {
          const [room,type,match,version]=key.split('|');
          if(type!=='ODDS_UPDATED') { exact.add(key); continue; }
          const slot=`${room}|${match}`;
          if(!odds.has(slot)) odds.set(slot,[]);
          odds.get(slot)!.push({key,version:Number(version)});
        }
        for(const list of odds.values()) list.sort((a,b)=>a.version-b.version);
        const cursor=new Map<string,number>(), seen=new Set<string>(), result: (Delivery & {frame_version:number})[]=[];
        for(const row of records) {
          if(row.type!=='ODDS_UPDATED') {
            const id=`${row.client}|${row.key}`;
            if(exact.has(row.key) && !seen.has(id)) { seen.add(id); result.push({...row,frame_version:row.version}); }
            continue;
          }
          const slot=`${row.room}|${row.match}`, list=odds.get(slot);
          if(!list) continue;
          const at=`${row.client}|${slot}`;
          let next=cursor.get(at) ?? 0;
          while(next<list.length && list[next].version<=row.version) { result.push({...row,key:list[next].key,frame_version:row.version}); next++; }
          cursor.set(at,next);
        }
        return result;
      };
      const waitForDeliveries = async (group?:string) => {
        const until=Date.now()+15_000;
        while(Date.now()<until) {
          const counts=new Map<string,number>(); for(const d of satisfy()) counts.set(d.key,(counts.get(d.key)||0)+1);
          if([...wanted].filter(([key])=>!group || groups.get(key)===group).every(([key,n])=>(counts.get(key)||0)>=n)) break;
          await new Promise(r=>setTimeout(r,100));
        }
      };
      await waitForDeliveries('baseline');
      const updatesStarted=performance.now();
      const updateDispatch: number[]=[];
      if(target.wsUpdateRounds) {
        console.log(JSON.stringify({phase:'websocket',event:'sustained-start',rounds_per_room:target.wsUpdateRounds}));
        await parallel(tenants,10,async(t,i)=>{
          let match=matches[i];
          const began=performance.now();
          for(let round=0;round<target.wsUpdateRounds;round++) {
            const due=began+round*target.wsUpdateIntervalMs;
            if(performance.now()<due) await new Promise(r=>setTimeout(r,due-performance.now()));
            const width=target.wsUpdateConcurrency;
            const results=await Promise.all(Array.from({length:width},(_,k)=>{
              updateDispatch.push(performance.now());
              return wager(t,t.players[2+(round*width+k)%98],match,'0.01',(round+k)%2?'SIDE_B':'SIDE_A',true);
            }));
            for(const r of results) {
              const placed=okay(r,201).match as Match;
              const key=`${expectedRooms[i]}|ODDS_UPDATED|${placed.id}|${placed.version}`;
              wanted.set(key,target.sockets/tenants.length); groups.set(key,'sustained');
              if(placed.version>match.version) match=placed;
            }
          }
          matches[i]=match;
        });
      }
      const updateSeconds=(performance.now()-updatesStarted)/1000;
      await waitForDeliveries();
      sampleRetention();
      const openAfterBroadcast = sockets.filter(s=>s.readyState===WebSocket.OPEN).length;
      const committed = new Map<string,number>(), published = new Map<string,number>();
      for(const name of await readdir(state)) if(/^publisher-\d+\.jsonl$/.test(name)) {
        for(const line of (await readFile(resolve(state,name),'utf8')).trim().split('\n').filter(Boolean)) {
          const row=JSON.parse(line);
          const key=`${row.room}|${row.type}|${row.match_id}|${row.version}`;
          const into=(row.stage ?? 'published')==='committed' ? committed : published;
          if(!into.has(key)) into.set(key,row.epoch_ms);
        }
      }
      const counts=new Map<string,number>();
      const latencies: number[]=[];
      const groupLatencies: Record<string,number[]>={baseline:[],sustained:[]};
      let superseded=0;
      const oddsFrames=records.filter(r=>r.type==='ODDS_UPDATED').length;
      const log = createWriteStream(resolve(out,'websocket-deliveries.jsonl'));
      for(const row of satisfy()) {
        counts.set(row.key,(counts.get(row.key)||0)+1);
        if(Number(row.key.split('|')[3])!==row.frame_version) superseded++;
        const start=committed.get(row.key) ?? published.get(row.key), latency=start===undefined?null:row.epoch_ms-start;
        if(latency!==null) { latencies.push(latency); groupLatencies[groups.get(row.key)!].push(latency); }
        log.write(JSON.stringify({...row,group:groups.get(row.key),committed_epoch_ms:committed.get(row.key),
          published_epoch_ms:published.get(row.key),latency_ms:latency})+'\n');
      }
      await new Promise<void>(r=>log.end(r));
      const missing=[...wanted].reduce((sum,[key,n])=>sum+Math.max(0,n-(counts.get(key)||0)),0);
      const latency = quantiles(latencies);
      await writeFile(resolve(out,'websocket-retention.json'),JSON.stringify(retention,null,2));
      const groupResults=Object.fromEntries(['baseline','sustained'].map(group=>[group,{
        latency:quantiles(groupLatencies[group]),
        expected_deliveries:[...wanted].filter(([key])=>groups.get(key)===group).reduce((n,[,count])=>n+count,0),
        missing_deliveries:[...wanted].filter(([key])=>groups.get(key)===group).reduce((n,[key,count])=>n+Math.max(0,count-(counts.get(key)||0)),0),
      }]));
      return { pass: openAtBarrier===target.sockets && openAfterBroadcast===target.sockets && failures===0 && closed===0 &&
        leaks===0 && missing===0 && duplicateFrames===0 && latencies.length===[...wanted.values()].reduce((a,b)=>a+b,0) &&
        latencies.every(ms=>ms>=-2) && latency.p95_ms!==null && latency.p95_ms<50 &&
        retention.every(sample=>sample.open===target.sockets),
        attempted_connections:target.sockets, subscribed:joins.filter(Boolean).length, open_at_barrier:openAtBarrier,
        open_after_broadcast:openAfterBroadcast, tenants:tenants.length, connections_per_tenant:target.sockets/tenants.length,
        connection_failures:failures, closed_during_measurement:closed, close_codes:closeCodes,
        minimum_open_after_barrier:Math.min(...retention.map(sample=>sample.open)),
        leakage_frames:leaks, duplicate_frames:duplicateFrames,
        expected_deliveries:[...wanted.values()].reduce((a,b)=>a+b,0), received_deliveries:latencies.length, missing_deliveries:missing,
        latency, groups:groupResults, legacy_every_delivery_under_50ms:latencies.every(ms=>ms>=-2 && ms<50),
        latency_start:committed.size ? 'after-commit hand-off, before coalescing and Redis publish' : 'immediately before Redis publish',
        coalescing:{wanted_versions:wanted.size,published_events:[...wanted.keys()].filter(k=>published.has(k)).length,
          odds_frames_received:oddsFrames,deliveries_satisfied_by_newer_version:superseded},
        sustained_updates:{rounds_per_room:target.wsUpdateRounds,completed_wagers:updateDispatch.length,
          interval_target_ms:target.wsUpdateIntervalMs,concurrency_per_room:target.wsUpdateConcurrency,seconds:updateSeconds,
          achieved_updates_per_second:updateSeconds?updateDispatch.length/updateSeconds:0},
        threshold_ms:50, threshold_policy:'p95 commit-to-client delivery <50ms (includes any coalescing delay); full retention and no missing deliveries',
        join_latency:quantiles(joinLatency), wall_seconds:(performance.now()-started)/1000 };
    } finally {
      if(retentionTimer) clearInterval(retentionTimer);
      observing=false;
      for(const ws of sockets) { try { ws.close(); } catch {} }
      await new Promise(r=>setTimeout(r,3000));
      await parallel(tenants, 2, async(t,i)=>{ try { await finish(t,matches[i]); } catch { /* Failed cleanup is visible in the final audit/active-state snapshot. */ } });
    }
  });
  activePhase='final-audit';
  report.final_audit=await audit('final');
} catch(error) { report.fatal_error=(error as Error).message; }
finally {
  clearInterval(ticks); loop.disable(); nodeLog.end();
  monitor.kill('SIGTERM'); monitor.stderr.unpipe(monitorLog); monitorLog.end();
  report.completed_at=new Date().toISOString(); report.interrupted=interrupted;
  report.generator={ memory:process.memoryUsage(),resource_usage:process.resourceUsage(), cpu:process.cpuUsage(),
    event_loop:{p50_ms:loop.percentile(50)/1e6,p95_ms:loop.percentile(95)/1e6,p99_ms:loop.percentile(99)/1e6,max_ms:loop.max/1e6} };
  try {
    const samples=(await readFile(resolve(out,'database-telemetry.jsonl'),'utf8')).split('\n').filter(Boolean).map(line=>JSON.parse(line));
    const first=samples[0], last=samples.at(-1);
    report.database_metrics={ samples:samples.length, peak_connections:Math.max(...samples.map(s=>s.connections)),
      peak_lock_waiters:Math.max(...samples.map(s=>s.lock_waiting)), peak_ungranted_locks:Math.max(...samples.map(s=>s.waiting_locks)),
      peak_api_rss_sum_bytes:Math.max(...samples.map(s=>s.api_rss_sum_bytes)), peak_api_cpu_pct_sum:Math.max(...samples.map(s=>s.api_cpu_pct_sum)),
      max_query_age_ms:Math.max(...samples.map(s=>s.max_query_age_ms)),
      commits_delta:last.xact_commit-first.xact_commit, rollbacks_delta:last.xact_rollback-first.xact_rollback,
      deadlocks_delta:last.deadlocks-first.deadlocks, temp_bytes_delta:last.temp_bytes-first.temp_bytes };
    for(const [name,result] of Object.entries(report.scenarios) as [string,any][]) {
      const subset=samples.filter(s=>s.epoch_ms>=result.started_epoch_ms && s.epoch_ms<=result.ended_epoch_ms);
      result.database={ samples:subset.length, peak_lock_waiters:Math.max(0,...subset.map(s=>s.lock_waiting)),
        peak_api_rss_sum_bytes:Math.max(0,...subset.map(s=>s.api_rss_sum_bytes)),
        query_age:quantiles(subset.map(s=>Number(s.max_query_age_ms))) };
    }
  } catch { report.telemetry_error='No readable database samples'; }
  report.pass=!report.fatal_error && !interrupted && Object.values(report.scenarios).every((s:any)=>s.pass) && report.final_audit?.pass;
  await writeFile(resolve(out,'report.json'),JSON.stringify(report,null,2));
  const rows=Object.entries(report.scenarios).map(([name,value]:[string,any])=>`| ${name} | ${value.pass?'PASS':'FAIL'} | ${value.seconds.toFixed(2)} | ${value.http.attempt_rps.toFixed(2)} | ${value.http.latency.p95_ms?.toFixed(2)} |`);
  const summary=`# Synthetic concurrency benchmark\n\nRun: ${fixture.runId}. Pilot: ${pilot}. Workers: ${config.workers}.\n\n`+
    `| Scenario | Verdict | Seconds | HTTP attempts/s | HTTP p95 ms |\n|---|---|---:|---:|---:|\n${rows.join('\n')}\n\n`+
    `Overall: **${report.pass?'PASS':'FAIL'}**. A failed gate is not silently relaxed.\n\n`+
    `See [report.json](report.json), database/generator telemetry, websocket-deliveries.jsonl and per-player accounting JSONL logs. `+
    `HTTP attempts/s includes setup and expected rejections; precision outcomes report successful unique wager TPS separately.\n\n`+
    report.limitations.map((l:string)=>`- ${l}`).join('\n')+'\n';
  await writeFile(resolve(out,'README.md'),summary);
  console.log(JSON.stringify({event:'finished',pass:report.pass,report:resolve(out,'report.json')}));
  process.exit(report.pass?0:1);
}
