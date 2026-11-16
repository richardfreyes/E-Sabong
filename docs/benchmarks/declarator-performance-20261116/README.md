# Declarator performance investigation — 2026-11-16

Local PostgreSQL 17/Redis, FastAPI ASGI integration tests, and Chromium using the Angular production build. Untouched source baseline: `03cb20dbe962b04f49d449a56b54532cd6efe8f3`. No production traffic, balances, database, or deployment was modified.

## Root causes and changes

1. Every `book.updated` invalidated monitor, paginated official records, ghost status, and online users. A monitor rebuilt every historical fight's player stakes and repeatedly queried betting settings. Continuous traffic could also starve the 150 ms debounce; slower reads were repeatedly cancelled by `switchMap`. The desk now refreshes a single authorized fight book, limits reads to one in flight plus one trailing invalidation, and immediately cancels reads when their scope changes. Structural/result events still refresh canonical history. Thirty-second reconciliation and reconnect refreshes remain.
2. Each manual/automatic ghost addition copied and rewrote an ever-growing UUID array on `fight_books`. At one-second automatic intervals with two sides, that means 21,600 IDs after three hours. Migration 0040 transfers existing IDs to indexed, tenant-scoped receipt rows and empties the legacy array. New receipts, pools, and audit entries commit in the same transaction under the existing event/fight/book locks. Historical retry protection survives both upgrade and downgrade.
3. Unbounded tables rendered every player's sides and all settlement rows. The desk now renders 25 rows per page, keeping every entry accessible. Quick-button labels and history statistics are memoized; history match arrays retain their identity on live updates. Optimistic ghost projections are pruned when the fight changes. Quick bets allow at most eight concurrent requests; other conflicting desk operations cannot start together.
4. Auto ghost betting formerly published two aggregate book snapshots per transaction when both sides were enabled. Only these uncommitted aggregate snapshots now coalesce to the final snapshot. Audit, declaration, close, settlement, and personal financial events remain separate. No betting arithmetic, prices, permissions, or settlement posting rules changed.
5. A stalled WebSocket send could hold a Redis subscriber indefinitely. Sends now have a five-second deadline; slow clients close with 1013, reconnect, and reload canonical state. Socket shutdown cancels **and awaits** all forwarding/receive/guard tasks before releasing presence and pub/sub resources. Existing one-socket/reconnect guards remain.

## Measurements

Raw evidence is in the adjacent JSON files. Five API samples per fixture size; browser metrics use Chrome DevTools Performance, with explicit garbage collection for heap samples. `TaskDuration` is browser main-thread task time, not host-wide CPU utilization. API RSS is process high-water RSS on macOS, not current resident memory.

| Measurement | Untouched baseline | Optimized |
| --- | ---: | ---: |
| Full monitor SQL statements, 1 book | 23 | 22 |
| Full monitor SQL statements, 20 books | 42 | 22 |
| Per-update SQL at 20 books | 42 (full monitor alone) | 11 (single book) |
| Per-update response at 20 books, no real player rows | 27,687 bytes | 732 bytes |
| DOM nodes after 60 s, 1,000 bettors | 37,681 | 2,133 |
| Rendered live table rows | 2,001 | 25 |
| Main-thread task time during matched 60 s replay | 3.021 s | 1.440 s |
| Heap at 60 s after GC | 13.78 MB | 10.62 MB |
| Long tasks during 60 s (>50 ms) | 3 (maximum 82 ms) | 0 |
| Live refreshes during 60 s | 3 full monitor refreshes (debounce starvation) | 373 single-book refreshes |

Both 60-second runs replayed the same workload algorithm, with 1,000 bettors, five events per burst, 50 ms between bursts, and a manual quick bet each 100 events. Actual event totals depend on browser speed. The optimized version refreshed more often while consuming less main-thread time. These are synthetic frontend measurements, not production latency guarantees. Tiny API fixtures did not show severe latency by themselves; the measured improvement is bounded query work and much smaller per-update payloads, not a claimed universal latency percentage.

### Ten-minute real-time browser soak

- 47,935 incoming book updates and 479 quick bets; one WebSocket throughout.
- 3,299 single-fight reads; 21 monitor and records refreshes, rather than history per book update.
- 0 browser errors and 0 long tasks.
- At 30 s: 2,133 DOM nodes, 172 listeners, heap 10.41 MB after GC.
- At 600 s: 2,133 DOM nodes, 172 listeners, heap 10.72 MB after GC.
- Main-thread task time: 19.56 s over 600 s, about 3.3% of elapsed time.

