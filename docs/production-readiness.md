# Production readiness — 2 November 2026

## Assessment

| Scope | Status | Why |
|---|---|---|
| **Staging deployment** | **READY FOR STAGING** | Every technical check that can run without a real server passed: from-scratch deploy with the production overlay, migrations, healthchecks, HTTPS, portals, Redis/worker, WebSocket, restart and crash recovery, load test, backup → restore rollback drill, smoke test (24/24), monitoring. |
| **Production deployment (technical)** | **CONDITIONALLY READY** | Remaining items are configuration on the real host, not code: off-site storage credentials, an alert channel, OCI network rules, real SMTP, a video provider with redistribution rights, approved plan prices. Each has an exact action below. |
| **Public launch** | **NOT READY** | External legal review and approvals are mandatory and not done. A non-monetary design does not by itself make the platform lawful (see [Legal and operational blockers](#legal-and-operational-blockers)). |

The product stays **strictly non-monetary**: play credits and points have no cash value and
cannot be deposited, bought, withdrawn, redeemed or converted. PHP (₱) is used only for
tenant subscription billing. No real-money wagering, cash-out, payment processing for
credits or credit-to-cash conversion exists or was added.

---

## 1. Completed fixes and enhancements (this round)

### Play credits are never shown as money
- **Before:** play balances, pools, bets and returns rendered with `₱` (`AmountPipe` →
  `formatMoney`), labelled "Payout", "Pays", "Balance", next to a "no cash value" note
  ([before capture](ui-ux/after/player-dashboard-320.png)).
- **After:** new `formatCredits` / `CreditsPipe` (`1,234.50`, or `1,234.50 credits`), no currency
  symbol or code anywhere in play-credit views; "Odds", "Returns", "Credited to winners",
  "Play credits", ledger kinds "System grant / Bet / Win credited / Refund / Correction".
  `AmountPipe` (₱ for play amounts) was removed so it cannot be reused by mistake.
  Covered: player betting panel and header, admin Betting (fights, wallets, ledger,
  reconciliation), fight book, betting settings (inputs now suffixed "credits"), Declarator desk.
- Backend wording: disclaimer "Play credits for simulation only … cannot be bought,
  transferred, withdrawn, redeemed or exchanged for money"; "Not enough play credits"; new
  grant memo wording. **Stored ledger rows, enum values and API field names are unchanged**
  — histories and audit records are preserved; no data migration.
- Enforcement tests: `unit_tests/test_non_monetary.py` fails if any route (outside platform
  billing) mentions deposit/withdraw/cash-out/top-up/redeem/purchase/convert/payout, if a
  player route can write to a balance, or if a play-credit or points schema gains a
  currency/peso/cents field. Frontend specs assert no `₱`, `PHP`, "peso", "payout", "deposit"
  or "withdrawal" in rendered player and admin betting views; e2e checks the live player portal.

### Subscription pricing needs explicit approval
- Migration **0012** adds `subscription_plans.prices_approved_at/_by`. Existing rows get NULL
  (= needs review). No price, currency, plan or subscription row is modified.
- A plan can be **checked out** (new subscription, provisioning, plan change, signup request,
  public plan list) only if it is active, in PHP, and its current prices were approved by a
  platform **admin** (`approve_prices: true`, audited). Changing a price withdraws approval.
- **Legacy USD plans** are flagged `legacy_currency`, cannot be sold, are never converted,
  and existing USD subscriptions keep renewing in USD at the recorded amount (tested).
- `app.cli seed` no longer contains prices: catalogue plans are created **inactive at ₱0,
  unapproved**. `demo-tenant` (refused in production) uses its own "Development (not for sale)" plan.
- Owner portal: "Sale status" column (for sale / needs price approval / legacy USD · review /
  retired), a review banner, PHP-only currency display, an explicit approval checkbox, and a
  warning when a price edit will withdraw approval.
- Tests: `test_plan_prices_need_explicit_approval_before_checkout`,
  `test_legacy_usd_plan_keeps_its_currency` (rewritten to the new rule; still proves USD
  records are never relabelled), `test_plan_pricing_is_admin_only`,
  `test_seeded_catalogue_has_no_prices_and_is_not_for_sale`, `plans.spec.ts` (approval, legacy,
  sale status).

### Bugs found and fixed during verification
| Bug | Impact | Fix / test |
|---|---|---|
| Compose passes unset optional secrets as `""`; the production check rejected an empty `VIDEO_CREDENTIALS_KEY` | **A production install without video credentials would refuse to start** (introduced in the previous round, found by the staging preflight) | blank → unset for optional secrets; `test_blank_optional_secrets_from_compose_are_unset` |
| Ops scripts loaded `.env` with `. ./.env` | an unquoted value with spaces (`.env.example` shipped `APP_NAME=Event Platform`) aborted **backup, restore and verify** under `set -e` | `db/scripts/lib-env.sh` parses `.env` like compose, never executes it; `.env.example` quoted |
| Backups written world-readable (`-rw-rw-rw-`) | any host user could read every tenant's data | dump streamed to a host file under `umask 077`; directory 700; existing local backups set to 600 |
| Restarting the API returned 502 to in-flight users | 2,342 of 6,000 requests failed during a 4 s restart (measured) | Caddy `lb_try_duration 10s`: **6,000 / 6,000** succeeded in the same test |
| Quick-amount chips announced 100× their value to screen readers | "50,000.00 credits" for the 500 chip | caught by a new unit test; fixed |
| WebSocket rejections were not logged | no signal for auth/proxy failures on live updates | `ws_rejected` / `ws_connected` / `ws_closed` events, never the token (test) |
| A points migration test hard-coded `0011 (head)` and its downgrade dropped later migrations' data | broke the suite as soon as 0012 existed | compares with `alembic heads`; restores fixture approvals |
| Owner review banner grammar | "4 plans need review before it can be sold" | fixed |

## 2. Files modified this round

- **API:** `app/core/config.py`, `app/cli.py`, `app/modules/betting/{schemas,service}.py`,
  `app/modules/subscriptions/{models,schemas,router,service}.py`, `app/modules/tenants/public_router.py`,
  `app/modules/realtime/router.py`, `scripts/{smoke,loadtest}.py`
- **Migration:** `db/migrations/versions/20261101_0012_plan_price_approval.py`
- **API tests:** `tests/{conftest,test_billing,test_websocket,test_points_management}.py`,
  `unit_tests/{test_non_monetary,test_production_config}.py`
- **Web:** `shared/pipes/{credits.ts,credits.spec.ts,index.ts}`, `shared/models/index.ts`,
  player `ui/fight-betting-panel{.ts,.spec.ts}`, `core/play-betting.ts`, `layout.ts`;
  admin `pages/{betting,betting.spec,fight-book,betting-settings}.ts`, `declarator/dashboard.{ts,html}`;
  owner `pages/plans{.ts,.spec.ts}`; e2e `betting.spec.ts`, `arena-responsive.spec.ts`, new `resilience.spec.ts`
- **Infrastructure:** `infrastructure/caddy/Caddyfile`, new `infrastructure/monitoring/{check.sh,README.md}`,
  new `infrastructure/systemd/*` (backup, restore-drill and monitor timers; cron example),
  new `infrastructure/scripts/preflight.py`
- **Backups:** `db/scripts/{backup,restore,verify-backup}.sh`, new `db/scripts/{lib-env,offsite-backup}.sh`
- **Config/docs:** `.gitignore`, `.env.example`, `docs/deployment.md`, `docs/testing.md`,
  `docs/ui-ux/README.md` + `docs/ui-ux/2026-11-02/`, this file

## 3–4. UI/UX and responsive verification

`web/scripts/ui-audit.cjs` (Playwright, real API and dev servers): **34 routes × 9 widths
(320–1920) = 306 measurements, 0 page/content horizontal overflow, 0 uncaught browser errors**;
102 screenshots in [`ui-ux/2026-11-02/`](ui-ux/2026-11-02/). Before/after pairs and findings:
[`ui-ux/README.md`](ui-ux/README.md). New e2e checks: 320 px player pages never scroll
sideways; the Admin phone menu opens and is usable. Earlier rounds' work on loading
skeletons, retry states, stable rows during live updates, duplicate-submit guards and video
continuity is unchanged and still covered by its tests.

Not done: no redesign (none was needed by the audit). Known nit: on phones the header shows
the play-credit balance as a bare number (unit in its tooltip/accessible name and the panel).

## 5–7. Test results and builds (run 2 November 2026 on this machine)

| Suite | Previous report | Now |
|---|---|---|
| Backend integration (pytest, real PostgreSQL + Redis, migrations from scratch) | 199 | **203 passed** |
| Backend unit | 91 | **96 passed** |
| `ruff check` / `ruff format --check` (app, tests, unit_tests, scripts) | clean | **clean** |
| Frontend unit (Vitest: owner 57, admin 81, player 67) | 199 | **205 passed** |
| Production builds owner / admin / player | pass | **pass, no warnings** (initial transfer 219.5 / 224.8 / 232.7 kB) |
| End-to-end (Playwright, Chromium; dev servers + API on current code) | 19 | **26 passed** (1.5 min): 19 existing + 7 in `resilience.spec.ts` — invalid sign-in, network failure → error + retry → recovery, double-click sends one request, revoked session → sign-in with notice, cross-workspace token refused, no money wording in the player portal, 320 px layouts and phone navigation |

## 8. Database verification

- Migrations **0001→0012 applied to an empty database** on staging; full `downgrade base → upgrade head`
  on every backend test run; 0010↔head round trip over live points data changes no row (tested).
- 0012 is additive (two nullable columns + FK); the local dev database was upgraded (its 4
  legacy USD plans are untouched and now flagged for review).
- Ledger integrity, immutability triggers and RLS remain covered by the existing suites.

## 9. Monitoring

`infrastructure/monitoring/check.sh` every 5 min (systemd timer): API liveness/readiness
(PostgreSQL + Redis), all three portals, WebSocket upgrade through the edge, container state and
health (Celery worker ping), memory/CPU per container, disk, database size, HTTP 5xx rate from
Caddy's JSON access log, WebSocket task errors, backup freshness and off-site status, TLS expiry.
Alerts via webhook (Slack/Discord/Teams/Chat), OCI Notifications, and a heartbeat URL (dead-man's
switch); first occurrence, hourly repeats, resolution. Thresholds and setup:
[`infrastructure/monitoring/README.md`](../infrastructure/monitoring/README.md).

Verified: all checks pass on the dev stack and on staging; **fault injection** (stopped worker)
raised `Container worker is exited` and then `RESOLVED`; on staging it flagged the 4,802
deliberate 502s from the restart-under-load test (true positive). Caddy access-log redaction
verified on a live request: `?token=`, `Authorization`, `Cookie` absent.
**Not operational until an alert channel is configured** (no credentials exist here).

## 10. Backup and restore

| Step | Result |
|---|---|
| `backup.sh` | dump mode 600, directory 700, `.sha256` written |
| `verify-backup.sh` (scratch DB, never live) | `RESTORE VERIFIED` on dev and staging: every table's row count, RLS policies and flags, grants, triggers and revision match live |
| `offsite-backup.sh` with `OFFSITE_RESTORE_DRILL=1` | upload → download-back → checksum match → **restore of the downloaded copy verified** → retention; status recorded for monitoring |
| Staging rollback drill | backup → simulated bad write → `restore.sh` (18 s) → bad write gone, earlier data intact, rev 0012, services healthy, smoke 24/24 |

**Off-site is verified against a local-filesystem rclone remote only.** It is not operational
until `.offsite.env` points at a real Oracle Object Storage bucket and one nightly run plus a
restore drill succeed there.

## 11. Oracle Cloud and production security checks

`infrastructure/scripts/preflight.py` (read-only). On the staging configuration: compose PASS
except the deliberate local 8080/8443 ports; secrets PASS (24+ char, distinct, not placeholders;
the API's `production_problems()` reports nothing). On the developer `.env`: correctly FAILs
(weak dev passwords, `.env` mode 644 on this laptop). Production overlay: only Caddy publishes
80/443; PostgreSQL and Redis have **no host port** and sit on an `internal` network; restart
policies, CPU/memory limits, healthchecks, named volumes, log rotation; API docs off; secure
cookies; HTTPS enforced (HTTP → 308); CORS empty (same-origin).

**Not verified (no access, and no authorization to change cloud resources):** OCI security
lists / NSGs, the host's listening sockets, OCI Bastion/SSH policy. Run the preflight on the
server with `OCI_SECURITY_LIST_IDS` / `OCI_NSG_IDS` set.

## 12. Security findings

Fixed this round: startup-blocking empty-secret bug, world-readable backups, `.env` executed by
scripts, unlogged WebSocket rejections, Caddy logs now redact tokens. Fixed last round and still
tested: public `tls-ask`, password spraying and registration throttles, unsafe production
configuration refused, API docs off, demo video disabled in production. No secrets found in
the diff or untracked files (pattern scan for keys, tokens, OCIDs, private keys).
Open: SSH/OCI rules unverified; per-IP throttles may need tuning behind carrier-grade NAT.

## 13. Performance (staging: production overlay, 4 uvicorn workers, API limit 2 CPUs; generator on the same laptop)

| Path | Requests (conc. 50) | req/s | mean | p95 | errors |
|---|---|---|---|---|---|
| `/api/health` | 3,000 | 503.9 | 98.3 ms | 269.7 ms | 0 |
| `/api/ready` (DB + Redis) | 3,000 | 480.6 | 103.4 ms | 312.3 ms | 0 |
| `/api/v1/public/tenant` | 3,000 | 371.7 | 134.0 ms | 455.2 ms | 0 |
| `/api/v1/portal/events` (player, RLS) | 3,000 | 337.6 | 147.1 ms | 491.8 ms | 0 |
| `/api/v1/auth/me` | 3,000 | 662.0 | 74.9 ms | 223.0 ms | 0 |

Resources during the run (peak): API 191.8 % CPU (= its 2-CPU limit, the bottleneck) / 428 MiB;
PostgreSQL 32.3 % / 249 MiB; worker 38.1 % / 190 MiB; Caddy 14.3 % / 24 MiB; Redis 3.6 % / 7.5 MiB.

Recovery (time until `/api/ready` healthy): restart API 4.4 s, Redis 0.9 s, PostgreSQL 1.1 s,
**SIGKILL of the API process 20.2 s (restarted automatically by the restart policy)**, whole
stack 16.1 s. API restart under load: 2,342 / 6,000 failed → **0 / 6,000** after the Caddy retry
window. **Not run from a separate machine** (none available): repeat `scripts/loadtest.py`
from another host against staging on the target OCI shape before sizing production.

## 14. Outstanding configuration (exact actions)

1. **Plan prices:** in the Owner console set PHP prices for each plan, tick approval, mark available.
2. **Off-site backups:** create a private OCI bucket in another region + a Customer Secret Key for a
   bucket-only IAM user; fill `/opt/esabong/.offsite.env` (keys in `offsite-backup.sh` header);
   enable `esabong-backup.timer` and `esabong-restore-drill.timer`; run once with
   `OFFSITE_RESTORE_DRILL=1` and confirm `OFFSITE BACKUP OK`.
3. **Alerts:** set `ALERT_WEBHOOK_URL` and/or `OCI_ONS_TOPIC_ID`, plus `HEARTBEAT_URL` and
   `MONITOR_TENANT_HOST`, in `.monitor.env`; enable `esabong-monitor.timer`.
4. **OCI network:** ingress 0.0.0.0/0 only TCP 80/443; SSH from admin IPs or OCI Bastion; run the preflight.
5. **Secrets:** generate all passwords and `JWT_SECRET` fresh on the server; `chmod 600 .env`;
   `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml`.
6. **SMTP:** real `SMTP_*`; send one invitation and confirm delivery (not verified here: staging used `smtp.invalid`).
7. **Video:** configure an authorized provider; test playback in Player and Declarator (not verified: no provider access).
8. **Staging on OCI:** repeat the smoke test, monitor check and load test on the real staging host.

## Legal and operational blockers

- **E-sabong remains suspended in the Philippines** (Executive Order No. 9, 2022). PAGCOR licenses
  no e-sabong operators and supports enforcement. This platform must stay non-monetary; even so,
  a cockfighting-themed betting simulation with an agent hierarchy needs **written legal advice**
  before public launch (gaming, advertising and consumer-protection rules), and that advice is
  not a formality: non-monetary design does not automatically make it lawful.
- **Video redistribution rights:** written authorization from each stream owner, per tenant; the
  platform only stores what an admin configures.
- **Data Privacy Act of 2012 (RA 10173):** the platform processes names, emails, phones, IP
  addresses and audit trails. Required before launch: privacy notice and lawful basis,
  NPC registration if thresholds apply, a Data Protection Officer, a breach-notification
  procedure (72 h to NPC), retention periods (audit logs, backups: 14 days local / 35 off-site
  by default), and data-processing agreements with tenants and with hosting (Oracle) and SMTP providers.
- **Terms of service** for tenants and players stating that credits and points have no value
  and cannot be exchanged, and age restrictions.

## Rollback

`docs/deployment.md` → *Rollback*: app-only releases redeploy the previous tag; releases with a
migration restore the pre-upgrade backup with `db/scripts/restore.sh` (data written after the
backup is lost) and redeploy the previous tag; then run `scripts/smoke.py`. Drilled on staging
this round (above).

## Git review and suggested commits

The working tree on `main` holds three rounds of uncommitted work (≈120 modified, ≈70 new
files). No secrets, no unused dependencies (`hls.js`/`dashjs` used by the video stage, `httpx`
by the video provider). Artifacts to decide on: **~47 MB of PNG/JPG screenshots** under
`docs/ui-ux/` (keep out of git or use Git LFS), `docs/declarator/*.log` test logs, empty
`api/test-results/`. Suggested commits, in order:

1. Points ledger: admin-only moves, refunds, guards (migrations 0010–0011, `demo_points`, admin Points pages, tests)
2. Simulated betting and play wallets (migration 0009, `betting`, panels, odds, e2e)
3. External video provider (migration 0008, `video`, admin page, video stage, tests)
4. Production hardening round 1 (config checks, throttles, Caddy internal block, prod overlay, backups verify)
5. Play credits instead of currency (pipes, views, wording, non-monetary guard tests)
6. Plan price approval (migration 0012, billing, seed, owner plans, tests)
7. Ops: monitoring, off-site backups, systemd timers, preflight, smoke/load scripts, `lib-env.sh`, Caddy logs/retry
8. Docs (deployment, testing, readiness) — and screenshots only if you choose to version them
