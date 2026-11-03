# Migration 0015 synthetic concurrency validation

**Overall: FAIL — peak-load acceptance criteria were not met.** Settlement accounting and idempotency passed. The boundary burst produced HTTP 500s, and the gateway could not sustain 1,000 sockets or deliver all events within 50 ms. No application tuning was applied during this experiment.

Full run: **2026-11-02 17:25:47–17:29:32 Asia/Manila** (09:25:47–09:29:32 UTC), approximately 224.6 seconds. The harness completed all four scenarios and intentionally returned exit code 1. [Raw report](full/report.json), [execution log](full/execution.log), [reproduction instructions](../../../scripts/benchmarks/README.md).

## Environment and method

Dedicated fresh database `esabong_bench_20261102_full`, upgraded to **0015**. Ten synthetic tenants, 5,900 player wallets, system-granted Arena Points; no production data, cash, purchases or legacy balance migration. Four loopback Uvicorn workers, each configured with an application database pool of 8 plus 8 overflow. PostgreSQL **17.11**, max connections 200, shared buffers 512 MiB, work memory 8 MiB, synchronous commit/fsync/full-page writes enabled. Node **26.12.1**, macOS Darwin 27.0.0, 10 logical CPUs and 24 GiB host memory. Generator, API, PostgreSQL container and Redis share this development machine. This is a reproducible local staging workload, not a production capacity guarantee. [Captured settings and versions](full/environment.json).

A smaller pilot warmed the same fixture before the timed full run. Its history was retained and included in later cumulative wallet audits; each measured precision match still contains exactly 5,000 distinct players. Setup creates grants and authentication tokens directly; all timed wager/transition activity uses the real authenticated API. HTTP phase rates below include setup, retries and expected rejections.

| Scenario | Gate | Phase seconds | HTTP attempts/s | HTTP p95 |
|---|---|---:|---:|---:|
| Boundary races | FAIL | 6.105 | 170.18 | 3,074.92 ms |
| Concurrent settlement | PASS | 0.410 | 68.33 | 79.22 ms |
| Four precision outcomes | PASS | 184.213 | 110.16 | 935.92 ms |
| WebSocket fanout | FAIL | 33.291 | 3.60 | 546.86 ms |

## Boundary safety

The requested `FINAL_CALL`→`WAGERING_CLOSED` burst issued 500 wagers plus the transition from one client barrier. Client dispatch spanned **16.765 ms**, so this does not establish same-millisecond arrival at the database. `FINAL_CALL` already rejects new wagers in this implementation.

- **306** wagers returned the required HTTP 400 `wagering_closed`; **194** returned HTTP 500. Therefore clean rejection failed.
- No wagers were stored for that match. Separate rollback-only closed-state probes returned **UPDATE 0 / INSERT 0**. These are direct guard checks, not affected-row counts instrumented inside each API request.
- A supplementary open-to-closed contest admitted **368** wagers; **132** responses fell outside the expected 201/400 statuses. The controller needed two version attempts to close.
- Stored stake counts were **368 at close acknowledgement and 368 after request drain**. All **20** subsequent sentinel wagers returned HTTP 400. No post-acknowledgement writes were observed; this is not a timestamp proof covering the interval between DB commit and HTTP acknowledgement.
- Across the boundary phase, there were **322 HTTP 500s**, four exhausted `serialization_retry` responses and one stale-version conflict. Ledger and wallet audits passed despite those failures.

The database safety checks held in this run, but the required 500-request error behavior did not. [Boundary audit](full/boundary-audit.json).

## Settlement idempotency and cent accounting

All **20 parallel settlement requests** returned successfully: **one new settlement, 19 replays, one receipt ID**. The batch took **82.921 ms**. Independent ledger checks found no duplicate updates. Settlement uses `POST /api/v1/simulations/matches/:id/transition` with `new_state=SETTLED`; no `/settle` route exists. [Idempotency audit](full/idempotency-audit.json).

Each precision outcome accepted **5,000 wagers from 5,000 distinct players**. Amounts were reproducible fractional Arena Points; combined stake was **2,440,203.99** per match. Accounting uses integer hundredths, never binary floating-point payout arithmetic.

| Outcome | Fee | Net distributable / awards | Remainder | Unique wagers/s | Settlement workflow |
|---|---:|---:|---:|---:|---:|
| SIDE_A | 122,010.20 | 2,318,193.79 | 0.00 | 119.42 | 2.648 s |
| SIDE_B | 122,010.20 | 2,318,193.79 | 0.00 | 114.98 | 2.677 s |
| DRAW | 0.00 | 2,440,203.99 | 0.00 | 120.22 | 5.246 s |
| CANCELLED | 0.00 | 2,440,203.99 | 0.00 | 125.52 | 5.038 s |

