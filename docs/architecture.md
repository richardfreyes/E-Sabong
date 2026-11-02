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
| Demonstration points cannot be staked on or settled by match outcomes | `point_*` tables have no FK to matches or bets; betting never reads or writes the points ledger |
| Play balances cannot be bought, transferred, adjusted or redeemed | `play_wallets` are funded only by a one-time system grant (`grant:<user>`) and their own bet settlements; no endpoint moves value between users or lets staff change a balance; `tests/test_betting.py` |
| Betting is backend-authoritative and exactly-once | pools, multipliers and payouts are computed in `betting/odds.py` with `Decimal`; unique `request_id` per bet and unique posting keys per wallet entry; settlement moves only the difference between owed and credited (see [simulated betting](#simulated-betting)) |
| Points have no monetary value and cannot be redeemed | No currency/value column on the ledger; no redemption endpoints; disclaimer returned by every points endpoint and shown in both portals |
| Only Admins issue credits; transfers never mint | Role and permission checks in `demo_points.service`; existing double-entry ledger; `test_admin_credits.py` |
| Admin transfers stay within the tenant; non-Admin transfers respect direct assignments | Admin to any active account; Master Agent to assigned Agent; Agent to assigned Player; no Player outbound transfers |
| Billing is for software only | `subscriptions/` invoices plans and licenses; nothing references points |

## Simulated betting

Off by default; a tenant admin enables it under *Settings → Betting*. Module: `api/app/modules/betting/`.

- **Book per fight** (`fight_books`): an operator opens betting on a fight of a live event and closes it
  ("last call"). Submitting a Declarator proposal or recording a result also closes it. Running pool
  totals and a monotonically increasing `version` live on the book row.
- **MERON/WALA are pari-mutuel**: `odds = (meron + wala) × (1 − deduction) ÷ side`, unrounded internally,
  displayed rounded half up to 2 dp, unavailable while either side is empty. They are provisional while
  the book is open and locked into `final_*_odds` when it closes. The deduction rate is snapshotted when
  the book opens. **DRAW is fixed odds**: the multiplier is locked on each bet and the DRAW pool never
  enters the MERON/WALA odds.
- **Settlement** runs inside every transaction that changes an official outcome (declaration, correction,
  fight/event cancellation, fight deletion), next to `predictions.resolve`. MERON or WALA wins: winners
  share the distributable pool pro rata (floor to the cent, leftover cents by largest remainder, ties by
  bet order). Either side empty: MERON/WALA stakes are refunded and no deduction is taken. DRAW: DRAW bets
  pay `stake × locked multiplier`, MERON/WALA stakes are refunded. Cancelled or no contest: everything is
  refunded. A correction re-settles by the difference; a reclaim never takes a wallet below zero and the
  uncollected amount is recorded in the audit entry.
- **Concurrency**: bets take shared locks on the event and fight (like predictions), then lock the book row
  and the player's wallet; closing and settlement take the event and fight `FOR UPDATE`, so a bet is either
  in the frozen pool or rejected with `409 betting_closed`.
- **Realtime**: `book.updated` (tenant-wide, aggregate totals only, carries `version`) after every bet and
  state change; `wallet.updated`, `bets.updated` and `bets.settled` to the player's own sessions. Clients
  drop frames whose version is not newer and refetch on reconnect (`connected`).

### Staff views

- **Starting balance** is `0` until an administrator sets it (*Settings → Betting*); each player is granted it once
  (`grant:<user>` ledger key), including wallets created before it was set. The settings change is audited.
- **Admin → Betting** (`/admin/betting`): live and settled fights across events with payout obligations per result,
  play wallets, the ledger, and a reconciliation report (`granted = in wallets + at stake + house result`, plus
  checks that each wallet equals its entries and each pool equals its bets).
- **Declarator desk**: the monitor snapshot includes the card's fight summaries, so the desk shows live pools,
  odds, obligations and a settlement history. Results still go through the two-person declaration workflow.
- **Points wallet** (Master Agent, Agent): read-only; the page lists directly assigned accounts and balances from
  `GET /admin/hierarchy`; postings send `points.updated` to the parties, every ancestor and authorized staff.

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

## Points ledger

Double-entry: each tenant has one `issuance` account (may go negative = total
issued); user accounts are `CHECK (balance >= 0)`. Every transaction posts two
ledger entries summing to zero.

Each Admin's wallet is their existing user point account. Create Points posts an
allocation into it (issue) or directly into another account (credit). Transfers
debit the selected sender (the Admin's own wallet, or any account with
`points.view_all`) and credit any active account in the same tenant without
creating supply. Every operation requires both the Admin account type and its
permission; custom permissions alone cannot let any other role mint or move
points. Refunds append a full reversal of a transfer. Amounts are strict
whole-point integers. `app/modules/demo_points/service.py` is the only code that
changes a balance, and database triggers (migration 0011) make the ledger
immutable and reject any balance change without a matching ledger entry. See
[points management](admin-credits/README.md).

Concurrency (READ COMMITTED):

1. Idempotency pre-check on `(tenant_id, idempotency_key)`; same request hash → replay (`200`, `Idempotent-Replay: true`), different → `409`.
2. `SELECT … FOR UPDATE` both accounts **ordered by id** (deadlock-free), with `populate_existing` so balances are re-read under the lock.
3. Recheck the idempotency key under the locks, before rejecting an insufficient
   balance. A full-balance retry must replay even if the first transfer emptied
   the treasury. Insert in a savepoint; conflicting inserts also replay safely.
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
`event.updated`, `points.received`, `points.updated`, `notification.created`.
Points updates go to the affected users, their ancestors and authorized
staff on personal channels. They invalidate reads; dashboards refetch their
authorized PostgreSQL balances rather than applying optimistic wallet changes.

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
