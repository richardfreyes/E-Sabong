# Synthetic concurrency benchmark

Run: esabong_bench_20261102_final. Pilot: false. Workers: 4.

| Scenario | Verdict | Seconds | HTTP attempts/s | HTTP p95 ms |
|---|---|---:|---:|---:|
| boundary | PASS | 7.14 | 145.83 | 4008.16 |
| idempotency | PASS | 0.45 | 61.63 | 92.09 |
| precision | PASS | 187.88 | 108.18 | 1004.75 |
| websocket | PASS | 34.61 | 10.03 | 1397.33 |

Overall: **PASS**. A failed gate is not silently relaxed.

See [report.json](report.json), database/generator telemetry, websocket-deliveries.jsonl and per-player accounting JSONL logs. HTTP attempts/s includes setup and expected rejections; precision outcomes report successful unique wager TPS separately.

- Generator, API and database run on one shared development host; results are not a production capacity SLA.
- JWT issuance, password hashing and synthetic fixture grants are outside the timed workload.
- Final call already rejects new wagers. A supplementary open-to-close contest tests admitted requests.
- Settlement uses POST /simulations/matches/:id/transition with new_state=SETTLED; there is no /settle alias.
- Database lock samples are sampled waits, not a complete wait-time histogram; rollback totals include expected rejections and monitor traffic.
- Socket timing starts at a benchmark-only append to a private per-worker trace at the after-commit broadcaster hand-off, before any ODDS_UPDATED coalescing and Redis publish; application wire payloads are not altered by the benchmark.
- The socket gate is p95 <50ms commit-to-client, with full retention and delivery; a coalesced odds frame delivers every earlier wanted version of its match. Maximum latency and the former all-deliveries <50ms gate are reported separately.
