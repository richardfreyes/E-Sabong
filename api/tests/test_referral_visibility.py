"""Referral hierarchy and player visibility: Master Agent -> Agent -> Player.

Every player list is scoped to the accounts the caller owns directly. An agent sees the players who
registered through its referral link. A master agent sees its agents (and any player it invited
itself), never the players of its agents. Commission totals still cover the whole network.
"""

import uuid
from decimal import Decimal as D

import pytest

from app.core.config import get_settings
from tests.conftest import PASSWORD, client_for

ADMIN = "/api/v1/admin"
AGENT = "/api/v1/agent"
COM = "/api/v1/commissions"
BAL = f"{ADMIN}/balance"


async def grant(admin, user_id: str, amount: str) -> None:  # type: ignore[no-untyped-def]
    response = await admin.post(
        f"{BAL}/account-grants",
        json={"target_user_id": user_id, "amount": amount, "idempotency_key": f"grant-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 201, response.text


async def balance_of(admin, user_id: str) -> D:  # type: ignore[no-untyped-def]
    response = await admin.get(f"{BAL}/accounts/{user_id}")
    assert response.status_code == 200, response.text
    return D(response.json()["account"]["balance"])


@pytest.fixture(autouse=True)
def _many_signups(monkeypatch):  # type: ignore[no-untyped-def]
    # Every referral sign-up here comes from the test client's single address.
    monkeypatch.setattr(get_settings(), "register_ip_max_per_hour", 10_000)


async def register(app, host: str, username: str, ref: str, apply: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    body = {"username": username, "full_name": username.title(), "password": PASSWORD, "ref": ref}
    if apply:
        body["apply"] = apply
    async with client_for(app, host) as c:
        r = await c.post("/api/v1/auth/register", json=body)
        assert r.status_code == 201, r.text
        return r.json()["user"]


async def names(client, path: str, **params) -> set[str]:  # type: ignore[no-untyped-def]
    r = await client.get(path, params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    rows = body["items"] if isinstance(body, dict) else body
    return {row["username"] for row in rows}


async def network(app, tenant):  # type: ignore[no-untyped-def]
    """Steps 1-3: the master agent brings in agent1 (Agent Application Link) and agent2 (created in
    the portal); agent1 refers ply1 and ply2, agent2 refers ply3, the master agent refers ply4."""
    boss = await tenant.create_user("boss", "master_agent")
    master = await tenant.login("boss")
    agent1 = await register(app, tenant.host, "agent1", "boss", apply="agent")
    agent2 = await tenant.create_user("agent2", "agent", via=master)
    users = {"boss": boss, "agent1": agent1, "agent2": agent2}
    for name, ref in (("ply1", "agent1"), ("ply2", "agent1"), ("ply3", "agent2"), ("ply4", "boss")):
        users[name] = await register(app, tenant.host, name, ref)
    clients = {"boss": master, "agent1": await tenant.login("agent1"), "agent2": await tenant.login("agent2")}
    return users, clients


async def test_referrals_assign_players_to_the_referring_agent(app, tenant):
    users, _ = await network(app, tenant)
    parent = {name: user["parent_id"] for name, user in users.items()}
    assert parent["agent1"] == parent["agent2"] == users["boss"]["id"]
    assert users["agent1"]["account_type"] == users["agent2"]["account_type"] == "agent"
    assert parent["ply1"] == parent["ply2"] == users["agent1"]["id"]
    assert parent["ply3"] == users["agent2"]["id"]
    assert parent["ply4"] == users["boss"]["id"]
    # An agent's link cannot sign up agents, and an unknown link is refused rather than unassigned.
    async with client_for(app, tenant.host) as c:
        body = {"username": "sneaky", "full_name": "S", "password": PASSWORD, "ref": "agent1", "apply": "agent"}
        assert (await c.post("/api/v1/auth/register", json=body)).json()["error"]["code"] == "agent_application_invalid"
        body = {"username": "lost", "full_name": "L", "password": PASSWORD, "ref": "nobody"}
        assert (await c.post("/api/v1/auth/register", json=body)).json()["error"]["code"] == "invalid_referral"


async def test_each_agent_has_its_own_player_list(app, tenant):
    users, c = await network(app, tenant)
    # Steps 4-5: each agent sees exactly the players it referred, with registration details.
    assert await names(c["agent1"], f"{AGENT}/users") == {"ply1", "ply2"}
    assert await names(c["agent2"], f"{AGENT}/users") == {"ply3"}
    row = (await c["agent1"].get(f"{AGENT}/users", params={"q": "ply1"})).json()["items"][0]
    assert (row["account_type"], row["status"], row["via_referral"], row["direct"]) == ("player", "active", True, True)
    assert row["created_at"] and row["full_name"] == "Ply1"
    assert (await c["agent1"].get(f"{AGENT}/users/summary")).json()["players"]["active"] == 2
    assert await names(c["agent1"], f"{ADMIN}/users") == {"ply1", "ply2"}
    assert await names(c["agent2"], f"{ADMIN}/users") == {"ply3"}
    # No agent reaches another agent's player by id.
    for path in (
        f"{ADMIN}/users/{users['ply1']['id']}",
        f"{BAL}/accounts/{users['ply1']['id']}",
    ):
        assert (await c["agent2"].get(path)).status_code == 404, path
    assert (await c["agent2"].post(f"{ADMIN}/users/{users['ply1']['id']}/suspend")).status_code == 404


async def test_master_agent_sees_agents_but_not_their_players(app, tenant):
    users, c = await network(app, tenant)
    boss = c["boss"]
    hidden = {"ply1", "ply2", "ply3"}
    # Step 6: My Agents.
    assert await names(boss, f"{AGENT}/users", kind="agent") == {"agent1", "agent2"}
    # Step 7: My Players holds only the master agent's own player.
    assert await names(boss, f"{AGENT}/users", kind="player") == {"ply4"}
    assert await names(boss, f"{AGENT}/users") == {"agent1", "agent2", "ply4"}
    assert await names(boss, f"{AGENT}/users", q="ply") == {"ply4"}
    summary = (await boss.get(f"{AGENT}/users/summary")).json()
    assert summary["players"]["active"] == 1 and summary["agents"]["active"] == 2

    # The same rule on every API that lists or opens accounts, not just the screen.
    assert await names(boss, f"{ADMIN}/users") == {"agent1", "agent2", "ply4"}
    assert await names(boss, f"{ADMIN}/users", parent_id=users["agent1"]["id"]) == set()
    assert await names(boss, f"{ADMIN}/hierarchy") == {"agent1", "agent2", "ply4"}
    assert all(n["child_count"] == 0 for n in (await boss.get(f"{ADMIN}/hierarchy")).json())
    assert (await boss.get(f"{ADMIN}/hierarchy", params={"parent_id": users["agent1"]["id"]})).status_code == 404
    assert await names(boss, f"{ADMIN}/users/{users['agent1']['id']}/downline") == set()
    assert await names(boss, f"{ADMIN}/users/{users['boss']['id']}/downline") == {"agent1", "agent2", "ply4"}
    assert await names(boss, f"{BAL}/accounts") == {"agent1", "agent2", "ply4"}
    for name in hidden:
        uid = users[name]["id"]
        for path in (f"{ADMIN}/users/{uid}", f"{BAL}/accounts/{uid}"):
            assert (await boss.get(path)).status_code == 404, path
        for action in ("suspend", "activate", "reset-password"):
            assert (await boss.post(f"{ADMIN}/users/{uid}/{action}")).status_code == 404, action
    # The master agent still sees and manages its own agents.
    assert (await boss.get(f"{ADMIN}/users/{users['agent1']['id']}")).status_code == 200
    assert (await boss.get(f"{BAL}/accounts/{users['agent2']['id']}")).status_code == 200
    # Network counts stay available without the player records.
    rows = {r["username"]: r for r in (await boss.get(f"{COM}/agents")).json()}
    assert (rows["agent1"]["players"], rows["agent2"]["players"]) == (2, 1)


async def test_visibility_does_not_change_balances_or_relationships(app, tenant):
    users, c = await network(app, tenant)
    await grant(tenant.admin, users["ply1"]["id"], "25.00")
    await grant(tenant.admin, users["agent1"]["id"], "40.00")
    # Step 8: balances, earnings and the hierarchy itself are untouched by who can see whom.
    earned = (await c["boss"].get(f"{COM}/me")).json()["total_earned"]
    assert await names(c["boss"], f"{AGENT}/users", kind="player") == {"ply4"}
    assert await balance_of(tenant.admin, users["ply1"]["id"]) == D("25.00")
    assert await balance_of(tenant.admin, users["agent1"]["id"]) == D("40.00")
    row = (await c["agent1"].get(f"{AGENT}/users", params={"q": "ply1"})).json()["items"][0]
    assert D(row["balance"]) == D("25.00")
    assert (await c["boss"].get(f"{COM}/me")).json()["total_earned"] == earned
    full = {u["username"]: u for u in (await tenant.admin.get(f"{ADMIN}/users", params={"page_size": 100})).json()["items"]}
    assert full["ply1"]["parent_id"] == users["agent1"]["id"] and full["ply3"]["parent_id"] == users["agent2"]["id"]
    assert full["agent1"]["parent_id"] == full["agent2"]["parent_id"] == users["boss"]["id"]
    # Administrators keep the whole tree.
    assert await names(tenant.admin, f"{ADMIN}/hierarchy", parent_id=users["agent1"]["id"]) == {"ply1", "ply2"}


async def test_no_cross_tenant_access(app, tenant, other_tenant):
    users, _ = await network(app, tenant)
    await other_tenant.create_user("boss", "master_agent")
    far = await other_tenant.login("boss")
    assert (await far.get(f"{AGENT}/users")).json()["items"] == []
    for name in ("agent1", "ply1", "ply4"):
        uid = users[name]["id"]
        for path in (f"{ADMIN}/users/{uid}", f"{BAL}/accounts/{uid}"):
            assert (await far.get(path)).status_code == 404, path
    # A master agent elsewhere in the same tenant sees nothing of this network either.
    await tenant.create_user("rival", "master_agent")
    rival = await tenant.login("rival")
    assert (await rival.get(f"{AGENT}/users")).json()["items"] == []
    assert (await rival.get(f"{ADMIN}/users/{users['agent1']['id']}")).status_code == 404
