# E-Sabong — shared AI progress log

**Single source of truth for every AI assistant (Claude Code, Cursor, Codex).**
If you are an AI agent starting work here: read this file and [`AGENTS.md`](../../AGENTS.md)
first, then verify the "Current state" section against `git status` before doing anything.
When you stop (finished, interrupted, or running low on context/tokens), update this file
using the protocol in `AGENTS.md`. The next agent may be a different tool.

Last updated: **2026-11-04 (UAT redeployed to prod-20261103.3; ops docs redacted and committed)** by **Claude Code (Opus 5.5)**

---

## 1. Current state (verify first)

| Item | Value |
|---|---|
| Branch | `production-readiness` |
| HEAD | `f18c9de3c78a426b4be3feaecb71ddb4b2dee825` = annotated tag **`prod-20261103.3`** (= `.2` + 9 readiness fixes), pushed. Production runs it. |
| Working tree | Clean after the ops-docs commit. Committed copies are **redacted** (admin IP → `203.0.113.10`, VPS IPv6 → `2001:db8::1`, owner email → `<owner-email>`, sshd root-login finding removed). Unredacted originals: `~/Documents/Repositories/E-Sabong-backups/20261104T022735Z-pre-redaction/` (700). The VPS-staged `esabong-host.nft` keeps the real /32. |
| Latest DB migration | `0016` head. Production DB `0016 (head)`; redeploy of `.3` needed no migration. UAT DB `0016`; UAT now runs `prod-20261103.3` (redeployed 2026-11-04, no migration; kombu worker/beat crash loop gone). |
| Pending user decisions | Public activation refused. Deployed `prod-20261103.3` is e-sabong fight betting (MERON/WALA/DRAW, fight books, Declarator, settlement). No PAGCOR or other authorization is in the repo or prior server audits. Gateway was not switched to dual (still `uat-only`). Off-site/alert/timer setup remains outstanding. |

Quick verification commands:

```bash
git status --short                 # expect uncommitted declarator betting + firewall/handoff docs; HEAD unchanged
git log --oneline -7               # six isolation commits plus UAT-only gateway follow-up
cd api && .venv/bin/alembic heads   # expect 0016 (head)
```

If these do not match, someone else changed the repo: read §6 (session log) and `git diff`
before continuing. Never discard or reset changes you did not make.

---

## 2. Project in one minute

Multi-tenant SaaS for live arena events (E-Sabong). **Play balances are non-monetary**
(simulated, "no cash value"; PHP is used only for tenant subscription billing).

| Part | Tech | Where |
|---|---|---|
| API | Python 3.12, FastAPI, SQLAlchemy async, Alembic, Celery (+beat) | `api/` (venv `api/.venv`) |
| DB | PostgreSQL 17 with forced row-level security per tenant | migrations in `db/migrations/versions/` |
| Cache / pub-sub | Redis 7 | |
| Frontend | Angular 22 (zoneless, signals), Vitest, Playwright; three apps: `owner`, `admin` (served at `/admin/`, also hosts the **Declarator** desk), `player` (`/`) | `web/` (Node 26) |
| Edge | Caddy (TLS, routing, CSP) | `infrastructure/caddy/Caddyfile` |
| Live video | MediaMTX 1.21.3 (opt-in compose profile `streaming`) | `infrastructure/mediamtx/`, `docs/live-streaming/README.md` |
| Deploy target | Docker Compose on Oracle Cloud | `docker-compose.yml`, `docker-compose.prod.yml`, `docs/deployment.md` |

Key docs: `docs/architecture.md`, `docs/api.md`, `docs/testing.md`, `docs/deployment.md`,
`docs/production-readiness.md`, `docs/live-streaming/README.md`, `docs/benchmarks/2026-11-02/`.

---

## 3. Work completed (newest first)

### UAT redeployed to prod-20261103.3 (2026-11-04) — owner-requested; gateway unchanged
- In owner-started tmux `-L esb-uat` (gid docker): `db/scripts/backup.sh` → `uat-esabong-20261104T022825Z.dump` (+sha256);
  `deploy.sh prod-20261103.3` (no APPROVED_MIGRATIONS; DB already 0016) → EXIT 0; api/worker/beat/caddy/mediamtx recreated,
  postgres/redis kept. After: all UAT containers healthy, worker `1 node online`, beat sending `video.reconcile`;
  UAT `/`, `/admin/`, `/owner/`, `/api/ready`, `/api/v1/public/tenant` HTTP 200 from the allowlisted workstation.
  Prod containers and gateway not recreated (uptimes unchanged). MediaMTX UAT 8190 public (as before), RTMP loopback.

### Public go-live refused (2026-11-04) — gateway left uat-only
- Fresh external check: apex and UAT both resolve to `46.250.249.61`. Production HTTPS on `/`, `/admin/`, `/owner/`, and `/api/ready` fails TLS (curl exit 35, HTTP 000). UAT homepage HTTP 200; certificate Let's Encrypt YE2, 2026-11-03 through 2027-02-01.
- Deployed release `prod-20261103.3` is e-sabong fight betting (MERON/WALA/DRAW, fight books, Declarator, settlement). No authorization document is in the repo. Gateway was not changed. UAT was not touched.

### Production go-live recheck (2026-11-04) — activation stopped on conditional authorization gate
- Fresh external checks with certificate verification: apex A = 46.250.249.61; all four requested HTTPS URLs fail TLS
  (curl exit 35, HTTP 000), identically using public DNS and direct `--resolve`. This is not DNS caching.
- UAT A = same VPS; homepage HTTP 200; trusted certificate issuer Let's Encrypt YE2, valid
  2026-11-03 12:27:53 UTC through 2027-02-01 12:27:52 UTC; TLS 1.2 negotiated by local client.
- SSH works; prod checkout exact f18c9de, tracked tree clean; registered betting/Declarator routers confirmed.
  Retained exact-release runtime audits show enabled points/matches, fight betting routes, MERON/WALA/DRAW controls.
  Those establish functionality, not actual real-money wagering or live cockfight content. Available material does not
  establish the claimed alternative event/content or applicable authorization. EO 9 scope must be assessed against actual use;
  do not infer illegality solely from terminology or require removal if a lawful authorized use can be substantiated.
- Current gateway env remains uat-only with correct prod/UAT hosts. Rollback syntax confirmed:
  `~/esb-jobs/gw-cutover/rollback.sh <backup-dir>`; backup-20261104T012207Z SHA256SUMS verified.
  Latest retained prod DB dump prod-esabong-20261103T234826Z.dump checksum verified; media archives present.
- Current host listeners: public binds TCP 22/80/443/8190 and UDP 8190 (existing UAT streaming); 1945/1946 loopback.
  No 5432/6379/1935/8189 public listener. Firewall reachability not independently audited.
- Docker access denied in ordinary SSH; no fresh container health/IDs/DB revision/network inspection. Earlier preflight
  records internal=true prod/UAT gateway networks, dual Caddy validation pass, healthy prod, stopped prod MediaMTX.
  No remote mutation, UAT change, gateway script execution, rebuild, migration, credential reset or acceptance rerun.

### Public activation functionality audit (2026-11-04) — stopped before cutover
- Read-only SSH confirmed Production checkout `f18c9de3c78a426b4be3feaecb71ddb4b2dee825`, clean tracked tree;
  current gateway env still `uat-only`, prod/UAT hostnames correct. Current server source registers betting routers;
  betting and Declarator routers retain fight betting open/close controls.
- Retained exact-release runtime audits `~/esb-jobs/gw-cutover/audit.log` / `audit2.log` show served bundles with
  MERON/WALA/DRAW, Betting Open, Fight controls; OpenAPI includes player bet submission, settlement, stream/video,
  and Declarator operations. Public tenant response had points + matches modules enabled. This fails the user's
  requested non-cockfighting functionality gate; no assertion of current real-money wagering or active broadcast.
- No remote mutations, gateway cutover, deployment, migrations, database writes, or UAT changes. Direct Docker
  inventory denied to ordinary deploy SSH session; current container IDs/health/ports not freshly verified.
  Existing rollback script and backup directory with SHA256SUMS exist; integrity/execution not revalidated.

### 3.0000000000000 Dual-gateway cutover preflight (2026-11-04) — gateway NOT changed
- Cause of `ERR_SSL_PROTOCOL_ERROR` on the apex: gateway runs `Caddyfile.uat-only` (no prod site, no prod network); DNS apex
  A + UAT A = 46.250.249.61, no AAAA, no CAA. Activation not performed: no documented legal authorization.
- `~/esb-jobs/gw-cutover/preflight.sh` (log `preflight.log`): prod 6 healthy/0 restarts, MediaMTX exited, gateway healthy
  (id 057c9980efb1, mounts Caddyfile.uat-only + entrypoint, nets public + uat). Backup (700/600, SHA256SUMS)
  `~/esb-jobs/gw-cutover/backup-20261104T012207Z/`. Prod origin over internal `esabong-prod-gateway` (throwaway, no ports):
  / /admin/ /owner/ /api/ready /api/v1/public/tenant 200; tls-ask apex 200, uat/unknown 404; internal route 404; no egress.
  Dual render: Caddyfile + nets prod/public/uat, same volumes (UAT cert kept); dual validate "Valid configuration";
  hosts [uat, apex], dials prod-origin/uat-origin only. Only UAT cert stored. UAT 200 from allowlisted IP, 403 from VPS IP.
  After: UAT/prod/gateway container IDs, UAT volumes/.env/checkout identical; 0 public media listeners; 0 leftovers.
- Rollback `~/esb-jobs/gw-cutover/rollback.sh <backup-dir>` (restores .env via `cat` keeping inode/600, `gateway.sh up`,
  asserts uat-only mount + no prod net). ShellCheck clean; not executed (would touch the live gateway).
- Pre-existing: UAT worker (1692) / beat (2657) restarts = known kombu crash loop; not touched.

### 3.000000000000 prod-20261103.3 released and deployed internally (2026-11-04) — Production private
- Commit `f18c9de` / tag `prod-20261103.3` (9 files, the four readiness fixes only), pushed fast-forward. Checks: ShellCheck
  (only 2 pre-existing warnings in check.sh), `caddy validate` Caddyfile + Caddyfile.origin, Compose render prod/UAT templates
  and UAT-without-variable (unchanged 0.0.0.0), preflight accepts loopback media ports, diff secret/owner-IP scan clean.
  No Python/web change: suites not rerun (last 240/142, 69/83/60 on `.1`).
