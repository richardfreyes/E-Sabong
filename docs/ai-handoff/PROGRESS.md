# E-Sabong — shared AI progress log

**Single source of truth for every AI assistant (Claude Code, Cursor, Codex).**
If you are an AI agent starting work here: read this file and [`AGENTS.md`](../../AGENTS.md)
first, then verify the "Current state" section against `git status` before doing anything.
When you stop (finished, interrupted, or running low on context/tokens), update this file
using the protocol in `AGENTS.md`. The next agent may be a different tool.

Last updated: **2026-11-03 (approved UAT-only gateway local commit)** by **Codex (GPT-6)**

---

## 1. Current state (verify first)

| Item | Value |
|---|---|
| Branch | `production-readiness` |
| HEAD | Approved UAT-only gateway follow-up (this commit), directly after `b65e519889d318da165487dd6d576455987d14fd`; seven local commits after `cccee4d`. Nothing pushed. Resolve exact SHA with `git rev-parse HEAD`. |
| Working tree | **0 uncommitted files** after the approved follow-up commit (includes this handoff). |
| Latest DB migration | `0016` (head), unchanged; no dev or production migrations run this session. |
| Pending user decisions | Local follow-up commit approved and complete. Push/tag `uat-20261103-rc.1`, Contabo UAT preparation (including Docker access method), actual UAT migration range (`uat:0016`) and go-live testing each await separate approval. No production approval. |

Quick verification commands:

```bash
git status --short                 # expect no output
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

### 3.000 Final UAT validation and deployment preparation — done locally; follow-up committed, not pushed
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

### 3.00 Isolated UAT + Production on one Contabo VPS — committed locally in six groups, NOT pushed or deployed
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

Final validation details and limitations: [UAT report](../readiness/2026-11-03-uat/REPORT.md).
Fresh results: integration **228** (100.08 s), unit **141** (1.57 s), frontend **60/58/83**,
all production builds, general/streaming E2E **34 passed / 7 skipped**, separate gateway E2E
**6 passed**, smoke **25/25**. Ruff (170 files), ShellCheck, four Caddy configs, both Compose
ownership checks, gateway mode checks and secret-pattern scans passed. Local synthetic UAT
backup/restore verified. Earlier evidence in the table is historical where marked.
No public ACME/OBS/VPS application acceptance or Linux cross-environment probe yet.

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

1. **Await explicit GitHub push/tag approval** for the full follow-up HEAD SHA and
   `uat-20261103-rc.1`. Follow-up local commit is approved and complete; no tag created.
   Next approvals remain separate: Contabo UAT preparation, exact pending UAT migration range,
   and UAT go-live testing. See `docs/readiness/2026-11-03-uat/REPORT.md` for commands.
   Choose Docker access deliberately: recommendation is attended administrator/sudo access,
   retaining socket permissions and no permanent deploy Docker-group membership by default.
   Direct Docker calls in existing scripts need a reviewed execution method before deployment.
   Preserve `/opt/esabong`; production stays disabled. Mail/tester settings still pending.
   Do not start production for the network probe; future dual-stack resource check must use
   actual VPS RAM. No VPS changes or migration commands authorized by the local commit.
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
