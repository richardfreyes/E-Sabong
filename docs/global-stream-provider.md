# Original Admin global livestream

Migration **0038** permanently designates the earliest surviving workspace as
`tenants.is_global_stream_provider`. Before release, confirm that the oldest
workspace is the original streaming Admin. There is one provider, enforced by a
partial unique index. An immutable database trigger prevents transferring the
flag, deleting the provider, or archiving it. New installations designate the
first provisioned tenant under a transaction-level lock; later tenants receive
playback automatically. This designation is absent from writable SaaS schemas.

The original Admin retains **Live streaming**, **Stream Operators**, and
**Operator console**, with their existing role and assignment checks. Its
**default** MediaMTX stream is the global source. Its first stream automatically
becomes the default. Select another default to switch the broadcast, or disable
the current default to take playback offline. Nondefault streams remain available
to provider staff for preview and preparation. Existing provider OBS keys and
sources are preserved; when the old Owner-selected stream belongs to the original
workspace, migration 0038 preserves that selection as its default.

All authenticated tenant streaming-management routes, including read endpoints,
require the provider designation. Generic account creation and management also
protect Stream Operator accounts. Consumer Admins have no streaming menus and
cannot gain access by editing client-side permissions. The Owner rental page is
SaaS-only; its stream routes and stream-selection inputs were removed. Existing
arena projection services remain unchanged for compatibility with established
fight workflows; this change does not copy events or financial data for video.

`POST /api/v1/portal/video/global/playback` makes video available even before a new
tenant publishes an event; it retains the same eligibility checks. Playback resolves the global default through the platform database, then signs
access using the **viewer's own** tenant, user, and session. Event visibility,
module entitlement, active-account checks, the 100-point available-balance rule,
capacity, and session revocation remain enforced. MediaMTX admits publishing only
for the provider's streams. Consumer paths are removed by reconciliation, and old per-tenant provider cookies are rejected. Players
cannot use stale tokens to watch a previous default. This works independently of
shared-domain mode, on verified custom domains, and for future tenants without
per-tenant video rows or encoders.

Authenticated WebSockets subscribe consumer tenants to `rt:global:stream`. Only
`video.source_changed` and the selected stream's `video.stream_status` are
promoted, with an explicit payload allowlist. Wallet, betting, commission, operator,
and recording frames retain their existing private channels. Changes use the
existing commit hooks and worker reconciliation, without new playback polling.
OBS and pulled sources run independently of the Admin browser. Browser capture
still requires its broadcasting tab/device to remain connected.

Global source, default-selection, key, enable/disable, operator, and recording
changes retain the existing audit records. Provider designation is included in
new-tenant provisioning audit records. No wallet, book, odds, payout, commission,
or financial-reporting implementation changes are required.

Deploy the API, web build, and migration together using the normal backup/release
workflow (`cd api && .venv/bin/alembic upgrade head`). No production migration or
deployment is performed by the local implementation. A downgrade does not restore
Owner streaming controls until the prior application version is deployed; it
also does not recover any old default selection changed after migration.

## Verification

`tests/test_global_stream_provider.py` exercises provider and Owner authorization,
existing/new consumers, shared/custom domains, viewer-specific signed claims,
low-balance denial, global notification privacy, offline propagation, and database
immutability. Existing stream/operator/balance tests reuse the original provider
with test-only schema-owner cleanup of media/account state; they never rewrite the protected designation.
The MediaMTX HTTP transport in those suites is a test fake, so they establish API,
database, authorization and reconciliation behavior rather than actual encoded
media delivery. Real encoder-to-player delivery remains a deployment check.

Local validation: all three production builds, the frontend unit suite, 64 focused
video/payout unit checks, 43 streaming/operator/recording/balance integration checks,
and 12 shared-domain checks were exercised. Shared-domain tests run separately
because video tests intentionally leave live fight books. Four older payout-policy
assertions (three in betting/commissions and one in the shared-arena suite) were
reproduced on unchanged HEAD code in a temporary checkout; financial behavior was
not modified to satisfy those stale expectations.
