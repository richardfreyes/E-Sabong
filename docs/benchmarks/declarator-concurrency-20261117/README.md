# Declarator ghost-bet concurrency and desk request load (2026-11-17)

Release `uat-20261117-rc.1` = `253cde9`, schema unchanged at `0040`. UAT only; production untouched.

## 1. Root causes (measured, not assumed)

| # | Finding | Evidence |
| --- | --- | --- |
| 1 | **UAT's API is CPU-starved, not DB-bound.** The API container is capped at **1.0 CPU** (`NanoCpus=1e9`) for 2 uvicorn workers on a 12-CPU / 48 GB host. With a desk open it ran at 86% CPU and was **CFS-throttled in 66% of scheduler periods** (85 of 129 in a 10 s window) while serving only ~3 requests/s. PostgreSQL was idle: 1 active backend, 0 lock waiters, 0 deadlocks, tiny tables (8k audit rows, 252 fights). Every endpoint had p50 300–760 ms (CPU queueing). This is why UAT is slow and local is fast. | `cpu.stat` and `pg_stat_activity`, read-only, before the deploy |
| 2 | **Desk reads dominated the CPU.** Live UAT traffic over 10 min: `book` 300, `online` 234, `ghost-auto` 167, `monitor` 106, `records` 106, versus 104 actual `dummy-bets`, so desk reads were 68% of tenant requests. Every ghost click and every `book.updated` frame (one per click, per auto tick, per player bet) re-fetched the full book; `online` reloaded on every bet-count change; a 30 s timer reloaded events + monitor + records + ghost-auto + online; a 15 s timer reloaded ghost-auto + online. | UAT request log breakdown, `store.ts` |
| 3 | **Every ghost write took the event row `FOR UPDATE`** (`_betting_desk`), then the fight row, and held them for ~12 round-trips (assignment checks, receipt, audit, snapshot, broadcast). Stop, Close, Declare, auto start/stop and every auto tick need the same row, so all betting writes on an event ran one at a time, and Stop waited behind every in-flight click. On a CPU-starved worker each held lock is stretched by event-loop queueing. | `pg_stat_activity`: a 32-click burst puts **26 backends in lock wait, oldest 238 ms**, locally on 1 CPU |
| 4 | Residual lock time after (3) was the holder **`idle in transaction` after its UPDATE**: finished in PostgreSQL, waiting for its own coroutine to get CPU and send COMMIT. | lock probe: waiters blocked on `transactionid`, blocker `idle in transaction` |
| 5 | Three Starlette `BaseHTTPMiddleware` layers wrapped every request (a task group and memory stream each). | cProfile; A/B −14% CPU per request after removal |
| 6 | Not causes: HTTP/1.1 connection limits (UAT is **HTTP/2**), DB pool saturation (40–51 connections, max 60), indexes (all hot queries are PK/unique lookups). `xact_rollback ≈ xact_commit` on UAT is the pool's reset-on-return ROLLBACK (one cheap round-trip per request). | `curl --http2`, `pg_stat_database` |

## 2. What changed

**Locking (the root fix).** Manual and auto ghost bets lock neither the event nor the fight row
(`betting.add_dummy_stakes`). Order: receipt `INSERT … ON CONFLICT DO NOTHING` (its FK takes the fight
row `FOR KEY SHARE`), then the audit row, then **one atomic `UPDATE fight_books … WHERE state = 'open'` as
the last statement before COMMIT**. Manual clicks commit immediately after it. So:

- Concurrent clicks overlap everywhere except that single statement; the book row is held from UPDATE to COMMIT only.
- Close, Declare, settlement and cancellation keep their exclusive event lock and move the book out of OPEN under the
  book's row lock. A ghost that commits first is in the frozen pool; one arriving after the close matches no row and
  rolls back together with its receipt and audit row. Nothing can be accepted after the authoritative close.
- Lock order fight (key share) → book → job never reverses Close's event → fight → book → job, so no deadlock.
- Idempotency is per click: one receipt per `request_id`; replays (including after Close) are answered, never re-added or refused.
- The auto runner sends both sides in one statement, then claims the run with a conditional job UPDATE (`runs` unchanged,
  still active, still due); a superseded run rolls back entirely.

**Desk (Angular).**
- Bounded concurrency: `GHOST_CONCURRENCY = 6` in flight, click-order dispatch, any-order completion (a bounded `mergeMap`; a
  slow click never holds back an unrelated one). Halt semantics unchanged: Stop/Close block new clicks, cancel unsent ones
  visibly and go out at once; every click ends exactly once (added · refused · cancelled).
