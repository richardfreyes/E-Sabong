# E-Sabong — shared AI progress log

**Single source of truth for every AI assistant (Claude Code, Cursor, Codex).**
If you are an AI agent starting work here: read this file and [`AGENTS.md`](../../AGENTS.md)
first, then verify the "Current state" section against `git status` before doing anything.
When you stop (finished, interrupted, or running low on context/tokens), update this file
using the protocol in `AGENTS.md`. The next agent may be a different tool.

Last updated: **2026-11-03 (Contabo preparation)** by **Claude Code (Opus 5.5)**

---

## 1. Current state (verify first)

| Item | Value |
|---|---|
| Branch | `production-readiness` |
| HEAD | the AI hand-off commit on top of `efba466` (portal sessions + live streaming + Contabo package); **pushed** to `origin/production-readiness` on 2026-11-03 together with the six earlier commits (`1553aec … ef97f90`) |
| Working tree | clean after the push (verify with `git status`). |
| Latest DB migration | `0016` (`db/migrations/versions/20261103_0016_video_streams.py`) — committed; **not applied to dev or production** (only isolated test/rehearsal DBs) |
| Pending user decision | approve deploying to the Contabo VPS and running migration 0016 there (runbook `docs/deployment-contabo.md`). **Do not deploy or migrate without explicit approval.** |

Quick verification commands:

```bash
git status --short | wc -l          # expect 0 before any new work
git log --oneline -3                # hand-off commit, efba466, ef97f90
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

### 3.0 Contabo Cloud VPS 6 deployment package — DONE, committed (`efba466`), NOT deployed
- Runbook: **`docs/deployment-contabo.md`** (VPS/SSH hardening, Docker, GitHub deploy-key workflow,
  `.env`, DNS, Caddy HTTPS, startup, approval-gated migration, MediaMTX/OBS, smoke tests,
  backup/rollback, monitoring, measured evidence §13).
- New: `.env.contabo.example` (git-tracked via `.gitignore` exception),
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

| Suite | Command | Result |
|---|---|---|
| Backend integration | `cd api && .venv/bin/python -m pytest tests -q` | **221 passed** |
| Backend unit (run separately) | `cd api && .venv/bin/python -m pytest unit_tests -q` | **133 passed** |
| Lint | `cd api && .venv/bin/ruff check app tests unit_tests && .venv/bin/ruff format --check app tests unit_tests` | clean |
| Frontend unit | `cd web && npx ng test owner|admin|player --watch=false` | 60 / 58 / 83 passed |
| Builds | `cd web && npx ng build owner|admin|player` | all succeed |
| E2E (isolated stack, `E2E_STREAMING=1`) | `cd web && npx playwright test` | **34 passed, 1 skipped** (one earlier run had a flaky viewport-overflow check in `points-management.spec.ts`; passed on rerun ×3) |
| Shell scripts | `shellcheck -S warning infrastructure/scripts/{deploy,rtmp-allowlist}.sh` | clean |

Notes: backend tests use DB `esabong_test` and Redis DBs 14/15 (set by `api/tests/conftest.py`).
Run `tests` and `unit_tests` in **separate** pytest processes (known env leak otherwise).
Prettier is **not** enforced in `web/` (most committed files don't conform) — don't mass-reformat.

---

## 5. Open items / next steps (prioritized)

1. **Committed and pushed (2026-11-03).** Next: await approval to deploy on Contabo following
   `docs/deployment-contabo.md` (migration only with `APPROVED_MIGRATIONS=0016`; tag a release first).
   Original item (done): (suggested split: auth fix; live
   streaming; handoff docs). Use the repo message style `[ENG-XXXX] #add|#fix|#change - …` and
   end with the attribution line the user's tool requires. Never push without approval.
2. Re-measure streaming latency/capacity with **real OBS** and viewers on separate machines /
   the target Oracle shape (current numbers are single-laptop).
3. Production streaming prerequisites: apply migration 0016 via release process; set
   `COMPOSE_PROFILES=streaming`, `MEDIAMTX_API_PASSWORD` (24+), `STREAM_PUBLIC_HOSTS`; open
   UDP+TCP 8189 and TCP 1935 (broadcaster IPs); Celery beat must run; size `MEDIAMTX_MEMORY`
   ≈ 1 GiB + 4 MiB × peak viewers; CDN or multiple MediaMTX edges for ~1,000 viewers.
4. Known pre-existing issues (not yet fixed):
   - `infrastructure/scripts/preflight.py` claims Python 3.8+ but needs 3.10+ (`str | None`).
   - Test env leak: `unit_tests/test_production_config.py::test_development_defaults_still_start_outside_production`
     fails if `tests/` and `unit_tests/` run in one process.
   - asyncpg `CancelledError` on simulation socket cleanup (gateway cancels tasks inside DB sessions).
   - Wager throughput ~17–33/s per match (hot-row serialization); access checks ~1,000 DB tx/s at 1,000 sockets.
   - No web client consumes simulation `ODDS_UPDATED` yet.
   - Benchmark archives (`benchmark-archive/`, git-ignored) and the backup exist only on this machine.
5. Simulated-tournament arenas have no player UI; streams currently attach to events only.

---

## 6. Session log (append newest at the top)

Format: `### YYYY-MM-DD — <agent/tool> — <status: done | partial | blocked>` then
*Did* / *Files* / *Verified* / *Next* / *Notes*. Keep each entry short; put details in docs.

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
