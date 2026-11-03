# Final code and deployment readiness review — 2 November 2026

## 1. Readiness decision

| Gate | Classification | Evidence and conditions |
|---|---|---|
| Technical staging readiness | **Conditionally ready** | Current regression, builds, migration preservation, restore and Compose checks pass. A separate local staging configuration and candidate images are prepared. Deploying that stack, its container restarts, TLS and worker operations still need the user's deployment approval. Peak concurrency targets remain unverified after the Redis fix. |
| Public production launch readiness | **Not ready** | No approved production deployment, target-host verification, external staging load generator, operational alert/off-site backup proof, licensed live-video credentials or legal/operational sign-off is available. The previous 1,000-socket/50 ms benchmark failed. |

This supersedes the earlier readiness claim. The complete prior report is retained as [historical text](previous-production-readiness.txt), not current validation. Tests below were executed in this review; historical baseline counts were not reused as new results. No commits, pushes, resets, production migrations, volume removals or deployments were performed.

## 2. Confirmed issues fixed

| Severity | Finding | Change and verification |
|---|---|---|
| High | WebSocket PubSub connections exhausted the same 100-connection Redis pool used by tenant/auth/API commands. The earlier synthetic burst returned HTTP 500s. | Separate bounded subscription pool (1,024 per worker) and bounded-wait command pool (100, 5-second acquisition timeout), configurable through Compose. Both socket gateways use the subscription pool. Real-Redis tests hold 120 subscriptions while commands remain available and verify waiting for released command capacity. This fixes pool contention; it does **not** certify 1,000-client throughput or the 50 ms SLA. |
| High | A purportedly read-only preflight built/started a Compose project and ran `down -v --remove-orphans`. | Preflight now accepts an already-built `--api-image`, evaluates settings in a network-disabled, read-only, capability-dropped temporary container with no attached volumes, and never runs Compose up/down/build. A regression test verifies these boundaries and that credentials travel over stdin rather than argv. |
| Medium | Restore verification unconditionally dropped its named scratch DB, including a retained prior drill. | It refuses existing scratch databases, validates source/scratch names, accepts `SCRATCH_DB_NAME`, and only cleans up a scratch DB it created. This review used `KEEP=1`. |
| Medium | Verification backups would execute normal retention pruning. | Added `PRUNE_BACKUPS=0` for evidence runs; existing retention behavior is otherwise preserved. No old backup was pruned in this review. |
| Medium | Additional `.env.*` files and operational secret files could enter Git/build context. | Expanded Git/Docker exclusions; example files remain reviewable. Checked exclusion of staging/API env files, monitoring/off-site secrets and dumps. |
| Documentation | Readiness still described play credits, revision 0012 and old successful load tests as current. | Replaced that assessment with measured current results, explicit deployment gates and the failed peak-load history. |

One existing archive-router formatting inconsistency was corrected. No existing migration was rewritten. No dependencies were removed: review did not establish an unused production dependency. The audit tool was installed only in a temporary external venv; application dependency manifests were not changed.

## 3. Arena Points and product boundary

Code review, source searches, unit/integration tests and real scratch browser flows confirm the candidate implementation:

- Header, gameplay available amount and Arena Points statement share `PlayWalletStore` and the same `play_wallets` record/ledger. Unavailable balances render a dash. No CR/PTS chip or separate transferable wallet is player-facing.
- Inputs, returns, staff gameplay reports and Declarator records use Arena Points without PHP symbols. Meron remains red, Wala blue and Draw green; existing gameplay state/result processing is preserved.
- Legacy `point_accounts`, `point_transactions` and `point_ledger_entries` remain separate, preserved and read-only at revision 0013+. Retired write routes/services and new-account creation are absent. Historical read/report/CSV paths retain their permissions, downline filters and tenant RLS.
- Historical archived points may still appear in authorized staff archive reports, clearly separate from Arena Points. Owner monetary reporting concerns software subscriptions, not gameplay balances.
- There is no purchase, cash-loading, withdrawal, cash-out, redemption, exchange or agent-to-player funding endpoint for Arena Points. Simulation fees are virtual accounting, not cash or host commission wallets. PHP stays in subscription billing. Historical legacy-currency subscription records are retained rather than relabeled or converted.
- Browser balances came from the scratch backend's system grants and real entries. The broad visual inventory did not mock balances. Existing explicitly synthetic extreme-value tests remain test-only.