All monetary-looking figures in this table are **non-redeemable Arena Points**, expressed to two decimal places. Decisive outcomes apply a 5% simulation fee rounded to the nearest cent; the fee is accounted for in the receipt, without cash or commission wallets. Largest-remainder distribution allocates the complete net pool. Draws and cancellations return the full stake.

All **20,000** full-run precision wagers were eventually accepted. The phase recorded **265 HTTP 409 `serialization_retry` responses**, retried with unchanged keys; these are disclosed rather than counted as additional unique wagers. Settlement workflow timings include close, start, declaration and settlement requests, not just SQL execution.

Every outcome had **zero individual award mismatches, zero wallet mismatches and zero remainder**. The final cumulative oracle covered **5,900 wallets, 20,633 stakes and 34 receipts**, including warm-up and other scenarios: **zero balance leakage**. The post-run reporting-role query found **34 settled matches and zero unsettled stakes**. [Final audit](full/final-audit.json), [per-stake/award accounting](full/final-accounting.jsonl), [per-outcome summaries](full/precision-progress.json). Final accounting SHA-256: `e20eb8f34bcd38ac73cb06a9df2a0371f5abf3eea61ba644339bca58811d234c`.

## WebSocket capacity and latency

Attempted **1,000 connections over 10 tenant/arena rooms** (100 per room). **640 subscribed cumulatively**, but only **374 were open at the join barrier** and **215 after broadcasting**. Cumulative joins must not be read as simultaneous capacity. There were **360 connection failures** and **425 subscribed sockets closed** during measurement.

Ten state changes plus 20 odds updates required **3,000 deliveries**. Only **662 arrived; 2,338 were missing**. Among delivered frames there were **zero observed cross-tenant leaks and zero duplicates**. Failed occupancy and missing frames prevent certification of tenant isolation or delivery at the requested 1,000-client load.

| Delivered-frame latency | Value |
|---|---:|
| p50 | 18.323 ms |
| p95 | 130.111 ms |
| p99 | 222.080 ms |
| maximum | 255.718 ms |

These timings cover **only the 662 delivered frames**, from the benchmark observer immediately before Redis publish to client receipt. The strict gate requires every expected delivery below **50 ms** with no losses; it failed. [Delivery observations](full/websocket-deliveries.jsonl), [30 correlated publisher events](full/publisher-events.jsonl).

Server traces show **`redis.exceptions.MaxConnectionsError: Too many connections`**, including the tenant-cache read path. The captured Redis client pool limit is **100 per worker**. The gateway holds a PubSub connection for each socket in that same pool, leaving fewer than 400 aggregate persistent sockets across four workers once regular commands need capacity. This is consistent with the observed capacity and HTTP failures; it does not exclude additional bottlenecks after that limit is resolved. [Reviewed server excerpt](full/server-errors.txt).

## Lock contention and memory

| Observation | Full-run value |
|---|---:|
| Database/process samples | 1,027, approximately every 200 ms |
| Peak benchmark DB connections | 67 |
| Peak sampled sessions waiting on locks / ungranted locks | 3 / 3 |
| Deadlocks / temporary bytes delta | 0 / 0 |
| Commit / rollback counter delta | 62,322 / 75,537 |
| Maximum sampled active-query age | 111.442 ms |
| Peak summed API-process RSS | 629.89 MiB |
| Peak summed API `ps` CPU | 349% |
| Peak sampled generator RSS / used heap | 184.47 / 78.48 MiB |
| Generator event-loop delay p99 / max | 11.387 / 36.471 ms |

Rollback totals include normal rejected requests and transaction scopes, not only serialization conflicts. Query age is not lock-wait duration. Sampling can miss brief contention; these data do not constitute a complete row-lock latency histogram. Process RSS sums may double-count shared pages; `ps` CPU is smoothed and 349% approximates 3.49 cores. PostgreSQL/Redis container RSS is not included in the API-process figure. [DB/process telemetry](full/database-telemetry.jsonl), [generator telemetry](full/generator-telemetry.jsonl).

## Follow-up and retained state

Address Redis PubSub capacity and reserve command-pool headroom, preferably with worker-level fanout rather than a dedicated Redis connection per browser. Then examine burst backpressure, close-transition fairness and serialization retry behavior. Repeat the unchanged full targets and 50 ms gate after any fix; this run cannot establish their future success.

The benchmark API was stopped after inspection. The isolated synthetic database and private state were retained for investigation. No production migrations, commits, pushes or deployments were performed. No production builds or unrelated regression suites were rerun for these benchmark-only additions; Python lint/format checks and native Node execution were checked, with both pilot and full workloads executed.
