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

**Errors.** Every error uses one envelope:

```json
{ "error": { "code": "insufficient_points", "message": "Insufficient demonstration points",
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

**Idempotency.** `POST /admin/points/{allocations,transfers,adjustments}` require
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
| `GET /admin/points/transactions · summary · me`, `GET /admin/points/accounts/{user_id}[/entries]` | `points.view_*` |
| `GET /admin/reports/activity · points · matches?from=&to=&format=csv` | `reports.view` |
| `GET /admin/audit-logs` | `audit.view` |
| `GET/PUT /admin/settings`, `GET/POST/DELETE /admin/domains`, `POST /admin/domains/{id}/verify` | `settings.manage` |
| `GET/PUT /admin/branding`, `POST/DELETE /admin/branding/assets/{logo,favicon,login_background}` | `branding.manage` |
| `GET /admin/subscription · invoices` | `billing.view` |

Module-gated routes return `403 module_disabled` when the module is off for the tenant or not in its plan.

### End-user portal (any signed-in tenant user)
`GET /portal/dashboard`, `GET /portal/events[/{id}[/matches]]`, `GET /portal/matches/{id}`,
`GET /portal/results`, `GET /portal/points[/history]`, `GET /portal/activity`,
`GET /portal/notifications`, `POST /portal/notifications/{id}/read · read-all`.

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
| `points.received` *(user)* | `{transaction_id, amount, balance}` |
| `notification.created` *(user)* | `{id, type, title, body}` |
| `pong` | reply to `{"type": "ping"}` |

Close codes: `4401` invalid/revoked, `4403` workspace unavailable, `4408` token expired (refresh and reconnect).

## Declarator verification workflow

See [the Declarator contract](declarator/contract.md) for assigned-event access, idempotent submission, independent approval, amendments, private notifications, and record filters. The generated `openapi.json` includes these endpoints.