**Runtime qualification:** the untouched development stack still uses revision 0012 and older images. It is not the candidate release and cannot be certified as having the new DB write guards until an approved migration/deployment. No production revision or live-host configuration was available to inspect.

## 4. Git review and files

The [working-tree inventory](git-review.json) records modified/deleted/untracked paths, sizes and hashes against base commit `d885fda`. Existing staged changes were preserved: three retired-points test deletions and two pipe renames. No staging, committing, resetting or deletion of legitimate changes was done.

Reviewed areas include backend/API/RBAC, new simulation services and migrations, archive/report queries, shared models/pipes, all portal changes, deleted obsolete UI, updated tests, benchmark scripts/evidence and infrastructure. Added this round: Redis capacity tests, preflight safety test, optional visual evidence test, staging overlay/example, current readiness evidence and documentation. A limited changed-file scan found no JWT/private-key/AWS-access-key patterns; this is not a comprehensive secret or penetration audit. Dumps, actual credentials and full resolved Compose environments remain outside Git.

Proposed commits are in [COMMIT_PLAN.md](COMMIT_PLAN.md). Large benchmark JSONL and UI PNG evidence are intentionally called out for review; neither `web/dist` nor test reports/cache directories are release source.

## 5. Database and migration status

**Verified local source: 0012. Pending: 0013 → 0014 → 0015; one head.** Revision 0012 is unchanged. Source DB writes were not performed by migration tooling.

- 0013 installs rejecting legacy-ledger mutation triggers without converting/deleting rows.
- 0014 adds tenant-scoped neutral simulation arenas/controllers/matches/stakes/commands and guards, plus additive role permissions.
- 0015 adds immutable settlement receipts and state/open-wager/settlement constraints.
- The rehearsal ran `0012→0013→0012→0015→0014→0013→0015` on a separate restored database. After **each** step, full stored-row SHA-256 hashes/counts matched for all three archive tables, Arena Points wallets and entries.
- Preserved: **458 legacy accounts, 177 transactions, 354 entries, 73 gameplay wallets and 184 gameplay entries**. Zero wallet/ledger mismatches, duplicate wallet groups or unvalidated foreign keys. No balance merge.
- Empty simulation-schema downgrades succeeded in rehearsal. Funded simulation-history refusal and atomic rollback behavior are covered by integration tests. Do not use those empty-schema results to justify dropping a populated ledger. [Migration evidence](migration.json).

## 6. Backup and recovery evidence

The actual `backup.sh` generated `esabong-20261102T100004Z.dump`, checksum and media archive under restricted permissions, with pruning disabled. The dump's checksum passed and restore succeeded into `esabong_restore_readiness_20261102`. Comparison to the active source found **one changed `tenant_usage` content hash**, so that comparison is recorded as **mismatch**, not silently accepted. Source writes were not frozen. All other recorded table facts matched. [Source restore log](source-restore.txt).

After the migration drill, a fresh backup of the quiescent scratch DB was restored into a second fresh scratch DB. **RESTORE VERIFIED: 47 tables, 13,502 rows, 39 RLS policies, 27 triggers, revision 0015**; row counts/content hashes, RLS, grants and triggers matched. Both scratch DBs and all backup evidence were retained. [Verified scratch restore](scratch-restore.txt), [backup](backup.txt), [scratch backup](scratch-backup.txt).

This proves local recovery and migration preservation, not a production point-in-time restore. Before deployment, quiesce the approved target and repeat backup/restore comparisons without counter drift. Off-site upload, download-back and restore have not been verified against a real off-host destination in this review.

## 7. Docker and staging status

