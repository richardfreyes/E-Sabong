"""Cross-tenant isolation: application layer and PostgreSQL row-level security."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.core.database import get_session_factory, tenant_session
from app.models import TENANT_TABLES
from tests.conftest import client_for


async def test_tenant_token_rejected_on_other_tenant_host(app, tenant, other_tenant) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app, other_tenant.host) as c:
        r = await c.get("/api/v1/auth/me", headers={"Authorization": tenant.admin.headers["Authorization"]})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_token"


async def test_admin_cannot_reach_other_tenant_resources_by_id(app, tenant, other_tenant) -> None:  # type: ignore[no-untyped-def]
    foreign_user = await other_tenant.create_user("foreigner", "player")
    r = await other_tenant.admin.post(
        "/api/v1/admin/events", json={"name": "Foreign Event", "starts_at": "2030-01-01T10:00:00+00:00"}
    )
    foreign_event = r.json()

    a = tenant.admin
    assert (await a.get(f"/api/v1/admin/users/{foreign_user['id']}")).status_code == 404
    assert (await a.patch(f"/api/v1/admin/users/{foreign_user['id']}", json={"full_name": "pwned"})).status_code == 404
    assert (await a.post(f"/api/v1/admin/users/{foreign_user['id']}/suspend")).status_code == 404
    assert (await a.get(f"/api/v1/admin/events/{foreign_event['id']}")).status_code == 404
    assert (
        await a.post(f"/api/v1/admin/events/{foreign_event['id']}/matches", json={"meron_name": "x", "wala_name": "y"})
    ).status_code == 404
    assert (await a.get(f"/api/v1/admin/points/accounts/{foreign_user['id']}")).status_code == 404
    r = await a.post(
        "/api/v1/admin/points/allocations",
        json={"user_id": foreign_user["id"], "amount": 10},
        headers={"Idempotency-Key": f"x-{uuid.uuid4()}"},
    )
    assert r.status_code == 404

    # Cross-tenant parent ids are rejected too.
    r = await a.post(
        "/api/v1/admin/users",
        json={
            "username": "child",
            "full_name": "c",
            "account_type": "player",
            "parent_id": foreign_user["id"],
            "password": "Str0ng!Passw0rd",
        },
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_parent"

    listing = (await a.get("/api/v1/admin/users", params={"page_size": 100})).json()
    assert foreign_user["id"] not in {u["id"] for u in listing["items"]}
    events = (await a.get("/api/v1/admin/events")).json()
    assert foreign_event["id"] not in {e["id"] for e in events["items"]}


async def test_rls_returns_nothing_without_tenant_context(app, tenant) -> None:  # type: ignore[no-untyped-def]
    factory = get_session_factory("app")
    async with factory() as db:
        count = (await db.execute(text("SELECT count(*) FROM tenant_users"))).scalar_one()
        tenants = (await db.execute(text("SELECT count(*) FROM tenants"))).scalar_one()
    assert count == 0
    assert tenants == 0


async def test_rls_limits_rows_to_current_tenant(app, tenant, other_tenant) -> None:  # type: ignore[no-untyped-def]
    await other_tenant.create_user("rlsvictim", "player")
    async with tenant_session(tenant.id) as db:
        tenant_ids = set((await db.execute(text("SELECT DISTINCT tenant_id FROM tenant_users"))).scalars())
        all_tenants = set((await db.execute(text("SELECT id FROM tenants"))).scalars())
    assert tenant_ids == {tenant.id}
    assert all_tenants == {tenant.id}


async def test_rls_blocks_writes_into_another_tenant(app, tenant, other_tenant) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(DBAPIError) as exc:
        async with tenant_session(tenant.id) as db:
            await db.execute(
                text(
                    "INSERT INTO notifications (id, tenant_id, user_id, type, title, data, created_at) "
                    "VALUES (gen_random_uuid(), :tid, :uid, 't', 't', '{}', now())"
                ),
                {"tid": other_tenant.id, "uid": other_tenant.admin_id},
            )
    assert "row-level security" in str(exc.value)


async def test_app_role_has_no_access_to_platform_tables(app) -> None:  # type: ignore[no-untyped-def]
    for table in ("platform_users", "license_keys", "deployment_instances", "signup_requests"):
        with pytest.raises(ProgrammingError) as exc:
            async with get_session_factory("app")() as db:
                await db.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))  # noqa: S608 - fixed identifiers
        assert "permission denied" in str(exc.value)


@pytest.mark.parametrize("table", ["audit_logs", "point_transactions", "point_ledger_entries", "match_result_history"])
async def test_append_only_tables_reject_update_and_delete(app, tenant, table) -> None:  # type: ignore[no-untyped-def]
    for stmt in (f"UPDATE {table} SET created_at = now()", f"DELETE FROM {table}"):  # noqa: S608
        with pytest.raises(ProgrammingError) as exc:
            async with tenant_session(tenant.id) as db:
                await db.execute(text(stmt))
        assert "permission denied" in str(exc.value)


async def test_every_tenant_table_has_forced_rls_policy(app) -> None:  # type: ignore[no-untyped-def]
    """Guards against a future migration adding a tenant table without isolation."""
    async with get_session_factory("platform")() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                           EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid) AS has_policy
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                    WHERE c.relkind = 'r'
                      AND EXISTS (SELECT 1 FROM information_schema.columns col
                                  WHERE col.table_name = c.relname AND col.column_name = 'tenant_id')
                    """
                )
            )
        ).all()
    platform_owned = {"signup_requests", "license_keys", "deployment_instances"}
    tenant_tables = {r.relname: r for r in rows if r.relname not in platform_owned}
    assert set(tenant_tables) == set(TENANT_TABLES)
    for name, row in tenant_tables.items():
        assert row.relrowsecurity and row.relforcerowsecurity and row.has_policy, name


async def test_suspended_tenant_is_blocked(app, owner, tenant) -> None:  # type: ignore[no-untyped-def]
    r = await owner.client.post(f"/api/v1/platform/tenants/{tenant.id}/suspend", json={"reason": "test"})
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    r = await tenant.admin.get("/api/v1/auth/me")
    assert r.status_code == 403 and r.json()["error"]["code"] == "tenant_suspended"
    async with client_for(app, tenant.host) as c:
        public = await c.get("/api/v1/public/tenant")
    assert public.status_code == 200 and public.json()["status"] == "suspended"
    assert (await owner.client.post(f"/api/v1/platform/tenants/{tenant.id}/activate", json={})).status_code == 200
    assert (await tenant.admin.get("/api/v1/auth/me")).status_code == 200
