# UAT API CPU, ghost concurrency and desk traffic (2026-11-17)

Continues `docs/benchmarks/declarator-concurrency-20261117/` (rc.1, `972934f`). UAT only was changed; production was
measured read-only and is untouched (still `prod-20261109.1-22-g03cb20d`).

## What changed on UAT

| When (UTC) | Change |
| --- | --- |
| 01:38 | API container 1 → **2 CPUs** (approved): `docker update --cpus 2` (live, no restart) and `API_CPUS=2` in `/opt/esabong/uat/.env` (backup `~/esb-jobs/perf/uat.env.bak-20261117-api-cpus1`). Memory (640 MiB) and workers (2) unchanged. |
| 02:02 | Deployed **`uat-20261117-rc.2`** (`2794e7d`, no migration, schema 0040). |
| 02:13–02:48 | `DECLARATOR_GHOST_CONCURRENCY` 4 → 3 → back to **6** (api container recreated with `--no-deps` each time). |

Host headroom before the change: 12 vCPU / 47 GB, 42 GB free, load ≈ 2–3. CPU limits on the host now sum to 15.25 of 12
vCPU (limits are caps, not reservations). Measured during every run: host CPU **15.6–16.9 % average, 44–50 % peak**;
production API throttled 1.1–3.1 % of periods (its own traffic), unchanged by the UAT change.

## rc.2 (`2794e7d`)

- **Desk: one read per change.** After an action the monitor read (what the desk shows) goes out alone; history, ghost
  job and presence follow once it has answered, once. Realtime changes reload history/extras only when the new snapshot
  shows they changed (card, results, current fight, betting switch). Presence reloads at most every 3 s on new bets.
  Before, a declaration fired ~10 overlapping reads at once (monitor ×2, records ×2–3, ghost-auto ×2, online ×2–3, book)
  that competed with the monitor read the desk was waiting for.
- **Ghost concurrency is a deployment setting**: `DECLARATOR_GHOST_CONCURRENCY` (1–12, default 6) is served in the
  monitor and the desk queue follows it without a rebuild. No click is dropped: the queue is unchanged (click order,
  bounded in flight, Stop/Close first, every click ends added · refused · cancelled).