- Prod `.env`: `STREAM_WEBRTC_BIND=127.0.0.1` inserted in place (inode 797817, 600; backup `~/esb-jobs/prod-env-before-webrtc-bind.bak`).
  Pre-render of `.3` with the real `.env`: all MediaMTX ports 127.0.0.1. `deploy.sh prod-20261103.3` (no APPROVED_MIGRATIONS):
  db 0016 = release 0016; only caddy + mediamtx recreated (api/worker/beat images unchanged); MediaMTX loopback-only then
  stopped; per-second listener watch: 0 public 8189/1935/1936 samples. UAT + gateway container IDs unchanged.
- After: api/worker/caddy/postgres/redis healthy, beat running, all 0 restarts; worker pong; smoke 11/11; frontend 17/19
  (2 = unhashed `theme-init.js` correctly not immutable; assertion too strict); hashed assets now immutable.
  New backup `prod-esabong-20261103T224456Z.dump` (sha OK) + `media-…tar.gz` written on prod (media fix proven).
- Acceptance script hardened (`~/esb-jobs/prod_acceptance.py`, sha256 `c130151031e12753…`, previous kept as `.prev-3d8e`):
  TTY required, target fixed to prod origin (overrides only with ACCEPT_REHEARSAL=1), rotation only on typed ROTATE,
  handles already-rotated, Declarator write check uses an invalid body, signs out all sessions. Throwaway rehearsal:
  skip 15/15, ROTATE 25/25, already-rotated 20/20, no-TTY refusal; 0 leftovers.
- Off-site/alerts prepared, not configured: `~/esb-jobs/{configure-offsite,validate-offsite,configure-alerts,test-alert}.sh`
  (hidden prompts; rclone crypt over S3; tested locally against a throwaway rclone S3 server: names + content encrypted,
  download-back identical, wrong key reads nothing). Timers staged in `~/esabong-timers/` (700; SHA256SUMS; install.sh
  backup|monitor; `run-as-deploy.conf` drop-in User=deploy Group=docker; Contabo `esabong-restore-drill@`); `systemd-analyze verify` clean.

### 3.00000000000 Go-live readiness (2026-11-04) — Production private; fixes uncommitted
- Verified prod: tag `prod-20261103.2`, DB `0016`, api/worker/beat/caddy/postgres/redis running 0 restarts; MediaMTX stopped.
- Streaming: `STREAM_WEBRTC_BIND` (default 0.0.0.0; prod template 127.0.0.1) + prod template `STREAM_RTMP_BIND=127.0.0.1`;
  ingest = WHIP over HTTPS (Bearer publish key). Isolated MediaMTX 1.21.3 test: host bindings loopback only, unauthenticated
  WHIP/WHEP 401, HLS 302 cookie check → 401. Compose rendering checked both ways.
- Backups: prod `db/backups/prod-esabong-20261103T162120Z.dump` (600, sha256 OK); restored in a no-network tmpfs container:
  48 tables / 79 rows / 0016 / 40 forced-RLS policies / 15 triggers identical to prod. Found: media archive never written on
  Contabo (container uid 999 cannot write the 700 dir; "not present" message misleading) → fixed by streaming (tested locally).
  Off-site not configured (needs storage creds); timers not installed (needs root).
- Monitoring: `/opt/esabong/prod/.monitor.env` (600, no channel). Dry run: only public-edge checks fail (expected, not public).
  Added restart-count crash-loop detection (+ `MONITOR_EXTRA_SERVICES`), tested read-only vs prod via `~/esb-jobs/montest`.
- Acceptance: `~/esb-jobs/prod_acceptance.py` (sha256 `3d8e7ddf7a4569b6…`) rehearsed 24/24 on throwaway stack incl. temp-password
  rotation; not yet run on prod (owner passwords). Frontend: all 3 portals + 13 assets + deep links 200; found hashed assets
  never got immutable caching (regex expected `.HASH.`) → fixed, `caddy validate` OK on Caddyfile + Caddyfile.origin.
- Legal: no documented authorization; e-sabong remains suspended under EO No. 9 (sources in session report). Cutover blocked.

### 3.0000000000 Production stack running internally (2026-11-04) — NOT public
- `APPROVED_MIGRATIONS=prod:0016 deploy.sh prod-20261103.2`: prechecks (empty DB, chain base→0016, SHA = tag) passed; migrated;
  api/worker/beat/caddy/postgres/redis healthy, 0 restarts; worker `pong` (kombu 5.7.0 Redis transport OK).
- MediaMTX publishes WebRTC 8189 tcp/udp on 0.0.0.0 by design (`docker-compose.contabo.yml`, not configurable): stopped
  after it reported healthy; brief public windows ~2 min (first start) and 46 s (redeploy). Start again at cutover.
- `seed` (4 inactive plans, 2 flags). Owner ran in tmux: `create-platform-admin` (<owner-email>, super_admin),
  `create-tenant swc-main-183` "SWC Main 183" (dedicated: no plan/billing; admin `admin`, temporary password, must change),
  Declarator `declarator183` via `~/esb-jobs/create_declarator.py` (hidden prompt; same `users.create_user` path, audited).
  No events/fights/wallets/players. `.env`: `ROOT_TENANT_SLUG=swc-main-183` (backup `~/esb-jobs/prod-env-before-root-slug.bak`, 600);
  guarded redeploy passed (needs `COMPOSE_PROFILES=streaming`, else preflight flags UAT 8190 as a public port — false positive).
- Throwaway `esabong-verify` stack (exact prod images, tmpfs, internal network, throwaway secrets; removed, 0 leftovers):
  `~/esb-jobs/verify/verify_flow.py` **65/65** (Open/Close, #35→#39 progression, MERON/WALA/DRAW/CANCEL, settlement/refunds,
  duplicate declarations/approvals, concurrency, override, WebSocket, permissions, tenant isolation, audit, reconciliation).
