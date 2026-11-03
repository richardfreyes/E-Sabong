# Testing

## Current readiness evidence

The [final review](readiness/2026-11-02/REPORT.md) ran **194 backend integration + 108 unit,
190 frontend and 28 E2E tests**, plus the separately enabled **306-capture** visual job.
The normal E2E suite skips that explicit job. Run it with `READINESS_VISUAL=1` and
`UI_EVIDENCE_DIR=<evidence directory>` against an isolated E2E target. Historical results
elsewhere in these docs are not newly executed checks.

| Suite | Tool | Count | Command |
|---|---|---|---|
| Backend integration | pytest (real PostgreSQL + Redis) | 194 | `cd api && .venv/bin/pytest` |
| Backend unit (no services) | pytest | 108 | `cd api && .venv/bin/pytest unit_tests` |
| Frontend unit | Vitest via `ng test` | 190 | `cd web && npm run test:unit` |
| End-to-end | Playwright (Chromium) | 28 | `cd web && npm run e2e` |
| Load probe (read-only) | `api/scripts/loadtest.py` | — | see [production readiness](readiness/2026-11-02/REPORT.md) |
| Backup restore drill | `db/scripts/verify-backup.sh <dump>` | — | restores into a scratch DB and diffs it with live |
| Off-site backup drill | `OFFSITE_RESTORE_DRILL=1 db/scripts/offsite-backup.sh` | — | upload, download-back checksum, restore of the downloaded copy |
| Post-deploy smoke | `api/scripts/smoke.py` | 24 checks | edge, portals, sign-in, WebSocket; see docs/deployment.md |
| Production preflight | `infrastructure/scripts/preflight.py` | — | compose, secrets, file modes, host ports, OCI rules (read-only) |

## Backend

```bash
docker compose up -d postgres redis      # test DB "esabong_test" is created on first boot (DB_EXTRA_DATABASES)
cd api
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

The session migrates `esabong_test` from scratch (`downgrade base` → `upgrade head`),
first truncating the test database's own points ledger because migration 0010 refuses to
downgrade once refunds exist
and uses Redis DB 15. Tenants are addressed by `Host` header exactly as in
production. The destructive setup accepts only `esabong_test` or an `esabong_test_*` database name.
Override connection details with `TEST_DB_HOST`, `TEST_REDIS_URL`,
`TEST_DB_*_PASSWORD`.

| File | Covers |
|---|---|
| `test_auth.py` | login/lockout, per-IP spraying and registration throttles, uniform errors, refresh rotation + reuse detection, CSRF header, logout revocation, forced password change, token audiences, self-registration, suspended users |
| `test_isolation.py` | cross-tenant access by id (users, events, matches, points, parents), RLS with/without context, RLS write rejection, platform-table privileges, append-only tables, every tenant table has forced RLS, suspended tenants |
| `test_rbac.py` | player/operator/agent boundaries, hierarchy rules, downline scoping, custom roles, protected admin role, last-admin guard, plan user limit, module gating |
| `test_events.py` | event/match state machines, result declaration and audited correction, published-only portal, validation, reports and CSV |
| `test_simulation_runtime.py` | state graph/RBAC, precise pool settlement, draw/cancel/one-sided refunds, shared once-only wallet grants, concurrent wagers/closing across database sessions, idempotent settlement, partial-failure rollback, JWT arena rooms, countdown/odds, revocation and post-commit event privacy |
| `test_simulation_foundation.py` | neutral simulation schema, role aliases, tenant/assignment access, concurrent idempotency, atomic ledger debit guards, immutable receipts, safe migration rollback |
| `test_points_archive.py` | retired endpoints and permissions; no new legacy accounts; DB write refusal; migration preserving every account/transaction/entry, audit record and gameplay balance; archive search/filters/pagination/CSV; role, downline and tenant isolation; caller-only Arena Points history |
| `test_billing.py` | paid start, trial → past_due → expiry → suspension → payment reinstatement, prorated upgrade, scheduled downgrade, cancel at period end, billing roles |
| `test_licensing.py` | issue, activate, activation limit, heartbeat, revoke, perpetual rules, tampered/foreign-key token rejection, expiry |
| `test_betting.py` | live pools and odds through the API (incl. the reference values), personal vs all-player figures, MERON/WALA/DRAW settlement, one-sided book, cancellation/no-contest refunds, corrections that never go negative, close/reopen, declaration closes betting, limits and ALL IN, idempotent replay, **concurrent bets vs balance**, bets racing settlement, tenant/fight isolation, realtime frames per audience |
| `test_betting_staff.py` | admin-configured grants (none by default, once per player), admin overview and permissions, reconciliation identity, tenant scoping, Declarator monitor pools/obligations/settlement, `betting.closed` → `fight.result_declared` → `fight.settled` order, staff-only ledger frames |
| `unit_tests/test_betting_odds.py` | odds formula against the reference screenshots, rounding, zero pools, pro-rata allocation summing to the cent, settlement policy table |
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
player registration validation. Legacy archive specs cover preserved balances, absent mutation
controls, error/retry behavior, history filtering/search/pagination/export and read-only receipts.
Arena Points specs cover decimal formatting without currency/legacy units, shared gameplay balance,
null loading state, statement refresh and cancellation of stale responses across account changes.

## End-to-end

Runs against the API on `:8000` and the three dev servers (started automatically
if not running). Global setup seeds a fresh `e2e-<run>` tenant, so regression runs
do not exhaust the interactive demo tenant's event allowance or change its branding.
Set `E2E_TENANT_SLUG` to reuse a specific test tenant. With `E2E_SKIP_SEED=1`, supply
an already prepared `E2E_TENANT_SLUG` (defaults to `demo` if omitted). The owner
drill-down test still inspects the existing `demo.localhost` tenant. Seeded test
tenants remain available for investigation; the suite does not delete tenant data.

```bash
docker compose up -d postgres redis
cd api && .venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --port 8000 &
cd web && npx playwright install chromium && npm run e2e
```

Scenarios include owner sign-in and tenant drill-down, wrong password, player/admin boundaries,
live event/result workflows, neutral controller-to-browser WebSocket-to-settlement flow, retired-points write refusal, the read-only archive, one Arena Points
header balance matching gameplay and history, branding, resilience and video playback. Arena checks
cover nine widths from 320 to 1920px, red Meron / blue Wala / green Draw states, no horizontal
overflow, extreme decimal values (intercepted test reads only), preserved controls and actual video.
Cross-player bets and settlement use the real local API.

To isolate a browser run from a development API, configure `E2E_API_URL`, `E2E_OWNER_URL`,
`E2E_ADMIN_URL`, `E2E_PLAYER_URL`, and `E2E_PROXY_CONFIG` (a proxy JSON targeting that API).
All API/CLI database environment variables and Redis must refer to the isolated test environment;
global setup inherits them when seeding its fresh tenant. Defaults remain ports 8000 and 4200–4202.

Read-only visual inventories against the running `demo` portals:

```bash
cd web
node scripts/ui-audit.cjs after       # 34 routes × nine widths
node scripts/ui-flows.cjs after       # details, tabs, dialogs, validation, menus
```

Screenshots and measurements are saved in `docs/ui-ux/`. A third argument limits
either capture script to comma-separated portal names (for example `player`).

## Local development loop

Migration 0015 concurrency benchmarks have a separate [runbook](../scripts/benchmarks/README.md)
and [2026-11-02 execution report](benchmarks/2026-11-02/REPORT.md). The full run passed
settlement accounting/idempotency but failed the burst-error and 1,000-socket delivery gates.

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
