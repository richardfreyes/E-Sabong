# Synthetic concurrency benchmark

Run: esabong_bench_20261102_full. Pilot: false. Workers: 4.

| Scenario | Verdict | Seconds | HTTP attempts/s | HTTP p95 ms |
|---|---|---:|---:|---:|
| boundary | FAIL | 6.11 | 170.18 | 3074.92 |
| idempotency | PASS | 0.41 | 68.33 | 79.22 |
| precision | PASS | 184.21 | 110.16 | 935.92 |
| websocket | FAIL | 33.29 | 3.60 | 546.86 |

Overall: **FAIL**. A failed gate is not silently relaxed.

See [report.json](report.json), database/generator telemetry, websocket-deliveries.jsonl and per-player accounting JSONL logs. HTTP attempts/s includes setup and expected rejections; precision outcomes report successful unique wager TPS separately.

- Generator, API and database run on one shared development host; results are not a production capacity SLA.
- JWT issuance, password hashing and synthetic fixture grants are outside the timed workload.
- Final call already rejects new wagers. A supplementary open-to-close contest tests admitted requests.
- Settlement uses POST /simulations/matches/:id/transition with new_state=SETTLED; there is no /settle alias.
- Database lock samples are sampled waits, not a complete wait-time histogram; rollback totals include expected rejections and monitor traffic.
- Publisher timing adds a benchmark-only append to a private per-worker trace before Redis publish; application wire payloads are unchanged.
