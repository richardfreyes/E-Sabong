# rc.5 UAT smoke: independent tenants (2026-11-17)

Target `uat-20261116-rc.5` = `a6eb9ee` on `release/declarator-perf-20261116`. UAT only; production untouched.
Spec: `web/e2e/performance/declarator-uat.spec.ts`. Fixture and cleanup: `infrastructure/scripts/{seed,verify,cleanup}-declarator-uat.py`.

## Deployment (verified independently before testing)

- Checkout `a6eb9ee`, tag `uat-20261116-rc.5`. Deploy log: "Deployed uat-20261116-rc.5".
- Alembic `0040 (head)`. api, worker, caddy, postgres and redis all healthy; beat and mediamtx up; Redis PONG.
- `/`, `/admin/`, `/owner/`, `/api/health` and `/api/ready` all return 200.

## Regression suites (valid, isolated)

- Backend: **609 passed, 0 failed, 0 errors**. Pinned git worktree at `8330f6d` (rc.5 code plus docs), dedicated DB
  `esabong_test_declarator_iso`, dedicated Redis on :6390, so the parallel session cannot reset it.
  An earlier run showing 90 errors was invalid: the shared `esabong_test` DB was reset mid-run by the other session.
  A first isolated run showing 19 errors was a worktree setup gap (the git-ignored `.env` with the local superuser password was missing).
- Frontend: owner 99, admin 156, player 119 = **374 passed**. All three production builds succeed.

## Live smoke: **passed**

Tenant A is synthetic accounts in `uat-demo`, using the real Declarator browser desk, never reloaded.
Tenant B is a synthetic tenant (`development` plan, trial) provisioned for the run, driven by its own Admin and Declarator over the API.

| Check | Result |
| --- | --- |
| Full desk cycles (open, auto + manual ghost, player bets, close, declare, settle) | **12/12** (Meron, Wala, Draw and Cancel each ×3) |
| Newly activated event appears in the open desk | yes, 2.2 s |
| Admin closes betting (admin API) → desk shows CLOSED | yes (cycle 6) |
| Admin starts and opens a fight → desk shows OPEN, ghost controls enabled | yes (cycle 7) |
| Every bet verified against the settlement rules (66 bets) | 0 mismatches |
| Ghost totals ≥ manual clicks + auto runs, every fight | yes |
| Live `book.updated` frames to player sockets, every fight | 14–31 per fight |
| `fight.settled` delivered to every connected player, no duplicates | yes |
| Settlement revision 1 per result; none for cancellations | yes |
| Fight history lists every settled fight | yes |
| Audit has `betting.opened` and `ghost.auto_stopped` | yes |
| Tenant B: 2 fights (Meron, Cancel) by its own Declarator | settled, reconciled |

## Tenant isolation: 16/16 refused, 0 leaks

Every cross-tenant call returns **404**, the same as a nonexistent id, so nothing can be learned about the other tenant's fights.
Covered: monitor, book, open/close, ghost bets, declarations, auto-ghost start, player bet, player book, admin event read,
admin result and cross-tenant balance grant, in both directions. Neither tenant's player event list shows the other's events.
**0** WebSocket frames carried the other tenant's event, fight or tenant ids. Tenant B fights moved no tenant A balance.

(A first run reported 422 for one probe: `bet_below_minimum`, the caller's own tenant rejecting a 1.00 stake before
lookup. With a valid amount the response is 404, identical to a nonexistent fight. Not a leak.)

## Financial integrity

- Tenant A, from just before the four synthetic 5,000 grants to the end: wallets +20,231.20 = 20,000.00 grants + 231.20 net play.
  Staked +660.00 and paid+refunded +891.20 both match the 66 verified bets exactly. Reconciliation balanced after every fight.
- Tenant B: wallets 1,999.80 = 2 × 1,000.00 starting − 120.00 staked + 59.80 paid + 60.00 refunded. Commission 0.20 on the Meron fight
  (= pot − paid, as the rule defines); 0 on the cancellation.
- Pre-existing rows: the **7,175** fingerprinted financial and audit rows across 10 tables were unchanged.
- `financial-before.csv` / `financial-after.csv`: in every tenant `wallet_sum == entry_sum`; 0 open bets; 0 active ghost jobs; the only
  unsettled book is the user's own event's (unchanged).

## Performance and Stop

