"""Master agent and agent commissions: rates, snapshot, settlement on official results, earnings
kept apart from wallets, corrections, and who may see or change what."""

import asyncio
from decimal import Decimal as D

from httpx_ws import aconnect_ws

from tests.test_betting import ADMIN, START, balance, declare, open_card, placed
from tests.test_betting_staff import token
from tests.test_declarator import proposal, setup_desk
from tests.test_websocket import _receive, ws_client

COM = "/api/v1/commissions"


async def network(tenant):  # type: ignore[no-untyped-def]
    """MasterAgent (8.5% default) -> agent1 (3%), agent2 (5%); p1 under agent1, p2 under agent2,
    p3 directly under the master agent."""
    master = await tenant.create_user("boss", "master_agent")
    a1 = await tenant.create_user("agent1", "agent", parent_id=master["id"])
    a2 = await tenant.create_user("agent2", "agent", parent_id=master["id"])
    await tenant.create_user("ply1", "player", parent_id=a1["id"])
    await tenant.create_user("ply2", "player", parent_id=a2["id"])
    await tenant.create_user("ply3", "player", parent_id=master["id"])
    clients = {name: await tenant.login(name) for name in ("boss", "agent1", "agent2", "ply1", "ply2", "ply3")}
    for agent, rate in ((a1, "0.0300"), (a2, "0.0500")):
        r = await clients["boss"].put(f"{COM}/agents/{agent['id']}", json={"rate": rate})
        assert r.status_code == 200, r.text
    return clients, {"boss": master, "agent1": a1, "agent2": a2}


async def me(client) -> dict:  # type: ignore[no-untyped-def]
    r = await client.get(f"{COM}/me")
    assert r.status_code == 200, r.text
    return r.json()


async def test_reference_examples_winning_and_losing_bets_earn_alike(app, tenant):
    _, [fight] = await open_card(tenant)
    c, users = await network(tenant)
    rows = {r["username"]: r for r in (await c["boss"].get(f"{COM}/agents")).json()}
    assert (rows["agent1"]["agent_rate"], rows["agent1"]["master_rate"]) == ("0.0300", "0.0550")
    assert (rows["agent2"]["agent_rate"], rows["agent2"]["master_rate"]) == ("0.0500", "0.0350")
    assert rows["agent1"]["players"] == 1

    await placed(c["ply1"], fight["id"], "meron", 1000)  # wins
    await placed(c["ply2"], fight["id"], "wala", 1000)  # loses
    await placed(c["ply3"], fight["id"], "meron", 100)  # direct player of the master agent
    # Nothing is earned before the official result.
    assert D((await me(c["agent1"]))["total_earned"]) == 0

    async with ws_client(app, tenant.host) as client:
        async with aconnect_ws(f"/api/ws?token={token(c['agent1'])}", client) as agent_ws:
            await _receive(agent_ws)
            await asyncio.sleep(0.2)
            await declare(tenant, fight["id"], "meron")
            frame = await _receive(agent_ws)
            while frame["type"] != "commission.updated":
                frame = await _receive(agent_ws)
            assert frame["data"]["match_id"] == fight["id"]

    a1, a2, boss = await me(c["agent1"]), await me(c["agent2"]), await me(c["boss"])
    assert (a1["role"], D(a1["rate"]), D(a1["total_earned"]), D(a1["this_month"])) == ("agent", D("0.03"), D(30), D(30))
    assert D(a2["total_earned"]) == D(50)  # the losing bet earns on the same basis
    # 55 (from agent1's player) + 35 (from agent2's player) + 8.50 (8.5% of the direct player's 100)
    assert (boss["role"], D(boss["allocation"]), D(boss["total_earned"])) == ("master_agent", D("0.085"), D("98.50"))
    assert D(boss["network_total"]) == D("178.50")  # 30 + 50 + 98.50

    # Earnings never reach a wallet: players' balances move by the bet result only.
    assert await balance(c["ply1"]) == START - 1000 + D("1830.00")
    assert await balance(c["ply2"]) == START - 1000

    history = (await c["boss"].get(f"{COM}/history")).json()
    assert history["total"] == 3
    by_player = {r["player_username"]: r for r in history["items"]}
    p1 = by_player["ply1"]
    assert (p1["agent_username"], D(p1["bet_amount"]), D(p1["agent_points"]), D(p1["master_points"])) == ("agent1", 1000, 30, 55)
    assert (p1["outcome"], p1["status"], D(p1["agent_rate"]), D(p1["master_rate"])) == ("meron", "earned", D("0.03"), D("0.055"))
    assert by_player["ply3"]["agent_username"] is None and D(by_player["ply3"]["master_points"]) == D("8.50")
    # An agent sees only their own rows and never the master agent's share.
    [own] = (await c["agent1"].get(f"{COM}/history")).json()["items"]
    assert own["player_username"] == "ply1" and own["master_points"] is None and own["master_rate"] is None

    per_agent = (await c["boss"].get(f"{COM}/history", params={"agent_id": users["agent2"]["id"]})).json()["items"]
    assert [r["player_username"] for r in per_agent] == ["ply2"]
    players = {r["label"]: r for r in (await c["boss"].get(f"{COM}/breakdown", params={"by": "player"})).json()}
    assert (D(players["ply2"]["eligible_points"]), D(players["ply2"]["agent_points"]), D(players["ply2"]["master_points"])) == (
        1000,
        50,
        35,
    )
    [fight_row] = (await c["boss"].get(f"{COM}/breakdown", params={"by": "fight"})).json()
    assert fight_row["bets"] == 3 and D(fight_row["eligible_points"]) == 2100
    rows = {r["username"]: r for r in (await c["boss"].get(f"{COM}/agents")).json()}
    assert (D(rows["agent1"]["agent_earned"]), D(rows["agent1"]["master_earned"])) == (30, 55)