- API and three-portal Caddy images built twice successfully under **`esabong-readiness-*`**, leaving existing stack images/containers untouched. API runtime user is `app`; credentials are runtime settings, not Docker build arguments.
- Merged production configuration and startup settings preflight: **3 PASS, 0 WARN, 0 FAIL**. Only Caddy publishes TCP **80/443**; PostgreSQL/Redis publish no ports and backend networking is internal. Restart policies, named persistent volumes, resource caps and rotated logs are configured. [Preflight](preflight.txt), [redacted Compose review](compose-review.json).
- `docker-compose.staging.yml`, applied after the production overlay, selects a separate project/volumes, a separate backup path and loopback **8080/8443**, using internal-CA TLS. Its differences are intentional; they are not public production bindings.
- The existing development stack remained healthy; current Caddy configuration validated. These checks do not certify runtime health of the new candidate images. Beat currently has restart/state monitoring, without an independent scheduler heartbeat healthcheck.
- **No candidate staging deployment was started.** The user explicitly said not to deploy without approval. The precise proposed local target is `esabong-staging`; private generated configuration is `/tmp/esabong-final-readiness/staging.env`. It contains no approved external SMTP/video credentials and may need recreation after reboot. Deployment steps and remaining container tests are in [deployment.md](../../deployment.md).

## 8. Current automated results

| Check | Actual result |
|---|---|
| Backend integration, fresh `esabong_test_readiness_20261102`, real PostgreSQL/Redis | **194 passed** |
| Backend unit, including preflight safety | **108 passed** |
| Frontend owner/admin/player | **57 / 58 / 75 passed — 190 total** |
| Full Chromium E2E against scratch API/current portals | **28 passed; 1 optional visual job skipped in ordinary suite** |
| Separately enabled visual job | **1 passed**, 306 captures |
| Angular production builds | **Owner, Admin, Player passed** |
| Candidate Docker builds | **API and Caddy/three portals passed** |
| Ruff check / format | **Passed; 160 files formatted** |
| Git whitespace and shell syntax checks | **Passed** |

Logs: [backend](backend.txt), [unit](backend-unit.txt), [frontend](frontend.txt), [E2E](e2e.txt), [visual](visual.txt), [portal builds](portal-builds.txt). Auth, RBAC, RLS, caller-only history, non-monetary boundary, transaction concurrency, settlement math/idempotency/rollback, subscription separation and live events are covered. No tests were weakened to get a pass. The visual fixture initially had gameplay disabled; its missing-balance assertions failed, then fixture setup was corrected through the real settings API and restored afterward. Original evidence is retained in the UI folder.

## 9. Rendered UI/UX

**34 routes × 9 widths = 306 captures/measurements** across Owner, Admin, Declarator and Player. Widths: 320, 375, 390, 430, 768, 1024, 1280, 1440, 1920. Zero page/content horizontal overflow, zero uncaught browser errors, exactly one Player header balance with gameplay enabled. Four dashboard contact sheets and detailed phone/archive screenshots were visually inspected. [Screenshots and findings](../../ui-ux/2026-11-02-readiness/README.md).

The real unconfigured-video error is visible and retry controls remain available. No unauthorized feed was substituted. Forms, dialogs, loading/retry/error handling, logout/revocation and phone navigation are exercised by regression tests. This is not WCAG certification, a full contrast audit, screen-reader certification or a quantified cumulative-layout-shift study. Only Chromium/dark theme was captured in the broad nine-width inventory.

## 10. Security and operational gates

Production startup settings reject unsafe cookie/tenant-header/scheme/domain/default-secret/CORS values. API docs are off by default in production; Caddy blocks internal TLS-ask endpoints publicly. JWT audience/tenant/session revocation, password hashing, role guards and tenant-specific socket access have current regression coverage. Per-IP/auth throttles exist. Command-pool backpressure and dedicated subscription capacity are bounded, not unlimited.

