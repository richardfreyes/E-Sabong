# Declarator release gates and recovery

Production deployment requires a separate explicit authorization after UAT and every gate below pass. The current user request authorizes preparation and UAT deployment, not production deployment.

## Gates

1. Maintained backend tests, frontend tests, production builds, syntax/lint checks pass. Every original failing/error test has a baseline comparison and root cause in `failure-matrix.csv`. Conflict-copy files remain in Git but are excluded from collection; their maintained counterparts run.
2. Real frontend/backend soak reaches 1,800, 3,600, and 10,800 elapsed seconds with live PostgreSQL, Redis, ghost runner, twelve bettors, native player sockets, a Declarator browser, and three player browsers. Reconciliation must remain balanced at every checkpoint; no pool mismatch, duplicate settlement event, browser error, or failed lifecycle operation is accepted. Token rotation must retain twelve native sockets and sixteen total server subscriptions. Growing result history is distinguished from leaked DOM/listeners. After the 30-minute warm-up, the API RSS budget allows at most 64 MiB additional resident memory; browser heap must stay below 50 MiB after GC. DOM nodes and listeners are evaluated against visible result-history growth rather than requiring a constant count while new fights are being added.
3. Responsiveness: all critical UI action p95 values under 2 s; all action completions under 5 s, excluding deliberate pauses and confirmation input. Request metrics, CPU time, resident memory, heap after GC, DOM/listener counts, connection pools, ghost jobs and array size are retained. Actual 3 h domain history may grow; no uncontrolled subscriptions, timers, jobs or queues are accepted.
4. Migration 0040 upgrade/downgrade/reapply passes on a restored UAT copy, preserving every existing application table and retry ID; forced tenant RLS is verified. Trial on 2026-11-16: 65 tables, 1,689 IDs, upgrade 560.85 ms, downgrade 154.60 ms, reapply 587.85 ms.
5. UAT release deploy uses a drained, fresh backup and strict restore verification. The earlier online drill restored successfully and matched financial data but differed in mutable `tenant_usage`; it is not accepted as the final strict release backup gate.
6. UAT `/api/health`, `/api/ready`, tenant isolation, private receipt visibility, betting/ghost/result/settlement behavior, socket rotation and service health pass after deployment. UAT currently has shared-arena mode enabled; its OWNER-controlled official actions must remain enforced. Do not disable shared-arena mode or settle existing UAT fights just to satisfy a smoke test.
7. Record exact release SHA, image IDs, schema revision and backup path. Obtain explicit production authorization for that SHA only. Recheck production's live revision, isolation, health and backup immediately before any production mutation.

## UAT deployment

- Existing UAT checkout: `/opt/esabong/uat`, Compose project `esabong-uat`; production is a separate sibling project and is not a deployment target in this phase.
- Build and preflight the exact release without migration approval first. Pending migrations must stop the script before service changes.
- Once local gates pass, run with the stack-specific migration approval `APPROVED_MIGRATIONS=uat:0040`. The revised deploy script gracefully stops beat, worker and API **before** backup and migration, so no old ghost runner can append legacy arrays after backfill.
- Save and restore-verify a fresh non-pruned database/media backup while writers are stopped. Then migrate and recreate API/worker/beat/Caddy. Check migration exit status and service health.
- Do not change resource limits, prices, payout math, odds configuration, commissions or existing user balances. Use isolated synthetic fixtures for smoke traffic.

## Recovery

- Before schema migration begins: stopped old containers retain their old image IDs. If a backup/preflight gate fails, no schema change has occurred; investigate, then restart the unchanged containers with `docker compose start api worker beat` if abandoning the deployment.
- After 0040: drain all writers again and take a new backup of current data. Record receipt counts, ledger/wallet reconciliation, audit/declaration counts and hashes. Using the **new release image**, run `alembic downgrade 0039`; this copies all new and historical ghost receipts back to the legacy arrays before dropping the receipt table. Verify receipt membership and financial/audit fingerprints, then recreate services from the previous image/release.
- Never simply restart old array-writing API code against 0040, and never restore the pre-release dump over valid post-release betting activity. A dump restore that would discard activity requires an explicit recovery decision and accounting review.
- Keep both backups, release refs and manifests. A failed health check leaves release gates closed; do not continue to production.