- A lost answer (connection drop, 502/503/504) is resent with the **same** idempotency key, up to twice; server refusals are never resent.
- Ghost answers and `book.updated` frames patch the current book in place. With an unchanged bet count every pool difference is
  ghost money, so the ghost split is derived in exact cents; the Declarator's own answer carries it explicitly. Players' frames
  never contain the split. New player bets show totals/odds at once and reload player rows at most every 750 ms.
- No periodic events/monitor/records reload while the socket is open (2-minute canonical check); full fallback polling every 15 s only
  while disconnected; ghost-auto polled only while a job runs; user actions reconcile immediately.

**API.** Pure-ASGI middleware; every response carries `Server-Timing: app;dur=…, db;dur=…;desc="N queries"` (visible in DevTools
→ Network → Timing, so browser queueing, network, app CPU and SQL time can be told apart on UAT), also written to the request log.

## 3. Local results (UAT-equivalent container: 1 CPU, 640 MiB, 2 workers; background player bets + desk reads)

`api/before.json` (HEAD `17b0f53`) and `api/after.json` (`253cde9`), run back to back by `scripts/benchmarks/declarator_concurrency.py`
(5 rounds per scenario, fresh fight each round). Lock waits sampled from `pg_stat_activity` every 20 ms.

| Scenario | Before p50 / p95 | After p50 / p95 | Lock waiters peak / oldest |
| --- | ---: | ---: | --- |
| 1 click (×20) | 19 / 34 ms | 15 / 28 ms | – |
| 8 simultaneous | 47 / 70 ms | 32 / 56 ms | 7 / 44 ms → 1 / 1 ms |
| 16 simultaneous | 84 / 122 ms | 60 / 86 ms | 13 / 83 → 10 / 29 ms |
| 32 simultaneous | 221 / 385 ms | 100 / 147 ms | 26 / 238 → 7 / 43 ms |
| 16 Meron + 16 Wala | 196 / 353 ms | 156 / 176 ms | 25 / 228 → 9 / 64 ms |
| 32 manual while auto runs | p95 389 ms | p95 205 ms | 24 / 232 → 14 / 60 ms |
| Stop auto during 32-burst | 119 / 250 ms | 76 / 90 ms | 27 / 239 → 17 / 103 ms |
| Close during 32-burst (30 ms in) | 176 / 297 ms | 111 / 212 ms | 28 / 226 → 16 / 118 ms |
| Close during 32-burst (120 ms in) | 191 / 285 ms | 64 / 103 ms | 29 / 222 → 16 / 86 ms |
| Declare right after Close | 47 / 53 ms | 52 / 57 ms | – |
| Two Declarator sessions, 32 clicks | 231 / 387 ms | 117 / 142 ms | 29 / 297 → 8 / 24 ms |
| Two tenants, 32 each at once (A / B p95) | 546 / 579 ms | 456 / 359 ms | – |
| API CPU for the whole run | 15.6 s | 13.1 s | |

Every round of every scenario: accepted clicks = book ghost delta = receipts in the database; refusals only `409 betting_closed`
(Close scenarios); the book unchanged 1 s after Close; settlement correct; both tenants' reconciliations balanced; 0 cross-tenant
WebSocket frames. 32 simultaneous requests on one CPU cannot finish faster than their total CPU (~150 ms wall), which is the floor
at that size; at the desk's 6 in flight there is no lock wait at all.

**Browser** (`browser/*.json`, `web/e2e/performance/declarator-requests.spec.ts`, production Angular build, no interception;
60 s with auto ghost each second and 3 players betting ~1 bet/s):

| | Before | After |
| --- | ---: | ---: |
| Desk API requests / minute | 138 | **79** |
| `book` / `online` / `ghost-auto` | 87 / 39 / 6 | 39 / 36 / 4 |
| periodic `events` + `monitor` + `records` | 6 | 0 |
| Peak API requests pending in the browser | 11 | 3 |
| Non-ghost requests during a 32-click burst | 6–10 | 0–5 |
| Click → request start (all bursts) p95 | 34–38 ms | 34–39 ms |
| Stop click → answer during a burst | 48 ms | 50 ms |
| Close click → answer during a burst | 84 ms | 84–85 ms |
| Declare → next "Open bet" ready | ~490 ms | ~490 ms |

All 6 bursts in both builds: added + refused + cancelled = clicks, added = server book delta, reconciliation balanced.
(Locally the idle server answers a click faster than Playwright can click, ~33 ms apart, so browser latency is equal; the
concurrency gain shows under contention, above and on UAT below.)