UAT is 1 CPU / 2 workers for the API. During the run, one of the user's own browser tabs had the `TEST` event's desk open on a
**pre-release bundle**. It made about 1,100 requests in 9.5 min (full monitor without `compact=true`), refetching on every
tenant-wide book update. That is the production lag pattern, and it inflated server latency throughout:

| Server-side (this run window) | p50 | p95 |
| --- | ---: | ---: |
| Manual ghost bet | 2.46 s | 7.09 s |
| Player bet | 0.65 s | 5.62 s |
| Full monitor (mostly the stale tab) | 1.49 s | 4.40 s |
| Single-book read | 0.77 s | 3.20 s |

Desk actions (client-observed p95): open 1.8 s, close 6.9 s, declare 7.1 s (includes dialog), auto stop 3.8 s,
manual ghost click 41 ms (UI). Desk browser after 12 cycles: heap 12.9 MB, 3.2k nodes, 283 listeners, no errors.

**Stop under a burst** (16 manual ghost clicks as fast as possible, 8-in-flight cap unchanged): **16/16 accepted, none dropped**.
Stop to INACTIVE took 6.3 s in the browser and **1.6 s server-side**; Close afterwards took 0.8 s.
The previous run measured 2.8 s and 4.7 s respectively. UAT serves HTTP/2, so the client-side remainder is not browser
connection queueing; it is unattributed.

### Event-row locking: still present (code unchanged by rc.5)

Every manual ghost bet calls `_betting_desk`, which takes `SELECT … FOR UPDATE` on the **event** row, then the fight row,
then the book row, and holds all three until commit. Stop, Close, Declare and the auto-ghost tick take the same event lock.
So on one event all of these run single-file, and a Stop issued behind N in-flight manual bets waits for all N transactions.

Recommended, safest first (none of these discards a click):

1. **Desk:** send manual ghost clicks through an ordered client queue with at most 2 in flight, instead of 8 in parallel. Every
   click is still sent and recorded with its own receipt and audit entry, in order. Stop, Close and Declare then wait behind
   at most 2 transactions. Frontend only, no change to betting rules or limits. Needs your approval because it changes how
   quickly a burst of clicks lands.
2. **Server:** profile the per-transaction cost of a ghost bet on UAT-class hardware. Under contention it is 0.4–1.7 s, against
   about 30 ms locally. Then shorten the lock hold, for example by computing the response snapshot after commit.
3. Only after that, consider weaker event/fight lock modes for ghost bets. That changes the locking discipline every
   result path relies on and needs its own concurrency tests.

## New findings

1. **Stale desk tabs after deploy (production risk).** SPA tabs keep running the old bundle until reloaded. Before a production
   rollout: reload all Declarator desks, or add a version check that prompts a reload. Otherwise the old full-refresh behaviour
   continues against the new server.
2. **Admin can open betting on an unstarted fight.** `POST /admin/matches/{id}/betting/open` on a `scheduled` (prepared) fight opens
   the book. Players can bet, but the desk's ghost controls are disabled and the Declarator cannot declare ("A live event and a
   fight in progress are required") until someone starts the fight. Recommendation: require `in_progress`, or start the fight
   the way the desk's open does. Pre-existing; not changed here.
3. **An Admin result on a Declarator-run fight is refused** (`409 verification_required`). This is correct behaviour; the smoke uses
   Admin close/open plus Declarator declarations instead.
4. **WebSocket token in UAT logs:** uvicorn's `uvicorn.error` WebSocket handshake line logged `?token=…`; Caddy already strips it.
   **Fixed in code** (`app/core/logging.py`, `unit_tests/test_log_redaction.py`), proven under real uvicorn
   (`"WebSocket /api/ws?token=[redacted]" [accepted]`, 0 secret occurrences). **Not deployed yet.**

## Cleanup

Seven tenant A fixture accounts, including the temporary owner, suspended. Five synthetic tenant B records (one per run attempt)
suspended, with 20 users suspended and sessions revoked. Every synthetic event is completed, with 0 unsettled synthetic books,
0 open bets and 0 active ghost jobs. Fixture and credential files were removed from the container and locally. UAT is healthy.
History and audit are retained.

## Production readiness

**Not production-ready yet.** Functionally, rc.5 passed everything here. Open items:

- Stop latency under bursts (recommendation 1 needs a decision).
- The stale-tab reload plan.
- The admin open-on-unstarted-fight edge case.
- The redaction fix still has to ship.
- Production must get its own backup, migration (0040) and gates, plus explicit authorization.
