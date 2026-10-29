# Declarator implementation verification

Status: code implemented; automated offline/frontend checks pass. PostgreSQL-backed execution and the authenticated dashboard's live desktop/mobile browser pass remain pending environment activation.

## Executed checks

| Check | Result | Evidence |
|---|---|---|
| Angular unit suites | **95 passed**: shared/Owner 37, Admin 21, Player 37 | [frontend-tests.log](frontend-tests.log) |
| Backend offline domain + HTTP contract checks | **33 passed** | [backend-offline-tests.log](backend-offline-tests.log) |
| Production builds | **Owner, Admin, Player passed**; no final build warnings/errors | [production-builds.log](production-builds.log) |
| Python static checks | Ruff passed for application, tests, offline tests and new revisions | Reproducible command below |
| Migration SQL generation | Complete base-to-head and incremental 0003-to-head SQL rendered without connecting to a database | [Incremental SQL](migration.sql) |
| Constraint compatibility | Initial schema creates `ck_tenant_users_accounttype`; revision 0004 drops exactly that name and expands it | Inspected complete generated SQL |
| Authorized video packaging | Player source and Admin production copy have identical SHA-256; unit test resolves the Admin clip under `/admin/media/demo-loop.mp4` | `18b99ec25f32f6bd2223aa54e4b5632533328bf5cc81c283eba7604c42649f75` |
| Browser authentication and route permissions | Demo administrator sign-in works; the new route is denied without the un-applied permission backfill; no captured warnings/errors | [Browser guard screenshot](browser-permission-guard.jpg) |

Offline backend checks cover initial/amended state validation, invalid requests/reasons, extreme cancellation reason length, stale revisions, self-approval denial, denied actors, revoked assignment, finalized replay behavior, new-role permission limits, the active-proposal/request-key constraints, holding/advancing the monitored fight, and rejecting attempts to disable verification through event updates.

Frontend checks cover required confirmation, cancelled confirmation, Meron/Wala payloads, captured fight identity while a dialog is open, uncertain retry keys, fresh keys after a known returned declaration, disabled actions, stale-response cancellation, repeated/out-of-order notifications, reconnection, permission/module navigation, authorized media path resolution, live-update feature gating, and reviewer notification visibility, safe detail reloads after uncertain requests, and default-route handling when required modules are disabled. Existing Player history/replay/reconciliation and other frontend tests continue passing.

## Live environment blocker

### Follow-up: dashboard route error

Browser inspection of the running `http://localhost:8000/api/openapi.json` confirmed that its Declarator path list was empty and its account-type enum contained only `admin`, `operator`, `master_agent`, `agent`, and `player`. The local source registers the Declarator router. The observed dashboard 404 therefore requires updating/restarting the running backend, alongside the schema activation described above.

The dashboard now distinguishes a missing workspace API from normal request errors. Its control panel explicitly checks reviewer permission instead of displaying reviewer controls for every account that is not a submitter. Accounts with neither capability receive an account-type/role explanation. Six regression cases cover missing API recovery, preserved permission errors, and all four submitter/reviewer combinations. The Admin suite passes **27 tests** after this fix (the earlier full-platform run below had 21 Admin tests).

The Admin production build also passed. Follow-up evidence: [27 passing tests](dashboard-fix-tests.log), [production build](dashboard-fix-build.log). This follow-up did not apply migrations or change account permissions. Live declaration and finalization remain unverified until the running API is activated.

The execution workspace denies TCP connections to PostgreSQL/Redis/local services and denies the Docker Unix socket. A real integration test command was attempted and stopped in the existing session migration hook before test collection. The underlying database reset/test assertions did not execute. No schema migration was applied from this workspace.

The connected browser can reach the local app, but its existing tenant administrator session still lacks `results.approve`. Signing in again and requesting `/admin/declarator` correctly redirects to the permission-denied page. This pass therefore **does not claim a successful live declaration, approval, database write, result broadcast, or rendered dashboard desktop/mobile inspection**. The guard screenshot is access-control evidence, not a completed dashboard screenshot.

Run the following from a terminal that can reach the existing PostgreSQL/Redis services:

```sh
cd /Users/richardfreyreyes/Documents/Repositories/E-Sabong/api
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m pytest tests -q
```

The integration suite uses the separate `esabong_test` database and Redis DB 15, as configured in the existing fixtures. Schema changes for the application and destructive resets of the test database are separate operations.

After applying revisions 0004/0005 and refreshing the authenticated profile, the browser pass must cover the live workspace at desktop/tablet/mobile sizes, an assigned Declarator account, independent reviewer confirmation, duplicate/reconnect behavior, history details, and Player/Admin synchronization. No application result or user/assignment records were created or modified by this browser session; routine sign-in is the only authentication action performed.

## Database-backed tests supplied, not yet executed

`api/tests/test_declarator.py` adds 13 parametrized cases for:

- All five proposed outcomes, approval metadata, canonical Player projection, and unchanged demo point balances.
- Duplicate request rejection, exact retry responses, concurrent approval idempotency, and one official revision.
- Authorized amendments with required reasons, retaining the original result until approval, stale revision rejection, and full audit history.
- Assignment scope, cross-tenant denial, unauthorized players, revoked assignments, and self-approval denial even with approval permission.
- Forced-RLS visibility/write protection and append-only declaration audit grants.
- Private pending notifications, public finalized WebSocket delivery, and Admin's official result projection.
- Persistent verification mode after every assignment is revoked, including protection against legacy result and event-cancellation bypasses.
- Refresh-safe current-fight selection and advancement when the next fight starts.

The existing full database integration suite should be run alongside these cases before deployment. This report does not substitute offline assertions for actual PostgreSQL RLS, transaction, Redis, or WebSocket evidence.

## Reproduce passing local checks

```sh
CI=1 NG_BUILD_SASS_EMBEDDED=0 npm --prefix web run test:unit
CI=1 NG_BUILD_SASS_EMBEDDED=0 npm --prefix web run build:all
cd api
.venv/bin/python -m pytest unit_tests -q
.venv/bin/ruff check app tests unit_tests
.venv/bin/alembic upgrade 0003:head --sql
```

## Scope and design

The supplied [reference](reference.png) guides the monitor/control/history hierarchy. Its illustrative fight numbers are not application data. Runtime screens consume authenticated FastAPI responses; no random or hardcoded match history is generated.

The existing Midnight Arena tokens, role guards, tenant context, branding, authorized demonstration clip, and official-result WebSocket contract are reused. Winning-history and media implementations moved to `web/shared/arena`; Player paths re-export them for compatibility. Backend additions are limited to declarations, event assignments, approval/audit records, and the compatibility/locking protections needed to prevent bypass through old result actions.

No real-money transactions, match-dependent point payouts, cash-out, broadcast-source configuration, or regulatory-authorization claims were introduced.
