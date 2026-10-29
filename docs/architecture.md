# Architecture

## System overview

```
Browser ──HTTPS──► Caddy ──► /api/*, /api/ws ──► FastAPI (uvicorn workers) ──► PostgreSQL 17
                     │                                   │                    (RLS enforced)
                     ├─► /admin/*  admin SPA             ├──────────────────► Redis 7
                     ├─► /*        player SPA            │   cache · pub/sub · Celery broker
                     └─► owner host: owner SPA           │
                                                         Celery worker + beat
                                                         billing · usage · DNS checks · email · license heartbeat
```

One API serves all three portals. Each portal is a static Angular build; Caddy
serves them same-origin with the API, so there is no CORS and the refresh-token
cookie can be `SameSite=Strict`.

## Product invariants

These are enforced in code and tests, not just policy:

| Invariant | Where |
|---|---|
| Points cannot be staked on or settled by match outcomes | `point_*` tables have no FK to matches; no endpoint takes a match/side with an amount; `results/service.py` never touches the ledger |
| Points have no monetary value and cannot be redeemed | No currency/value column on the ledger; no redemption endpoints; disclaimer returned by every points endpoint and shown in both portals |
| Transfers only flow down the hierarchy | `demo_points.service.transfer` → `is_in_downline`; `test_transfers_never_flow_up_or_sideways` |
| Billing is for software only | `subscriptions/` invoices plans and licenses; nothing references points |

## Backend layout

```
api/app/
  main.py            app factory, middleware (request id/logging, security headers, usage metering, license enforcement)
  cli.py             seed, create-platform-admin, demo-tenant, demo-derby, generate-license-keys, activate-license
  core/              config, database engines, tenancy resolver, deps (auth/RBAC), security, errors, storage
  modules/<domain>/  models.py · schemas.py · service.py · router.py (per domain)
  workers/           Celery app and tasks
```

Routers stay thin; services hold business rules and take an explicit
`tenant_id`/actor; every write records an audit entry in the same transaction.
Side effects (WebSocket publish, email) are registered with `on_commit()` and
run only after a successful commit.

## Multi-tenancy

**Model:** shared database, shared schema, `tenant_id` on every tenant-owned row.

**Resolution** (`core/tenancy.py`), cached in Redis for 60 s:

1. `DEPLOYMENT_MODE=dedicated` → the configured tenant.
2. `Host` equals a *verified* custom domain → that tenant.
3. `Host` is `<slug>.<BASE_DOMAIN>` → tenant by slug.
4. Development only (`ALLOW_TENANT_HEADER=true`, refused in production): `X-Tenant-Slug` header or `?tenant=` on the WebSocket.

**Isolation, in four independent layers:**

1. **Token binding.** Tenant access tokens carry `tid` and `aud=tenant`; a token
   presented on another tenant's host is rejected. Platform tokens (`aud=platform`)
   are never accepted on tenant routes and vice versa.
2. **Explicit filters.** Services filter on `tenant_id` in every query
   (`shared/repository.get_scoped`), including when running on the platform engine.
3. **PostgreSQL RLS.** The tenant engine connects as `esabong_app` (no BYPASSRLS).
   A SQLAlchemy `after_begin` hook runs `set_config('app.tenant_id', …, true)` for
   *every* transaction, so even a mid-request commit cannot lose context. All 23
   tenant tables have `ENABLE` + `FORCE ROW LEVEL SECURITY` with
   `tenant_id = app_current_tenant()`; with no context the policy matches nothing.
4. **Composite foreign keys.** Cross-row references (parent account, match→event,
   ledger entry→account…) are `(tenant_id, id)` pairs, so the database refuses a
   reference to another tenant's row even if application code tried.

Platform-owner routes and workers use a second engine as `esabong_platform`
(BYPASSRLS). The app role has no grants at all on platform tables
(`platform_users`, `license_keys`, …) and only `SELECT, INSERT` on append-only
tables (audit, ledger, result history).

## Authentication

| | Tenant users | Platform staff |
|---|---|---|
| Endpoint prefix | `/api/v1/auth` | `/api/v1/platform/auth` |
| Identifier | username or email (per tenant) | email |
| Access token | JWT HS256, 15 min, in memory only | same, `aud=platform` |
| Refresh token | opaque, hashed in `auth_sessions`, `HttpOnly; Secure; SameSite=Strict` cookie scoped to the auth path | same, separate cookie |

- Passwords: Argon2id; policy = 10–128 chars mixing three character classes.
- Refresh rotation with **reuse detection**: presenting a rotated token revokes the
  whole session family and blocks its access tokens immediately (Redis deny-list).
- Lockout: 5 failures per (tenant, identifier) → 15 min, with `Retry-After`.
- Cookie endpoints additionally require `X-Requested-With: XMLHttpRequest` (CSRF).
- Accounts created with a temporary password can reach only the change-password
  endpoints until it is changed (enforced in `get_current_user`).

## Authorization (RBAC)

Permissions are a fixed catalogue (`core/permissions.py`); roles are per-tenant
rows. System roles (`tenant_admin`, `operator`, `master_agent`, `agent`,
`player`) are seeded at provisioning; tenants add custom roles. The tenant admin
role cannot be stripped and the last active admin cannot be suspended, deleted or
demoted.

Account **type** decides hierarchy position; **roles** decide abilities:

```
(top level)  admin · operator · master_agent · player
master_agent └─ agent · player
agent        └─ player
```

