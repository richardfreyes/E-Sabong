# Synthetic concurrency benchmark

Run: esabong_bench_20261102_latency_v2. Pilot: false. Workers: 4.

| Scenario | Verdict | Seconds | HTTP attempts/s | HTTP p95 ms |
|---|---|---:|---:|---:|
| boundary | PASS | 6.91 | 150.56 | 3076.79 |
| idempotency | PASS | 0.44 | 63.51 | 131.39 |
| precision | PASS | 148.11 | 135.86 | 270.32 |
| websocket | PASS | 26.87 | 12.47 | 956.53 |

Overall: **PASS**. A failed gate is not silently relaxed.

See [report.json](report.json), database/generator telemetry, websocket-deliveries.jsonl and per-player accounting JSONL logs. HTTP attempts/s includes setup and expected rejections; precision outcomes report successful unique wager TPS separately.

- Generator, API and database run on one shared development host; results are not a production capacity SLA.
- JWT issuance, password hashing and synthetic fixture grants are outside the timed workload.
- Final call already rejects new wagers. A supplementary open-to-close contest tests admitted requests.
- Settlement uses POST /simulations/matches/:id/transition with new_state=SETTLED; there is no /settle alias.
- Database lock samples are sampled waits, not a complete wait-time histogram; rollback totals include expected rejections and monitor traffic.
- Publisher timing adds a benchmark-only append to a private per-worker trace before Redis publish; application wire payloads are unchanged.
- The retest socket gate is p95 <50ms, with full retention and delivery; maximum latency and the former all-deliveries <50ms gate are reported separately.