async def test_draw_and_cancel_earn_nothing(tenant):
    _, [first, second] = await open_card(tenant, fights=2)
    c, _ = await network(tenant)
    await placed(c["ply1"], first["id"], "meron", 1000)
    await placed(c["ply1"], first["id"], "draw", 100)
    await declare(tenant, first["id"], "draw")
    assert (await tenant.admin.post(f"{ADMIN}/matches/{second['id']}/betting/open")).status_code == 200
    await placed(c["ply1"], second["id"], "wala", 1000)
    r = await tenant.admin.post(f"{ADMIN}/matches/{second['id']}/cancel", json={"reason": "Injury"})
    assert r.status_code == 200, r.text
    assert D((await me(c["agent1"]))["total_earned"]) == 0 and D((await me(c["boss"]))["total_earned"]) == 0
    assert (await c["boss"].get(f"{COM}/history")).json()["total"] == 0


async def test_corrections_reverse_and_restore_without_duplicates(tenant):
    _, [fight] = await open_card(tenant)
    c, _ = await network(tenant)
    await placed(c["ply1"], fight["id"], "meron", 1000)
    await declare(tenant, fight["id"], "meron")
    # The same result again is refused and earns nothing twice.
    again = await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/result", json={"outcome": "meron"})
    assert again.status_code in (409, 422)
    assert D((await me(c["agent1"]))["total_earned"]) == 30

    async def correct(outcome: str) -> None:
        r = await tenant.admin.put(f"{ADMIN}/matches/{fight['id']}/result", json={"outcome": outcome, "reason": "Review"})
        assert r.status_code == 200, r.text

    await correct("wala")  # still a winner declared: same commission, no new entry
    assert (await c["boss"].get(f"{COM}/history")).json()["total"] == 1
    await correct("draw")  # no commission on a draw: reversed
    assert D((await me(c["agent1"]))["total_earned"]) == 0 and D((await me(c["boss"]))["total_earned"]) == 0
    await correct("meron")
    assert D((await me(c["agent1"]))["total_earned"]) == 30 and D((await me(c["boss"]))["total_earned"]) == 55
    statuses = [r["status"] for r in (await c["boss"].get(f"{COM}/history")).json()["items"]]
    assert statuses == ["earned", "reversed", "earned"]
    logs = (await tenant.admin.get(f"{ADMIN}/audit-logs", params={"action": "commission.reversed"})).json()["items"]
    assert len(logs) == 1 and logs[0]["changes"]["agent_points"] == "-30.00"