- Frontend production-dependency audit: **zero reported vulnerabilities**. Dev dependencies were not included in that audit. [npm result](npm-audit.json).
- Exact API-image distributions: **57 public packages audited, zero known advisories; local `esabong-api` skipped by PyPI auditor** and reviewed/tested as application code. [Python result](python-audit.json). OS/base-image CVE scanning and a penetration test were not performed. Python resolution/base image tags are not fully digest-locked; record and promote reviewed immutable image IDs rather than rebuilding a production release later.
- `pip-audit 2.9.0` already exists in `/tmp/esabong-readiness-auditor`; installation succeeded. Its Python 3.9/LibreSSL warning concerns the temporary auditor, not the Python 3.12 API image. The exact installation shell rule persisted at `~/.codex/rules/default.rules:60`, and `codex execpolicy check` returned allow. Direct pip/module variants did not match. No rules were changed, global controls disabled or reinstall retried after the user's instruction.
- No production host/OCI firewall, public DNS/certificate issuance, SMTP delivery, actual provider authorization, real alert destination or off-site storage credentials were available. Current code/config tests cannot replace those checks.

Monitoring scripts/timers cover API/DB/Redis/worker/container/disk/CPU/RSS/errors/backups/TLS. Their real-host installation, alert delivery, dead-man's switch and off-site restore remain **outstanding**, not operationally certified. No external alerts/messages were sent.

## 11. Performance gate

No separate staging load-generator machine was available; no new load test was run against production or presented as separate-machine evidence. The earlier [full synthetic run](../../benchmarks/2026-11-02/REPORT.md) remains the measured peak result: boundary HTTP failures, only **374/1,000 sockets** open at the barrier, delivered-frame p95 **130.111 ms**, max **255.718 ms**, **2,338 missing events**. Exact accounting and 20-call idempotency passed; precision throughput was **114.98–125.52 unique wagers/s**, peak sampled API RSS **629.89 MiB**. Those are historical local measurements, before this review's pool fix.

The new focused Redis regressions verify pool separation and bounded waiting. They do not supersede the failed 500-request/1,000-socket acceptance test. After staging approval, use a separate generator host, collect server/DB/Redis metrics and rerun the unchanged gates. A controlled read-only HTTP first step is `api/scripts/loadtest.py --base <approved-staging-https> --path /api/ready --requests 200 --concurrency 10`; agree the traffic budget before any larger run.

## 12. Legal and operational launch gate

No evidence of counsel approval, applicable regulatory clearance or video redistribution rights was provided. Obtain jurisdiction-specific legal review of the actual events, content, terminology, access/age policies and non-monetary model; virtual points alone are not a legal approval. Obtain a written provider license covering embedding/proxying/distribution before enabling an external feed. Browser integration tests use the licensed demo/test path and do not prove rights to a real feed.

Have counsel/privacy officers assess lawful processing, notices, retention, processors, data-subject rights and security/breach procedures under the [Philippine Data Privacy Act](https://privacy.gov.ph/data-privacy-act/). Publish operational policies for non-transferability, no cash value, support, account suspension, complaints and incidents. These are launch conditions to review, not a legal compliance determination.

## 13. Deployment and rollback procedure

1. Review the proposed commits and approve the **separate staging** target. Preserve existing Git work; do not run partial commits as separate releases.
2. Supply private staging config, independent volumes/backups and an authorized mail/video test setup. Validate merged Compose and the already-built image with the corrected preflight.
3. Before a data-bearing staging migration, take/checksum/restore its backup into a fresh scratch DB. Compare archive and Arena Points hashes. Apply only forward migrations to the approved target; never edit 0012 or copy a developer dump into production.
4. Deploy the reviewed candidate images together, then test container health, DB/Redis, worker execution, auth/tenant routing, HTTPS/cookies, both WebSocket gateways, UI/history, error handling and graceful API/worker restarts. Verify monitoring alerts and off-site recovery. Run the approved separate-machine performance checks.
5. Production remains a later explicit approval with its own quiescent backup and restore drill, DNS/ACME/firewall/secrets/provider checks and legal/operational sign-offs. Promote identical reviewed image digests and migrate once in maintenance mode.

Prefer application rollback or a forward fix while retaining 0013+ write guards and every immutable receipt. Never downgrade a populated simulation ledger or restore an old DB over later gameplay/billing activity. Empty-schema downgrade success is only a rehearsal result. A full restore requires explicit approval, maintenance mode, a reconciled recovery point and accounting for all post-backup writes. Preserve PostgreSQL/media/Caddy volumes; do not use `down -v`. Run reconciliation, auth/tenant/socket and smoke tests before reopening.
