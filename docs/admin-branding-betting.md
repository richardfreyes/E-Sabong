# Admin branding and betting settings

Owner → Admin Management → select the Admin workspace → Branding & Betting Settings.
The existing tenant is the Admin network boundary: its Admin dashboard, Master Agents,
Agents, Sub Agents, and Players share its configuration. Co-administrators of one workspace
share that workspace's configuration. Shared-host requests continue to resolve the tenant
from the authenticated account; custom-domain isolation and the Owner's global stream are unchanged.

Logo uploads reuse the existing database branding record and validated image storage
(PNG, JPEG, WEBP, GIF, ICO; deployment upload limit, normally 2 MB). Removing the logo
restores the shared system asset. Image URLs are versioned. Changes publish tenant-only
`branding.updated` after commit; connected dashboards reload branding once per change or
reconnection. The public config response is private and never cached.

## API contract

- `GET /api/v1/platform/tenants/{id}/betting-odds`: returns `meron`, `wala`,
  `configured`, `minimum`, `maximum`. Read access follows existing platform reader roles.
- `PUT /api/v1/platform/tenants/{id}/betting-odds`: Owner (`super_admin`) only;
  requires both integer board prices, e.g. `{"meron":180,"wala":190}`. Returns the saved configuration.
- `POST /api/v1/platform/tenants/{id}/branding-betting/reset`: Owner only;
  atomically removes the logo and saves 183–183. Other brand fields remain as configured.

A board price of 185 means a gross multiplier of 1.85. Each side is independently
validated; the Declarator's 3.66 pair limit does not apply to Owner prices.
`OWNER_ODDS_MIN` (default 100, at most 183) and `OWNER_ODDS_MAX` (default 300,
183–1000) configure the allowed range. Reset remains valid for all supported bounds.
Audit entries record before/after odds and logo changes. Odds notifications use the
existing tenant-only `betting.config_updated` event after commit.

Saving Owner odds activates fixed pricing for that workspace. Until the first save,
existing workspaces retain their Declarator/pool pricing. The form explicitly identifies
that mode. Once activated, Declarator odds edits return `owner_controlled`; the Owner
configuration takes precedence even on a fight already open.

## Acceptance and historical payouts

Every newly accepted bet stores its rounded, displayed price in `bets.multiplier` and
sets `acceptance_priced`. A shared lock on the settings row serializes acceptance against
an Owner update: a bet uses either the configuration before the commit or after it.
Retries retain the original price and never create a second bet. Closed books retain the
board price captured on close; later settings cannot rewrite that snapshot.

Settlement and personal return estimates sum each accepted price, using the existing
rounding, pot cap, wallet ledger, refunds, and commission pipeline. The frontend uses
server-computed accepted returns, while its input ticket estimates use the current board.
Pool events refresh personal capped estimates in batches, with no background API polling.

Migration **0037** adds the settings and pricing-policy columns. It marks still-accepted
bets with an existing recorded multiplier as acceptance-priced, without changing that
multiplier. Completed historical bets retain the prior closing-price policy, including
result corrections. Very old bets without recorded multipliers retain the legacy fallback.
No completed settlement, wallet entry, commission snapshot, or logo is rewritten.

Apply the migration before starting the updated API (`cd api && .venv/bin/alembic upgrade head`).
Use normal release backups and migrations. Downgrading after accepting new price-locked
bets loses their pricing policy and should not be used as a live rollback.

## Verification

Pure payout tests cover mixed historical/acceptance policy lines, changing prices, the
pot cap, and cancellation/draw refunds. `tests/test_admin_odds.py` exercises persisted
independent settings, permission checks, configured bounds, new bet pricing, settlement
stability, and reset. Existing owner branding tests exercise image isolation and validation.
These integration tests require local PostgreSQL and Redis; responsive end-to-end checks
require the running API and portals.

Local validation for this change: all three production builds and 359 frontend tests passed;
26 targeted payout tests passed. The complete backend unit run produced 277 passes and
15 failures. All 15 failures reproduced on the unchanged HEAD revision (14 assertions in
`test_betting_odds 2.py` and one existing invitation-route guard). Excluding those known
failures and the duplicate odds file yielded 270 passes. Ruff, diff checks, and offline
0036→0037 migration SQL generation passed. PostgreSQL was unavailable on localhost:5432,
and the Docker daemon was not running, so integration tests and live desktop/mobile
verification were not completed. No live database migration or deployment was performed.