async def test_rate_changes_apply_only_to_later_bets(tenant):
    _, [fight] = await open_card(tenant)
    c, users = await network(tenant)
    await placed(c["ply1"], fight["id"], "meron", 1000)  # at 3% / 5.5%
    r = await c["boss"].put(f"{COM}/agents/{users['agent1']['id']}", json={"rate": "0.0400"})
    assert r.status_code == 200 and (r.json()["agent_rate"], r.json()["master_rate"]) == ("0.0400", "0.0450")
    await placed(c["ply1"], fight["id"], "meron", 1000)  # at 4% / 4.5%
    await declare(tenant, fight["id"], "meron")
    assert D((await me(c["agent1"]))["total_earned"]) == 70
    assert D((await me(c["boss"]))["total_earned"]) == 100
    assert D((await me(c["agent1"]))["rate"]) == D("0.04")
    logs = (await tenant.admin.get(f"{ADMIN}/audit-logs", params={"action": "commission.agent_rate_changed"})).json()["items"]
    assert any(e["changes"]["agent_rate"] == {"from": "0.0300", "to": "0.0400"} for e in logs)


async def test_ghost_points_earn_nothing(tenant):
    _, event, fight, judge = await setup_desk(tenant)
    r = await tenant.admin.put(f"{ADMIN}/betting/settings", json={"enabled": True, "starting_balance": "5000.00"})
    assert r.status_code == 200
    assert (await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")).status_code == 200
    c, _ = await network(tenant)
    await placed(c["ply1"], fight["id"], "wala", 1000)
    body = {"request_id": "1c7e6f1a-3b2d-4d1e-8f0a-5a6b7c8d9e0f", "side": "meron", "amount": "500000.00"}
    assert (await judge.post(f"/api/v1/declarator/matches/{fight['id']}/dummy-bets", json=body)).status_code == 200
    decl = (await judge.post(f"/api/v1/declarator/matches/{fight['id']}/declarations", json=proposal("wala"))).json()
    assert decl["status"] == "finalized"
    assert D((await me(c["agent1"]))["total_earned"]) == 30
    assert D((await me(c["boss"]))["network_total"]) == 85  # 8.5% of the one real 1,000 bet
    assert event


async def test_only_the_master_agent_sets_their_agents_rates(tenant, other_tenant):
    c, users = await network(tenant)
    agent1 = users["agent1"]["id"]
    # An agent cannot change any rate, their own included.
    r = await c["agent1"].put(f"{COM}/agents/{agent1}", json={"rate": "0.0800"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "commission_master_only"
    over = await c["boss"].put(f"{COM}/agents/{agent1}", json={"rate": "0.0900"})
    assert over.status_code == 422 and over.json()["error"]["code"] == "rate_above_allocation"
    assert (await c["boss"].put(f"{COM}/agents/{agent1}", json={"rate": "0.6"})).status_code == 422
    # Another master agent cannot touch this network.
    await tenant.create_user("rival", "master_agent")
    rival = await tenant.login("rival")
    assert (await rival.put(f"{COM}/agents/{agent1}", json={"rate": "0.01"})).status_code == 404
    assert (await rival.get(f"{COM}/agents")).json() == []
    # Players see no commission data.
    assert (await c["ply1"].get(f"{COM}/me")).status_code == 403
    assert (await c["ply1"].get(f"{COM}/history")).status_code == 403
    # Other tenants never resolve these accounts.
    await other_tenant.create_user("boss2", "master_agent")
    boss2 = await other_tenant.login("boss2")
    assert (await boss2.put(f"{COM}/agents/{agent1}", json={"rate": "0.01"})).status_code == 404
    assert (await boss2.get(f"{COM}/history")).json()["total"] == 0

    # Administrators set the master agent's allocation, never below an agent's rate.
    master = users["boss"]["id"]
    low = await tenant.admin.put(f"{COM}/masters/{master}", json={"allocation": "0.0400"})
    assert low.status_code == 409 and low.json()["error"]["code"] == "allocation_below_agent_rate"
    ok = await tenant.admin.put(f"{COM}/masters/{master}", json={"allocation": "0.1000"})
    assert ok.status_code == 200 and ok.json()["allocation"] == "0.1000"
    assert (await c["boss"].put(f"{COM}/masters/{master}", json={"allocation": "0.2"})).status_code == 403
    rows = {r["username"]: r for r in (await c["boss"].get(f"{COM}/agents")).json()}
    assert rows["agent1"]["master_rate"] == "0.0700"
