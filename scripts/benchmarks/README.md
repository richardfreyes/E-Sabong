# Synthetic concurrency benchmark

Runs the actual migration 0015 HTTP API and authenticated WebSocket gateway, with a separate integer-cent database accounting oracle. No Postman collection or CLI binding is required. Uses the existing Python venv and Node with native TypeScript stripping (tested on Node 26.12.1); adds no package dependencies.

## Run locally

Start the repository's PostgreSQL and Redis services first. The existing `api/.env` connection URLs and root `.env` database-owner credentials must work locally. Only loopback database/Redis hosts and fresh `esabong_bench_*` databases are accepted. Choose a unique database, private state directory and empty output directory for each experiment.

From `api/`:

```bash
.venv/bin/python ../scripts/benchmarks/staging.py prepare \
  --database esabong_bench_example \
  --state /tmp/esabong-bench-example \
  --players 5000 --burst 500 --tenants 10 --ws-clients 1000 --workers 4

.venv/bin/python ../scripts/benchmarks/staging.py serve \
  --state /tmp/esabong-bench-example
```

In another terminal, from the repository root:

```bash
node scripts/benchmarks/concurrency-settlement.ts \
  --state /tmp/esabong-bench-example --out docs/benchmarks/example
```

For the Redis-remediation retest, add `--ws-update-rounds 20 --ws-update-interval-ms 100`.
After the original 10 state-change and 20 odds broadcasts have drained, this adds 200
actual wager-driven odds updates across the ten rooms, targeting one update per room
per 100 ms with at most one outstanding write per room. The achieved update rate is
reported separately: backpressure can make it lower than the offered schedule. One-second
retention samples and baseline/sustained latency groups are saved. The current requested
socket gate is **p95 <50 ms**, full retention and no missing/duplicate/cross-room deliveries;
maximum latency and the original every-delivery-under-50-ms result are also retained.

Exit code **0** means every selected gate and the independent accounting audit passed; **1** means a gate or execution failed. A failing scenario does not skip subsequent scenarios. Use `--pilot true` for 25 boundary requests, 50 wagers per outcome and 50 sockets; it cannot certify the full targets. `--scenario boundary|idempotency|precision|websocket` selects one scenario. Use a separate empty `--out` for every invocation, including warm-ups.

After the run, capture the read-only environment and outstanding-match snapshot from `api/`:

```bash
.venv/bin/python ../scripts/benchmarks/staging.py describe \
  --state /tmp/esabong-bench-example --out ../docs/benchmarks/example/environment.json
```

Stop the benchmark server with Ctrl-C. The synthetic database, private fixture and all receipts are retained. No script drops databases, truncates tables, resets shared statistics or flushes Redis. Preparation upgrades only the newly created benchmark database to **0015**, never the application's existing database. Redis database 12 is used with random tenant IDs; it shares the local Redis process, so this is logical isolation rather than dedicated hardware.

## Workload and evidence

- **Boundary:** a shared client barrier releases 500 wagers and a close transition. `FINAL_CALL` already rejects new wagers in this backend, so a supplementary `WAGERING_OPEN`→closed contest tests actual admission. It compares committed stake counts at close acknowledgement and after draining requests, sends 20 post-close sentinels, and runs rollback-only conditional UPDATE/INSERT probes. The probes measure affected-row counts separately from the production API transaction. Dispatch spread is recorded; a JavaScript barrier cannot promise 500 network arrivals in one millisecond.
- **Idempotency:** 20 concurrent `SETTLED` transitions with distinct command IDs and the same expected version. Requires one new settlement, 19 replay responses, one receipt ID and no duplicate wallet entries. This backend exposes settlement through `POST /api/v1/simulations/matches/:id/transition`, not a `/settle` alias.
- **Precision:** 5,000 distinct players per outcome, 32 outstanding wager operations, deterministic fractional amounts (`0x52e14f0b` seed), real HTTP writes. Same-player set and amounts are reused across the four outcomes. Transport and `serialization_retry` responses may be retried with the original idempotency key, up to seven attempts. Attempt counts and errors remain visible; unique success TPS excludes duplicates.
- **WebSockets:** 1,000 attempted connections, 100 per tenant/arena, up to 25 concurrent handshakes. A 10-second occupancy period precedes 10 state-change and 20 odds broadcasts, optionally followed by the sustained update rounds above. Requires all 1,000 connections, no closes, no missing/duplicate/cross-room deliveries and **p95** below 50 ms. The original run used a stricter every-delivery-under-50-ms gate; that result remains unchanged. Timing starts immediately before Redis publish using the isolated ASGI wrapper; unchanged application frames are correlated by room, match, type and version. Timing includes Redis fanout and client receipt, excludes the preceding DB transaction. All processes share the host clock.
- **Accounting:** the Python oracle independently reconstructs fees, integer-cent proportional awards and largest-remainder allocations; verifies each stake debit, each award entry, receipt totals, ledger sums and every wallet. JSONL contains individual stake/award audit lines and JSON contains summaries plus SHA-256 hashes. Grants and JWT issuance are fixture setup outside the timed workload; no purchases or transferable points are used.
- **Telemetry:** approximately 200 ms database/process samples and one-second generator samples. HTTP phase rates include setup and expected errors; per-outcome wager TPS is the useful success rate. `settlement_seconds` includes close/run/result/settle API calls. Lock counts and active query ages are sampled observations, not per-transaction lock-wait durations. API RSS is summed over supervisor/workers and can count shared pages more than once; `ps` CPU is smoothed. Database rollback counters include ordinary rejected requests and are not a serialization-failure count.

Private `runtime.json`, `fixture.json` and per-worker publisher traces remain mode 0600 outside Git. Never publish them: they contain credentials/JWTs. Public reports omit credentials. The local server log can contain framework tracebacks; publish only bounded reviewed excerpts.

See the measured [2026-11-02 execution report](../../docs/benchmarks/2026-11-02/REPORT.md). Its failed gates were preserved without changing Redis pools, application behavior or the 50 ms threshold.

The [Redis-remediation retest](../../docs/benchmarks/2026-11-02/REPORT-RETEST.md) reports
zero boundary HTTP 500s, full 1,000-client retention and complete delivery, with p95 latency
still above the requested 50 ms limit. It includes both the original-size burst and the
additional sustained-update workload; the old report and raw results remain unchanged.