## 4. Tests

- Backend: **all pass**. Full suite 600 passed in one run; the 21 that need the PostgreSQL superuser or `infrastructure/` files
  (absent from the duplicate-free scratch copy) pass on rerun with them (92/92 in those modules). New
  `tests/test_ghost_concurrency.py`: a click succeeds while another transaction holds the event row `FOR UPDATE`; Close during 40
  concurrent clicks freezes exactly the accepted ones (book = receipts = accepted); a refused click leaves no receipt or audit row;
  a superseded auto run rolls back and the next run applies both sides once.
- Frontend: admin **170 passed** (new: bounded-concurrency queue, slow click doesn't block others, answers patch the book without a
  reload, lost answers resent with the same key and refusals not, exact ghost derivation from frames, echo/stale frames ignored,
  player bets fetch once, polling only while needed). Admin, player production builds pass.

## 5. UAT (`uat-20261117-rc.1`, same smoke as rc.7: `declarator-uat.spec.ts`, 12 cycles, fresh synthetic fixture)

Deployed 2026-11-17 ~19:05Z, no migration (0040). Health 200s; 426 desk gate intact; `Server-Timing` and security headers served.

**Correctness: passed.** 12/12 cycles; every burst accounted (added + refused + cancelled = clicks; added = server book);
nothing accepted after Close; 16/16 cross-tenant probes refused, 0 leaked frames; reconciliation balanced; verify script:
15,394 protected rows, **0 changed**. Cleanup: 7 synthetic accounts and the synthetic tenant's 4 users suspended (history kept),
fixture files removed from the container and locally.

| UAT, desk action p95 (5–12 samples) | rc.7 (≤ 2 in flight, event lock) | rc.1 (6 in flight, no event lock) |
| --- | ---: | ---: |
| Stop under a burst (median / p95) | 0.82 / 3.34 s | 1.82 / **2.32 s** |
| Close under a burst | 1.83 s | 1.82 s |
| Close (normal cycle) | 2.33 s | **1.81 s** |
| Auto-stop (normal cycle) | 3.34 s | **1.32 s** |
| Declare (incl. ~0.35 s confirm dialog) | 4.12 s | 6.11 s (worse) |
| Click → request sent | ~33 ms | ~33 ms |

**Targets on UAT: not met** for Stop/Close p95 < 1 s (measured 1.8–2.3 s), and Declare regressed. Measured cause: the UAT API is
CPU-bound, not lock-bound. During the smoke, *every* endpoint is slow, including trivial ones (betting settings 0.67 s, reconciliation
1.1 s p95), and SQL time per request includes event-loop wait (an 11-query read reports ~107 ms of "SQL" that is ~2 ms locally).
The container is capped at **1.0 CPU** (2 workers) on a 12-core AMD EPYC host; at idle it uses 0.17 cores, under the smoke roughly
0.8 cores (estimate from cgroup counters) for ~3 HTTP requests/s, i.e. far more CPU per request than the 3–5 ms measured locally.
With the lock gone, 6 concurrent clicks now compete with Stop for that one core (with rc.7's 2 in flight they waited on the lock),
so Stop's median rose while its tail fell.

**Not yet explained, measured next:** what consumes the CPU outside request handlers on UAT. The leading suspect is WebSocket
fan-out: every socket holds its own Redis subscription to the tenant channel, so each frame (each ghost click, auto tick and bet)
is decoded and re-sent once per connected socket in the same process. A local A/B (0 / 20 / 40 sockets at a fixed click rate) was
started but blocked when the local Docker engine (OrbStack) stopped responding; it was not restarted because it also hosts
the shared local PostgreSQL/Redis.

**Options, in order:** (1) measure fan-out CPU and, if confirmed, subscribe once per process and fan out in memory;
(2) give the UAT API 2 CPUs (`cpus` limit; the host has headroom: load ~4 of 12 cores) and re-measure, which isolates CPU as the cause;
(3) if UAT must stay at 1 CPU, lower `GHOST_CONCURRENCY` (e.g. 3) to trade click throughput for Stop latency.

## 6. Continuous run

Local real soak (`declarator-real.spec.ts`, production assets, 12 player sockets, ghost runner) against the after-stack on the
1-CPU container: **passed, 1.0 h**: 166 cycles, 22,243 bets, 590,116 WebSocket frames, 12 sockets connected throughout, 0 errors at every checkpoint, JS heap 10.5 → 16.8 MB.
