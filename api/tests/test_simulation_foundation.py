"""Real PostgreSQL checks for the neutral arcade foundation; no wagering engine is exposed."""

import asyncio
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import get_session_factory, platform_session, tenant_session
from app.modules.betting.models import PlayWallet, WalletEntryKind
from app.modules.betting.service import _post
from app.modules.simulations.models import SimulationArena, SimulationCommand, SimulationMatch, SimulationStake
from tests.conftest import legacy_points_rows, run_alembic

BASE = "/api/v1/simulations"


async def test_empty_migration_rollback_preserves_existing_roles_and_balances(tenant) -> None:  # type: ignore[no-untyped-def]
    async def snapshot():  # type: ignore[no-untyped-def]
        async with platform_session() as db:
            return [
                (await db.execute(text(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY id"))).all()  # noqa: S608
                for table in (
                    "tenant_users",
                    "roles",
                    "play_wallets",
                    "play_wallet_entries",
                    "point_accounts",
                    "point_transactions",
                    "point_ledger_entries",
                    "audit_logs",
                )
            ]

    before = await snapshot()
    result = run_alembic("downgrade", "0013")
    assert result.returncode == 0, result.stderr
    result = run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert await snapshot() == before
    assert (await tenant.admin.get(f"{BASE}/capabilities")).json()["permissions"] == ["simulations.control", "simulations.manage"]


@pytest.mark.parametrize(
    "account_type,role",
    [
        ("master_agent", "MASTER_HOST"),
        ("agent", "REGIONAL_HOST"),
        ("declarator", "DECLARATOR"),
        ("player", "PLAYER"),
    ],
)
async def test_role_aliases_keep_stored_identity_and_least_privilege(tenant, account_type, role) -> None:  # type: ignore[no-untyped-def]
    parent = await tenant.create_user("host-root", "master_agent") if account_type == "agent" else None
    user = await tenant.create_user("sim-user", account_type, parent_id=parent["id"] if parent else None)
    c = await tenant.login("sim-user")
    cap = (await c.get(f"{BASE}/capabilities")).json()
    assert cap["role"] == role and cap["engine_enabled"] is True
    assert "non-transferable" in cap["disclaimer"]
    assert cap["permissions"] == (["simulations.control"] if account_type == "declarator" else [])
    assert (await c.get("/api/v1/auth/me")).json()["account_type"] == user["account_type"]
    assert (await c.post(f"{BASE}/arenas", json={"name": "No privilege"})).status_code == 403


async def test_arena_management_is_validated_audited_and_tenant_isolated(tenant, other_tenant) -> None:  # type: ignore[no-untyped-def]
    a = tenant.admin
    assert (await a.post(f"{BASE}/arenas", json={"name": "  "})).status_code == 422
    r = await a.post(f"{BASE}/arenas", json={"name": "Tactical Arena"})
    assert r.status_code == 201, r.text
    arena = r.json()
    assert arena["enabled"] is False and arena["tenant_id"] == str(tenant.id)
    assert (await a.post(f"{BASE}/arenas", json={"name": "Tactical Arena"})).status_code == 409
    assert (await other_tenant.admin.get(f"{BASE}/arenas/{arena['id']}")).status_code == 404
    assert (await other_tenant.admin.get(f"{BASE}/arenas")).json()["total"] == 0
    assert (await a.get(f"{BASE}/arenas", params={"q": "Tactical"})).json()["total"] == 1
    logs = (await a.get("/api/v1/admin/audit-logs", params={"action": "simulation.arena_created"})).json()
    assert logs["total"] == 1 and logs["items"][0]["resource_id"] == arena["id"]
    assert await legacy_points_rows(tenant.id) == (0, 0, 0)
    async with tenant_session(tenant.id) as db:
        assert (await db.execute(select(PlayWallet))).scalars().all() == []
    # No tenant context gives no arena rows even when data exists.
    async with get_session_factory("app")() as db:
        assert (await db.execute(select(SimulationArena))).scalars().all() == []


async def test_declarator_assignment_is_idempotent_and_scoped(tenant, other_tenant) -> None:  # type: ignore[no-untyped-def]
    user = await tenant.create_user("sim-declarator", "declarator")
    foreign = await other_tenant.create_user("foreign-declarator", "declarator")
    player = await tenant.create_user("sim-player", "player")
    c = await tenant.login("sim-declarator")
    arena = (await tenant.admin.post(f"{BASE}/arenas", json={"name": "Arcade Arena"})).json()
    other = (await tenant.admin.post(f"{BASE}/arenas", json={"name": "Other Arena"})).json()
    assert (await c.get(f"{BASE}/arenas/{arena['id']}")).status_code == 404
    path = f"{BASE}/arenas/{arena['id']}/controllers/{user['id']}"
    responses = await asyncio.gather(tenant.admin.put(path), tenant.admin.put(path))
    assert [r.status_code for r in responses] == [200, 200]
    assert (await c.get(f"{BASE}/arenas")).json()["total"] == 1
    assert (await c.get(f"{BASE}/arenas/{arena['id']}")).status_code == 200
    assert (await c.get(f"{BASE}/arenas/{other['id']}")).status_code == 404
    assert (await c.put(path)).status_code == 403
    assert (await tenant.admin.put(f"{BASE}/arenas/{arena['id']}/controllers/{foreign['id']}")).status_code == 404
    assert (await tenant.admin.put(f"{BASE}/arenas/{arena['id']}/controllers/{player['id']}")).status_code == 422
    logs = (await tenant.admin.get("/api/v1/admin/audit-logs", params={"action": "simulation.controller_assigned"})).json()
    assert logs["total"] == 1
    assert (await tenant.admin.delete(path)).status_code == 200
    assert (await tenant.admin.delete(path)).status_code == 200
    assert (await c.get(f"{BASE}/arenas/{arena['id']}")).status_code == 404
    assert (await c.get(f"{BASE}/arenas")).json()["total"] == 0
    logs = (await tenant.admin.get("/api/v1/admin/audit-logs", params={"action": "simulation.controller_removed"})).json()
    assert logs["total"] == 1


async def test_database_guards_cross_tenant_links_replays_and_immutable_receipts(tenant, other_tenant) -> None:  # type: ignore[no-untyped-def]
    arena = (await tenant.admin.post(f"{BASE}/arenas", json={"name": "Neutral Arena"})).json()
    foreign = (await other_tenant.admin.post(f"{BASE}/arenas", json={"name": "Foreign Arena"})).json()
    with pytest.raises(IntegrityError):
        async with tenant_session(tenant.id) as db:
            db.add(SimulationMatch(tenant_id=tenant.id, arena_id=uuid.UUID(foreign["id"]), number=1, team_a="Red", team_b="Blue"))
            await db.flush()
    player = await tenant.create_user("simulated-player", "player")
    await tenant.admin.put("/api/v1/admin/betting/settings", json={"enabled": True, "starting_balance": "100.00"})
    c = await tenant.login("simulated-player")
    assert (await c.get("/api/v1/portal/betting")).status_code == 200
    async with tenant_session(tenant.id) as db:
        arena_row = await db.get(SimulationArena, uuid.UUID(arena["id"]))
        arena_row.enabled = True
        wallet = await db.scalar(select(PlayWallet).where(PlayWallet.user_id == uuid.UUID(player["id"])))
        match = SimulationMatch(
            tenant_id=tenant.id, arena_id=uuid.UUID(arena["id"]), number=1, team_a="Red", team_b="Blue", state="wagering_open"
        )
        db.add(match)
        await db.flush()
        match_id = match.id
    request_id = uuid.uuid4()

    async def stake(*, funded: bool = True) -> None:
        async with tenant_session(tenant.id) as db:
            locked = await db.scalar(select(PlayWallet).where(PlayWallet.id == wallet.id).with_for_update())
            row = SimulationStake(
                tenant_id=tenant.id,
                match_id=match_id,
                player_id=uuid.UUID(player["id"]),
                request_id=request_id,
                side="team_a",
                amount=Decimal("0.01"),
            )
            db.add(row)
            await db.flush()
            if funded:
                await _post(db, locked, WalletEntryKind.STAKE, -row.amount, f"simulation:stake:{row.id}", bet_id=row.id)

    with pytest.raises(DBAPIError, match="atomic Arena Points ledger debit"):
        await stake(funded=False)
    posted = await asyncio.gather(stake(), stake(), return_exceptions=True)
    assert sum(isinstance(r, IntegrityError) for r in posted) == 1, [repr(r) for r in posted]
    command_key = uuid.uuid4()
    async with tenant_session(tenant.id) as db:
        db.add(
            SimulationCommand(
                tenant_id=tenant.id,
                match_id=match_id,
                actor_id=tenant.admin_id,
                request_id=command_key,
                request_hash="a" * 64,
                version=0,
                response={"state": "pending"},
            )
        )
    with pytest.raises(IntegrityError):
        async with tenant_session(tenant.id) as db:
            db.add(
                SimulationCommand(
                    tenant_id=tenant.id,
                    match_id=match_id,
                    actor_id=tenant.admin_id,
                    request_id=command_key,
                    request_hash="b" * 64,
                    version=1,
                    response={"changed": True},
                )
            )
            await db.flush()
    engine = create_async_engine(os.environ["DATABASE_URL_MIGRATIONS"])
    try:
        for table in ("simulation_stakes", "simulation_commands"):
            for operation in (f"UPDATE {table} SET created_at = now()", f"DELETE FROM {table}"):  # noqa: S608
                with pytest.raises(DBAPIError, match="immutable"):
                    async with engine.begin() as conn:
                        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.id)})
                        await conn.execute(text(operation))
    finally:
        await engine.dispose()
    # Downgrade must refuse to drop a schema that holds simulation history.
    result = run_alembic("downgrade", "0013")
    assert result.returncode != 0 and "Refusing to discard simulation history" in result.stderr
    async with tenant_session(tenant.id) as db:
        assert len((await db.execute(select(SimulationStake))).scalars().all()) == 1
        assert (await db.get(PlayWallet, wallet.id)).balance == Decimal("99.99")
