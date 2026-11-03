# Redis pool remediation — peak-load retest

**Overall: FAIL.** The updated backend passed the HTTP error-mapping, socket-retention, accounting and idempotency gates. **The p95 broadcast-latency gate remains unmet.** Connection-pool remediation resolved the previously observed HTTP 500/socket-drop symptoms in this workload; it did not establish acceptable delivery latency.

Executed **2 November 2026, 21:18:50–21:23:03 Asia/Manila** (13:18:50–13:23:03 UTC), **253.107 seconds**. The runner completed all four scenarios and correctly exited **1**. [Current raw results](retest/report.json), [original report](REPORT.md), [original raw results](full/report.json). The original report/results were checked against their previously recorded hashes and remain unchanged.

## Comparison and acceptance gates

| Metric | Previous run | Updated retest | Verdict |
|---|---:|---:|---|
| Boundary-phase HTTP 500s | 322 | **0** | PASS |
| FINAL_CALL burst, clean HTTP 400 responses | 306/500 | **500/500** | PASS |
| Supplementary open-to-close wager responses | 368 accepted; 132 outside 201/400 | **497 HTTP 201; 3 HTTP 409; no other statuses** | PASS |
| Post-close sentinel rejections | 20/20 | **20/20 HTTP 400** | PASS |
| Sockets open at join barrier | 374/1,000 | **1,000/1,000** | PASS |
| Sockets open after broadcasts | 215/1,000 | **1,000/1,000** | PASS |
| Subscribed clients dropped during measurement | 425 | **0** | PASS |
| Missing / cross-tenant / duplicate deliveries | 2,338 / 0 / 0 | **0 / 0 / 0** | PASS |
| Delivery p95, all measured frames | 130.111 ms, 662 delivered frames | **2,191.876 ms, 23,000 frames** | **FAIL: must be <50 ms** |
| Delivery p95, original-size 30-event burst | 130.111 ms, partial delivery | **842.911 ms, all 3,000 deliveries** | **FAIL** |
| Settlement creation / replay / distinct receipts | 1 / 19 / 1 | **1 / 19 / 1** | PASS |
| Accounting line / wallet mismatches | 0 / 0 | **0 / 0** | PASS |

The latency figures are **not an equal-achieved-load comparison**: the original run lost most sockets/deliveries, while the retest retained all 1,000 clients and added sustained traffic. The original gate required every delivery below 50 ms; this retest uses the user's requested **p95 below 50 ms**, while retaining maximum latency and the legacy strict-gate result. Both latency definitions fail in the retest.

## Target, isolation and reproduction

The target was the **updated working-tree backend**, served by the same four-worker Uvicorn benchmark wrapper as the original run, not a production or Docker staging deployment. [Source hashes](retest/source-manifest.json) identify the exact Redis/gateway/settlement and runner files. No application implementation or production configuration was modified during the retest. Runner changes add sustained-update/retention instrumentation and evaluate the explicitly requested 400/409 and p95 criteria.

- Fresh local DB **`esabong_bench_20261102_retest`**, migrated to **0015**; 10 synthetic tenants and 5,900 system-granted player wallets. No customer records were copied or merged.
- Actual Redis configuration: separate **100-connection `BlockingConnectionPool`** for commands and **1,024 subscription connections per worker**. PostgreSQL pools remain **8 + 8 overflow per worker**. [Runtime metadata](retest/environment.json).
- Same local host topology: Node 26.12.1, macOS Darwin 27.0.0, 10 logical CPUs, 24 GiB RAM; PostgreSQL 17.11 and Redis on the local Docker services. PostgreSQL max connections 200; synchronous commit, fsync and full-page writes enabled. API/runtime package versions are recorded in metadata.
- A smaller pilot ran first on the same fresh fixture and passed. Its 273 wagers remain in the cumulative audit; each full precision match still has exactly 5,000 distinct players. [Pilot evidence](retest/pilot-report.json).

From `api/`, prepare a **fresh** database/state pair and serve it:

```bash
.venv/bin/python ../scripts/benchmarks/staging.py prepare \
  --database esabong_bench_20261102_retest \
  --state /tmp/esabong-bench-20261102-retest \
  --players 5000 --burst 500 --tenants 10 --ws-clients 1000 --workers 4
.venv/bin/python ../scripts/benchmarks/staging.py serve \
  --state /tmp/esabong-bench-20261102-retest
```

The measured full-run command, from repository root:

```bash
node scripts/benchmarks/concurrency-settlement.ts \
  --state /tmp/esabong-bench-20261102-retest \
  --out docs/benchmarks/2026-11-02/retest \
  --ws-update-rounds 20 --ws-update-interval-ms 100
```

These database/state/output names now exist and are retained; choose new names for another run. The generator and API shared this machine, so results are not a separate-host capacity SLA. Private JWTs/URLs and per-worker timing traces stay outside Git. No dependency installation was performed.

## Boundary and idempotency details

The shared client barrier dispatched **500 FINAL_CALL wagers** over **18.108 ms**, plus a close transition. All 500 returned `400 wagering_closed`; no wager row was stored. Separate rollback-only closed-state probes reported **UPDATE 0 / INSERT 0**. These are guard probes, not row-count instrumentation inside each production request. This test does not promise same-millisecond network arrival; FINAL_CALL already rejects new wagers in this backend.

The supplementary **WAGERING_OPEN→WAGERING_CLOSED** contest accepted 497 wagers and cleanly rejected three with `409 serialization_retry`. The controller needed three version attempts, with two expected stale-version conflicts. Counts were **497 at close acknowledgement and 497 after draining**, and all 20 later sentinels returned HTTP 400. No post-acknowledgement writes were observed; the test does not timestamp-prove the interval between DB commit and HTTP acknowledgement. [Boundary audit](retest/boundary-audit.json).

**All 21,661 full-run HTTP attempts across the four phases produced zero HTTP 500s and zero transport errors.** Expected 409 retries remain visible rather than being hidden as unique successes.

All **20 parallel settlement requests** returned HTTP 200: one new settlement and 19 existing-receipt replays, one unique receipt, no duplicate wallet postings. Concurrent batch time **107.195 ms**, compared with **82.921 ms** previously. [Idempotency audit](retest/idempotency-audit.json).

## Socket retention, delivery and offered update rate

All **1,000 clients authenticated/subscribed** across 10 isolated tenant/arena rooms, 100 clients per room. There were **zero connection failures, zero subscribed-client closes and zero cross-room or duplicate frames**. The 51 retention samples covered **49.404 seconds after the join barrier**; minimum retained sockets was **1,000**. A 10-second occupancy period preceded the broadcasts. [Retention samples](retest/websocket-retention.json).

The original-size burst emitted 10 state transitions and 20 wager-driven odds events: **3,000/3,000 deliveries**. After those drained, 20 additional wager-driven updates per room generated **200 events and 20,000/20,000 deliveries**. Total: **230 publisher events, 23,000/23,000 deliveries**. [Publisher timestamps](retest/publisher-events.jsonl), [per-delivery timestamps](retest/websocket-deliveries.jsonl).

The offered schedule targeted one update per room per **100 ms**, with one outstanding write per room. Backpressure stretched the 200-update phase to **33.777 seconds**, yielding only **5.921 updates/s aggregate**, far below the nominal 100/s schedule. The scheduled interval must not be presented as achieved high-frequency throughput. Each update came from a real wager API commit; events were not injected directly into Redis.

| Publisher-to-client latency | Original-size burst | Sustained updates | Combined |
|---|---:|---:|---:|
| Delivered frames | 3,000 | 20,000 | 23,000 |
| p50 | 415.553 ms | 686.664 ms | 631.722 ms |
| p95 | **842.911 ms** | **2,256.069 ms** | **2,191.876 ms** |
| p99 | 1,131.414 ms | 3,596.403 ms | 3,438.644 ms |
| Maximum | 1,562.408 ms | 6,988.803 ms | 6,988.803 ms |

Timing starts immediately **before Redis publish** and ends at native WebSocket client receipt, using the shared host clock. It includes gateway processing/fanout but excludes the preceding wager transaction. The benchmark-only append observer does not change application frames. The retention and loss criteria pass, but neither the baseline nor sustained latency meets the 50 ms gate.

