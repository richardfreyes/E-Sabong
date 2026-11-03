# API

- Interactive docs: `/api/docs` (Swagger UI), `/api/redoc`
- Machine-readable contract: [`openapi.json`](openapi.json) (regenerate: `cd api && .venv/bin/python -c "import json,app.main as m; json.dump(m.app.openapi(), open('../docs/openapi.json','w'), indent=2)"`)
- Base path: `/api/v1` (health checks at `/api/health`, `/api/ready`; WebSocket at `/api/ws`)

## Conventions

**Tenant selection.** Tenant endpoints act on the tenant that owns the request
`Host` (`<slug>.<base-domain>` or a verified custom domain). There is no tenant
id in URLs or bodies.

**Authentication.** `Authorization: Bearer <access_token>` from a login endpoint.
Refresh uses the HttpOnly cookie and requires `X-Requested-With: XMLHttpRequest`.
Browser portals also send `X-Portal: admin` or `X-Portal: player` on login, register, refresh
and logout; each portal then keeps its own refresh cookie, so the admin and player apps on one
origin stay signed in independently. With `X-Portal: admin`, accounts without administration
permissions are refused (`403 portal_not_permitted`) before any session is created.

**Errors.** Every error uses one envelope:

```json
{ "error": { "code": "insufficient_points", "message": "Insufficient points",
             "details": { "balance": 120, "requested": 300 }, "request_id": "4f1c…" } }
```