- **Admin Close stops the fight's auto ghost job in the same transaction**, as the Declarator's Close does (found by the
  2-CPU run: the desk briefly showed a job running on a closed fight until the runner's next tick; no money was affected,
  the runner's book update matches only `state = 'open'`).
- **uvicorn**: `--timeout-worker-healthcheck 30` and `--timeout-keep-alive 130` (see *Production* below).
- Benchmark tooling: `scripts/benchmarks/uat_observe.py` (host observer: cgroup CPU, CFS throttling, CPU pressure,
  memory, per-worker CPU, PostgreSQL activity through one psql session) and `uat_summary.py`; the UAT smoke now records
  every sample (median and p95), desk request volume, requests pending in the browser, ghost request latency and
  ghost throughput (bursts allowed to finish).

Financial calculations, settlement order and audit records are unchanged. The declaration path was profiled locally
(≈ 40 ms CPU on the dev Mac for 16 bets, SQL compile + per-bet settlement writes, no single hotspot) and left as is.

## Results on UAT (same synthetic fixture, same smoke, 12 cycles + 6 Stop/Close bursts + throughput bursts each)

`uat/comparison.txt` has every row; `uat/<run>/summary.json` the full server metrics. Desk action times are measured
in the browser and include Playwright's polling (they move in ~0.5 s steps), so medians and the request timings are the
reliable figures.

| | 1 CPU rc.1 | 2 CPU rc.1 | **2 CPU rc.2** |
| --- | ---: | ---: | ---: |
| Stop under a burst, median / p95 (desk) | 1.32 / 2.33 s | 0.82 / 1.32 s | 0.82 / 1.83 s |
| Stop/Close request send → answer, median | 1.11 s | 0.79 s | 0.84 s |
| Close, median / p95 | 0.85 / 1.81 s | 0.82 / 2.30 s | 0.82 / **1.32 s** |
| Auto-stop, median / p95 | 1.32 / 1.32 s | 0.82 / 1.32 s | 0.82 / **0.85 s** |
| Open, median | 1.30 s | 1.29 s | **0.82 s** |
| **Declare**, median / p95 (incl. ~0.35 s confirm dialog) | 4.11 / 6.11 s | 3.10 / 4.08 s | **2.13 / 3.60 s** |
| Manual ghost click → shown | 47 ms | 37 ms | 45 ms |
| Ghost request (browser), median / p95 | 668 / 2291 ms | 585 / 1702 ms | 612 / **1393 ms** |
| 24-click burst throughput, median | 4.5 clicks/s | 5.5 clicks/s | **8.4 clicks/s** |
| API CPU throttled (share of CFS periods) | **56.5 %** | 9.6 % | 9.2 % |
| API time throttled per run | 430 s | 48 s | 40 s |
| API CPU pressure (time stalled on CPU) | 16.3 % | 2.4 % | 2.3 % |
| API CPU used, average / p95 | 0.80 / 1.07 cores | 0.88 / 1.58 | 0.87 / 1.57 |
| API memory peak (limit 640 MiB) | 403 MiB | 410 MiB | 354 MiB |
| Requests in flight on the server, max / p95 | 12 / 9 | 10 / 5 | 11 / 5 |
| Requests pending in the desk browser, peak / p95 | 9 / 5 | 9 / 4 | 10 / 5 |
| PostgreSQL active time per commit | 9.7 ms | 10.4 ms | 10.6 ms |
| PostgreSQL lock waiters max / oldest | 4 / 557 ms | 2 / 235 ms | 2 / 311 ms |
| Server: ghost POST p50 / p95 | 466 / 2039 ms | 328 / 1375 ms | 390 / 1096 ms |
| Server: declaration POST p50 / p95 | 1488 / 2700 ms | 928 / 2309 ms | 846 / 2484 ms |
| Server: monitor p50 / p95 | 934 / 2841 ms | 517 / 1590 ms | 425 / 1577 ms |
| Server: time per SQL statement (monitor) | 18.0 ms | 9.1 ms | 7.2 ms |
| uvicorn workers killed by the supervisor | 1 (during the run) | 0 | 0 |
| Smoke failures | 1 (harness: audit page size, fixed) | 1 (Admin Close, fixed in rc.2) | **0** |

Every run: every burst accounted (added + refused + cancelled = clicks; added = server book), nothing accepted after
Close (late ghost and player bets → 409), 16/16 cross-tenant probes refused, 0 leaked frames, both tenants' reconciliation
balanced, wallet deltas = settled bets.

### Why Declare was slow (rc.1: p95 4.12 → 6.11 s)

Measured from the request log of yesterday's rc.1 smoke and today's runs: **CPU contention, not locks or settlement
logic.** On 1 CPU the declaration POST itself took 1.49 s p50 (67 SQL statements at ~18 ms each, the statement time being
mostly CPU queueing in the throttled API), then the desk fired ~10 reads at once (monitor ×2, records ×2–3, ghost-auto ×2,
online ×2–3, book), all competing with the monitor read that enables "Open bet" (0.93 s p50, up to 2–4 s). Lock waits
were negligible (≤ 4 waiters, ≤ 0.56 s oldest). The 6.11 s was the maximum of 12 samples; the 1-CPU median was 4.1 s.
2 CPUs halved the per-statement time (Declare p50 3.1 s); rc.2's single prioritized read took it to 2.1 s.

## Ghost concurrency 3 / 4 / 6 (2 CPU, rc.2)

| | **6** | 4 | 3 |
| --- | ---: | ---: | ---: |
| 24-click burst throughput, median of 6 bursts (min–max) | **8.4** (5.2–10.1) clicks/s | 6.2 (3.1–6.5) | 3.2 (2.0–4.1) |
| Ghost request median / p95 (pooled over bursts) | 594 / 1089 ms | 524 / 970 ms | 888 / 1995 ms |
| Stop/Close request send → answer, median / max | 840 / 1490 ms | 849 / 6251 ms | 927 / 1291 ms |
| Slowest Stop, Close or auto-stop on the desk | 1.83 s | 6.35 s | 7.35 s |
| Declare median / p95 | **2.1 / 3.6 s** | 2.6 / 4.6 s | 2.6 / 5.6 s |
| API CPU average, throttled periods | 0.87 cores, 9.2 % | 0.86, 9.7 % | 0.93, 12.7 % |
| API memory peak | 354 MiB | 384 MiB | 388 MiB |
| Failures | 0 | 0 | 0 |

Lowering the bound cut throughput by up to 2.7× and did **not** make Stop or Close faster (median ≈ 0.85 s at every
setting: Stop competes with at most a handful of short ghost writes either way). The multi-second outliers in the 4 and 3
runs were not caused by concurrency: they coincide with UAT **PostgreSQL** at its 1-CPU limit (0.9–1.2 cores, idle-in-
transaction 3–5, a lock wait of 3.8 s) and UAT **Redis** stalls (Redis is capped at 0.5 CPU, throttled in 7.8 % of its
periods since start; its slow log shows plain `SET` up to 2.0 s, `BRPOP` 0.76 s, `PUBLISH` 0.23 s). Both got worse run by
run (PostgreSQL active time per commit 9.7 → 14.6 ms) as the synthetic data grew.

**Selected: `DECLARATOR_GHOST_CONCURRENCY=6` with 2 API CPUs** (UAT is back on 6).

## Production (measured read-only, not modified)

Allocation: API **2 CPUs**, 1.5 GiB, **4 workers**; PostgreSQL 2 CPUs, 2 GiB, `max_connections` 200, `statement_timeout`
30 s; Redis 1 CPU; worker 1 CPU. Same host as UAT, so the same per-core speed (≈ 5–6× slower than the dev Mac).

Since the API started (2026-11-15 10:47Z): 0.61 cores average, **throttled in 15.4 % of periods**. Its request log for
2026-11-16 (`prod/requests-20261116.txt`) shows the overload the rc.1/rc.2 changes target:

- 02:00–09:00Z (real desk use, tenant `swc-main-183`): 63,524 requests, **79 % of them desk reads** (ghost-auto 13.6k,
  online 13.6k, records 11.6k, monitor 11.5k). Monitor p50 2.5 s / **p95 23 s**; manual ghost POST p50 4.7 s / **p95 35 s**
  (the old event-row lock, removed in rc.1); hourly p95 up to 23 s.
- **50 uvicorn workers killed** by the supervisor's 5 s health check (up to 5 a minute) — each kill drops that worker's
  requests and WebSockets, and each respawn's startup costs more CPU; **34 Caddy 502s** since 11-15 (16 upstream EOF);
  51 PostgreSQL statement timeouts and 22 Redis read timeouts surfaced as unhandled errors.
- Off-peak the same API answers in 150–230 ms p50.

rc.2's uvicorn flags address the kills (`--timeout-worker-healthcheck 30`: on UAT the rc.1 deploy needed 5 respawns to
start a worker, rc.2's needed none) and the 502s (`--timeout-keep-alive 130`, above Caddy's 2-minute idle upstream
connections, so a POST can no longer meet a connection uvicorn is closing).

