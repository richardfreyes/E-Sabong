"""Each master agent, agent, and sub-agent sees only invitations they created.

Visibility follows invited_by_user_id. Parent accounts, downlines, and other tenants do not
inherit those rows. Listing, counts, search, export, detail, and revocation all use that rule.
Referral parents and account ownership stay as they were.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import tenant_session
from app.modules.agents.invitations import backfill_invitations
from app.modules.auth.models import AccountStatus
from app.modules.users.models import AccountType, TenantUser
from tests.conftest import PASSWORD, client_for

AGENT = "/api/v1/agent"
ADMIN = "/api/v1/admin"


@pytest.fixture(autouse=True)
def _many_signups(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "register_ip_max_per_hour", 10_000)


async def register(app, host: str, username: str, ref: str, apply: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    body: dict = {"username": username, "full_name": username.title(), "password": PASSWORD, "ref": ref}
    if apply:
        body["apply"] = apply
    async with client_for(app, host) as client:
        response = await client.post("/api/v1/auth/register", json=body)
        assert response.status_code == 201, response.text
        return response.json()["user"]


async def invite_names(client, **params) -> set[str]:  # type: ignore[no-untyped-def]
    response = await client.get(f"{AGENT}/invitations", params=params or None)
    assert response.status_code == 200, response.text
    return {row["invitee_username"] for row in response.json()["items"]}


async def hierarchy(app, tenant):  # type: ignore[no-untyped-def]
    """Master Agent 1, two agents, one sub-agent, and players invited by each of them."""
    boss = await tenant.create_user("boss", "master_agent")
    master = await tenant.login("boss")
    agent1 = await register(app, tenant.host, "agent1", "boss", apply="agent")
    agent2 = await tenant.create_user("agent2", "agent", via=master)
    agent_client = await tenant.login("agent1")
    agent2_client = await tenant.login("agent2")
    sub = await tenant.create_user("sub1", "sub_agent", parent_id=agent1["id"], via=agent_client)
    sub_client = await tenant.login("sub1")
    users = {"boss": boss, "agent1": agent1, "agent2": agent2, "sub1": sub}
    for name, ref in (("ply1", "agent1"), ("ply2", "agent1"), ("ply3", "agent2"), ("ply4", "boss"), ("subply", "sub1")):
        users[name] = await register(app, tenant.host, name, ref)
    # Created by the master agent, parented under agent 1. The invitation stays with the master.
    users["placed"] = await tenant.create_user("placed", "player", parent_id=agent1["id"], via=master)
    clients = {"boss": master, "agent1": agent_client, "agent2": agent2_client, "sub1": sub_client}
    return users, clients


async def test_each_role_sees_only_invitations_it_created(app, tenant, other_tenant):
    users, clients = await hierarchy(app, tenant)
    boss, agent1, agent2, sub = clients["boss"], clients["agent1"], clients["agent2"], clients["sub1"]

    assert await invite_names(boss) == {"agent1", "agent2", "ply4", "placed"}
    assert await invite_names(agent1) == {"ply1", "ply2", "sub1"}
    assert await invite_names(agent2) == {"ply3"}
    assert await invite_names(sub) == {"subply"}

    # A parent id, an owner id, or a tenant id in the query cannot widen the list.
    stolen = await boss.get(
        f"{AGENT}/invitations",
        params={"invited_by_user_id": users["agent1"]["id"], "tenant_id": str(other_tenant.id), "q": "ply1"},
    )
    assert stolen.status_code == 200 and stolen.json()["items"] == []
    assert await invite_names(agent1, q="ply1") == {"ply1"}
    assert await invite_names(agent1, status="accepted") == {"ply1", "ply2", "sub1"}
    assert await invite_names(boss, status="pending") == set()

    boss_summary = (await boss.get(f"{AGENT}/invitations/summary")).json()
    assert (boss_summary["accepted"], boss_summary["pending"], boss_summary["history"]) == (4, 0, 4)
    assert (await agent1.get(f"{AGENT}/invitations/summary")).json()["accepted"] == 3
    assert (await sub.get(f"{AGENT}/invitations/summary")).json()["history"] == 1

    exported = await boss.get(f"{AGENT}/invitations/export", params={"invited_by_user_id": users["sub1"]["id"]})
    assert exported.status_code == 200 and exported.headers["content-type"].startswith("text/csv")
    assert "ply4" in exported.text and "placed" in exported.text
    assert "ply1" not in exported.text and "subply" not in exported.text
    sub_export = await sub.get(f"{AGENT}/invitations/export")
    assert "subply" in sub_export.text and "ply4" not in sub_export.text and "ply1" not in sub_export.text

    pending = await agent1.post(f"{AGENT}/invitations", json={
        "account_type": "sub_agent",
        "invitee_username": "next-sub",
        "invited_by_user_id": users["boss"]["id"],
        "tenant_id": str(other_tenant.id),
    })
    assert pending.status_code == 201, pending.text
    body = pending.json()
    assert body["invited_by_user_id"] == users["agent1"]["id"]
    assert body["tenant_id"] == str(tenant.id)
    assert body["status"] == "pending"
    assert await invite_names(agent1, status="pending") == {"next-sub"}
    assert await invite_names(boss, q="next-sub") == set()
    assert await invite_names(sub, q="next-sub") == set()
    assert (await agent1.get(f"{AGENT}/invitations/summary")).json()["pending"] == 1

    invitation_id = body["id"]
    assert (await agent1.get(f"{AGENT}/invitations/{invitation_id}")).status_code == 200
    for outsider in (boss, agent2, sub):
        assert (await outsider.get(f"{AGENT}/invitations/{invitation_id}")).status_code == 404
        assert (await outsider.patch(f"{AGENT}/invitations/{invitation_id}", json={"status": "revoked"})).status_code == 404

    await other_tenant.create_user("outsider", "master_agent")
    foreign = await other_tenant.login("outsider")
    assert (await foreign.get(f"{AGENT}/invitations/{invitation_id}")).status_code == 404
    assert "next-sub" not in await invite_names(foreign)
    assert "ply4" not in await invite_names(foreign)

    revoked = await agent1.patch(f"{AGENT}/invitations/{invitation_id}", json={"status": "revoked"})
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    assert await invite_names(agent1, status="pending") == set()
    assert "next-sub" in await invite_names(agent1, status="revoked")
    assert (await agent1.get(f"{AGENT}/invitations/summary")).json()["pending"] == 0

    # Accepted invitations are not a lever for changing the account.
    rows = (await agent1.get(f"{AGENT}/invitations")).json()["items"]
    accepted_id = next(row["id"] for row in rows if row["invitee_username"] == "ply1")
    refused = await agent1.patch(f"{AGENT}/invitations/{accepted_id}", json={"status": "revoked"})
    assert refused.status_code == 409
    blocked = await boss.post(f"{AGENT}/invitations", json={"account_type": "sub_agent", "invitee_username": "nope"})
    assert blocked.status_code == 403
    blocked = await agent1.post(f"{AGENT}/invitations", json={"account_type": "agent", "invitee_username": "nope"})
    assert blocked.status_code == 403
    blocked = await sub.post(f"{AGENT}/invitations", json={"account_type": "agent", "invitee_username": "nope"})
    assert blocked.status_code == 403
    created = await sub.post(f"{AGENT}/invitations", json={"account_type": "player", "invitee_username": "sub-pending"})
    assert created.status_code == 201
    assert await invite_names(sub, status="pending") == {"sub-pending"}
    assert await invite_names(agent1, q="sub-pending") == set()
    assert await invite_names(boss, q="sub-pending") == set()

    player = await tenant.login("ply1")
    assert (await player.get(f"{AGENT}/invitations")).status_code == 403
    assert (await player.get(f"{AGENT}/invitations/{accepted_id}")).status_code == 403

    owned = {
        "agent1": users["boss"]["id"],
        "ply1": users["agent1"]["id"],
        "ply3": users["agent2"]["id"],
        "ply4": users["boss"]["id"],
        "sub1": users["agent1"]["id"],
        "subply": users["sub1"]["id"],
        "placed": users["agent1"]["id"],
    }
    for name, parent_id in owned.items():
        account = (await tenant.admin.get(f"{ADMIN}/users/{users[name]['id']}")).json()
        assert account["parent_id"] == parent_id, name


async def test_backfill_keeps_the_creator_and_ignores_the_parent(app, tenant):
    users, clients = await hierarchy(app, tenant)
    async with tenant_session(tenant.id) as db:
        agent = await db.get(TenantUser, uuid.UUID(users["agent1"]["id"]))
        assert agent is not None
        created_by_agent = TenantUser(
            tenant_id=tenant.id,
            username="legacy_child",
            full_name="Legacy Child",
            password_hash=agent.password_hash,
            account_type=AccountType.PLAYER,
            parent_id=uuid.UUID(users["boss"]["id"]),
            created_by_id=agent.id,
            status=AccountStatus.ACTIVE,
            must_change_password=False,
        )
        referred = TenantUser(
            tenant_id=tenant.id,
            username="legacy_ref",
            full_name="Legacy Ref",
            password_hash=agent.password_hash,
            account_type=AccountType.PLAYER,
            parent_id=agent.id,
            created_by_id=None,
            status=AccountStatus.ACTIVE,
            must_change_password=False,
        )
        assigned_by_admin = TenantUser(
            tenant_id=tenant.id,
            username="legacy_admin",
            full_name="Legacy Admin",
            password_hash=agent.password_hash,
            account_type=AccountType.PLAYER,
            parent_id=uuid.UUID(users["boss"]["id"]),
            created_by_id=tenant.admin_id,
            status=AccountStatus.ACTIVE,
            must_change_password=False,
        )
        db.add_all([created_by_agent, referred, assigned_by_admin])
        await db.flush()
        await backfill_invitations(db)
        parents = dict(
            (
                await db.execute(
                    select(TenantUser.username, TenantUser.parent_id).where(
                        TenantUser.username.in_(["legacy_child", "legacy_ref", "legacy_admin"])
                    )
                )
            ).all()
        )

    assert parents["legacy_child"] == uuid.UUID(users["boss"]["id"])
    assert parents["legacy_ref"] == uuid.UUID(users["agent1"]["id"])
    assert parents["legacy_admin"] == uuid.UUID(users["boss"]["id"])
    assert await invite_names(clients["agent1"], q="legacy") == {"legacy_child", "legacy_ref"}
    assert await invite_names(clients["boss"], q="legacy") == set()
    assert "legacy_admin" not in await invite_names(clients["agent2"])