## Exact-cent accounting

All four full precision outcomes accepted **5,000 wagers from 5,000 distinct players**. Total stake per match: **2,440,203.99 Arena Points**, using the original deterministic fractional-amount seed. Decisive outcomes accounted for a virtual fee of **122,010.20** and awarded **2,318,193.79**. DRAW and CANCELLED refunded **2,440,203.99** without a fee. Every outcome had **zero rounding remainder, zero per-line mismatches and zero wallet mismatches**.

| Outcome | Previous unique wager TPS | Retest TPS | Settlement workflow |
|---|---:|---:|---:|
| SIDE_A | 119.42 | 112.92 | 2.726 s |
| SIDE_B | 114.98 | 117.86 | 2.602 s |
| DRAW | 120.22 | 120.13 | 5.094 s |
| CANCELLED | 125.52 | 130.43 | 4.666 s |

The precision phase disclosed **245 `409 serialization_retry` responses**, retried with unchanged request keys; all 20,000 unique wagers were eventually accepted. Settlement workflow time includes close/start/declaration/settlement calls. These are virtual points, not money or transferable balances.

The final independent integer-cent oracle covered **20,992 cumulative stakes** (20,719 full-run + 273 pilot), **5,900 wallets and 34 receipts**, with **zero balance leakage**. All 34 matches were settled and no stakes remained outstanding. [Final audit](retest/final-audit.json), [per-stake/award accounting](retest/final-accounting.jsonl), [per-outcome summaries](retest/precision-progress.json). Accounting log SHA-256: `3dceaf79f9d4dc9cacc532ca97707bb4d69fe4318ceaa44fa604af9e09b14a47`.

## Resources, diagnostics and remaining issue

| Observation | Previous run | Retest |
|---|---:|---:|
| Database/process samples | 1,027 | 1,141 |
| Peak DB connections | 67 | 77 |
| Peak lock waiters / ungranted locks | 3 / 3 | 2 / 2 |
| Deadlocks / temp bytes | 0 / 0 | 0 / 0 |
| Peak summed API RSS | 629.89 MiB | 702.66 MiB |
| Peak summed API `ps` CPU | 349% | 369.4% |
| Maximum sampled active-query age | 111.442 ms | 206.840 ms |
| Generator event-loop p99 / max | 11.387 / 36.471 ms | 11.600 / 106.889 ms |

Retest peak sampled generator RSS/used heap: **245.68 / 98.40 MiB**. Commit/rollback counter deltas: **132,243 / 145,101**; rollback totals include ordinary rejected/read transaction scopes and are not a serialization-error count. Lock observations are approximately 200 ms samples, not a complete wait-time histogram; summed RSS can count shared pages more than once and `ps` CPU is smoothed. [DB/process telemetry](retest/database-telemetry.jsonl), [generator telemetry](retest/generator-telemetry.jsonl).

During the socket phase, sampled lock waiters peaked at **0**, while active-query age p95 reached **136.117 ms**. The gateway currently performs database-backed authorization before **each forwarded event** and runs a database-backed pulse every **0.5 seconds per socket**. That design can produce substantial DB/pool work at 1,000 clients. It is a plausible remaining bottleneck, **not a profiler-proven root cause**; shared-host generator scheduling also contributes uncertainty. Profile DB pool queueing, per-message authorization, pulse work and fanout scheduling before changing capacity settings. Do not weaken authorization to improve benchmark numbers.

One `asyncio.exceptions.CancelledError` while SQLAlchemy/asyncpg was closing a connection was observed during socket cleanup. It did not cause an HTTP 500 or accounting mismatch, but graceful-disconnect cleanup remains a follow-up. The API stopped cleanly after the run; the benchmark database/receipts were retained.

**Decision:** HTTP error mapping, full socket retention/delivery and exact accounting/idempotency are verified for this run. **Peak-load latency is still not ready.** No production traffic, production migration, deployment, commit or push was performed. No backend tuning or lower-load rerun was used to replace the failed full result.
