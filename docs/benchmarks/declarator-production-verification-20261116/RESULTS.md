# Declarator release verification: results (2026-11-16/17)

This supersedes the "pending" statements in `README.md`. Times are local (UTC+8).

## Real soak, matched 30-minute baseline vs optimized (3 hours)

Real production Angular assets, FastAPI, PostgreSQL 17, Redis, ghost runner, 12 native player sockets,
3 player browsers, 1 Declarator browser, no request interception. The first clean attempt (19:16) died
at about 15 minutes when the agent session that launched it ended. No sleep or kill was logged, and its
last status showed 0 errors with books balanced. It was relaunched detached at 19:39 (`run-manifest.json` → `restart_r2`).

| At 30 min (matched) | Baseline `03cb20d` | Optimized |
| --- | ---: | ---: |
| Result | passed, 83 cycles, 0 errors | passed, 83 cycles, 0 errors |
| Main-thread time per interval, min 5 → min 30 | 1.85% → 3.68% (**+99%**) | 2.80% → 3.54% (+26%) |
| Layouts/s (desk freshness) | ~4.2 (debounce starves under traffic) | ~8.5 |
| DOM nodes | 6,396 | 5,534 |
| API RSS | 156.7 MiB | 121.5 MiB |
| API CPU time | 311 s | 348 s (more single-book reads) |

3-hour optimized gate: **passed**. 10,803 s, 493 cycles, 60,347 bets (15,278 manual ghost),
1.65 M frames, 0 errors, reconciliation balanced at every checkpoint, no duplicate settlement events
(11 players received all 493 settlements live; 1 recovered once from canonical state). Ghost jobs went
back to 0 and the legacy retry array stayed empty.

| Gate | Budget | Result |
| --- | --- | --- |
| Action p95 | < 2 s | open 132 ms, close 129 ms, manual ghost 48 ms, auto start/stop 139/90 ms, declare 1.16 s (includes confirm) |
| Action max | < 5 s | 1.67 s |
| API RSS growth after warm-up | ≤ 64 MiB | +52 MiB (118.7 → 170.6), level since ~45 min |
| Heap after GC | < 50 MiB | 20.1 MB |
| Long tasks | — | 1 in 3 h (66 ms) |

Residual growth: main-thread time rises 3.4% → 5.2% over 3 h with the history-bead board
(`app-history-beads`, one clickable bead per fight; 13k nodes at 493 fights). A real ~100-fight derby
stays near 3%. Capping or virtualizing the bead board would remove this.

Evidence: `real-optimized-3h-*.json[l]`, `real-baseline-30m-*`, `real-30m-matched-comparison.txt`,
`real-optimized-10m-rc3-result.json` (after the rc.4 desk fix: identical cost, 16.9 s vs 17.0 s task time at 10 min).

## Production (old code) vs UAT (new code), server-side

Production `prod-20261109.1`, last 24 h of real use (2 CPU / 4 workers):

| Endpoint | Calls | p50 | p95 | max |
| --- | ---: | ---: | ---: | ---: |
| Desk full monitor (refetched per book update) | 17,634 | 1.47 s | 19.4 s | 100 s |
| Manual ghost bet | 933 | 1.70 s | 32.7 s | 60 s |
| Records | 17,688 | 0.48 s | 6.5 s | 41.5 s |

UAT rc.1 to rc.4 under a deliberately harsher synthetic load (1 CPU / 2 workers): manual ghost bet p50 0.67 s
(max 5.1 s at 4 clicks/s), monitor max 1.0 s, new single-book read p50 0.28 s.

## UAT

| Release | Content | Result |
| --- | --- | --- |
| rc.1 `bc91c61` | Declarator release + 0040 | writers drained, backup `uat-esabong-20261116T144658Z.dump` **RESTORE VERIFIED**, 0039→0040, 1,689 retry IDs migrated, legacy arrays empty, forced RLS + grants verified, new bundle served |
| rc.2 `9500d50` | reconciliation counts `earnings` wallet entries | uat-demo `balanced` false → true (the +2,032.00 earnings gap had existed since 2026-11-09, d236eba) |
| rc.3 `b82d51d` | (peer session) official-arena admin events | deployed by the peer session |
| rc.4 `0718571` | desk switch follows externally opened betting | live smoke: 12 full cycles passed (owner open/close/result, judge 403 `owner_controlled`, auto + manual ghost, desk state, settlement on 4 player sockets, reconciliation balanced each cycle); 5,022 protected rows unchanged |
| rc.5 `a6eb9ee` | (peer session) independent tenant events | deployed by the peer session; smoke spec must move to the tenant admin/desk API |

Findings from the live UAT runs:

1. **Fixed (rc.4).** Opening betting publishes only `book.updated`. The optimized desk refreshed only the live
   book on it, so betting opened by the owner or an admin left the Open/Close switch stale for up to 30 s.
2. **Fixed (rc.2).** The reconciliation formula did not count `earnings` entries.
3. **Open.** Every manual ghost bet takes the event row lock (`_betting_desk`), as Stop, Close and auto ticks
   do, so betting writes on one event run one at a time. Stop issued behind N in-flight manual ghost bets
   waits about N × transaction time. On UAT's 1-CPU API that is about 0.6 s each, and the desk allows 8 in flight.
   Cycle 13 failed when Stop took 4.7 s. This design predates the release, and production shows it worse.
   Options: lower the desk's in-flight cap (clicks over the cap are ignored), or review lock scope.
4. **Harness.** In shared mode at the time, players could see only the global event, so UAT smoke players only listened for settlement.
   Cancel needed verification on UAT, so `no_contest` was used. Cleanup now covers setup.
5. **Pre-existing, outside scope.** Uvicorn access logs on UAT include the WebSocket `?token=` query string.