`*_downline` permissions scope agents to their subtree (recursive CTE); out-of-scope
ids return 404, never 403, to avoid confirming existence.

## Demonstration points ledger

Double-entry: each tenant has one `issuance` account (may go negative = total
issued); user accounts are `CHECK (balance >= 0)`. Every transaction posts two
ledger entries summing to zero.

Concurrency (READ COMMITTED):

1. Idempotency pre-check on `(tenant_id, idempotency_key)`; same request hash → replay (`200`, `Idempotent-Replay: true`), different → `409`.
2. `SELECT … FOR UPDATE` both accounts **ordered by id** (deadlock-free), with `populate_existing` so balances are re-read under the lock.
3. Insert the transaction in a savepoint; a concurrent duplicate blocks on the unique index, then replays.
4. `UPDATE … SET balance = balance + :delta RETURNING balance`; the CHECK is the final overdraft guard.

Verified by `test_concurrent_transfers_never_overdraw` (50 parallel requests,
exactly ⌊10000/300⌋ succeed, every transaction balances) and
`test_concurrent_duplicate_key_posts_once`.

## Events, matches, results

State machines in `events/models.py` and `matches/models.py`:

```
event:  draft ⇄ scheduled → live → completed      (any non-final → cancelled)
match:  scheduled → in_progress → completed        (scheduled|in_progress → cancelled)
```

Only one match per event can be in progress. Declaring a result completes the
match; corrections require a reason, bump `revision`, and append to
`match_result_history`.

## Realtime

`/api/ws?token=…` authenticates with the access token (bound to the host's tenant),
subscribes to Redis channels `rt:tenant:<id>` and `rt:tenant:<id>:user:<uid>`, and
forwards messages. Any API replica can publish; all replicas deliver. The socket
closes with `4408` at token expiry and `4401` on revocation; the SPA refreshes and
reconnects with backoff.

Events: `match.updated`, `match.result_declared`, `match.result_corrected`,
`event.updated`, `points.received`, `notification.created`.

## Background work (Celery)

| Task | Schedule | Purpose |
|---|---|---|
| `billing.run_cycle` | hourly (shared mode) | trials → past_due, renewals & invoices, overdue, expiry → suspension |
| `usage.flush` | every 10 min | Redis request counters + DB metrics → `tenant_usage` (idempotent absolute values) |
| `domains.recheck_pending` | hourly | DNS TXT verification of new custom domains |
| `licensing.heartbeat` | every 6 h (dedicated mode) | refresh signed token; mark revoked/expired |
| `email.send` | on demand | SMTP with retry/backoff |

## Frontend

Angular 22 (standalone components, signals, zoneless change detection, new
control flow, Reactive Forms). Shared libraries are path-mapped source folders:

| Alias | Purpose |
|---|---|
| `@shared/models` | DTOs mirroring API schemas |
| `@shared/services` | API clients, `AuthStore` (in-memory token, single-flight refresh), `BrandingService`, `ThemeService`, `RealtimeService`, `PagedList`, toasts, title strategy |
| `@shared/interceptors` | bearer + retry-after-refresh, error normalisation, dev tenant header |
| `@shared/guards` | auth (incl. forced password change), guest, permission, module |
| `@shared/components` | shell, data table, stat card, bar chart, match card, dialogs, login, profile, skeleton/empty/error states |
| `@shared/directives`, `@shared/pipes` | `*appHasPermission`, points/money/label/relative-time |

### White-label theming

At startup the tenant apps fetch `/api/v1/public/tenant` for the request host.
`ThemeService.applyBrand()` builds light and dark Material 3 schemes from the
tenant's primary/secondary colours (`@material/material-color-utilities`, tonal-spot
variant) and writes every `--mat-sys-*` token as `light-dark(light, dark)` on
`<html>`. Tailwind colour utilities (`bg-primary`, `text-on-surface-variant`, …)
are mapped to the same tokens, so one build serves every tenant and dark mode is
just `color-scheme: dark`. Title (`Page · Tenant name`) and favicon follow the tenant.

## Dedicated deployments and licensing

The same images run with `DEPLOYMENT_MODE=dedicated` and one tenant. The control
plane issues a license key (only a hash is stored); the install runs
`app.cli activate-license`, receiving an Ed25519-signed token bound to its
fingerprint. The install verifies the token **offline** with the public key; a
6-hourly heartbeat refreshes it and learns of revocation. Without a valid token,
`LicenseEnforcementMiddleware` answers API requests with `403 license_invalid`
(health and license-status endpoints stay available).

## Key decisions

| Decision | Alternatives | Why |
|---|---|---|
| Shared schema + RLS | schema-per-tenant, DB-per-tenant | Cheapest to operate at many tenants; RLS + composite FKs give DB-enforced isolation; dedicated installs cover customers needing physical separation |
| Two DB roles (app / platform) | single role with a bypass flag | A forgotten filter on the tenant path cannot leak, and platform tables are unreachable from it |
| Opaque rotating refresh tokens | long-lived JWT refresh | Revocable, reuse-detectable |
| Same-origin per tenant domain | central API domain + CORS | No CORS, strict cookies, custom domains work identically |
| Caddy on-demand TLS gated by API | wildcard DNS-01 cert | Works for arbitrary customer domains without DNS credentials |
| Signed offline licenses + heartbeat | online-only checks | Installs keep running through control-plane outages, still revocable |
