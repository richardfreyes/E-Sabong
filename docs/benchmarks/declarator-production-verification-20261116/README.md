# Continued Declarator production verification — 2026-11-16

This continues the completed optimization; the original investigation and matched frontend replay evidence remain in `../declarator-performance-20261116/`.

> **Final results and deployment status: see `RESULTS.md`.** Statements below marked pending were written before the soaks finished.

## Current evidence

- Maintained backend suite: **609 passed**, including all repaired canonical failures, migration guards and global-source authorization. See `backend-final.txt`.
- All original 84 failures and 39 errors are classified individually in `failure-matrix.csv`. Fifty-three failures came from archived Finder conflict-copy modules; the files remain available, but canonical counterparts are collected. The other failures came from stale accepted-price expectations, cross-test migration/entitlement state, or old video contracts. No betting or settlement calculation was changed to repair them.
- Real frontend/backend soak: the clean three-hour run started at **2026-11-16 11:16:15 UTC** (19:16 Asia/Manila) with gates at 30 minutes, one hour and three hours. It uses actual production Angular assets, FastAPI, PostgreSQL 17, Redis and the production ghost runner. Twelve native player sockets, three player browsers and one Declarator browser use the ordinary fifteen-minute access-token lifetime. The clean run is still pending; interrupted-attempt checkpoints are diagnostic evidence only.
- A matched, untraced thirty-minute baseline runs beside the optimized test against a separate real backend/database/Redis database and the original production frontend. Both prevent host idle sleep. No requests are intercepted.
- A server-side observer confirms sixteen total subscriptions, fifteen player-presence connections representing twelve unique players, one ghost job, a bounded connection pool and empty legacy retry arrays. It is read-only and adds one database observer connection.
- UAT backup: `db/backups/uat-esabong-20261116T101847Z.dump`, checksum verified and successfully restored. Strict source comparison differed only in mutable `tenant_usage` while UAT was active. A fresh drained backup must pass the final strict gate before migration/deployment.
- Restored UAT migration trial passed: 65 existing application tables and all 1,689 retry IDs survived upgrade/downgrade/reapply unchanged; forced RLS remained enabled. Upgrade 560.85 ms, downgrade 154.60 ms, reapply 587.85 ms. Trial target was `esabong_restore_declarator_trial20261116`, never the live UAT database.

## Deployment status

No UAT or production application deployment has occurred yet. UAT remains on revision 0039 and release `03cb20dbe962b04f49d449a56b54532cd6efe8f3`. The three-hour gate remains pending. Production requires explicit separate authorization after every gate passes.

The safe restart, backup, migration, health and rollback procedures are in `RELEASE.md`. In particular, old API/worker/beat writers are drained before the receipt-array backfill, and an old application must not restart against 0040 without first restoring receipt arrays via the supported downgrade.

UAT uses shared-arena mode. Official Open/Close/result actions must stay OWNER-controlled; its Declarator UI must retain tenant ghost controls and enforce that restriction. UAT smoke fixtures must not settle existing fights or change existing balances/settings.

## Test-environment incident and clean rerun

The first optimized attempt was interrupted after about 65 minutes. macOS power logs confirm idle sleep at 19:06:02, dark wake at 19:06:05, another sleep at 19:06:14, and wake at 19:06:51 Asia/Manila. One optimized betting request spanned 36.6 s; the simultaneous baseline hit its 15 s request timeout. PostgreSQL recorded no corresponding slow statement. These attempts are retained as diagnostic/recovery evidence, not accepted as continuously-active release gates. The first 30-minute checkpoint preceded the host sleep.

A new three-hour attempt and a clean untraced 30-minute baseline now run under `caffeinate -i`, which prevents only system idle sleep during their process lifetime. No server resource limit or application timing was increased. The long-run release gate is pending this clean attempt. The earlier `interrupted-attempt-1h.json` belongs to the interrupted attempt and is not a final gate.
