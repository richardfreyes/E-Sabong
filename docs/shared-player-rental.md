# Shared player domain and tenant rental

The OWNER console's **Tenant Rental** page extends the existing tenant,
subscription, invoice, and event services. No new wallet or commission engine is introduced.

## Identity and registration

Enable shared-domain access under `/owner/rental`. The configured `BASE_DOMAIN` is the shared
tenant application host in this mode: `swcmain183.live` in production and the existing UAT
hostname in UAT. Admin management continues at `/admin/`; players and agents use the same
application domain. The OWNER console and platform authentication remain separate. Optional verified custom
domains bind requests to their mapped tenant; see [Custom domains](custom-domains.md).

Authenticated HTTP and WebSocket requests derive tenant ownership from a verified access
token. Refresh and logout derive it from the hashed HttpOnly refresh cookie, including when
the access token has expired. A request cannot select a tenant with a body field or header.
The existing application database role and PostgreSQL tenant RLS still serve tenant APIs.

Usernames remain unchanged. Unique usernames and emails sign in normally. Existing duplicate
usernames use `username@workspace-code`; the OWNER tenant summary displays the Admin login.
New referral links use the inviter's immutable UUID as the globally unique `ref` code and the
one configured domain. Legacy username codes work only when unambiguous. Admin invitations
create direct Players; Master Agent / Agent applications preserve the Agent / Sub-Agent flow.
An invitation's validated account determines both `tenant_id` and immediate `parent_id`.
Without an invitation, registration requires an explicitly selected default tenant with
self-registration enabled. Otherwise an invitation is required.

The account's `tenant_id` cannot be changed through the API or by updating the database row.
Existing usernames, parents, balances, ledger rows, and invoices are not reassigned or deleted.

## Rental billing

Tenant creation, activation, suspension, invoices, mark-paid actions, payment references,
revenue analytics, billing history, and scheduled billing reuse the existing OWNER pages.
The rental page adds an individual monthly PHP fee, optional activation and next billing
dates, tenant account counts, registration links, and upcoming/overdue invoice alerts.

Custom rental fees use a private plan per tenant. Updating a fee changes future billing;
issued invoices retain their amounts. Private rental plans are not offered in public checkout.
The existing billing worker generates monthly invoices and applies the configured grace-period
suspension policy. Suspension blocks the tenant's whole hierarchy and its stream viewers,
while preserving data and leaving other tenants operational. Future activation dates block
account access until activation. OWNER reactivation and the existing payment workflow remain
available.

## One global stream, independent events

Every tenant runs its own card. Each Admin creates, publishes, starts and completes its own
events and fights, and each Declarator (an account created inside that workspace) opens and
closes betting and declares results for that workspace only. Shared mode adds no event or
fight restrictions: nothing is copied between tenants, and Players see only their own
tenant's published events. Pools, ghost amounts, wallets, bets, odds and commissions stay in
each tenant's own books; tenant RLS keeps one workspace's Admin, Declarator and Players away
from another's events. Enabling shared mode leaves running tenant cards in place.

Until 2026-11-16 a single "official arena" event was copied read-only into every tenant and
every Declarator desk was locked (`owner_controlled`). That projection, its
`/platform/rental/{events,fights,synchronize}` endpoints and the activation check that
required settling other events were removed. `rental_settings.global_event_id` remains in the
schema but is unused. Rows that were projected before the change are now ordinary events of
the tenant that holds them; that tenant's Admin can complete or cancel them.

The original Admin is the only global video provider, designated permanently by migration
0038. Its Live streaming, Stream Operators and Operator console pages manage the existing
MediaMTX sources. Its default stream reaches every tenant independently of shared-domain
mode. Consumer tenants cannot configure streams or publish sessions. The Owner has no
streaming controls or streaming management endpoints. Existing provider source keys are
preserved. See [Global livestream](global-stream-provider.md). All viewers use the same
MediaMTX path; playback tokens carry the **viewer's** tenant, account, and session.
Balance >= 100 is still required for Players, including media callbacks and ongoing viewer
reconciliation. Old tenant provider URLs and unselected MediaMTX paths cannot provide a
second player stream. No additional MediaMTX instances are created.

Private updates stay on tenant/user WebSocket channels. Only the selected stream's
allowlisted status/source notifications use the authenticated shared stream channel.

## Rollout

1. Use the existing environment-isolated deployment and verified backup procedure, applying
   migration `0034`. It adds platform controls and grants needed for OWNER orchestration;
   existing account and financial data are unchanged. Shared mode starts disabled.
2. Apply migration 0038 and verify that the original workspace has the protected provider
   designation. In that Admin's Live streaming page, select its default broadcast. Existing
   SaaS tenants inherit playback without a stream-selection step. Each Admin continues to
   configure its own tenant's betting settings.
3. Set rental fees and registration policy.
4. Enable shared mode and save. Streaming control remains with the original Admin.
   Existing tenant-host sessions must move to the main domain. New tenants receive no player
   subdomain. The anonymous shared login page remains available even if the default Admin's
   subscription is suspended.
5. Verify two different tenants on the same domain, their referral registrations, a low-balance
   Player, each tenant running its own fight with its own Declarator, isolated payouts,
   invoice renewal, and tenant suspension.

Migration `0034` has a reversible schema downgrade, but normal rollback should first settle
active fights and disable shared mode. Follow the existing backup/restore runbook
for application/database rollback; never discard settlement history to roll back a release.

## Verification

`api/tests/test_shared_rental.py` covers shared login/refresh, duplicate usernames, invitation
ownership, suspension, future activation, database ownership immutability, monthly fee history,
each tenant running its own card and Declarator with isolated payouts, stream authorization, and WebSocket
privacy. Existing auth, tenant, billing, betting, commission, video-balance, and realtime suites
remain the regression checks. `rental.spec.ts` covers OWNER UI requests, amount validation,
error handling, and duplicate-submit prevention. Build all three Angular applications.
