# Declarator workspace

The Declarator dashboard is an additional route in the existing Admin Angular application: **`/admin/declarator`**. It does not require another frontend app or server.

## Included

- Midnight Arena dashboard with approximately 65/35 video/control columns and a single-column phone layout: fight information, video, declaration controls, winning history, records.
- Shared, authorized demonstration video monitor: loading, offline, playback errors, refresh, fullscreen, current fight badge, and live-update status. The Admin build contains the same bundled clip and attribution as the Player build; no configurable broadcast source was added.
- Plain Meron/Wala declaration actions, Draw, No contest, and Cancelled. Every submission requires confirmation; cancellation requires a reason.
- Assigned-event scope, submission permission, independent approval permission, no self-approval, idempotent retries, canonical revision checks, transactional finalization, and retained original result revisions.
- Pending proposals remain outside official match results and the winning board. Approved outcomes use the existing Player/Admin WebSocket event contract. Existing non-monetary Player predictions derive their outcome from those official results; points are never moved by this workflow.
- A paginated records table with exact fight-number search, event/outcome/verification/date filters, original declarator and approver metadata, result details, and declaration/revision audit history.
- Private approval notifications and a review queue. Repeated or out-of-order notifications invalidate canonical snapshots rather than inserting speculative records.
- Event assignment management inside the existing event detail page, plus the Declarator account type in existing account management.

## Activate in the existing environment

Apply the schema and existing-tenant role backfill before using the new route:

```sh
cd /Users/richardfreyreyes/Documents/Repositories/E-Sabong/api
.venv/bin/python -m alembic upgrade head
```

Revisions `0004` and `0005` add the queue/assignment/audit tables, extend the account-type constraint, create the history lookup index, backfill system roles, and preserve verification mode once an event is assigned. Existing administrator permissions need the backfill; merely granting a frontend route is insufficient.

The migration owner intentionally has `NOBYPASSRLS`. The backfill temporarily removes **FORCE** for that table owner inside its DDL transaction while keeping RLS enabled for the application role, then restores FORCE before commit. The final schema retains forced RLS on all tenant tables. [Generated SQL](migration.sql) is available for review.

Restart the API after migrating. For the local development setup, stop the existing Uvicorn process with Ctrl+C in its terminal, then run:

```sh
cd /Users/richardfreyreyes/Documents/Repositories/E-Sabong/api
.venv/bin/python -m alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --port 8000
```

An API process started before the Declarator changes can still serve the old application even while Angular displays the new dashboard. Its `/api/openapi.json` will have no `/api/v1/declarator/events` route and its account-type enum will lack `declarator`. Restarting only Angular does not update that process. A current API with no event assignments returns an empty list, not a route-level 404.

For a full Compose deployment, rebuild the API/migration images and apply migrations before recreating the API and workers. Do not start a second local Uvicorn process on an occupied port.

After migrating and restarting, sign out and sign in again so the frontend receives the new permission set. Use `http://localhost:4201/admin/login` for the local Admin development server; Declarator accounts land at `/admin/declarator`.

The username does not determine permissions. An account named `declarator` with account type **Player** or role **Master agent** is not a Declarator. Use account type **Declarator** and the **Declarator** role; a tenant administrator uses the separate approval controls.

1. An administrator creates an account with type **Declarator** through Accounts, or uses an existing Declarator account.
2. In Events → event details → **Declarator assignments**, assign that account to the event.
3. The Declarator signs in through the existing Admin sign-in screen and lands in the dedicated workspace. Only assigned published/cancelled events and their retained records are available.
4. An authorized event operator starts the event/fight through the existing lifecycle. Submission requires a live event and an in-progress fight.
5. The Declarator confirms a proposed result. A different authorized approver reviews it in the same workspace and confirms finalization.
6. For an amendment to a completed result, open the fight's history, propose a different result with a reason, and obtain independent approval. Cancelled fights retain their existing terminal lifecycle.

Default Declarators receive only `results.submit`. Tenant administrators receive `results.approve`; operators keep their previous permissions. Tenant role configuration remains available through the existing role management interface.

Existing direct result/correction APIs remain available for unmanaged events. Once an event has a Declarator assignment, verification stays enabled even after all assignments are revoked. Verified events can only be cancelled by an event manager who also has result approval permission. Assigned or previously declared fights use verification; those APIs cannot bypass the queue. Scheduled-match operational cancellation is retained. Events/matches with declaration audit history cannot be silently deleted through the existing soft-delete actions.

The monitor holds the last finalized fight on screen until the next starts, or displays the first scheduled fight on a new card. It does not start fights automatically.

## Verification

See [verification.md](verification.md) for executed checks and the remaining live-environment blocker. The full request/response contract is documented in [contract.md](contract.md) and [the platform OpenAPI schema](../openapi.json).

To run PostgreSQL/Redis integration tests locally:

```sh
cd /Users/richardfreyreyes/Documents/Repositories/E-Sabong/api
.venv/bin/python -m pytest tests -q
```

The test fixtures reset **`esabong_test`**, and use Redis database 15. They do not use the application's normal database for test records. Keep the existing test DB configuration in `api/tests/conftest.py` in mind when running them.

Rollback retains Declarator accounts and role records rather than deleting user data. An older application version cannot interpret the new account type; plan account compatibility before rolling the application back. No migration or deployment was executed from this restricted workspace.

## Main sources

| Area | Source |
|---|---|
| Dashboard, records, dialogs, assignments | `web/projects/admin/src/app/declarator/` |
| Canonical UI store and realtime reconciliation | `web/projects/admin/src/app/declarator/store.ts` |
| Shared video, board, and pure streak utility | `web/shared/arena/` |
| Typed API client | `web/shared/services/declarator-api.ts` |
| Tenant-scoped API and domain workflow | `api/app/modules/declarator/` |
| Official result compatibility/locking | `api/app/modules/results/service.py` |
| Database migrations | `db/migrations/versions/20261029_0004_declarator_workflow.py`, `20261029_0005_declarator_system_roles.py` |
| Backend integration cases | `api/tests/test_declarator.py` |
| Offline backend contract/policy tests | `api/unit_tests/` |

Player import paths remain compatibility exports; both portals execute the same winning-history calculation and media component. Existing authentication, tenant resolution, branding, subscriptions, and non-monetary points integrations are retained.