- Internal Production smoke via Caddy with Host `swcmain183.live`: **11/11** (`~/esb-jobs/prod_smoke.py`). Sign-ins with real
  accounts not run (owner's passwords).

### 3.000000000 Production release prod-20261103.1 — committed, tagged, pushed; prod checkout prepared; NOT deployed
- Release `84bf5ce` / tag `prod-20261103.1` (annotated). Adds on top of §3.00000000: one open fight per event in
  `betting.service.open_betting` (event row lock; admin + Declarator paths; 409 `betting_open_elsewhere`; audited admin
  override `{"supersede": true, "reason"}` needs `events.manage`, writes `betting.superseded`); Declarator
  `POST /declarator/events/{id}/fights` and `PATCH /declarator/matches/{id}` (entries from the Declarator, never invented);
  event completion soft-deletes an unopened auto-prepared fight (`match.prepared_discarded`); Admin Events Add match form
  removed (API kept); desk "Fight #N entries" / "Prepare fight" form. Tests: +6 integration, +4 admin specs, E2E extended.
- VPS: `/opt/esabong/prod` cloned at the tag (clean, detached), `db/backups` 700, `.env` 600 from `.env.contabo.prod.example`
  with fresh server-side secrets (none shared with UAT, verified), `ROOT_TENANT_SLUG=` (until tenant exists),
  `STREAM_RTMP_BIND=127.0.0.1`, mail/ACME lines copied from UAT except `SMTP_PASSWORD` (placeholder; must differ from UAT).
  UAT `.env` byte-identical before/after. No Docker, containers, networks, DNS, gateway or firewall change.

### 3.00000000 Declarator Open/Close Betting controls — committed in 84bf5ce (see 3.000000000)
- Backend: `POST /api/v1/declarator/matches/{id}/betting/open|close` (`declarator/router.py`, `service.py`:
  `betting_target`, `next_scheduled`, `betting_control`, `_betting_desk`, `open_betting`, `close_betting`,
  `prepare_next_fight`); monitor gains `betting: BettingControl` (`schemas.py`). Reuses `fight_books` state (no migration);
  `betting/service.py` only gained read helpers (`book_for`, `window_open`, `open_books_in_event`) and a `by` audit tag on
  open/close (+`reopened`, `match_number`). No stake/pool/settlement logic changed. Opening the next scheduled fight starts it;
  one open book per event on the Declarator path; reopen blocked after a pending/finalized result. Approval of a first result
  on a live event with no scheduled fight creates the next fight (`Meron`/`Wala` placeholders, audit `match.next_prepared`).
- Frontend: Declarator "Betting control" panel (OPEN/CLOSED, fight #, Open/Close Betting, blocked reason, next-fight line);
  `DeclaratorApi.openBetting/closeBetting`, `BettingControl` type; player label now "Betting Open"/"Betting Closed".
- Tests: `api/tests/test_declarator_betting.py` (6), unit test in `test_declarator_policy.py`, admin specs (+7),
  `web/e2e/declarator-betting.spec.ts` (2). `web/e2e/declarator.spec.ts` D adjusted: after the last fight the arena now moves
  to the auto-prepared #5; cleanup deletes it before completing the event. Doc: `docs/declarator/contract.md`.

### 3.0000000 Host firewall Stage 1 — designed, locally tested, staged on VPS; NOT applied
- Strategy: one nftables table `inet esabong_host` (input policy drop; forward chain vets only new
  DNAT-ed connections from eth0 by `ct original proto-dst`). Never flushes Docker/Fail2Ban tables;
  UFW stays disabled; stock `nftables.service` must never be enabled (`/etc/nftables.conf` flushes).
  Policy: SSH 22 only from `admin_v4` (203.0.113.10/32); public IPv4 TCP 80/443/8190 + UDP 8190;
  IPv6 only ICMPv6/ND + established (no SSH, no 80/443); everything else incl. 5432/6379/1935/8189 dropped.
- Files: `infrastructure/firewall/{esabong-host.nft,firewall.sh,esabong-host-firewall.service}`;
  runbook §1a in `docs/deployment-contabo.md` (replaced the unsafe UFW recipe in §1); findings in
  `VPS-PREPARATION.md`. Rollback: systemd-run timer deleting only that table; persist is a separate step.
- VPS findings (unprivileged): global IPv6 2001:db8::1 with SSH on [::]:22 and INPUT ACCEPT;
  UFW ENABLED=no; Fail2Ban banaction nftables; no daemon.json; `systemd-run` present, `at` absent;
  sshd root-login setting to be reviewed (see private notes). Sudo still needs the owner's password.
- Staged to `/home/deploy/esabong-firewall/` (700); hashes in VPS-PREPARATION.md. No rule changed.

### 3.000000 Actual email verification and workstation tester access — done; administrator auth pending
- Verified administrator's actual Gmail SMTP/ACME/user/sender values and existing nonempty
  non-placeholder SMTP password, never printed or authenticated; no email sent. Both envs 600.
- Determined authorized workstation IPv4 using two independent HTTPS services plus SSH client
  address; all three agreed. Set gateway UAT_ALLOWED_IPS to that exact /32 only; current value
  remains private server config and was reported to owner. All other bytes/token/inode/mode
  preserved; private pre-update backup `/home/deploy/uat-gateway-env-backup-q344so69/gateway.env`.
- VPS daemon-free Compose/mail/secret/ownership/UAT-only checks passed. Both clean SHA/tag
  checkouts still cc7c39aa9e9e25ab2240bdda4b1ff270191746d6; guards unchanged. RTMP/RTMPS
  loopback only; no production directory/env. Caddy syntax/provision validation passed in an
  existing local rehearsal process with synthetic credentials (no container creation/start).
- Read-only admin inventory is approved. Sudo authentication still required; native Terminal
  computer-use access denied by tool safety restriction. Asked owner to run bounded identity
  command in their own terminal; do not request password in chat or transfer sudo timestamps.
- Resources ~11 GiB available RAM / 94 GB disk free; only SSH public listener. Docker containers,
  images, volumes, networks and firewall rules still unknown pending administrator authentication.

### 3.00000 UAT prerequisite recheck and safe operator commands — done; real inputs/access still missing
- Rechecked both VPS checkouts at approved SHA/tag, clean; both existing mode-600 env files
  and infrastructure secrets retained. Daemon-free Compose ownership/UAT-only/no-production
  checks passed again; original/production env files remain absent.
- Extended `VPS-PREPARATION.md` with an exact hidden-prompt settings updater, read-only
  administrator inventory, conditional UAT firewall proposals, and access/migration gates.
  Updater edits only mail/contact/tester settings in existing files, preserves inode/mode
  and all infra credentials, and keeps private pre-update backups. Not executed on VPS.
- Tested the documented updater on synthetic local fixtures, seven Compose literal-quoting
  cases, and rejection of unsupported implicit-TLS endpoint without writes. No containers run.
- Email implementation uses STARTTLS, not SMTP_SSL; actual provider endpoint must match.
  No actual SMTP/contact/tester values have been supplied; no values invented or updated.
- Sudo user/group options installed, but identity-only noninteractive probe still requires
  password. Docker/firewall inventories remain inaccessible. Attended process session is a
  proposal requiring explicit administrator approval/authentication, not verified access.

### 3.0000 Contabo UAT preparation — files prepared; Docker access and real settings pending
- New report: **`docs/readiness/2026-11-03-uat/VPS-PREPARATION.md`** (actual work, missing
  values, attended sudo proposal, future deployment commands and migration gates).
- VPS clones `/opt/esabong/{uat,gateway}` at approved `cc7c39aa9e9e25ab2240bdda4b1ff270191746d6`,
  tag `uat-20261103-rc.1`, clean tracked release files. Original parent HEAD cccee4d and
  tracked files preserved (parent now shows only new untracked nested gateway/uat directories).
- Generated independent infra credentials on server only, never printed; private env files
  mode 600. UAT backup directory mode 700. No production checkout/config created.
- Set UAT/root tenant/gateway identity, synthetic flags and UAT-only routing. Public RTMP
  remains loopback-only until broadcaster access is defined. No containers/networks/volumes/DBs
  started or created; no migrations, image builds, DNS/firewall/socket/group/sudoers changes.
- Daemon-free Compose and ownership checks passed, as did private file/secret properties;
  approved Caddy/Compose/deploy/migration blobs unchanged. Static linear 16-revision chain
  through 0016 verified; actual pending DB plan unknown until privileged inventory/read.
- Blockers: sudo needs an attended password; deploy lacks Docker access. Missing ACME_EMAIL,
  SMTP_HOST/USER/PASSWORD, MAIL_FROM, UAT_ALLOWED_IPS; confirm SMTP_PORT/SMTP_USE_TLS.
  No values invented. Proposed temporary sudo UID-deploy/GID-docker process session is not
  authenticated/validated on VPS; no script changes proposed or installed.

### 3.000 Final UAT validation and deployment preparation — release published to GitHub; not deployed
- Release published: branch `production-readiness` and annotated tag `uat-20261103-rc.1` at
  `cc7c39aa9e9e25ab2240bdda4b1ff270191746d6` (tag peeled to commit). No GitHub Release object created.
- Full report and exact approval/deployment commands: **`docs/readiness/2026-11-03-uat/REPORT.md`**.
- Backend 228 integration / 141 unit; frontend 60/58/83; three production builds; 34 general +
  streaming E2E; 6 gateway E2E; 25/25 smoke; Ruff, ShellCheck, Compose/Caddy and credential-pattern
  checks passed. One initial E2E missing-demo-fixture failure resolved by synthetic fixture creation.
- New UAT-only gateway mode avoids exposing production host/catch-all TLS on the first UAT
  deployment. Files: `Caddyfile.uat-only`, `docker-compose.uat-only.yml`, gateway template/script.
  Tested modes and production SNI refusal. Full dual mode remains available for later approval.
- Read-only VPS inventory: original clean checkout cccee4d preserved; UAT DNS correct; only SSH
  listening; deploy lacks Docker group access and noninteractive sudo. No server changes.
- Fresh local synthetic UAT backup/restore verified (48 tables, 421 rows, 40 policies, 27 triggers,
  0016). Retained dump and scratch database listed in report. No UAT/production migrations run.

### 3.00 Isolated UAT + Production on one Contabo VPS — committed and pushed; NOT deployed
Runbook: **`docs/deployment-contabo.md`** (rewritten: layout/isolation table, exact UAT steps §7,
production §8, per-environment streaming, smoke tests, backups, limits, evidence §14).
- Layout: two Compose projects `esabong-prod` / `esabong-uat` (checkouts `/opt/esabong/{prod,uat}`,
  `ESB_STACK` in `.env`) + one gateway project `esabong-gateway` (only 80/443). New overlay
  `docker-compose.contabo.yml` (project name from `ESB_STACK`, Caddy becomes a TLS-less origin
  `infrastructure/caddy/Caddyfile.origin` on internal network `esabong-<stack>-gateway`, owner
  built with `--base-href /owner/`, MediaMTX WebRTC port inside = outside, CPU shares / OOM adj /
  pids / API workers / worker concurrency / PG + Redis memory per env). Gateway:
  `infrastructure/gateway/{Caddyfile,docker-compose.yml,entrypoint.sh,.env.example}` (Host routing,
  UAT gate = IP allowlist OR cookie from a Basic-auth sign-in at `/__uat/access` (token never in a URL; cookie stripped before the origin), optional `PROD_OWNER_ALLOWED_IPS`,
  on-demand TLS catch-all → production only). Shared snippets `infrastructure/caddy/snippets.caddy`
  (the single-host `Caddyfile` imports them; behaviour unchanged).
- API: `ROOT_TENANT_SLUG` (bare `BASE_DOMAIN` → one tenant; `tenancy.resolve_tenant`, `tenant_base_url`,
  tls-ask), `SYNTHETIC_DATA_ENABLED` (UAT: demo-tenant/demo-derby + test patterns in production
  mode; demo-tenant refuses the public default password there), reserved slugs `uat`, `staging`.
  No wallet/settlement/auth/RLS logic changed; OpenAPI unchanged.
- Scripts: `deploy.sh` (stack/dir/compose checks, refuses shell vars overriding `.env`,
  stack-qualified `APPROVED_MIGRATIONS=<stack>:<rev>`, isolation checks, per-stack temp/history),
  new `check-env-isolation.py` (envs + compose modes), `gateway.sh`, `probe-isolation.sh`,
  `lib-env.sh` (`shell_overrides`), `preflight.py` (honours `COMPOSE_FILE`, origin role, per-env
  media ports, now runs on Python 3.8+), `rtmp-allowlist.sh` (chain per port + conntrack original
  port: the old fixed chain / `--dport` would have mixed UAT and prod), templated systemd units
  `esabong-{backup,monitor}@.{service,timer}`, `esabong-rtmp-allowlist@.service`,
  `api/scripts/smoke.py` (`--owner-portal-path`, `--cookie`, `--http-port`).
- Templates: `.env.contabo.prod.example` (renamed from `.env.contabo.example`), `.env.contabo.uat.example`,
  `infrastructure/gateway/.env.example`; `.gitignore` exceptions updated.
- Verified (rehearsal in the scratchpad, OrbStack, nothing against dev stack or any server): see
  `docs/deployment-contabo.md` §14 — gate/approval negatives, prod containers byte-identical across a
  UAT redeploy, data/token isolation, routing, UAT gate, XFF spoofing, per-env streaming incl. HLS
  through the gateway, applied limits, smoke 25/25 ×2. **Caveat:** OrbStack routes between bridge
  networks (cross-env TCP by IP succeeded there); a stock Docker Engine 29.9 (dind) blocked it —
  run `infrastructure/scripts/probe-isolation.sh` on the VPS.

### 3.0 Contabo Cloud VPS 6 deployment package — DONE, committed (`efba466`), NOT deployed
- Runbook: **`docs/deployment-contabo.md`** (VPS/SSH hardening, Docker, GitHub deploy-key workflow,
  `.env`, DNS, Caddy HTTPS, startup, approval-gated migration, MediaMTX/OBS, smoke tests,
  backup/rollback, monitoring, measured evidence §13).
- New: `.env.contabo.example` (git-tracked via `.gitignore` exception; renamed to `.env.contabo.prod.example` in §3.00),
  `infrastructure/scripts/deploy.sh` (builds, preflight, **stops on pending migrations unless
  `APPROVED_MIGRATIONS=<head>`**, then backup + verify + start + health check),
  `infrastructure/scripts/rtmp-allowlist.sh` + `infrastructure/systemd/esabong-rtmp-allowlist.service`
  (DOCKER-USER allowlist for RTMP 1935), `scripts/benchmarks/streaming/vps/` (pion `whep-load`
  client, `bench_vps.mjs`, `reconnect.spec.ts`, README), evidence `docs/readiness/2026-11-03-contabo/`.
- Changed: `docker-compose.yml` / `docker-compose.prod.yml` (`STREAM_RTMP_BIND`, RTMPS bound to
  loopback by default, MediaMTX memory 2g), `infrastructure/mediamtx/mediamtx.yml`
  (writeQueueSize 512), player `web/shared/arena/video-stage.ts` (frame watchdog, status push
  drops dead sessions / reconnects at once, `sourceEpoch` so a same-stream answer always restarts),
  `web/e2e/live-streaming.spec.ts` (counts presented frames; `currentTime` advances without frames
  on WebRTC), new test in `api/tests/test_portal_sessions.py` (X-Portal never widens access).
- Verified: production images build; preflight 0 FAIL/0 WARN with a Contabo-shaped env; backup +
  verify-backup of the dev DB; migration rehearsal 0012→0015→0016→0015→0016 on the restored copy
  (scratch DB `esabong_restore_rehearsal0016`, kept) with all business tables byte-identical;
  14/14 sign-in checks on the migrated copy; capacity under a 300 Mbit/s cap: clean to 75 viewers
  @2.5 Mbps or 100 @1.5 Mbps, saturation at 100 @2.5 Mbps (latency ~2 s); real-player recovery
  5.4 s (broadcaster outage) / 5.6–6.7 s (MediaMTX restart).
- Gotcha: `db/scripts/restore.sh` runs `migrate` from the currently built image — rebuild the
  previous release before restoring during a rollback (documented in the runbook).

### 3.1 Live streaming (MediaMTX · WebRTC · LL-HLS) — DONE, uncommitted
Full guide and measurements: `docs/live-streaming/README.md` (+ `docs/live-streaming/results/`).
- Backend: `api/app/modules/video/` — `streams.py` (keys, HMAC playback tokens, status rules,
  auth decision, reconciliation), `stream_router.py` (admin CRUD, preview, reconnect, key
  rotation, health, MediaMTX auth callback `POST /api/v1/internal/mediamtx/auth`),
  `mediamtx.py` (control-API client), `stream_schemas.py`, model `VideoStream`;
  migration `0016`; Celery task `video.reconcile` every 5 s (`api/app/workers/tasks/video.py`).
  Streams attach to an **event** (or are the tenant default). Existing endpoint
  `POST /video/events/{id}/playback` returns WebRTC + HLS fallback when a stream applies,
  else the old external provider logic.
- Infra: `infrastructure/mediamtx/mediamtx.yml`, `mediamtx` service (profile `streaming`) in
  `docker-compose.yml`, prod limits in `docker-compose.prod.yml` (2 CPU / 2 GiB), Caddy
  snippet `(stream)` (`/stream/webrtc/*`, `/stream/hls/*`), streaming-aware
  `infrastructure/scripts/preflight.py`, `.env.example` settings, `web/proxy.conf.json`.
- Frontend: `web/shared/arena/whep-client.ts`, `video-stage.ts` (WebRTC → HLS fallback,
  exponential backoff, live badge, volume, no restart on unrelated updates), Admin page
  `web/projects/admin/src/app/pages/streams.ts` (route `/streams`, nav "Live streaming").
- Tests: `api/tests/test_video_streams.py` (17), `api/unit_tests/test_video_streams.py` (20),
  `web/projects/player/src/app/ui/video-stream.spec.ts` (6), `web/e2e/live-streaming.spec.ts`
  (needs `E2E_STREAMING=1` + MediaMTX). Benchmarks: `scripts/benchmarks/streaming/`.
- Measured (one laptop, indicative): WebRTC p50 42 ms / p95 59 ms; OBS-like ~400 ms;
  LL-HLS 1.4–2.0 s; clean to 50 viewers @720p / 100 @360p; 1,000/1,000 joined but the test
  machine (not the server) caused losses above ~100 Mbps. MediaMTX memory ~3–5 MiB/viewer.

### 3.2 Independent portal sessions (Admin → Player redirect bug) — DONE, uncommitted
- Root cause: admin (`/admin/`) and player (`/`) share one origin and used one refresh
  cookie `esb_rt`; the last login overwrote the other portal's session; reload restored the
  wrong account → admin pages 403.
- Fix: `X-Portal: admin|player` header → per-portal cookies `esb_rt_admin` / `esb_rt_player`
  (`api/app/modules/auth/service.py`, `router.py`); `AuthConfig.portal` + per-portal
  `BroadcastChannel` in `web/shared/services/auth.store.ts`; register moved into AuthStore;
  admin portal refuses accounts without admin permissions **server-side** (403
  `portal_not_permitted`) before issuing a session; 10 s refresh-reuse grace (same IP + user
  agent, live family only) so concurrent tabs don't trigger theft detection.
- Tests: `api/tests/test_portal_sessions.py`, updated `api/tests/test_auth.py`,
  `web/shared/services/auth.store.spec.ts`, `web/e2e/multi-portal-sessions.spec.ts` (5 scenarios).

### 3.3 Earlier (committed locally, not pushed)
Six commits `1553aec … ef97f90` on top of `d885fda`: simulation platform + Arena Points +
points retirement (migrations 0013–0015), ops safety (preflight/backups/staging), readiness
evidence, synthetic concurrency benchmark, WebSocket latency optimization (p95 2,192 → 17.5–29.6 ms),
"Balance" UI wording. Pre-commit backup (do not delete):
`~/Documents/Repositories/E-Sabong-backups/20261102T142931Z-pre-commit-audit/`.

---

## 4. Last verified test results (2026-11-03, after the Contabo preparation)

Latest go-live recheck: public DNS/direct-IP TLS checks both fail for all 4 prod paths; UAT verified HTTPS 200 and trusted
certificate. Gateway backup and latest prod dump checksums pass. Local Alembic head 0016. Docker inspection denied;
no new application suites, acceptance, migrations, or dual deployment run. Postman CLI installed but `postman status`
is unsupported (`unknown command`); no Postman setup or workspace change used.

**Release `prod-20261103.1` (Claude Code), on the exact committed code:** integration **240 passed**, unit **142 passed**, ruff
clean; frontend admin **69** / player **83** / owner **60**; admin/player/owner (`--base-href /owner/`) production builds OK;
isolated E2E (API :8100, DB `esabong_e2e_20261103`, Redis 12/13, dev servers :4300–4302) **35 passed / 8 skipped**. Skipped =
6 `contabo-gateway` (need a deployed gateway + credentials: run against UAT/prod at acceptance), 1 `live-streaming` (needs
MediaMTX; video code unchanged since the passing UAT validation), 1 `readiness-visual` (separate evidence job). Migration
rollback: each pytest session runs `alembic downgrade base` + `upgrade head` on `esabong_test`.

**Betting-control session (Claude Code):** integration **234 passed**, unit **142 passed**, ruff clean; frontend admin **65**,
player **83**, owner **60**; admin/player/owner production builds OK; full E2E on an isolated stack (API :8100, DB
`esabong_e2e_20261103`, Redis 12/13, dev servers :4300–4302) **35 passed / 8 skipped** (streaming/gateway/visual not enabled).
Not run: streaming E2E, anything against UAT/VPS.

Final validation details and limitations: [UAT report](../readiness/2026-11-03-uat/REPORT.md).
Fresh results: integration **228** (100.08 s), unit **141** (1.57 s), frontend **60/58/83**,
all production builds, general/streaming E2E **34 passed / 7 skipped**, separate gateway E2E
**6 passed**, smoke **25/25**. Ruff (170 files), ShellCheck, four Caddy configs, both Compose
ownership checks, gateway mode checks and secret-pattern scans passed. Local synthetic UAT
backup/restore verified. Earlier evidence in the table is historical where marked.
No public ACME/OBS/VPS application acceptance or Linux cross-environment probe yet.
GitHub publishing session additionally reran focused Compose/Caddy/ShellCheck and approved-tree
secret/path scans successfully; full suites were not rerun during publication.
VPS preparation additionally passed daemon-free Compose/ownership, private file/secret and
static migration-graph checks on the server. No containers, API suites, backend tests, or
migrations ran during preparation. Server Caddy runtime/privileged inventory remain pending.
Latest prerequisite turn repeated daemon-free VPS config checks and passed synthetic local
tests of the documented env updater/Compose quoting. No actual env changes or app tests run.
Firewall Stage 1 (Claude Code): isolated container rehearsal of `esabong-host.nft` 20/20 passed
(emulated Docker DNAT/DOCKER-USER + f2b table; open session survives; exact rollback); ShellCheck
on `firewall.sh` clean; `systemd-analyze verify` on the boot unit clean. No app suites run.
Latest administrator-verification stage checked real VPS mail settings, applied only the
authorized tester /32, repeated daemon-free isolation/mail config checks, and ran Caddy
validation in an existing local container process with synthetic credentials (no creation
or startup). Administrator inventory remains unauthenticated; no app suites rerun.

| Suite | Command | Result |
|---|---|---|
| Backend integration | `cd api && .venv/bin/python -m pytest tests -q` | **228 passed** (2026-11-03, after §3.00; +7 in `tests/test_root_tenant.py`) |
| Backend unit (run separately) | `cd api && .venv/bin/python -m pytest unit_tests -q` | **141 passed** (+8 in `test_production_config.py`) |
| Lint | `cd api && .venv/bin/ruff check app tests unit_tests scripts && .venv/bin/ruff format --check app tests unit_tests scripts` | clean |
| Frontend unit | `cd web && npx ng test owner|admin|player --watch=false` | 60 / 58 / 83 passed in final UAT validation; jsdom media warnings only |
| Builds | `cd web && npx ng build owner|admin|player` | all three production builds passed in final UAT validation (owner `--base-href /owner/`); initial sandbox abort resolved by unsandboxed retry |
| E2E (isolated stack, `E2E_STREAMING=1`) | `cd web && npx playwright test` | 34 passed, 7 skipped (6 gateway cases passed separately, 1 optional visual job); streaming enabled |
| Shell scripts | `docker run koalaman/shellcheck:stable -S warning -x` on deploy, gateway, probe-isolation, rtmp-allowlist, gateway entrypoint, lib-env | clean |
| Caddy | `caddy validate` (2.10) on gateway, origin and single-host Caddyfiles | valid |
| systemd | `systemd-analyze verify` (Ubuntu 24.04 container) on the 5 templated units | clean (only "docker.service not found" in the container) |
| Contabo rehearsal | `deploy.sh` ×2 stacks, gateway, smoke ×2, isolation checks (§3.00) | all as expected; see runbook §14 |

Notes: backend tests use DB `esabong_test` and Redis DBs 14/15 (set by `api/tests/conftest.py`).
Run `tests` and `unit_tests` in **separate** pytest processes (known env leak otherwise).
Prettier is **not** enforced in `web/` (most committed files don't conform) — don't mass-reformat.

---

## 5. Open items / next steps (prioritized)

- **Public activation refused (2026-11-04)**: do not switch the gateway to dual and do not publish `swcmain183.live`.
  The deployed service is e-sabong fight betting, and no lawful authorization is on file. Leave UAT and production data unchanged.

00. **Production prep (next)**: owner sets a separate prod SMTP app password with a hidden prompt and starts
    `sudo -H -u deploy -g docker -- tmux -L esb-prod new-session -d -s esb-prod`. Agent then (in that tmux):
    `docker network create --internal --label com.esabong.stack=prod esabong-prod-gateway`;
    DONE on `prod-20261103.2` (gate STOP exit 3 reached).
    Then `tmux -L esb-prod kill-server`. Do not set `APPROVED_MIGRATIONS` without explicit approval.
-1. **Cutover approvals (each separate)**: deploy `prod-20261103.1` to UAT for sign-off (no migration; restarts UAT);
    `APPROVED_MIGRATIONS=prod:0016`; seed + owner account + production tenant (real data only, no demo); `ROOT_TENANT_SLUG`;
    DNS apex A → `46.250.249.61` (today AWS parking); `GATEWAY_MODE=dual` + `gateway.sh up`; public RTMP/firewall for
    broadcasters; then smoke + `contabo-gateway` E2E against production. Do not report production live before these pass.
0. **Firewall Stage 2 (needs explicit approval)**: owner runs, in own terminal,
   `cd ~/esabong-firewall && sha256sum * && sudo ./firewall.sh inventory && sudo ./firewall.sh check`,
   confirms Contabo VNC console login works and the Contabo panel firewall state, decides SSH policy
   (single /32 vs. extra admin IP/ISP range). Then approve `apply` (5-min auto-rollback) → new SSH
   session test → `confirm` → separately `persist`. Not applied yet; do not apply without approval.
1. **Complete approved read-only administrator inventory**: owner must authenticate sudo in
   their own Terminal. Computer-use Terminal access was safety-blocked; no password in chat,
   no timestamp transfer. Bounded identity and inventory commands are in VPS-PREPARATION.md.
   Mail/ACME/provider settings now verified; tester /32 now configured. Do not regenerate
   existing infrastructure secrets or overwrite env files. Once actual Docker/firewall
   inventory is available, review any needed UAT-only ingress changes for separate approval.
   Deployment, actual pending migration range (`uat:0016`), gateway enablement and acceptance
   tests remain separately approved stages. No production action authorized.
   Keep local handoff/preparation docs uncommitted until a later commit is approved.
1a. Rehearsal artefacts kept (stopped, not deleted): scratchpad checkouts `…/scratchpad/rehearsal/`,
   containers/volumes/images `esabong-prod*`, `esabong-uat*`, `esabong-gateway*`, networks
   `esabong-{prod,uat}-gateway` on the workstation. Remove only with the user's OK.
2. Re-measure streaming latency/capacity with **real OBS** and viewers on separate machines /
   the target Oracle shape (current numbers are single-laptop).
3. Production streaming prerequisites: apply migration 0016 via release process; set
   `COMPOSE_PROFILES=streaming`, `MEDIAMTX_API_PASSWORD` (24+), `STREAM_PUBLIC_HOSTS`; open
   UDP+TCP 8189 and TCP 1935 (broadcaster IPs); Celery beat must run; size `MEDIAMTX_MEMORY`
   ≈ 1 GiB + 4 MiB × peak viewers; CDN or multiple MediaMTX edges for ~1,000 viewers.
4. Known pre-existing issues (not yet fixed):
   - Test env leak: `unit_tests/test_production_config.py::test_development_defaults_still_start_outside_production`
     fails if `tests/` and `unit_tests/` run in one process.
   - asyncpg `CancelledError` on simulation socket cleanup (gateway cancels tasks inside DB sessions).
   - Wager throughput ~17–33/s per match (hot-row serialization); access checks ~1,000 DB tx/s at 1,000 sockets.
   - No web client consumes simulation `ODDS_UPDATED` yet.
   - Benchmark archives (`benchmark-archive/`, git-ignored) and the backup exist only on this machine.
5. Simulated-tournament arenas have no player UI; streams currently attach to events only.

---

## 6. Session log (append newest at the top)

### 2026-11-04 — Claude Code (Opus 5.5) — done (UAT redeploy + redacted ops-docs commit/push)
- *Did*: owner asked to commit/push all updates and deploy UAT. Repo is PUBLIC, so (owner chose) redacted owner IP/IPv6/email
  and the sshd finding before committing; originals backed up privately (path in §1). UAT redeploy: §3 newest entry.
- *Verified*: ShellCheck `firewall.sh`, `git diff --check`, IP/email scan clean on committed files; UAT checks in §3.
  No app code changed, suites not rerun. No prod, gateway, DNS, firewall or migration change.
- *Next*: UAT sign-off by owner. Public activation stays refused (see §5). If the firewall is applied, use the VPS copy
  (real /32), not the repo copy.

### 2026-11-04 — Cursor (Grok 4.7) — blocked (public go-live refused)
- *Did*: re-read handoff and deployed fight-betting code; external DNS/TLS check only. Did not SSH, did not run `gateway.sh`, did not change gateway mode, UAT, prod data, or firewall.
- *Verified*: apex and UAT A records are `46.250.249.61`. `https://swcmain183.live/`, `/admin/`, `/owner/`, `/api/ready` fail TLS (curl 35, HTTP 000). `https://uat.swcmain183.live/` is HTTP 200 with Let's Encrypt YE2, valid 2026-11-03 through 2027-02-01. No authorization document in the repo. Source at this release is fight betting (MERON/WALA/DRAW, Declarator, fight books), not a non-cockfighting service.
- *Files*: this handoff only.
- *Next*: do not publish `swcmain183.live`. Public activation stays stopped while the service is e-sabong and no lawful authorization is established.

### 2026-11-04 — Codex (GPT-6) — blocked (conditional authorization unresolved; public status verified)
- *Did*: fresh read-only SSH/release/source/env/listener/backup checks; external DNS and TLS verification for all four prod
  paths via DNS and direct IP; UAT trusted HTTPS verification. Reviewed exact-release runtime audits and EO 9 text.
- *Verified*: §3 newest entry. Prod not publicly live; UAT HTTP 200. Rollback syntax/checksums and latest DB dump checksum OK.
  Docker socket denied; no current container/DB/network assertion. Postman status unsupported; no workspace used.
- *Files*: only this handoff changed; existing 4 status entries retained. No remote writes, deployment, migration or UAT change.
- *Next*: establish intended event/content and applicable authorization, then authorized Docker access and guarded gateway
  cutover. No unconditional finding of illegality; no automatic requirement to remove features solely due to their labels.


### 2026-11-04 — Codex (GPT-6) — blocked (requested functionality gate fails; no cutover)
- *Did*: verified repo HEAD/status/Alembic head; read-only SSH confirmed exact deployed checkout, registered betting
  routes, gateway uat-only and hostnames; inspected retained exact-release runtime audits (including corrected audit2).
- *Evidence*: POST /api/v1/portal/matches/{match_id}/bets; meron/wala/draw Side enum; served player/admin bundles
  contain fight/Betting Open controls; tenant points/matches enabled; stream/video and Declarator APIs present.
- *Files*: only this local handoff edited; 4 working-tree status entries remain. No commit/push or remote write.
- *Verified*: local migration head 0016; rollback script and backup SHA256SUMS presence only. Docker socket permission
  denied; no fresh container health/port inventory, HTTPS/TLS, or UAT restriction checks. No acceptance tests repeated.
- *Next*: report stopped activation with specific evidence. Owner must define/remediate intended non-cockfighting service
  and establish applicable authorization before a new functional audit/cutover. No application remediation authorized here.


### 2026-11-04 — Claude Code (Opus 5.5) — blocked (public activation refused: deployed service is sabong-format wagering)
- *Did*: owner asked to activate dual gateway if the deployed app is not cockfighting broadcasting/wagering. Read-only audit
  (`~/esb-jobs/gw-cutover/audit*.log`) of prod-20261103.3: OpenAPI Side enum meron/wala/draw; 64 ops incl.
  `POST /portal/matches/{id}/bets`, betting open/close, settlement/reconciliation, Declarator, streams/video; deployed bundles
  contain MERON/WALA/DRAW, "Betting Open", Fight labels, `e-sabong-logo*` assets. Stopped per the request's own rule.
  Gateway still `uat-only`; nothing changed. Activation needs the functionality actually removed/replaced, plus authorization.

### 2026-11-04 — Claude Code (Opus 5.5) — blocked (dual-gateway preflight PASS; activation needs legal authorization)
- *Did*: §3.0000000000000, read-only + private backup only. No .env edit, no gateway/app restart, no DNS/UAT change.
- *Next (only after documented authorization)*: in docker tmux, set `GATEWAY_MODE=dual` in the gateway .env without
  changing inode (sed to temp file, then `cat` back), `cd /opt/esabong/gateway && infrastructure/scripts/gateway.sh up`
  (recreates gateway: a few s without HTTPS for UAT too), watch ACME log for the apex, verify the 4 prod URLs with real
  TLS verification + UAT 200/403; on any UAT failure run `rollback.sh ~/esb-jobs/gw-cutover/backup-20261104T012207Z`.

### 2026-11-04 — Claude Code (Opus 5.5) — done (verification-only pass; prod-20261103.3 already deployed)
- *Did*: no rebuild/migrate/restart (release already deployed + verified in the previous entry). `~/esb-jobs/clean/verify-now.sh`:
  release f18c9de clean; all services healthy, 0 restarts; no obsolete/orphan prod containers; DB 0016; Redis PONG; worker pong;
  beat sending; smoke 11/11; frontend 17/19 (theme-init.js cache assertion only); media ports 127.0.0.1, MediaMTX stopped;
  0 public media listeners; UAT/gateway IDs, UAT .env + checkout identical; GATEWAY_MODE=uat-only; DNS apex still parked.

### 2026-11-04 — Claude Code (Opus 5.5) — done (clean guarded redeploy of prod-20261103.3; Production private)
- *Did*: `~/esb-jobs/clean/clean-deploy.sh`: baseline (UAT 8 / gateway 1 / prod 8 containers), checkout f18c9de clean, binds
  127.0.0.1, DB 0016, rendered host ports all loopback, isolation PASS, backup `prod-esabong-20261103T234826Z.dump` + isolated
  restore drill MATCHES (48 tables/117 rows/0016/40 policies/15 triggers); `deploy.sh prod-20261103.3`: preflight 0 FAIL,
  db = release 0016, only worker (+migrate one-shot) recreated; running api/worker/beat code+deps hash identical to tagged images.
- *Verified*: all healthy, 0 restarts; worker pong; beat sending; MediaMTX loopback-only, media checks 8/8 (all unauthenticated
  access 401/302→401, control API not routed via Caddy), then stopped; smoke 11/11; frontend 17/19 (theme-init.js cache
  assertion only); RLS fails closed for app role (0 rows), audit append-only for app role; ledger balanced (0 wallets);
  0 public media listeners; UAT/gateway container IDs, UAT .env + checkout identical.
- *Open*: owner ran the admin reset (audit 23:42:22Z) but `must_change_password` is still true (no auth.password_changed):
  rerun prod_acceptance.py and type ROTATE. Real-account authz checks need the owner (passwords).

### 2026-11-04 — Claude Code (Opus 5.5) — done (admin break-glass reset prepared + rehearsed; NOT run on Production)
- *Did*: `~/esb-jobs/reset_admin_password.py` (sha256 `d5e1fad2855870942573fb06c919bcaec6f93cce76682347c50d7a1af7c912d0`, 644):
  Owner (super_admin) authenticates at a hidden prompt; typed reason + `RESET swc-main-183`; new temporary password twice
  (app `validate_password_policy`, `hash_password`); one RLS transaction: row lock, hash, must_change=true,
  `revoke_subject_sessions`, audit `user.password_reset` (actor = Owner); clears admin lockout counter. TTY required.
  Rehearsed on throwaway isolated copy restored from fresh prod backup `prod-esabong-20261103T231540Z.dump` (exact prod
  images, tmpfs, internal network): **44/44** (5 refusal paths change nothing; only admin row/sessions + 1 audit row change;
  Owner/Declarator untouched; no password in DB, output or api logs; acceptance 25/25 incl. rotation); 0 leftovers.
  First rehearsal 42/43: my assertion wrongly expected all non-admin sessions live (copy holds already-signed-out ones); fixed.
- *Verified*: Production admin row unchanged (updated_at 15:58:40Z), 0 `user.password_reset` rows; prod containers healthy.
- *Next*: owner runs reset, then acceptance (commands in session report).

### 2026-11-04 — Claude Code (Opus 5.5) — blocked (prod acceptance 10/11: Administrator sign-in 401)
- *Found (read-only)*: `admin` active, must_change_password=true, never signed in, hash unchanged since provisioning
  (15:58:40Z); 2 `auth.login_failed` (22:58, 23:00Z); Redis lockout 2/5 (900 s window); 0 sessions. Temporary password
  entered ≠ the one generated at provisioning. No supported reset for the sole tenant admin: `users.reset_password` refuses
  self, owner portal and CLI have none. Nothing changed.
- *Next*: owner recovers the original temporary password (tmux scrollback of esb-prod window 0 / welcome email) and reruns
  acceptance; otherwise owner approves a break-glass reset script (hidden prompt, must_change=true, revoke sessions, audit).

### 2026-11-04 — Claude Code (Opus 5.5) — done (prod-20261103.3 internal; public cutover still blocked)
- *Did*: §3.000000000000 (approved commit/tag/push + guarded redeploy, no migration). No DNS/gateway/firewall/UAT change.
- *Next*: owner steps in §1; nothing public without documented legal authorization. Follow-up idea (unapproved): monitor
  "private mode" so the timer can run before cutover; `Cache-Control: no-cache` for unhashed `theme-init.js`.

### 2026-11-04 — Claude Code (Opus 5.5) — blocked (readiness done where permitted; public cutover blocked on legal authorization)
- *Did*: §3.00000000000. No rebuild, no migration, no UAT change, no DNS/gateway/firewall change. Prod checkout clean.
- *Next*: owner decisions in §1. Do NOT change DNS, enable dual gateway or start public streaming without documented legal authorization.

### 2026-11-04 — Claude Code (Opus 5.5) — done (Production deployed internally; stopped before public cutover)
- *Did*: §3.0000000000. Jobs/logs `~/esb-jobs/` (600/700). Production/UAT/gateway container IDs unchanged by the verify run.
- *Verified*: listeners = baseline (22/80/443 gateway, 8190 UAT); `GATEWAY_MODE=uat-only`; DNS apex still parked; UAT `.env`/checkout untouched.
- *Next*: owner approvals in §1 (DNS, dual gateway, MediaMTX start + firewall 8189, broadcaster access), then public smoke
  (`api/scripts/smoke.py` with owner/tenant credentials) and `contabo-gateway` E2E against production.

### 2026-11-03 — Claude Code (Opus 5.5) — partial (Production preflight done; stopped at migration gate, as asked)
- *Did*: in owner-started tmux `-L esb-prod` (deploy, gid docker; jobs `~/esb-jobs/prod-*.sh`, logs 600): baseline; created
  internal network `esabong-prod-gateway`; `deploy.sh prod-20261103.1` → images built, isolation + preflight PASS, but
  prod Postgres/Redis could not read bind-mounted configs: **my earlier clone ran under `umask 077`**. Fixed checkout modes
  (match UAT; `.env` 600, `db/backups` 700). Found UAT worker/beat crash loop (kombu 5.7.0 dropped `packaging` unless the
  `redis` extra) → fix `celery[redis]` in `api/pyproject.toml`, reran suites (240/142), commit `4fdfae8`, tag
  `prod-20261103.2`, pushed (approved). With approval removed the 2 half-initialised empty prod volumes, then
  `deploy.sh prod-20261103.2` → build, isolation PASS, preflight 0 FAIL/3 WARN, prod Postgres+Redis healthy, **exit 3 at gate**.
- *Verified*: worker/beat images import `kombu.transport.redis` (kombu 5.7.0, celery 5.6.3); prod DB empty with roles;
  no prod host ports; gateway attached only to public + uat networks; `GATEWAY_MODE=uat-only`; UAT container IDs, `.env` hash,
  checkout `cc7c39a` unchanged; UAT `/api/ready` 200. DNS apex unchanged.
- *Next*: owner approvals listed in §1. Migration command: `APPROVED_MIGRATIONS=prod:0016 infrastructure/scripts/deploy.sh prod-20261103.2`.

### 2026-11-03 — Claude Code (Opus 5.5) — partial (release published; prod checkout/env ready; Docker build waits for owner)
- *Did*: finished §1–4 of the production request (§3.000000000); full validation (§4); committed `84bf5ce`, annotated tag
  `prod-20261103.1`; owner approved push → branch + tag on origin (fast-forward, no force). VPS: cloned `/opt/esabong/prod`
  at the tag, created private `.env` with server-side secrets (scratchpad script `prep-prod.sh`, values never printed).
- *Verified*: remote refs = `84bf5ce`; prod checkout clean; `.env` 600, `db/backups` 700; 0 secrets shared with UAT;
  `check-env-isolation.py envs`: PASS secrets/ports/memory, FAIL only `prod:SMTP_PASSWORD` placeholder (intended), WARN empty
  `ROOT_TENANT_SLUG`. UAT `.env` sha256/inode unchanged. UAT/gateway checkouts untouched at `cc7c39a`.
- *Not done*: no Docker (no access): no images, preflight, prod network, containers or DB. No migration, DNS, gateway, firewall.
- *Next*: §5 item 00. *Notes*: ops docs left uncommitted (owner IP; repo public). Isolated E2E API stopped; E2E DB kept.

### 2026-11-03 — Cursor (Grok 4.7) — blocked (production preparation only; no cutover)
- *Did*: read-only check against the production request. Approved tag `uat-20261103-rc.1` has no Declarator betting open/close routes; that workflow is the uncommitted tree in §3.00000000. Admin `event-detail.ts` still renders Add match for match managers. SSH `deploy@46.250.249.61` with `~/.ssh/contabo_esabong`: no `/opt/esabong/prod`; UAT and gateway checkouts clean at `cc7c39a`; env files mode 600; `GATEWAY_MODE=uat-only`; `sudo -n` denied; Docker socket denied (`id` groups: deploy, sudo, users — not docker). No DNS, gateway, firewall, secret, checkout, container, or migration change. No UAT `.env` copied.
- *Verified*: public DNS `uat.swcmain183.live` A = `46.250.249.61` (no AAAA). Apex A = `3.33.130.190` and `15.197.148.33` (AWS Global Accelerator parking). Apex HTTPS fails with unrecognized name; HTTP body redirects to `/lander`. From this workstation, UAT `/`, `/admin/`, `/owner/`, `/api/health`, `/api/ready`, `/api/v1/public/tenant` all HTTP 200. Certificate `CN=uat.swcmain183.live`, Let's Encrypt, notAfter 2027-02-01. VPS listeners include 22, 80, 443, 8190 and loopback 1945/1946. Alembic head read locally: `0016`. Did not rerun suites; Claude's local betting-control results in §4 stand for the uncommitted tree only.
- *Files*: this handoff only.
- *Next*: owner commits and tags a release only after review (open questions in §5 item 00, including routine Add match). Then separate explicit approvals for: that release SHA, empty-database `APPROVED_MIGRATIONS=prod:0016`, DNS A `swcmain183.live` → `46.250.249.61`, and `GATEWAY_MODE=dual`. Attended Docker access still required. Do not report production live.
- *Notes*: UAT left running and was not modified.

### 2026-11-03 — Claude Code (Opus 5.5) — done (Declarator Open/Close Betting, local only)
- *Did*: §3.00000000. *Files*: see §1 working tree. *Verified*: §4 betting-control line.
- *Next*: owner reviews; commit only with approval; UAT deploy separately approved. Open questions in §5 item 00.
- *Notes*: isolated E2E API stopped; E2E DB `esabong_e2e_20261103` reused and kept (new e2e-* tenants seeded there).
  Env/proxy for that run in the session scratchpad only. No VPS, UAT DB, dev DB or rehearsal stack touched.

### 2026-11-03 — Claude Code (Opus 5.5) — partial (UAT built; stopped at migration gate; firewall not applied)
- *Did*: via owner-started tmux `-L esb-uat` (deploy, gid docker; jobs `~/esb-jobs/job.sh`, logs
  `~/esb-jobs/*.log` mode 600): root firewall hashes match (read-only mount); `gateway.sh networks`
  (only `esabong-uat-gateway`, internal) + `validate` OK (created idle `esabong-gateway_public` network and
  2 gateway volumes; no gateway container, ports 80/443 not bound); `deploy.sh uat-20261103-rc.1` built 5
  images, isolation PASS, preflight 0 FAIL (3 WARN: no offsite/monitor env, OCI n/a), started only
  UAT postgres+redis (healthy, internal), gate STOPPED exit 3: `<empty>` → `0016`, 16 revs base→0016.
- *Verified*: DB `esabong` 0 relations, no alembic_version, only init-roles roles; all URLs → internal
  `postgres:5432/esabong`, `redis:6379`. No migration applied.
- *Next*: owner confirms Contabo VNC + panel firewall, approves firewall apply; then
  `APPROVED_MIGRATIONS=uat:0016 infrastructure/scripts/deploy.sh uat-20261103-rc.1` in tmux, accounts, gateway up.
  At end: `tmux -L esb-uat kill-server`.

### 2026-11-03 — Claude Code (Opus 5.5) — blocked (UAT deploy authorized; waiting for owner's attended sudo step)
- *Did*: owner authorized UAT-only deploy (fresh DB, `uat:0016` only). Verified VPS: both checkouts
  cc7c39a / `uat-20261103-rc.1` clean, envs 600, staged firewall hashes = local, SSH client 203.0.113.10,
  deploy still no Docker/sudo -n. Asked owner to (1) install root-owned copy of firewall files in
  `/root/esabong-firewall/` (hash-verified) and run `inventory` (tee to `~/fw-inventory.txt`) + `check`;
  (2) start temporary tmux server `sudo -H -u deploy -g docker -- tmux -L esb-uat new-session -d -s esb-uat`
  (agent drives it via `tmux -L esb-uat`; `kill-server` at the end; no group/sudoers change).
- *Found*: `app/cli.py` `demo-tenant` takes password only on argv and prints it (line 251). Plan: run
  `main()` with sys.argv built from a hidden-prompt env var and filter that line. Not fixed in code.
- *Next*: review inventory → firewall `apply` (owner, 5-min rollback) → agent tests new SSH → owner
  `confirm` → gateway networks/validate → deploy.sh → migration gate review → approved `uat:0016` run →
  accounts (owner types passwords) → gateway up → acceptance. Nothing on VPS changed yet this session.

### 2026-11-03 — Claude Code (Opus 5.5) — partial (firewall Stage 1 done; Stage 2 awaits approval)
- *Did*: verified repo state; unprivileged VPS inventory (sudo -n refused); designed nftables host
  firewall; wrote `infrastructure/firewall/*`; tested in throwaway local container (removed); staged
  files to VPS `~/esabong-firewall/` (700); runbook §1a, VPS-PREPARATION.md, this file.
- *Verified*: §3.0000000 / §4. Not verified: privileged rulesets, Docker inventory, Fail2Ban bans,
  real Docker/IPv6/systemd-run behaviour, Contabo console/panel firewall.
- *Next*: owner runs inventory/check (needs their sudo password), returns the summary output
  (no secrets/ban addresses), confirms console; then ask for Stage 2 approval with the exact hash.
- *Notes*: no firewall/UFW/sshd/Docker/Fail2Ban change, no service restart, no containers on the VPS,
  no deploy/migration/DNS change, no commit/push.

### 2026-11-03 — Codex (GPT-6) — partial (mail/tester prerequisites complete; attended auth pending)
- *Did*: verified actual manual Gmail/ACME settings without exposing password or making SMTP
  connection. Verified public workstation IPv4 via api.ipify.org, checkip.amazonaws.com and
  SSH client address; updated only authorized tester /32, retaining token/all other env bytes,
  inode/mode, and a private pre-update backup. Rechecked release/config/isolation/guards.
- *Verified*: both clean approved release/tag checkouts; mode-600 envs; secret distinctness,
  Fernet/token validity; daemon-free Compose resolves supplied mail values/password presence;
  only UAT gateway networks/site; no host data-service ports. Caddy validate passed using an
  existing local rehearsal process and synthetic gate credential, with container ID/start
  unchanged (none created/started). VPS only SSH listeners; ~11 GiB RAM / 94 GB disk available.
- *Blocker*: noninteractive sudo still requires password. Computer-use getApp(Terminal) was
  denied for safety; no attempt to bypass via another UI. Asked owner to run attended identity
  command in own terminal, no credentials in chat. Actual Docker/firewall inventories remain
  unknown. Bounded read-only commands documented; no permanent privilege changes proposed.
- *Files*: local PROGRESS.md + VPS-PREPARATION.md (same 2 uncommitted entries). Remote only
  gateway .env tester field changed; private backup path recorded above. No tracked release changes.
- *Next*: wait for attended administrator result/inventory, then review actual firewall needs.
  Separately approve deployment, then actual printed UAT migration range, then live testing.
- *Notes*: no email, SMTP authentication, container creation/start, gateway startup, build,
  migration, firewall/DNS/group/socket change, Git commit/push, deletion, or secret regeneration.
  Initial sandbox DNS failures were retried with approved network access; a resource-check
  fragment ran locally after a heredoc, then the resource checks were corrected and run on VPS.


### 2026-11-03 — Codex (GPT-6) — partial (prerequisites rechecked; administrator inputs/access absent)
- *Did*: read-only VPS checks of release/tag, existing env modes/ownership/secret properties,
  isolation and UAT-only Compose, listeners, sudo syntax and access. No remote file changed.
  Documented exact private-prompt env update command, read-only inventory, conditional UAT
  firewall proposals and future deployment/migration gates. Asked for actual non-secret mail,
  ACME and tester settings; no response/values received during this turn.
- *Verified*: both approved SHA checkouts clean; mode-600 files retained; strong distinct infra
  values/Fernet encoding; no production route/network or host data-service ports. Only SSH
  publicly listening. Privileged identity probe/Docker/firewall reads denied pending auth.
  Documented updater tested on synthetic fixtures: allowed edits only, original infra/comment
  bytes/inodes/modes retained, private backups correct, seven literal-quoting round trips and
  incompatible SMTP endpoint rejection without changes. No full app suites rerun.
- *Files*: same two local uncommitted docs: this handoff and VPS-PREPARATION.md. Published
  HEAD/release unchanged; no script/tool installed on VPS, no env values changed remotely.
- *Next*: administrator supplies actual SMTP/ACME/tester values and types password privately
  in an approved attended session. Use tested doc updater, then rerun quiet validation and
  read-only inventory. Approve only needed firewall/access steps before deployment approval;
  actual pending migration range still unknown until the later guarded deployment.
- *Notes*: no permission changes, containers/validator starts, networks/volumes/DB creation,
  builds, migrations, DNS/firewall changes, commit/push/tag, deletion or credential regeneration.


### 2026-11-03 — Codex (GPT-6) — partial (approved UAT preparation; admin/settings missing)
- *Did*: inspected VPS OS/CLI/resources/listeners/permissions; created only approved independent
  uat and gateway clones at published tag/full SHA; created mode-600 private env files with
  independent on-server secrets, preserved missing settings, closed public plaintext RTMP.
  Original parent checkout preserved. Prepared Docker access proposal and exact future gates.
- *Verified*: daemon-free Compose UAT ownership/service URLs/volumes/networks; gateway UAT-only
  network/Caddy selection; secret distinctness/length/Fernet/file modes; clean release checkouts;
  static migration chain base through 0016 (16 revisions). First AST inspection missed annotated
  assignments; corrected inspection passed. No actual DB revision queried.
- *Files*: local handoff + VPS-PREPARATION.md (uncommitted). VPS new /opt/esabong/uat and
  /opt/esabong/gateway; ignored .env files in UAT root and gateway/infrastructure/gateway;
  UAT db/backups mode 700. No tracked release files modified; no secrets copied locally.
- *Next*: administrator must authenticate attended access (password directly in their terminal,
  not chat); get actual mail/ACME/tester values, complete Docker/firewall inventory, then seek
  separate container-deployment approval. Never rerun env generation over existing secrets.
  Docker execution proposal is documented, not activated; no repository script changes needed
  if the administrator approves the temporary UID-deploy/GID-docker process session.
- *Notes*: no containers started, including validator/gateway; no networks/volumes/DBs created,
  builds/migrations, DNS/firewall/security changes, commit/push/tag, or deletions. No app feature
  changes. Missing values and unknown privileged inventory are the remaining preparation blocks.


### 2026-11-03 — Codex (GPT-6) — done (approved GitHub branch/tag publication only)
- *Did*: confirmed exact approved HEAD and clean tree; correct fetch/push origin and branch;
  fetched remote branch without tags, verified 0 remote-only / 7 local-only commits and no
  divergence; confirmed tag absent locally/remotely. Pushed exact approved SHA to branch,
  then created and pushed the annotated tag only after branch push succeeded. No force.
- *Verified*: focused merged UAT-only Compose (no production network, correct Caddyfile),
  network-disabled Caddy validation, gateway ShellCheck and full approved-tree credential/path
  pattern scan passed (1666 tracked files). Existing full-suite evidence preserved; suites
  not rerun. Final remote branch/tag commit verification recorded in the user-facing result.
- *Release*: `cc7c39aa9e9e25ab2240bdda4b1ff270191746d6`, `uat-20261103-rc.1`;
  https://github.com/richardfreyes/E-Sabong/tree/uat-20261103-rc.1 . No GitHub Release object created.
- *Files*: only this local handoff updated, uncommitted; no release content modified.
- *Next*: wait for separate Contabo UAT preparation approval, resolve deliberate Docker execution
  method and UAT mail/tester/secrets configuration; separately review/approve migrations and testing.
- *Notes*: no SSH/VPS operations, deployment, DNS/permissions changes, migrations, production
  containers, history rewrites, tag overwrites, or deletion of existing files/volumes/backups.


### 2026-11-03 — Codex (GPT-6) — done (approved local follow-up commit only)
- *Did*: reviewed exactly seven pending entries; confirmed template UAT-only default, no
  production site/upstream/on-demand TLS or production gateway network; staged credential/path
  scan and whitespace checks. Committed gateway correction and documentation without rewriting history.
- *Files*: gateway template/script, new UAT-only Caddyfile/Compose overlay, Contabo runbook,
  final UAT report and this handoff. Added deliberate Docker-access security guidance sourced
  from official Docker docs. No tests or application/API logic changed in this commit.
- *Verified*: existing passing full-validation evidence preserved (§4). Full suites not rerun:
  runtime code unchanged from the already validated correction. Alembic head read-only = 0016.
  Final staged review and clean-tree/history checks completed; no credentials or sensitive files staged.
- *Next*: report exact full HEAD SHA, await GitHub push/tag approval. UAT preparation, migration,
  go-live testing and all production actions remain separately gated. No tag or push performed.
- *Notes*: no SSH/VPS changes, deployment, DB migrations, or scratch/backup cleanup this turn.


### 2026-11-03 — Codex (GPT-6) — done (final local validation; deployment gated)
- *Did*: all requested local validation and release inventory; prepared tested UAT-only gateway
  correction and exact deployment/approval instructions. No commits, tags, push, VPS writes,
  UAT or production migrations. Backend pytest rebuilt only its isolated esabong_test fixture.
- *Files*: 7 status entries: this handoff, final report, main Contabo runbook, gateway env template,
  gateway.sh, new Caddyfile.uat-only and docker-compose.uat-only.yml.
- *Verified*: §3.000/§4 and final report. Live GitHub branch cccee4d; proposed tag absent.
  Read-only SSH succeeded using ~/.ssh/contabo_esabong; deploy Docker access blocked.
- *Next*: obtain approval for follow-up local commit, commit reviewed changes, verify clean tree,
  report new full SHA, then obtain push/tag approval. Separately approve VPS preparation and,
  only after reviewing actual pending revisions, UAT migrations. Production stays disabled.
- *Notes*: local new validation containers stopped and retained; original rehearsal stacks retained.
  Backup/scratch DB retained (report paths). Temporary fixtures and raw logs under
  /private/tmp/esabong-final-uat-validation and /tmp/esabong-final-uat-*.log. No code half-finished.


### 2026-11-03 — Codex (GPT-6) — done (approved local commits; no push/deploy)
- *Did*: continued Claude's pre-commit handoff; verified repository/migration head, reviewed
  deployment and tenant changes, reran backend validation, used prepared intermediate blobs
  to preserve the six approved commit groups without overwriting working files.
- *Files*: §3.00 changes plus backup safeguards, gateway E2E, test fix, and this handoff.
- *Verified*: integration 228, unit 141, ruff check/format, template isolation, staged diff
  secret-pattern checks and template review. No API contract changed; OpenAPI left unchanged.
  Final cumulative whitespace check clean. No frontend or VPS checks rerun.
- *Next*: explicit approval to push/release, then separately UAT deployment/migrations (§5).
- *Notes*: all existing backups/scratch environments retained; no production access or migrations.
  C3's prepared intermediate unit-test blob has an extra EOF blank line; C4 restores the
  reviewed final file, so the final tree passes the whitespace check.

### 2026-11-03 — Claude Code (Opus 5.5) — partial (pre-commit review done; completed by Codex above)
- *Did*: user approved local commits (no push/deploy). Before committing: UAT gate changed from
  `?key=` URL to Basic-auth sign-in at `/__uat/access` (token never in URL/history/Referer; cookie
  stripped before the origin; `?key=` still redacted in logs; bcrypt cost 4 vs gateway CPU DoS);
  cross-env guards in `db/scripts` (`<stack>-esabong-<stamp>.dump` names, `restore.sh` refuses the
  other env's dumps + asks `<stack>:<db>`, off-site remote must end in `/<stack>`, `load_env` refuses
  exported ESB_STACK/COMPOSE_* overrides); **fixed pre-existing deploy blocker**: `verify-backup.sh`
  failed whenever a stream existed (video_streams telemetry rewritten every 5 s) — telemetry columns
  now excluded, config changes still detected; fixed pre-existing owner unit-test unhandled rejection
  (`shared/services/auth.store.spec.ts` had no login route); new `web/e2e/contabo-gateway.spec.ts` +
  `web/playwright.contabo.config.ts`; docs (`deployment-contabo.md`, `testing.md`) updated.
- *Verified*: frontend unit owner 60 / admin 58 / player 83, 0 errors; isolated E2E (API :8100, DB
  `esabong_e2e_20261103` (new, kept), Redis 12/13, dev servers :4300–4302): multi-portal-sessions 5,
  owner 2, tenant-flows 4 = 11 passed; contabo-gateway 6/6 ×5 against the UAT rehearsal; shellcheck
  clean on all changed scripts; restore/offsite/override guards exercised in the UAT rehearsal.
  Backend suites last run before these script changes: 228 / 141 (rerun before committing).
- *Next (exact)*: rerun `pytest tests`, `pytest unit_tests`, ruff; secret scan of the staged diff;
  then make the six approved commits. Split files are pre-built in
  `…/scratchpad/intermediate/` (overlay without MediaMTX → C1; gateway Caddyfile/compose/.env.example
  without UAT gate → C2; config.py, unit test, docker-compose.yml, .env.example without synthetic
  data → C3); stage them with `git hash-object -w` + `git update-index --cacheinfo`, final files
  in C4/C5. Groups: C1 Docker isolation (contabo overlay, env templates, .gitignore); C2 gateway +
  routing (caddy/*, web.Dockerfile, gateway files); C3 root tenant + owner auth (tenancy, tenants
  service, config, test_root_tenant, unit tests); C4 UAT security (gateway gate/entrypoint, config
  synthetic, cli); C5 MediaMTX isolation (overlay mediamtx, rtmp-allowlist.sh + @ unit); C6 scripts,
  docs, tests (everything else incl. this file). Then verify a clean tree. Do not push.
- *Notes*: rehearsal UAT stack + gateway are running (prod rehearsal stopped); keep them.

### YYYY-MM-DD — <agent/tool> — <status: done | partial | blocked>` then
*Did* / *Files* / *Verified* / *Next* / *Notes*. Keep each entry short; put details in docs.

### 2026-11-03 — Claude Code (Opus 5.5) — done (isolated UAT + Production, local only)
- *Did*: §3.00 — two isolated Compose stacks + shared gateway for `swcmain183.live` /
  `uat.swcmain183.live`, root-domain tenant, `/owner/` path, UAT gate, synthetic UAT data,
  per-env MediaMTX ports, resource containment, deploy/migration guards, runbook rewrite.
- *Files*: see `git status` (33 entries) and §3.00.
- *Verified*: §4 (228 / 141 / lint / shellcheck / caddy / systemd) + full rehearsal (runbook §14).
  Not run: frontend unit + E2E (no `web/` change), anything on the real VPS.
- *Next*: §5 item 1 (user approval to commit, then UAT deploy).
- *Notes*: bugs found and fixed during rehearsal: `shell_overrides` exit status under `set -e`,
  Compose `pids_limit` vs `deploy.resources.limits`, Caddy `redir / 303` parsed `/` as a matcher,
  preflight on Python 3.9, `db/backups` mode. OrbStack cross-network routing caveat in §3.00.

### 2026-11-03 — Claude Code (Opus 5.5) — done (Contabo preparation)
- *Did*: §3.0 — Contabo VPS 6 deployment package, migration rehearsal, auth validation,
  VPS-shaped streaming benchmark, player recovery fix.
- *Files*: see §3.0 (64 status entries total, uncommitted).
- *Verified*: §4 all green; evidence in `docs/readiness/2026-11-03-contabo/`.
- *Next*: user approval to deploy (runbook sections 1–10). Update: user approved commit + push; done
  as `efba466` + the hand-off commit, pushed to `origin/production-readiness`.
- *Notes*: kept DB `esabong_restore_rehearsal0016`, backup `db/backups/esabong-20261103T052852Z.dump`,
  images `esabong-contabo-review-api|caddy`, `esabong-bench-whepload`. All bench containers stopped.

### 2026-11-03 — Claude Code (Opus 5.5) — done
- *Did*: Fixed multi-portal auth collision (§3.2); implemented live streaming (§3.1);
  created this shared handoff (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/handoff.mdc`, this file).
- *Files*: see `git status` (56 entries, all uncommitted).
- *Verified*: all suites in §4 green; MediaMTX auth callback verified against a real
  MediaMTX 1.21.3 container; Caddy stream routes verified at runtime; secret scan clean.
- *Next*: §5 item 1 (user approval to commit).
- *Notes*: temporary test containers/processes were stopped; isolated DB
  `esabong_e2e_20261102` and benchmark DBs are kept (do not drop without asking).
