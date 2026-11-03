# Synthetic concurrency benchmark

Run: esabong_bench_20261102_retest. Pilot: false. Workers: 4.

| Scenario | Verdict | Seconds | HTTP attempts/s | HTTP p95 ms |
|---|---|---:|---:|---:|
| boundary | PASS | 7.76 | 134.22 | 3919.71 |
| idempotency | PASS | 0.47 | 60.04 | 103.64 |
| precision | PASS | 183.51 | 110.47 | 1073.23 |
| websocket | FAIL | 60.85 | 5.24 | 3298.60 |

Overall: **FAIL**. A failed gate is not silently relaxed.

See [report.json](report.json), database/generator telemetry, websocket-deliveries.jsonl and per-player accounting JSONL logs. HTTP attempts/s includes setup and expected rejections; precision outcomes report successful unique wager TPS separately.

- Generator, API and database run on one shared development host; results are not a production capacity SLA.
- JWT issuance, password hashing and synthetic fixture grants are outside the timed workload.
- Final call already rejects new wagers. A supplementary open-to-close contest tests admitted requests.
- Settlement uses POST /simulations/matches/:id/transition with new_state=SETTLED; there is no /settle alias.
- Database lock samples are sampled waits, not a complete wait-time histogram; rollback totals include expected rejections and monitor traffic.
- Publisher timing adds a benchmark-only append to a private per-worker trace before Redis publish; application wire payloads are unchanged.
- The retest socket gate is p95 <50ms, with full retention and delivery; maximum latency and the former all-deliveries <50ms gate are reported separately.
