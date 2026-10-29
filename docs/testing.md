# Testing

| Suite | Tool | Count | Command |
|---|---|---|---|
| Backend unit + integration | pytest (real PostgreSQL + Redis) | 89 | `cd api && .venv/bin/pytest` |
| Frontend unit | Vitest via `ng test` | 26 | `cd web && npm run test:unit` |
| End-to-end | Playwright (Chromium) | 6 | `cd web && npm run e2e` |

## Backend

```bash
docker compose up -d postgres redis      # test DB "esabong_test" is created on first boot (DB_EXTRA_DATABASES)
cd api
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

The session migrates `esabong_test` from scratch (`downgrade base` → `upgrade head`)
and uses Redis DB 15. Tenants are addressed by `Host` header exactly as in
production. Override connection details with `TEST_DB_HOST`, `TEST_REDIS_URL`,
`TEST_DB_*_PASSWORD`.

| File | Covers |
|---|---|
| `test_auth.py` | login/lockout, uniform errors, refresh rotation + reuse detection, CSRF header, logout revocation, forced password change, token audiences, self-registration, suspended users |
| `test_isolation.py` | cross-tenant access by id (users, events, matches, points, parents), RLS with/without context, RLS write rejection, platform-table privileges, append-only tables, every tenant table has forced RLS, suspended tenants |
| `test_rbac.py` | player/operator/agent boundaries, hierarchy rules, downline scoping, custom roles, protected admin role, last-admin guard, plan user limit, module gating |
| `test_events.py` | event/match state machines, result declaration and audited correction, published-only portal, validation, reports and CSV |
| `test_points.py` | allocation, downline transfers, upward/lateral refusal, insufficient balance, idempotent replay/conflict, adjustments, **50 concurrent transfers**, **concurrent duplicate keys**, scoped visibility |
| `test_billing.py` | paid start, trial → past_due → expiry → suspension → payment reinstatement, prorated upgrade, scheduled downgrade, cancel at period end, billing roles |
| `test_licensing.py` | issue, activate, activation limit, heartbeat, revoke, perpetual rules, tampered/foreign-key token rejection, expiry |
| `test_websocket.py` | tenant-scoped broadcast, per-user notifications, invalid/foreign tokens closed with 4401 |
| `test_provisioning.py` | signup approval → provisioning → welcome email, slug rules, custom domains + DNS verification + TLS gate, branding + safe image upload, feature flags (incl. `csv_exports` enforcement), modules, platform lists/audit |
| `test_dedicated_and_usage.py` | dedicated license state rules, unlicensed install refuses traffic, usage metering flush |
| `test_units.py`, `test_smoke.py` | hashing, password policy, tokens, request hashing, CSV injection guard, slug/domain validation, health, OpenAPI |

## Frontend unit tests

```bash
cd web && npm ci && npm run test:unit
```

Shared-library specs live next to the code in `web/shared/**` and run under the
`owner` test target (see `angular.json`); app specs live in each project.
Covered: pipes, API error mapping and params, theme token generation and dark
mode, `AuthStore` (login, single-flight refresh, session loss), route guards
(return URL, forced password change, permissions), data table (templates,
states, debounced search), admin navigation filtering by permission and module,
player registration validation.

## End-to-end

Runs against the API on `:8000` and the three dev servers (started automatically
if not running), using the `demo` tenant (created by `global-setup.ts`, idempotent).

```bash
docker compose up -d postgres redis
cd api && .venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --port 8000 &
cd web && npx playwright install chromium && npm run e2e
```

Scenarios: owner sign-in and tenant drill-down; wrong password; players refused
by the admin portal; **full event lifecycle where the player's open match page
receives the official result over the WebSocket without reloading**; points
allocation reflected in the player's balance and history; branding change
visible in the player portal (title and name) without a rebuild.

## Local development loop

```bash
docker compose up -d postgres redis
cp api/.env.example api/.env              # host-side API settings (ALLOW_TENANT_HEADER=true)
cd api && .venv/bin/alembic upgrade head && .venv/bin/python -m app.cli seed && .venv/bin/python -m app.cli demo-tenant
.venv/bin/uvicorn app.main:app --reload --port 8000
cd web && npm run start:owner   # :4200
cd web && npm run start:admin   # :4201/admin/
cd web && npm run start:player  # :4202
```

The dev servers proxy `/api` (including WebSockets) to `:8000`. Because every app
runs on `localhost`, the tenant is chosen with `?tenant=<slug>` (default `demo`),
sent as `X-Tenant-Slug` — honoured only when `ALLOW_TENANT_HEADER=true`.

`demo-tenant` creates a four-fight card, which is too sparse to judge the player
portal's result board and statistics. For that, add a full derby:

```bash
cd api && .venv/bin/python -m app.cli demo-derby --fights 80
```

It builds one live event with mostly decided fights (a few draws, cancellations
and one corrected result), one fight in progress and five still to come. The
card is reproducible for a given `--seed`.
