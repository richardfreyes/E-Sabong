# rc.6 / rc.7: approved Declarator fixes, UAT validation (2026-11-17)

UAT only; production untouched. Deployed `uat-20261116-rc.7` = `1cdf073` (rc.6 `f0a06b5` plus a desk tally fix), schema `0040`.

## What changed

| # | Fix | Where |
| --- | --- | --- |
| 1 | Manual ghost clicks go through an ordered queue: FIFO dispatch, **≤ 2 in flight**, capacity 64. Stop and Close are never refused because another desk action is running. They block new clicks at once, cancel unsent ones with a visible report, and are sent immediately. In-flight clicks finish; the server decides them and refuses any after Close. Every click is counted once: added, refused or cancelled. | `declarator/ghost-queue.ts`, `dashboard.ts/html` |
| 2 | WebSocket `?token=` redacted from uvicorn log lines (Caddy already stripped it). | `core/logging.py` |
| 3 | Betting opens only on an **in-progress** fight: `409 fight_not_started` on every route. The Admin fight-book offers Open only for started fights. | `betting/service.py`, `pages/fight-book.ts` |
| 4 | Desk version gate. The web client sends `X-ESB-Client: 2`. `/api/v1/declarator/*` answers **426 `client_outdated`** to older or unversioned clients before any query (`desk_min_client_version`, 0 disables). The desk shows a reload banner when a new build is served. After a 426 it stops calling Declarator APIs and reloads once pending clicks finish. | `declarator/router.py`, `shared/services/app-version.ts`, interceptors |

## Regression (isolated test DB and Redis; valid)

- Backend: **617 passed**, 0 failed (609 before plus 8 new: start-before-open, the desk gate, redaction).
- Frontend: admin 164, owner 104, player 119 = **387 passed**. Owner, admin and player production builds pass.
- Local real stack (real API, PG and Redis plus production desk bundle), 3 min: 9 cycles, 308 manual ghost clicks through the queue, 0 errors.

## UAT deployment checks

- `X-ESB-Client` empty or `1` → **426 `client_outdated`**; `2` → passes the gate (401 without a token).
- Since deploy, uvicorn WebSocket lines show `token=[redacted]` (3 of 3); **0** JWTs in API logs; **0** `token=` in Caddy logs.

## Live smoke on rc.7: **passed** (9.7 min, 0 failures)

- **12/12 desk cycles.** Meron, Wala, Draw and Cancel each ×3. **90 player bets** each verified against the payout rules; settlement reached every connected socket once; history and audit present.
- An Admin closing betting reaches the open desk in **1.2 s**; an Admin starting and opening a fight in **1.9 s**. A newly activated event appears in **3.0 s**. No reloads.
- **Tenant isolation:** 16/16 cross-tenant probes refused (404); 0 WebSocket frames leaked.
- **Money:** tenant A wallets +20,407.10 = 20,000.00 synthetic grants + 407.10 net play (900.00 staked, 1,307.10 returned).
  Tenant B 2,003.00. Both reconciliations balanced. **12,420** pre-existing rows unchanged.
  In every tenant, wallets equal the ledger exactly. 0 open bets, 0 active ghost jobs after the run.

### Stop / Close under bursts (Stop clicked right after the burst; the 2-in-flight cap applies)

| Burst | Queue when pressed | Added | Refused | Cancelled | Click → request sent | Request → answer | Total |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 8 → Stop | 2 sending · 5 queued | 3 | 0 | 5 | 35 ms | 0.48 s | **0.82 s** |
| 16 → Stop | 2 · 12 | 3 | 0 | 13 | 33 ms | 0.29 s | **0.82 s** |
| 32 → Stop | 2 · 29 | 2 | 0 | 30 | 33 ms | 1.80 s | 2.34 s |
| 8 → Stop | 2 · 5 | 2 | 0 | 6 | 31 ms | 0.61 s | **0.82 s** |
| 16 → Stop | 2 · 13 | 2 | 0 | 14 | 35 ms | 2.84 s | 3.34 s |
| 32 → **Close** | 2 · 27 | 3 | 1 | 28 | 32 ms | 1.57 s | 1.83 s |

Every burst: added + refused + cancelled = clicks, and "added" equals the server's book. Nothing was accepted after Close,
whether ghost or player (409), and the book did not change afterwards.

**Stop: median 0.82 s, p95 3.34 s (5 samples). The < 1 s p95 target is not met on UAT.**
The browser part is solved: click → request was **about 4.5 s on rc.5 and is 31–35 ms now**. That was the old desk's 8 parallel ghost requests
plus a desk action guard that could silently drop Stop. The remainder is server-side: the UAT API is capped at 1 CPU / 2 workers
and is CPU-throttled in about 66% of scheduling periods (measured by the parallel session). Every ghost write also takes the event
row lock (`_betting_desk`, `FOR UPDATE`), the same lock Stop needs. So Stop still waits for the ≤ 2 in-flight clicks and any auto-ghost tick.
Desk actions p95: close 2.3 s (rc.5: 6.9 s), declare 4.1 s including dialog (7.1 s), auto-stop in normal cycles 3.3 s.

## Token exposure assessment

UAT container logs use Docker `json-file` (3 × 10 MB). Every deploy recreates the container and deletes its old logs. No log
shipping is configured, and the deploy job logs contain no JWTs. Exposed values were 15-minute access tokens (UAT uses the default lifetime),
and refresh tokens never appear in URLs. All tokens logged before the fix are expired, and their logs are already gone.
**Production still runs the old logging.** Its current API logs very likely contain live 15-minute WebSocket tokens. Checking
production was blocked as out of scope; it needs your authorization, and the fix ships with the production release.

## Remaining issues and risks

1. **Stop p95 above 1 s on UAT.** Cause: server CPU and the event lock, as above. The parallel session is reworking ghost writes to lock only the book row,
   at your instruction; it also plans to raise the 2-in-flight bound. Re-measure after that lands.
2. The Admin "pre-open the next fight" flow is gone by design (#3). Supersede only clears a book left open on another fight.
3. Any script or tool calling `/api/v1/declarator/*` must send `X-ESB-Client: 2`.
4. Result-history beads are uncapped per event (about 19 DOM nodes per fight); fine for a normal derby.

## Production readiness

**Not ready for production approval yet.** Item 1 is in progress in another session, and rc.7 must be re-validated after it.
A production plan also needs a production backup, migration 0040 with its rollback, a forced reload of every open Declarator desk
(the gate does this automatically once production runs rc.6+), and explicit authorization.