| Status | Typical codes |
|---|---|
| 401 | `not_authenticated`, `invalid_credentials`, `token_expired`, `invalid_token`, `session_revoked`, `refresh_token_reused`, `csrf_check_failed` |
| 402 | `plan_limit_reached` |
| 403 | `permission_denied`, `tenant_suspended`, `module_disabled`, `password_change_required`, `transfer_not_allowed`, `license_invalid` |
| 404 | `not_found`, `tenant_not_found` (also returned for other tenants' or out-of-scope ids) |
| 409 | `conflict`, `username_taken`, `idempotency_conflict`, `result_exists`, `last_admin`, `slug_taken` |
| 422 | `validation_error` (`details.errors[]`), `invalid_transition`, `insufficient_points`, `invalid_parent`, `weak_password` |
| 429 | `login_locked`, `too_many_requests` (with `Retry-After`) |

**Pagination.** `?page=1&page_size=25&q=&sort=-created_at` →
`{ "items": [...], "total": 0, "page": 1, "page_size": 25 }` (`page_size` ≤ 100).

**Idempotency.** `POST /admin/points/{allocations,transfers,adjustments}` and `POST /admin/points/transactions/{id}/refund` require
`Idempotency-Key` (8–128 chars of `A-Z a-z 0-9 _ - : .`). Repeating a request with
the same key and body returns the original transaction with `200` and
`Idempotent-Replay: true`; the same key with a different body returns `409`.

**Request ids.** Every response carries `X-Request-ID` (echoed if supplied); it
also appears in logs, audit entries and error envelopes.

## Endpoint catalogue

### Public
| | |
|---|---|
| `GET /public/tenant` | Branding, modules, flags, registration flag for the host's tenant (answers for suspended tenants) |
| `GET /public/plans` | Active SaaS plans |
| `GET /public/slug-availability?slug=` | Slug validity/availability |
| `POST /public/signup-requests` | Request a subscription (5/hour/IP) |
| `GET /license/status` | License state of this installation |
| `POST /licensing/activate`, `POST /licensing/heartbeat` | Dedicated installs (license key auth, 30/min/IP) |

### Platform owner (`aud=platform`; roles: support < billing < admin < super_admin)
| | |
|---|---|
| `POST /platform/auth/login · refresh · logout`, `GET /platform/auth/me`, `POST /platform/auth/me/password` | Staff auth |
| `GET/POST /platform/tenants`, `GET/PATCH/DELETE /platform/tenants/{id}` | List, provision, rename, archive |
| `POST /platform/tenants/{id}/suspend · activate` | Status |
| `GET/POST /platform/tenants/{id}/domains`, `POST /platform/domains/{id}/verify · primary`, `DELETE /platform/domains/{id}` | Domains |
| `GET/PUT /platform/tenants/{id}/branding` | Branding |
| `GET/PUT /platform/tenants/{id}/feature-flags`, `GET/POST/PATCH /platform/feature-flags` | Flags |
| `GET /platform/tenants/{id}/usage?days=` | Daily usage metrics |
| `GET /platform/signup-requests`, `POST …/{id}/approve · reject` | Signups (approve provisions) |
| `GET/POST/PATCH /platform/plans` | Plans |
| `GET /platform/subscriptions`, `POST /platform/tenants/{id}/subscription`, `POST /platform/subscriptions/{id}/change-plan · cancel · resume`, `POST /platform/billing/run` | Subscriptions |
| `GET /platform/invoices`, `GET …/{id}`, `POST …/{id}/mark-paid · void` | Invoices |
| `GET/POST /platform/licenses`, `GET/PATCH …/{id}`, `GET …/{id}/activations`, `POST …/{id}/revoke` | Licenses |
| `GET/POST/PATCH /platform/deployments` | Dedicated deployments |
| `GET /platform/analytics/overview` | KPIs (tenants, MRR, trials, invoices, usage) |
| `GET /platform/audit-logs` | Platform-wide audit (filters: tenant, actor, action, resource, dates) |

### Tenant auth (`aud=tenant`)
`POST /auth/login · register · refresh · logout`, `GET/PATCH /auth/me`, `POST /auth/me/password`

**Live streaming** (`settings.manage`; see [live streaming](live-streaming/README.md)): `GET/POST /admin/streams`, `PATCH/DELETE /admin/streams/{id}`, `POST /admin/streams/{id}/rotate-key · reconnect · preview`, `GET /admin/streams/health`. Players and declarators receive stream playback from the existing `POST /video/events/{event_id}/playback` (type `webrtc` with an HLS `fallback_url`).

### Tenant administration
| | Permission |
|---|---|
| `GET /admin/dashboard` | `dashboard.view` |
| `GET/POST /admin/users`, `GET/PATCH/DELETE /admin/users/{id}`, `POST …/{id}/suspend · activate · reset-password`, `GET …/{id}/downline` | `users.*` (downline-scoped for agents) |
| `GET /admin/hierarchy?parent_id=` | `users.view_*` |
| `GET /admin/permissions`, `GET/POST/PATCH/DELETE /admin/roles` | `roles.manage` |
| `GET/POST /admin/events`, `GET/PATCH/DELETE /admin/events/{id}`, `POST …/{id}/publish · unpublish · start · complete · cancel` | `events.*` |
| `GET/POST /admin/events/{id}/matches`, `GET/PATCH/DELETE /admin/matches/{id}`, `POST …/{id}/start · cancel` | `matches.manage` |
| `POST /admin/matches/{id}/result` · `PUT` (correction, reason required) · `GET …/result/history` | `results.declare` / `results.correct` |
| `POST /admin/points/allocations · transfers · adjustments` | `points.allocate` / `transfer` / `adjust` |
| `POST /admin/points/transactions/{id}/refund` | Admin + `points.adjust` + `points.transfer` |
| `GET /admin/points/transactions[/{id}] · transactions/export · accounts · summary · me`, `GET /admin/points/accounts/{user_id}[/entries]` | `points.view_*` |
| `GET /admin/points/integrity` | `points.view_all` |
| `GET /admin/reports/activity · points · matches?from=&to=&format=csv` | `reports.view` |
| `GET /admin/audit-logs` | `audit.view` |
| `GET/PUT /admin/settings`, `GET/POST/DELETE /admin/domains`, `POST /admin/domains/{id}/verify` | `settings.manage` |
| `GET/PUT /admin/branding`, `POST/DELETE /admin/branding/assets/{logo,favicon,login_background}` | `branding.manage` |
| `GET /admin/subscription · invoices` | `billing.view` |

### Legacy points archive

`/admin/points` is read-only, gated by the existing `points` module and either
`points.view_all` (tenant) or `points.view_downline` (authorized subtree). Existing
role assignments are preserved. The retired `points.allocate`, `points.transfer`
and `points.adjust` permissions confer no write capability and are no longer offered.

- `GET /admin/points/accounts[?q=]`, `GET /admin/points/accounts/{user_id}` and
  `/entries` read preserved historical accounts and statements. New users have no
  legacy account; a missing individual account returns 404 rather than creating one.
- `GET /admin/points/me` reads the caller's historical account, or returns 404.
- `GET /admin/points/summary` reads archive totals; `own_balance` is null when absent.
- `GET /admin/points/transactions` retains pagination, `q`, `kind`, `type`, `status`,
  `sender_id`, `recipient_id`, `user_id`, and inclusive Asia/Manila `date_from/date_to`
  filters. Detail: `GET /admin/points/transactions/{id}`. CSV: `GET …/transactions/export`
  (same authorization and filters, 10,000 rows maximum, `csv_exports` feature required).
- `GET /admin/points/integrity` requires `points.view_all` and reconciles preserved
  balances and entries. Foreign-tenant and out-of-scope IDs return 404.

Allocation, transfer, adjustment and refund POST routes have been removed (404/405).
Migration 0013 also rejects INSERT, UPDATE and DELETE on all three `point_*` tables.
No records or balances are merged into gameplay. Transaction references, original
amounts, before/after balances, reversals, initiators and audit records remain intact.

Module-gated routes return `403 module_disabled` when the module is off for the tenant or not in its plan.

### End-user portal (any signed-in tenant user)
`GET /portal/dashboard`, `GET /portal/events[/{id}[/matches]]`, `GET /portal/matches/{id}`,
`GET /portal/results`, `GET /portal/activity`,
`GET /portal/notifications`, `POST /portal/notifications/{id}/read · read-all`.

**Arena Points:** `GET /portal/betting` returns the caller's gameplay balance and
configuration; `GET /portal/betting/entries` returns only their own ledger, newest
first, with pagination and optional `kind`. Another user's `user_id` cannot change
that scope. Grants, stakes, wins, refunds and corrections all belong to this one
balance. The old `/portal/points` and `/portal/points/history` endpoints return 404;
`/portal/dashboard` no longer contains `balance` or `points_disclaimer`.

**Predictions** (non-monetary; player accounts only). `PUT /portal/matches/{id}/prediction` with
`{"side": "meron" | "wala" | "draw"}` creates or changes the caller's single prediction for that fight
(repeating the same side is a no-op). It returns `409 prediction_closed` once the window is shut: the fight
is no longer scheduled or in progress, the event is no longer scheduled or live, or a result has been
proposed for verification. `GET /portal/predictions` lists only the caller's own predictions, newest first,
with `q` (fight `#n`, corner or event name), `status` (`pending`/`win`/`lose`/`void`), `side`, `event_id`,
`match_id` and pagination. Outcomes are recomputed in the same transaction as every official result,
correction, fight cancellation or event cancellation (no contest and cancelled fights are `void`). They never
move demonstration points. `prediction.updated` is pushed to the player's own sessions over the WebSocket.
Only published events (scheduled, live, completed) are visible.

**Simulated betting** (play balance; player accounts place bets). Amounts are decimal strings with at most two decimals.

| Method & path | Notes |
|---|---|
| `GET /portal/betting` | `{enabled, draw_multiplier, commission_rate, min_bet, max_bet, max_fight_stake, wallet}`; the wallet is created with the starting grant on first use |
| `GET /portal/matches/{id}/book` | `{book, mine, wallet, limits}`: all-player `pools` and `odds` (unrounded + `*_display`), plus the caller's own `stake`/`pays`/`result` per side |
| `POST /portal/matches/{id}/bets` | `{side, amount, request_id}` → `201 {…book view, bet, replayed}`. Resending a `request_id` replays the bet; reusing it for another bet is `409 idempotency_conflict`. Errors: `409 betting_closed`, `409 betting_disabled`, `403 players_only`, `422 bet_below_minimum · bet_above_maximum · fight_limit_reached · insufficient_balance` |
| `GET /portal/bets` | the caller's own bets (`match_id`, `event_id`, `status`, pagination) |
| `GET · PUT /admin/betting/settings` | `settings.manage` to change; changes never alter accepted bets or opened books |
| `GET /admin/events/{id}/books`, `GET /admin/matches/{id}/book` | pools, odds, deduction and timestamps per fight |
| `POST /admin/matches/{id}/betting/open · close` | `matches.manage`; open needs a live event and an undecided fight; close locks the final multipliers |
| `GET /admin/betting/books` | Fights with betting across events (`state`, `event_id`, `live=true`, pagination): pools, odds, bet count, per-result payout `obligations` while unsettled, `settlement` (paid to winners, refunds, deduction) after |
| `GET /admin/betting/wallets · entries · reconciliation` | `points.view_all`: play wallets with granted/staked/returned/at-stake totals; the append-only ledger (`kind`, `user_id`, `match_id`); and where every play point is, with integrity checks |

The Declarator monitor (`GET /declarator/events/{id}/monitor`) also returns `books`: the same fight summaries for the event's card. The starting play balance is `0` until an administrator sets it; each player is granted it once, recorded as a `grant` ledger entry.

## Neutral simulated tournament engine

`/api/v1/simulations` provides tenant-scoped neutral arcade arenas, assigned controllers,
versioned match transitions, atomic wagers and immutable settlement receipts. Players use
`GET /simulations/wallet` to read the same existing Arena Points wallet, independent of
the older gameplay switch. There are no point-transfer, loading or redemption operations.

The lifecycle and uppercase side aliases, idempotency rules, decimal request examples,
room authentication and post-commit event protocol are documented in the
[engine contract and rollout guide](simulated-tournaments/README.md). Only enabled
arenas accept new wagers; only `wagering_open` accepts new stakes. The gateway is native
WebSocket at `/api/ws/simulations`, with rooms `tenant:{tenant_id}:arena:{arena_id}`.

## WebSocket

```
wss://<tenant-host>/api/ws?token=<access token>
```

Server → client frames: `{"type": "<event>", "data": {...}}`

| type | data |
|---|---|
| `connected` | `{tenant}` |
| `match.updated`, `match.result_declared`, `match.result_corrected` | `{id, event_id, match_number, status, meron_name, wala_name, result: {outcome, revision} | null}` |
| `event.updated` | `{id, status, name?}` |
| `notification.created` *(user)* | `{id, type, title, body}` |
| `book.updated` | `{match_id, event_id, state, accepting, pools, odds, final, draw_multiplier, commission_rate, bet_count, outcome, version}`: aggregate totals only |
| `betting.closed` | `{match_id, event_id, pools, final_odds, version}` when betting closes (operator, declaration, result or cancellation) |
| `fight.result_declared` | the `match.*` payload when a result is recorded or corrected (`corrected: true`), or `outcome: "cancelled"` for a cancelled fight; sent after `betting.closed` |
| `fight.settled` | `{match_id, event_id, outcome, revision, resettled, stakes, payouts, refunds, commission, version}`: fight totals after settlement |
| `betting.ledger_updated` *(staff)* | `{kind}` to accounts with `points.view_all` after grants and settlements |
| `betting.config_updated` | `{enabled, draw_multiplier, commission_rate}` after an admin changes betting settings: refetch `/portal/betting` |
| `wallet.updated` *(user)* | `{balance, version, reason}` |
| `bets.updated`, `bets.settled` *(user)* | `{match_id[, outcome]}`: refetch the book view |
| `pong` | reply to `{"type": "ping"}` |

Close codes: `4401` invalid/revoked, `4403` workspace unavailable, `4408` token expired (refresh and reconnect).

## Declarator verification workflow

See [the Declarator contract](declarator/contract.md) for assigned-event access, idempotent submission, independent approval, amendments, private notifications, and record filters. The generated `openapi.json` includes these endpoints.
