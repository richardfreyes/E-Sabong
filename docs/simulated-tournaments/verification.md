# Neutral engine verification — 2026-11-02

| Check | Result |
|---|---|
| Full backend integration suite, PostgreSQL/Redis | 192 passed |
| Backend unit suite, including exact-cent neutral math | 107 passed |
| Angular unit suites | 190 passed (owner/shared 57, admin 58, player 75) |
| Full Playwright suite against isolated live API | 28 passed |
| Owner/admin/player production builds | All passed |
| Ruff and Python compile checks | Passed |
| Generated OpenAPI matches the application schema | Passed |
| Offline migration SQL | 0014:0015 generated without a DB connection |
| `git diff --check` | Passed |

Integration uses only disposable `esabong_test`; its repeatable reset removes test-only
history before rebuilding migrations. The live browser suite used the previously restored
`esabong_restore_check` upgraded through 0014 to 0015, Redis DB 13, API port 8001 and frontend
ports 4300–4302. Neither the source database nor production was migrated.

The neutral browser test uses real HTTP requests and a browser's native WebSocket. It
creates an enabled arcade arena and pending match, opens wagering, receives room state
and live odds, places exact decimal stakes on both sides, receives final-call countdown,
rejects a late new stake, closes/runs/declares/settles, replays the settlement receipt and
verifies the players' shared Arena Points balances and reconciliation. Test settings are
restored afterwards; records remain available for audit in the scratch environment.

Backend concurrency checks include:

- Twelve concurrent stakes against one balance never overspend.
- A separate DB connection closes the match outside the worker mutex; the stale serializable
  wager snapshot is discarded, retried and rejected without a debit.
- Identical committed wager/transition keys replay, changed payloads conflict, and repeated
  settlement requests retain one immutable receipt with no duplicate postings.
- Failure after ledger posting or after broadcast registration rolls back state and balances
  and publishes no room event. Failure partway through a multi-line settlement rolls back
  every credit and the receipt; retry succeeds once.
- Arena/tenant room separation, invalid JWTs, live assignment revocation and caller-only receipts
  are exercised. Neutral receipts remain private/readable after an arena is disabled.
- DRAW, CANCELLED, empty and one-sided matches refund with no fee. Integer-cent remainder
  allocation conserves the virtual pool and fee exactly; inputs reject NaN/fractional cents.
- Once-only neutral wallet allocation uses the same wallet read by the existing gameplay API,
  including when the older gameplay switch is off. No transferable legacy point account is created.

The local room-state test asserts normal commit-to-client delivery under one second. This
is local evidence, not a production latency SLA. Redis pub/sub is ephemeral; reconnecting
clients must read authoritative snapshots. No Socket.IO compatibility is claimed.

No frontend screens were added in this backend phase; existing UI regressions/builds and
the added live protocol test passed. No commit, push or deployment was performed.

See [machine-readable results](verification.json) and [browser scenario results](e2e-results.txt).
