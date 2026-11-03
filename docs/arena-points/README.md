# Arena Points retirement and migration runbook

## Current verification status

The [final readiness review](../readiness/2026-11-02/REPORT.md) supersedes earlier test counts
and readiness claims. Current results: **194 backend integration + 108 unit, 190 frontend,
28 E2E**, a separately passing **306-capture** UI job, three portal builds and API/web Docker
builds. Technical staging is **conditionally ready**; public launch is **not ready**.

The local source is still **0012**; **0013–0015 are pending** there. The new rehearsal on a
separate restored database verified archive/gameplay row hashes through
`0012→0013→0012→0015→0014→0013→0015`, with no merges or lost rows. A quiescent scratch restore
matched **47 tables / 13,502 rows / 39 RLS policies / 27 triggers** at 0015. The initial
active-source comparison had a usage-counter mismatch and is reported as such.

The old retirement-only steps below remain applicable to 0013, but the complete current
release requires the reviewed 0013→0014→0015 chain. Do not downgrade a populated simulation
ledger. Production and candidate staging deployments/migrations still require explicit
approval. The old development stack was intentionally left unchanged.

Arena Points are the sole player gameplay balance. The header, gameplay panel and
statement read the same `PlayWalletStore`, backed by `play_wallets` and its append-only
ledger. Meron stays red, Wala blue, Draw green. Amounts use Arena Points with two decimal
places; there is no extra header wallet or dummy balance. Unavailable balances render a
dash. PHP remains confined to tenant software subscription billing.

Arena Points cannot be bought, sold, transferred, redeemed or exchanged for cash by
players, agents or admins. Existing audited starting allocations, bet acceptance, result
processing, refunds and corrections remain system controlled. No conversion path exists.

## Audit and contract changes

- `point_accounts`, `point_transactions`, `point_ledger_entries`: preserved separately.
  Their balances are never added to or exchanged for Arena Points.
- Legacy allocation, transfer, adjustment and refund routes, services and UI controls
  have been removed. New users/tenants receive no legacy points accounts. Existing
  permission codes remain recognizable for historical role assignments but confer no
  write operations and are absent from the assignable permission catalogue.
- `/admin/points` remains an authorized read-only archive, including account balances,
  transaction details, inclusive Manila date filters, search, pagination and CSV export.
  Existing RLS, tenant and downline checks remain. Historical audit records survive.
- `/portal/points` and `/portal/points/history` are removed. Old player UI bookmarks at
  `/points` redirect to `/arena-points`. `/portal/dashboard` has no legacy balance fields.
- `GET /portal/betting/entries` returns only the caller's Arena Points entries; a supplied
  foreign user ID cannot change the scope. Decimal strings retain gameplay precision.
- Admin account and hierarchy APIs no longer mix legacy balances into user identities.
  All preserved amounts remain available through the archive.
- Migration **0013** adds rejecting INSERT/UPDATE/DELETE triggers only. It performs no
  data updates, deletes, renames, balance merges or conversion. Earlier immutable-ledger
  guards, references, audit trails, indexes, RLS and grants remain in place.

The generated [OpenAPI contract](../openapi.json) and [API guide](../api.md) reflect this.

## Dry run and production preparation

Production changes have **not** been executed. The following commands are for an operator
to review and run after approval, with credentials configured for the intended target.
Do not point a migration command at production during a rehearsal.

1. Record the release and `alembic current`; this release expects revision 0012.
   Freeze application and worker writes and drain requests before collecting comparisons.
   This also prevents background usage counters from changing during full backup comparisons.
2. Run the read-only archive report using the platform identity that sees all tenants:

   ```bash
   cd api
   .venv/bin/python -m app.cli points-archive-report --json /secure/path/points-before.json
   .venv/bin/alembic upgrade 0012:0013 --sql > /secure/path/retire-points.sql
   ```

   Exit code 1 from the report means an integrity discrepancy; investigate before proceeding.
   It checks stored balances against ledger sums, two balanced entries of the correct amount
   per transaction, net supply and full-column SHA-256 fingerprints per table/tenant. The CLI
   uses a repeatable-read, read-only transaction; it never repairs data.
3. Back up the database and media with the existing `db/scripts/backup.sh`. Keep its checksum
   and copy off-host under restricted access. Run `db/scripts/verify-backup.sh <dump>` against
   a scratch database. It verifies restore/checksum, every table's row count and content hash,
   policies, FORCE RLS, grants, triggers and revision. Restored objects use `esabong_owner`.
   Keep source writes paused until comparison is complete; a changed live counter is a real
   comparison mismatch, not evidence that the dump is corrupt.
4. Restore into an isolated rehearsal environment, configure **all three database URLs**
   for that scratch DB, and isolate Redis. Apply 0013 there, repeat the report and compare
   JSON exactly. Rehearse rollback, then reapply 0013. Run the tests against this copy.
5. Only after approval, with the source still in maintenance mode, run
   `.venv/bin/alembic upgrade 0013` on the approved target. Deploy compatible API, worker
   and frontend artifacts together. Repeat the report and require an exact before/after match.
   Check archived reads, refused legacy writes and Arena Points reconciliation before reopening.

Never restore an old whole-database backup over active gameplay or billing activity merely
to undo this schema change. Keep backups, per-tenant reports and credentials out of Git.

## Rollback

Prefer rolling back application artifacts while keeping 0013's write guards in place.
Old clients may display obsolete controls, but the database must continue to reject writes.

If the trigger migration itself must be reversed, keep maintenance mode active and run
`.venv/bin/alembic downgrade 0012` **only when current revision is 0013** and after approval.
This drops only the three retirement triggers and their function. It preserves every row
and the older immutable-ledger guards. Keep this release's read-only API, prevent older
points-writing processes from starting, compare archive fingerprints again, then fix and
reapply 0013 before reopening. Do not re-enable transferable activity as part of rollback.

## Local evidence, 2026-11-02

The local source database stayed at 0012. A backup was restored into
`esabong_restore_check`; migration, rollback and browser tests used that separate database,
API port 8001 and frontend ports 4300–4302. No production migration or deployment occurred.

- Verified restore: **41 tables, 13,383 rows, 33 RLS policies, 8 original triggers**;
  every table's content hash, counts, policies, grants and revision matched the source.
- Rehearsed **0012 → 0013 → 0012 → 0013**. Archive reports matched across upgrade and
  rollback for **34 tenants, 458 accounts, 177 transactions, 354 entries**, including
  all stored-column hashes and **2,714,200** historical user points. No inconsistencies.
- [Regression results](verification.md), [machine-readable evidence](verification.json),
  [backup verification](backup-verification.txt) and [reviewed migration SQL](migration-0013.sql)
  are saved alongside this runbook.
  Synthetic extreme-value browser reads are test-only; gameplay and result checks use
  the real scratch API and database.

The development dump remains outside Git at `/tmp/esabong-arena-before.dump` with a
SHA-256 checksum. This local drill does not substitute for a production backup/restore
verification on the approved deployment target.