The ten-minute test preceded final refinements to scope handling and projection pruning. The matched 60-second optimized run uses those refinements. Table/search changes are additionally covered by frontend unit tests.

### Financial and lifecycle checks

Real PostgreSQL/Redis tests exercise concurrent retries, both automatic ghost sides, rapid manual bets, open/close, all five outcomes, repeated declarations, next-fight creation, balanced wallet/ledger reconciliation, pool reconciliation, authorization, cross-tenant receipts, and migration upgrade/downgrade. The default sustained workflow test runs 20 cycles; increase `DECLARATOR_LOAD_CYCLES` for larger fixtures.

Three **simulated** hours of one-second refresh traffic verify that timers, subscriptions, and request counts remain bounded and clean up on destruction. Separate tests verify a slow read is allowed to finish despite 10,000 invalidations, with only one trailing read.

Actual 30-minute, one-hour, and three-hour wall-clock end-to-end soaks have **not** been run. Browser traffic here uses replayed HTTP/WebSocket responses; backend transactional tests use the real local database/Redis. This does not certify production capacity, network reconnect behavior over hours, or production query plans at production data volumes.

## Reproduce

Run backend tests only against the disposable databases enforced by `tests/conftest.py`; they recreate their test schema.

```sh
cd api
DECLARATOR_METRICS=/tmp/declarator-api.json .venv/bin/python -m pytest tests/test_declarator_performance.py tests/test_ghost_receipt_migration.py tests/test_websocket.py unit_tests/test_realtime_coalescing.py -q -s
DECLARATOR_LOAD_CYCLES=100 .venv/bin/python -m pytest tests/test_declarator_performance.py -q -s
```

```sh
cd web
npm run build:all
npm run test:unit
DECLARATOR_BROWSER_SECONDS=600 npx playwright test --config playwright.declarator-perf.config.ts
# Repeat with 1800, 3600, or 10800 for real-time longer frontend replays.
```

The dedicated Playwright config serves only local production assets on 127.0.0.1:4301 and intercepts all API/WebSocket traffic. Its replay test is excluded from the normal integration E2E config. It requires existing Playwright Chromium installation. Each run attaches its JSON metrics to test results.

## Release and rollback

This change is local and not deployed. Drain API writes and stop all old API replicas/ghost runners before applying migration 0040, then start the new backend and publish the new frontend. Do not mix old array-writing ghost code and new receipt-writing ghost code during rollout. The migration temporarily lifts FORCE RLS for its owner-only backfill, then restores it within the migration transaction; tenant application grants never change for existing financial tables. Downgrade copies all receipts back before removing the receipt table. Backfill time and locks depend on existing receipt volume, so measure that on a staging copy before release.

## Verification status

- Frontend: 372 tests passed (owner 99, admin 154, player 119); all three production builds passed. Existing stylesheet budget warnings remain.
- Focused real backend verification: 95 tests passed, including receipt migration round-trip, forced RLS inventory and isolation, all Declarator lifecycle actions, automatic ghost jobs, duplicate requests, and slow WebSocket cleanup.
- Browser: 10-minute replay passed; matched 60-second baseline/final runs passed; final replay gates for heap/listener/DOM growth and minimum live-refresh rate passed.
- Full backend comparison: the first modified run and baseline replay each had the same 124 failing/error test identifiers, including old ` 2.py` copies and existing pricing/video/rental/migration expectations. The baseline replay carried migration 0040 only so its migration reader could understand the test schema; the new receipt table was not used by its application code. Its expected-table inventory failure is an artifact of that setup. The modified inventory check was subsequently fixed by registering the new table and passes in the focused run. A final full-suite run is recorded below.

The full backend suite is not green. Pricing/commission arithmetic was deliberately preserved, so old tests expecting different payouts were not made to pass by changing outcomes. Full UI E2E against a live backend, production-size query plans, and actual multi-hour soaks remain unverified.

Final full backend result: **742 passed, 84 failed, 39 errors** in 283.84 s. None of the remaining failing/error test identifiers is unique to the final changes. `backend-failures.txt` lists them; `verification.json` records both full runs. The final result improves the schema inventory check after registering the new table. The full-suite acceptance criterion remains unmet because of the existing failures.

At 20 books, five-sample median API timings were 14.78 ms before (full monitor), 13.30 ms after (full monitor), and 8.72 ms after (single book). Different local concurrency during these small runs means these are descriptive timings, not a controlled latency-speedup claim. API baseline process CPU/RSS were not captured; the CPU/heap before-and-after comparison above comes from Chromium.