## Recommendation

1. **UAT: keep API at 2 CPUs and ghost concurrency 6.** Next UAT limits are PostgreSQL (1 CPU) and Redis (0.5 CPU); raising
   them to 2 and 1 CPU (production's sizes) would remove the remaining multi-second outliers. Not done: not approved.
2. **Production: no CPU increase needed.** Its 2 CPUs / 4 workers matched UAT's 2-CPU results while idle; its 11-16 overload
   was request volume from the old desk, the event-row lock and worker kills. Deploy rc.1 + rc.2 with
   `DECLARATOR_GHOST_CONCURRENCY=6` **after explicit approval**, then re-measure the same way (`uat_observe.py` already
   samples the production containers read-only).
3. Follow-ups found on the way: login verifies Argon2 synchronously on the event loop (≈ 1 s per login on this host, stalling
   every request on that worker) — run it in a thread; per-request overhead of ~10 SQL statements (tenant resolution opens
   a separate session for `rental_settings` on every request).

## Tests

- Backend: full suite **433 passed** (after the Admin-Close fix; 432 before it). New: monitor states the deployment's ghost bound; an Admin Close stops the auto job at once
  with one audit entry; the runner's safety stop still covered (book closed by another path).
- Frontend: admin 173, player 119, owner 104 passed; admin production build passes. New: queue follows the API's bound
  (raise sends waiting clicks, lower lets in-flight finish), one snapshot read then one round of extras after an action,
  realtime changes reload only what changed.
- Local API concurrency benchmark on rc.2 (`local/api-rc2.json`, same harness and load as rc.1's `api/after.json`, 1-CPU
  container): every scenario consistent (accepted = book delta = receipts; Close freezes exactly the accepted clicks;
  Declare settles), 0 cross-tenant frames on real sockets, both tenants balanced, no errors; API CPU 13.2 s vs 13.1 s.
- Local browser benchmark (1-CPU container, production assets; `local/`): idle desk 80 → 63 requests/min (online list
  34 → 19), a declaration 13–15 → 7–10 requests, all 6 bursts accounted, Declare unchanged at ~0.49 s locally.
- UAT integrity: `verify-declarator-uat.py` over 17,012 pre-existing protected rows: **1 changed** — fight #34 of the
  existing "MAIN" event, declared MERON at 01:25:43Z by the real `declarator` account from a browser (human UAT use during
  the session, not the smoke, which only uses its own `perf-*` accounts and events). Cleanup: 7 synthetic accounts and 32
  synthetic tenant-B users suspended (history kept), fixture files removed. A UAT tester was active throughout and twice
  completed the smoke's live events mid-run; those runs were discarded and repeated.
