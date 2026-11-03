# WebSocket delivery optimization: peak-load rerun

**Overall: PASS.** At 1,000 concurrent sockets, the optimized backend delivered every expected room frame with **p95 29.640 ms** from commit to client. In the [Redis-remediation retest](REPORT-RETEST.md), p95 was **2,191.876 ms**. Socket retention was 1,000/1,000 throughout. There were no missing, duplicate or cross-room frames, and the independent accounting audit found **zero leakage**. All four scenarios passed and the runner exited **0**.

A websocket-only burst run with 8 simultaneous wagers per room also passed, at **p95 40.568 ms**. The p99 and maximum latencies remain above 50 ms in both runs (see [remaining limits](#remaining-limits-and-follow-up-priorities)).

Measured on **2 November 2026**:

- Full run: **21:52:44–21:55:47 Asia/Manila** (13:52:44–13:55:47 UTC).
- Burst run: 13:55:47–13:57:09 UTC.

Raw evidence: [full run](latency-optimized-v2/report.json), [burst run](latency-optimized-burst-v2/report.json), [source hashes](latency-optimized-v2/source-manifest.json), [environment](latency-optimized-v2/environment.json), [pilot](latency-optimized-v2/pilot-report.json). The earlier [original](REPORT.md) and [retest](REPORT-RETEST.md) reports and their raw results are unchanged. Bulky raw logs (`*.jsonl`) for every run are compressed outside Git; links to them resolve after a restore (see [Evidence archive](#evidence-archive)). A pre-commit [final validation](#final-validation-before-commit) rerun also passed.

## What was changed, and why it differs from the task brief

The brief assumed a Node.js broadcast server that publishes full nested match state on every wager. This backend's gateway is Python/asyncio (FastAPI/Uvicorn); Node runs only the load generator. `ODDS_UPDATED` was already a flat aggregate packet (`matchId`, `version`, totals, odds), with no participant data.

The retest pushed only **5.9 odds updates/s** in total and still measured a 2.2 s p95, so publish volume was not the bottleneck. The gateway was. Every forwarded frame ran a full database-backed authorization for every socket: about 100 transactions per event per room. Every socket also polled the database every 0.5 s. Batching alone would not have addressed either cost.

| Change | File | Effect |
|---|---|---|
| Authorization moved off the delivery path | `api/app/modules/simulations/gateway.py` | Each socket re-checks session, workspace modules, account and every joined arena once per second, with start times spread across sockets. A frame is forwarded only while the room's last successful check is under 2 s old. Otherwise the socket checks synchronously first, so a lagging refresh fails closed. |
| Pushed session revocation | `api/app/core/deps.py`, `api/app/modules/auth/service.py` | `revoke_family` publishes on `auth:revoked_family:{id}:events`. Open simulation sockets close with 4401 immediately, without polling. |
| Shared countdown ticker | `gateway.py` (`_RoomTicker`) | Each worker reads the FINAL_CALL deadline once per room every 0.5 s and shares the tick with its local sockets. Before, every socket ran this query. |
| Raw frame pass-through | `gateway.py` | Room frames go out as the exact text published to Redis. The gateway no longer does per-socket `json.loads`/`send_json` re-serialization. |
| `ODDS_UPDATED` coalescing | `api/app/modules/simulations/broadcast.py` | 50 ms per-match window with a leading edge. A wager after a quiet window is published immediately. Wagers committed within the window are merged into one trailing packet with the newest totals and version. Buffered odds are flushed before any state change for the same match. |
| Delta fields | `service.py`, `broadcast.py` | `ODDS_UPDATED` adds decimal `deltaSideA`/`deltaSideB` (the stakes merged into this packet) and an epoch-millisecond `timestamp`. Existing fields are unchanged; the `*SideA` naming matches the existing `totalSideA`. |

**Security trade-off (accepted before implementation).** Before, room access was re-checked on every frame. Now a revoked session closes at once through the push. Other access losses (arena disabled, Declarator unassigned, account suspended, module removed) close the socket at the next refresh, about 1 s later. Until then, frames already authorized for that room can still be delivered. They carry only room aggregates, never balances or player identifiers. Authorization itself is not weakened: the same checks run, with bounded staleness.

**Client contract.** Workers coalesce independently, so packets can arrive out of version order, and intermediate versions may never be sent. Clients must apply totals only from a version newer than the last one they applied, and must never sum deltas across packets. This is documented in [the simulation guide](../../simulated-tournaments/README.md).

## Acceptance gates: full run

| Metric | Retest | Optimized | Verdict |
|---|---:|---:|---|
| Sockets subscribed / open at barrier / open after broadcasts | 1,000 / 1,000 / 1,000 | **1,000 / 1,000 / 1,000** | PASS |
| Minimum open over 1-second retention samples | 1,000 | **1,000** | PASS |
| Connection failures / subscribed-client closes | 0 / 0 | **0 / 0** | PASS |
| Missing / cross-room / duplicate deliveries | 0 / 0 / 0 | **0 / 0 / 0** | PASS |
| Deliveries | 23,000/23,000 | **23,000/23,000** | PASS |
| **Delivery p95, all measured frames** | 2,191.876 ms | **29.640 ms** | **PASS (<50 ms)** |
| Delivery p95, original-size 30-event burst | 842.911 ms | **29.442 ms** | PASS |
| Delivery p95, sustained updates | 2,256.069 ms | **30.091 ms** | PASS |
| Boundary HTTP 500s / FINAL_CALL clean 400s | 0 / 500 of 500 | **0 / 500 of 500** | PASS |
| Settlement creation / replay / distinct receipts | 1 / 19 / 1 | **1 / 19 / 1** | PASS |
| Accounting line / wallet mismatches | 0 / 0 | **0 / 0** | PASS |
| Legacy gate: every delivery under 50 ms | false | **false** | not required; reported |

| Commit-to-client latency (full run) | 30-event burst | Sustained | Combined | Retest combined |
|---|---:|---:|---:|---:|
| Delivered frames | 3,000 | 20,000 | 23,000 | 23,000 |
| p50 | 2.969 ms | 3.093 ms | **3.084 ms** | 631.722 ms |
| p95 | 29.442 ms | 30.091 ms | **29.640 ms** | 2,191.876 ms |
| p99 | 91.852 ms | 52.828 ms | **52.854 ms** | 3,438.644 ms |
| Maximum | 95.148 ms | 100.502 ms | **100.502 ms** | 6,988.803 ms |

The offered sustained schedule matched the retest: 20 rounds per room, one update per room every 100 ms, at most one outstanding write per room. It completed in **6.114 s at 32.712 updates/s aggregate**; the retest managed 5.921 updates/s. That is still below the nominal 100/s (see limits below). Each update came from a real wager API commit.

### Timing definition (stricter than the retest)

The retest started the clock immediately before Redis publish. Coalescing would hide its own buffer delay from that clock. This run starts the clock at the **after-commit hand-off to the broadcaster**, before any coalescing delay. That start is never later than the old one, so these figures include more of the delivery path than the retest's did.

A wanted odds version counts as delivered to a client at that client's first `ODDS_UPDATED` frame for the match at or after that version. State changes must match exactly. Each client/version pair is counted once. Timing includes coalescing, Redis fanout and client receipt. It excludes the preceding wager transaction. All processes share the host clock.

Full run: 223 of 230 wanted versions went out as their own packet. 800 client deliveries were satisfied by a newer packet, both from coalescing and from cross-worker reordering. With one outstanding write per room, the leading edge sends almost every update immediately. So the improvement in this run comes from the gateway changes, not from batching.

## Burst run: coalescing under simultaneous wagers

Same fixture and server, `--scenario websocket --ws-update-concurrency 8`: each round dispatches 8 simultaneous wagers per room (80 in flight across rooms).

| Metric | Result |
|---|---:|
| Retention / failures / closes | 1,000/1,000 throughout / 0 / 0 |
| Missing / cross-room / duplicate | 0 / 0 / 0 |
| Wanted versions / published as own packet | 1,630 / 1,560 |
| Client deliveries satisfied by a newer packet | 8,400 |
| Deliveries | 163,000/163,000 |
| **p50 / p95 / p99 / max** | **4.148 / 40.568 / 102.822 / 459.702 ms** |
| Sustained achieved rate | 1,600 wagers in 59.365 s (26.952/s) |
| Wager responses | 1,630 × 201, 454 × 409 `serialization_retry` (retried with the same request key) |

The p95 gate passes with less headroom: 40.6 ms against the 50 ms limit. Write contention dominates this run. With 8 writers on one match row, wagers serialize, so commits for a match arrive about one per 50 ms window or slower, and only 70 of 1,630 versions were merged. The 454 serialization retries were visible and recovered; none were hidden.

## Exact-cent accounting (full run)

All four precision outcomes accepted **5,000 wagers from 5,000 distinct players**, with the same deterministic seed (`0x52e14f0b`) as before. Every outcome had zero rounding remainder, zero line mismatches and zero wallet mismatches. 94 `409 serialization_retry` responses were retried with unchanged request keys.

| Outcome | Retest unique wager TPS | This run | Settlement workflow |
|---|---:|---:|---:|
| SIDE_A | 112.92 | 149.65 | 2.394 s |
| SIDE_B | 117.86 | 151.82 | 2.383 s |
| DRAW | 120.13 | 151.17 | 4.662 s |
| CANCELLED | 130.43 | 152.26 | 4.728 s |

The precision phase runs without sockets connected. Its higher TPS is **not attributed** to this change; it is within plausible run-to-run variance on a shared host.

**Boundary:** 500 FINAL_CALL wagers were dispatched over 16.388 ms, and all 500 got a clean 400. The rollback-only guard probe reported UPDATE 0 / INSERT 0. The open-to-close contest accepted 500 wagers, with 500 stakes at close acknowledgement and 500 after draining, and all 20 late sentinels were rejected.

**Idempotency:** 20 parallel settlements produced 1 new settlement, 19 replays and 1 receipt, in a 135.158 ms batch.

**Final audit, full run:** 21,169 cumulative stakes (pilot included), 5,900 wallets, 34 receipts, **zero wallet or line mismatches**. Accounting log SHA-256: `28bac2ca5ffec7c269d80e4e0ad13721669ac9c0902c496c14b2e56f850d54fd`. The burst run's cumulative audit (22,789 stakes, 44 receipts) also found zero mismatches: `b8a082f78ef92fca2276d38f9b7496296a9d9d6485323bcc3472902bbfe5eb32`. These are virtual points, not money or transferable balances.

## Resources

| Socket-phase observation | Retest | Full run | Burst run |
|---|---:|---:|---:|
| PostgreSQL commits/s during socket phase | 1,479 | 1,040 | 1,095 |
| Peak DB connections (whole run) | 77 | 76 | 91 |
| Peak lock waiters | 2 | 1 | 4 |
| Peak summed API `ps` CPU | 369.4% | 329.1% | 321.2% |
| Generator event-loop p99 / max | 11.600 / 106.889 ms | 12.050 / 80.151 ms | 12.263 / 228.458 ms |

Deadlocks and temp bytes were 0 in both runs. Summed RSS can count shared pages more than once, and `ps` CPU is smoothed.

## Remaining limits and follow-up priorities

Recorded for follow-up. No further performance rewrite was made in the finalization pass.

| Priority | Issue | Evidence | Recommended next step |
|---|---|---|---|
| 1 | **Wager writes cap throughput.** Sustained updates reached 32.7/s (v2) and 17.0/s (final validation) against 100/s offered, with one write per room in flight. | Socket-phase wager HTTP p95 956.5 ms (v2); 454 serialization retries in the burst run | Profile the wager transaction under load: lock waits on the match row, wallet row and serializable retries. Measure before changing isolation or lock order. Settlement correctness is the constraint; do not trade it for speed. |
| 2 | **Access re-checks scale with sockets.** About 1 DB transaction per socket per second (~1,000/s at 1,000 sockets). | 1,040–1,095 commits/s during the socket phase | Share the arena check per worker per room, and cache user status per worker with a short TTL plus pushed invalidation for suspension and unassignment, as revocation already has. This changes authorization design and needs an explicit decision. |
| 3 | **Tail latency.** p99 52.9 ms (v2), 88.1 ms (final validation), 102.8 ms (burst); maximum up to 459.7 ms. Only p95 is gated. | Per-delivery logs in the archive | Profile one worker during the sustained phase (event-loop lag, Redis read batching, `send_text` back-pressure). Consider one shared Redis subscriber per worker with in-process fan-out instead of one pub/sub connection per socket. |
| 4 | **Test-suite environment leak.** `unit_tests/test_production_config.py::test_development_defaults_still_start_outside_production` fails when `api/tests` and `api/unit_tests` run in one pytest process. | `ENVIRONMENT=test` set by `tests/` persists | Isolate the config tests: `monkeypatch.delenv` the inherited variables and clear the settings cache in that test. Until then, run the two directories separately, as configured (`testpaths = ["tests"]`). |

Also still open:

- The `asyncio.CancelledError` during asyncpg connection close on socket cleanup, which the retest also saw. It caused no HTTP 500 and no accounting mismatch.
- All results come from one shared development host (Node 26.12.1, macOS Darwin 27.0.0, 10 logical CPUs). They are not a production capacity SLA.

## Superseded first attempt (retained)

The first attempt used fixture `esabong_bench_20261102_latency`, with evidence in [latency-optimized-attempt1](latency-optimized-attempt1/report.json) and [latency-optimized-burst-attempt1](latency-optimized-burst-attempt1/report.json). It also passed:

- Full run: p95 19.598 ms, maximum 149.474 ms.
- Burst run: p95 42.953 ms, maximum **5,579.194 ms**.

The burst maximum was a **load-generator artifact** caused by a bug in the new runner. The first version of the runner's delivery matcher scaled with frames × wanted versions and allocated a string per pair. It ran right after the last wagers and stalled the Node event loop for 6.17 s; the generator's 1 s telemetry shows the gap. Six clients' final-round frames were timestamped late, while other clients received the same frames in ≤31 ms.

The matcher was rewritten as a linear pass, and both runs were repeated on a fresh database. The v2 generator event-loop maximum was 80 ms (full) and 228 ms (burst). The v2 results above are the reported results. Attempt-1 artifacts were neither edited nor discarded; like every run, their raw logs are in the evidence archive and their summaries stay in Git.

## Final validation before commit

A confirmation rerun used the same options as the v2 full run, on a fresh fixture `esabong_bench_20261102_final`. It ran **2 November 2026, 14:15:54–14:19:44 UTC**. Every benchmark-relevant source file is byte-identical to the v2 run ([source hashes](final-validation/source-manifest.json), `changed_since_v2: []`). The runner exited **0** with all four scenarios passing. [Report](final-validation/report.json), [environment](final-validation/environment.json).

| Gate | Result |
|---|---|
| Sockets subscribed / open at barrier / open after broadcasts / minimum retained | 1,000 / 1,000 / 1,000 / 1,000 |
| Connection failures / subscribed-client closes | 0 / 0 |
| Missing / cross-room / duplicate deliveries | 0 / 0 / 0, with 23,000/23,000 delivered |
| **Commit-to-client p50 / p95 / p99 / max** | **3.836 / 17.481 / 88.095 / 164.308 ms** |
| Boundary: FINAL_CALL clean 400s / late sentinels rejected | 500 of 500 / 20 of 20 |
| Settlement creation / replay / distinct receipts | 1 / 19 / 1 |
| Precision: accepted wagers per outcome | 5,000 × 4 |
| Final audit: wallets / stakes / receipts / wallet or line mismatches | 5,900 / 20,648 / 17 / **0** |

Accounting log SHA-256: `8ba5e7216873b699cddbd217ec5941ad946384731f9a38daa4aa45a44f5bba01`. The generator event-loop maximum was 87.9 ms, so there was no generator stall.

Precision TPS (110.51–140.75) and the sustained update rate (16.96/s) were lower than in v2. That fits run-to-run variance on a shared host, which is why the precision TPS gain above is not attributed to this change.

Functional checks:

- **Session revocation:** `test_session_revocation_is_pushed_to_open_room_sockets` closes the socket with 4401 within 1 s, with the periodic re-check disabled. It was mutation-checked. Logout revocation is covered by `test_logout_revokes_access_token`.
- **Shared countdown and coalescing:** covered by `test_room_frames_carry_deltas_and_countdown_is_read_once_per_room`, the five coalescer unit tests, and the burst run (70 of 1,630 versions merged).
- **Out-of-version updates:** the server side is verified (a coalesced packet carries the newest version and only the merged deltas). The shipped player client ignores late and repeated `book.updated` frames (`play-betting.spec.ts`). **No web client consumes simulation `ODDS_UPDATED` yet**, so client-side rejection of stale versions and non-accumulation of deltas cannot be exercised today. Any future consumer must follow the documented rule and add the same kind of test.

## Evidence archive

Raw evidence for every run is packed whole into `benchmark-archive/2026-11-02/<run>.tar.zst` (tar + zstd -19) at the repository root. That folder is git-ignored and excluded from the Docker build context.

Removed from the docs tree: every `*.jsonl` (per-delivery, publisher, telemetry and accounting logs) and the redundant intermediate `report.partial.json`. Kept in Git: reports, `report.json`, audits, environment and source manifests, retention samples, and per-file checksums.

| Run | Original files / bytes | Archive bytes | Archive SHA-256 |
|---|---:|---:|---|
| full | 26 / 19,347,750 | 948,253 | `8ee3d1fb123cfed8639e54551953f48d7fe050d0a541ddf1662312808c63451c` |
| retest | 27 / 27,940,757 | 1,656,643 | `0cc4a47b317c0da3abee379b13b9aeeece23ebd111a6d6c34db2cbed0ea3908d` |
| latency-optimized-attempt1 | 24 / 31,478,835 | 1,629,876 | `eb7cac3db66f3cf42ef5238d68937d8e0e3a90d0736f873615a914f1ede3b905` |
| latency-optimized-burst-attempt1 | 10 / 89,564,241 | 2,553,231 | `956e5362356d7d39d3f303fff1175700e30fde1e3b928a48d5d0418f144bfea6` |
| latency-optimized-v2 | 26 / 31,472,127 | 1,617,366 | `737becb9b226c61a1393b10791063b4ca0ebdb50e089337c8f70b0e79234b356` |
| latency-optimized-burst-v2 | 10 / 89,783,488 | 2,561,601 | `39c862014498e467d4d070951afb365cf6a0dfb555352ea7d454406533454eb0` |
| final-validation | 25 / 30,598,533 | 1,613,453 | `3ff30f62728ef2228aa99065dbfd0622a5600c9c34a7a3a0dac8a2687f9d13b8` |
| **Total** | **148 / 320,185,731 (305.4 MiB)** | **12,580,423 (12.0 MiB)** | |

The tracked docs folder for this date is now about 0.9 MiB. The machine-readable manifest is [EVIDENCE-MANIFEST.json](EVIDENCE-MANIFEST.json). Per-file SHA-256 of every original file, including the removed ones, is under [checksums/](checksums/), and `benchmark-archive/2026-11-02/SHA256SUMS` covers the archives. Hashes recorded in earlier reports match: the retest accounting log `3dceaf79…` appears unchanged in `checksums/retest.sha256`.

The archives exist only on the machine that produced them. Copy `benchmark-archive/` to durable storage (for example the team's artifact bucket) before relying on it. Restore and verification were tested end to end on a scratch copy.

From the repository root:

```bash
# Check archive integrity against the manifest
api/.venv/bin/python scripts/benchmarks/evidence.py verify --date 2026-11-02
# Restore every run (or one with --run retest) into docs/benchmarks/2026-11-02/, then shasum-verify every file
api/.venv/bin/python scripts/benchmarks/evidence.py restore --date 2026-11-02
```

Restore extracts only files missing from the docs tree, then checks every file against `checksums/<run>.sha256`. Manual equivalent: `zstd -dc benchmark-archive/2026-11-02/retest.tar.zst | tar -xf - -C docs/benchmarks/2026-11-02`, then `(cd docs/benchmarks/2026-11-02 && shasum -a 256 -c checksums/retest.sha256)`. After a restore, delete the restored `*.jsonl` files again (or leave them untracked) before committing. A new run is archived by rerunning `evidence.py archive`; runs already in the manifest are skipped and never rewritten.

## Verification and reproduction

- `api/tests`: **196 passed**. `api/unit_tests`: **113 passed**. These run separately, as configured; run together, one config test fails (follow-up priority 4).
  - New tests cover coalescing semantics (leading edge, merged deltas, newest version, flush before a state change, per-match independence, pre-coalescing observer), pushed revocation, delta fields, and a single shared ticker that deregisters.
  - The revocation test was mutation-checked: it fails if the publish is removed.
- `ruff check` / `ruff format --check` on `app`, `tests`, `unit_tests` and `scripts/benchmarks`: clean.
- A pilot (50 sockets) passed first on the v2 fixture; its stakes are in the v2 cumulative audit.

From `api/`:

```bash
.venv/bin/python ../scripts/benchmarks/staging.py prepare \
  --database esabong_bench_20261102_latency_v2 --state /tmp/esabong-bench-latency-v2 \
  --players 5000 --burst 500 --tenants 10 --ws-clients 1000 --workers 4
.venv/bin/python ../scripts/benchmarks/staging.py serve --state /tmp/esabong-bench-latency-v2
```

From the repository root:

```bash
node scripts/benchmarks/concurrency-settlement.ts --state /tmp/esabong-bench-latency-v2 \
  --out docs/benchmarks/2026-11-02/latency-optimized-v2 --ws-update-rounds 20 --ws-update-interval-ms 100
node scripts/benchmarks/concurrency-settlement.ts --state /tmp/esabong-bench-latency-v2 \
  --out docs/benchmarks/2026-11-02/latency-optimized-burst-v2 --scenario websocket \
  --ws-update-rounds 20 --ws-update-interval-ms 100 --ws-update-concurrency 8
```

These database, state and output names now exist and are retained; choose new names for another run, then archive it with `evidence.py archive`. Private fixtures, JWTs and per-worker traces stay outside Git. No production traffic, deployment, migration of the application database, commit or push was performed.
