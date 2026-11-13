# Shared player domain and tenant rental

The OWNER console's **Tenant Rental & Global Arena** page extends the existing tenant,
subscription, invoice, and event services. No new wallet or commission engine is introduced.

## Identity and registration

Enable shared-domain access under `/owner/rental`. The configured `BASE_DOMAIN` is the only
tenant application host in this mode: `swcmain183.live` in production and the existing UAT
hostname in UAT. Admin management continues at `/admin/`; players and agents use the same
application domain. The OWNER console and platform authentication remain separate.

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

## One global stream and official arena

Select the existing main workspace as the OWNER broadcast workspace. It stores the canonical
stream and official event for compatibility with existing foreign keys. This does not make
its Admin an OWNER. Shared mode denies tenant stream configuration, publish sessions, and
official event mutations. Tenants retain their own betting settings, odds, and ghost pools.

The OWNER can create streams, obtain/rotate an OBS key, select a stream, and start or stop it.
Selecting a legacy OBS source transfers its key to OWNER control: the old publisher is
disconnected and a replacement key is returned once. Update the OWNER encoder with that key.
All viewers use the same MediaMTX path; playback tokens carry the **viewer's** tenant, account,
and session. Balance >= 100 is still required for Players, including media callbacks and
ongoing viewer reconciliation. Old tenant provider URLs and unselected MediaMTX paths cannot
provide a second player stream. No additional MediaMTX instances are created.

The OWNER schedules/starts the official event, adds fights, opens/closes betting, declares
results, and can correct results through the rental API. Event/fight facts are projected to
tenant-scoped rows with deterministic IDs. Only the selected official arena appears as active
in the Player portal; completed historical events remain available. Pools, ghost amounts,
wallets, bets, odds, and commissions are **never** copied between tenants. Each projection uses
the existing settlement/correction engine with that tenant's own books and bet snapshots.
Repeated synchronization cannot create a second payout or commission credit.

Official changes and tenant projections commit together; a failed projection rolls back the
official operation for retry. Newly provisioned tenants receive the current arena without
DNS or deployment changes. Enabling betting also joins an already active global fight.
Private updates stay on tenant/user WebSocket channels. Only the selected stream's allowlisted
status/source notifications use the authenticated shared stream channel; official fight
updates use each tenant's projected IDs and the existing client message format.

## Rollout

1. Use the existing environment-isolated deployment and verified backup procedure, applying
   migration `0034`. It adds platform controls and grants needed for OWNER orchestration;
   existing account and financial data are unchanged. Shared mode starts disabled.
2. In OWNER, select the existing main broadcast workspace while shared mode is disabled.
   Configure its stream and official event. Its betting settings must be enabled before
   opening an official book. Rental Admins configure their own tenant's betting settings.
3. Set rental fees and registration policy. Settle legacy tenant fights that are outside the
   selected global arena before enabling shared mode; activation refuses to abandon them.
4. Enable shared mode and save. Use the displayed replacement OBS key if one is returned.
   Existing tenant-host sessions must move to the main domain. New tenants receive no player
   subdomain. The anonymous shared login page remains available even if the default Admin's
   subscription is suspended.
5. Verify two different tenants on the same domain, their referral registrations, a low-balance
   Player, a complete global fight, isolated payouts, invoice renewal, and tenant suspension.

Migration `0034` has a reversible schema downgrade, but normal rollback should first settle
active official fights and disable shared mode. Follow the existing backup/restore runbook
for application/database rollback; never discard settlement history to roll back a release.

## Verification

`api/tests/test_shared_rental.py` covers shared login/refresh, duplicate usernames, invitation
ownership, suspension, future activation, database ownership immutability, monthly fee history,
independent global payouts/corrections, replay safety, stream authorization, and WebSocket
privacy. Existing auth, tenant, billing, betting, commission, video-balance, and realtime suites
remain the regression checks. `rental.spec.ts` covers OWNER UI requests, amount validation,
error handling, and duplicate-submit prevention. Build all three Angular applications.
