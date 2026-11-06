"""My Users: master agents and agents see their own downline with status, presence, balance and
rate; agents may block (suspend) their downline; only administrators ban or lift a ban."""

import asyncio

from httpx_ws import aconnect_ws

from tests.conftest import PASSWORD, client_for
from tests.test_betting_staff import token
from tests.test_websocket import _receive, ws_client

USERS = "/api/v1/agent/users"
ADMIN = "/api/v1/admin"


async def test_my_users_scope_status_presence_and_bans(app, tenant):
    boss_user = await tenant.create_user("boss", "master_agent")
    agent_user = await tenant.create_user("agent1", "agent", parent_id=boss_user["id"])
    ply1 = await tenant.create_user("ply1", "player", parent_id=agent_user["id"])
    ply3 = await tenant.create_user("ply3", "player", parent_id=boss_user["id"])
    await tenant.create_user("stranger", "player")
    boss, agent = await tenant.login("boss"), await tenant.login("agent1")
    assert (await boss.put(f"/api/v1/commissions/agents/{agent_user['id']}", json={"rate": "0.0300"})).status_code == 200
    # A referral sign-up lands in the agent's list, marked as such.
    async with client_for(app, tenant.host) as c:
        body = {"username": "refkid", "full_name": "Ref Kid", "password": PASSWORD, "ref": "agent1"}
        assert (await c.post("/api/v1/auth/register", json=body)).status_code == 201

    rows = {r["username"]: r for r in (await boss.get(USERS)).json()["items"]}
    assert set(rows) == {"agent1", "ply1", "ply3", "refkid"}  # the whole downline, never "stranger"
    assert (rows["agent1"]["account_type"], rows["agent1"]["rate"]) == ("agent", "0.0300")
    assert rows["agent1"]["balance"] == "0.00"
    assert (rows["ply3"]["direct"], rows["ply1"]["direct"], rows["ply1"]["parent_username"]) == (True, False, "agent1")
    assert rows["refkid"]["via_referral"] is True and rows["ply1"]["via_referral"] is False
    assert (await boss.get(USERS, params={"kind": "agent"})).json()["total"] == 1
    assert (await boss.get(USERS, params={"q": "ply"})).json()["total"] == 2
    mine = {r["username"] for r in (await agent.get(USERS)).json()["items"]}
    assert mine == {"ply1", "refkid"}
    summary = (await agent.get(f"{USERS}/summary")).json()
    assert summary["players"] == {"active": 2, "blocked": 0, "banned": 0, "online": 0}
    assert summary["agents"] == {"active": 0, "blocked": 0, "banned": 0, "online": 0}
    assert (await boss.get(f"{USERS}/summary")).json()["agents"]["active"] == 1

    # Online: a connected account shows up for its upline.
    player = await tenant.login("ply1")
    async with ws_client(app, tenant.host) as client, aconnect_ws(f"/api/ws?token={token(player)}", client) as ws:
        await _receive(ws)
        await asyncio.sleep(0.1)
        online = (await agent.get(USERS, params={"state": "online"})).json()["items"]
        assert [r["username"] for r in online] == ["ply1"] and online[0]["online"] is True
        assert (await agent.get(f"{USERS}/summary")).json()["players"]["online"] == 1

    # Block: an agent suspends their own player, who can no longer sign in.
    assert (await agent.post(f"{ADMIN}/users/{ply1['id']}/suspend")).status_code == 200
    assert (await agent.get(f"{USERS}/summary")).json()["players"]["blocked"] == 1
    assert [r["username"] for r in (await agent.get(USERS, params={"state": "blocked"})).json()["items"]] == ["ply1"]
    async with client_for(app, tenant.host) as c:
        r = await c.post("/api/v1/auth/login", json={"username": "ply1", "password": PASSWORD})
        assert r.status_code == 403 and r.json()["error"]["code"] == "account_suspended"
    assert (await agent.post(f"{ADMIN}/users/{ply1['id']}/activate")).status_code == 200
    # An agent cannot touch a player outside their downline.
    assert (await agent.post(f"{ADMIN}/users/{ply3['id']}/suspend")).status_code in (403, 404)

    # Ban: administrators only, and an upline cannot lift it.
    assert (await boss.post(f"{ADMIN}/users/{ply3['id']}/ban")).status_code == 403
    assert (await tenant.admin.post(f"{ADMIN}/users/{ply3['id']}/ban")).status_code == 200
    assert (await boss.get(f"{USERS}/summary")).json()["players"]["banned"] == 1
    lift = await boss.post(f"{ADMIN}/users/{ply3['id']}/activate")
    assert lift.status_code == 403 and lift.json()["error"]["code"] == "ban_admin_only"
    async with client_for(app, tenant.host) as c:
        r = await c.post("/api/v1/auth/login", json={"username": "ply3", "password": PASSWORD})
        assert r.status_code == 403 and r.json()["error"]["code"] == "account_banned"
    assert (await tenant.admin.post(f"{ADMIN}/users/{ply3['id']}/activate")).status_code == 200

    # Players and other roles have no My Users.
    assert (await player.get(USERS)).status_code in (401, 403)
    stranger = await tenant.login("stranger")
    assert (await stranger.get(USERS)).status_code == 403
