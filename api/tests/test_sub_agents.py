"""Three-level commissions use the existing immutable snapshots and accounting workflows."""

import asyncio
import uuid
from decimal import Decimal as D

from sqlalchemy import select

from app.core.database import tenant_session
from app.modules.betting.models import Bet
from tests.conftest import PASSWORD, client_for
from tests.test_betting import ADMIN, declare, open_card, placed
from tests.test_commissions import COM, me


async def sub_network(tenant):
    master = await tenant.create_user("master_sub", "master_agent")
    agent = await tenant.create_user("agent_sub", "agent", parent_id=master["id"])
    boss = await tenant.login("master_sub")
    owner = await tenant.login("agent_sub")
    assert (await boss.put(f"{COM}/agents/{agent['id']}", json={"rate": "0.0500"})).status_code == 200
    sub = await tenant.create_user("sub_one", "sub_agent", parent_id=agent["id"], via=owner)
    child = await tenant.login("sub_one")
    await tenant.create_user("sub_player", "player", parent_id=sub["id"], via=child)
    player = await tenant.login("sub_player")
    assert (await owner.put(f"{COM}/sub-agents/{sub['id']}", json={"rate": "0.0300"})).status_code == 200
    return master, agent, sub, boss, owner, child, player


async def test_three_level_split_snapshots_settlement_and_reversal(tenant):
    _, [fight] = await open_card(tenant)
    master, agent, sub, boss, owner, child, player = await sub_network(tenant)
    await placed(player, fight["id"], "meron", "10000.00")
    # Changing future allocations cannot change the accepted 3.5 / 2 / 3 snapshot.
    assert (await owner.put(f"{COM}/sub-agents/{sub['id']}", json={"rate": "0.0100"})).status_code == 200
    await declare(tenant, fight["id"], "meron")
    for client, amount in ((boss, 350), (owner, 200), (child, 300)):
        overview = await me(client)
        assert D(overview["total_earned"]) == amount
        assert D(overview["own_balance"]) == 0  # commission is not a wallet credit
    assert D((await me(boss))["network_total"]) == 850
    assert D((await me(owner))["network_total"]) == 500
    assert D((await me(child))["network_total"]) == 300
    async with tenant_session(tenant.id) as db:
        bet = await db.scalar(
            select(Bet).where(
                Bet.tenant_id == tenant.id, Bet.user_id == uuid.UUID((await player.get("/api/v1/auth/me")).json()["id"])
            )
        )
        assert (bet.master_rate, bet.agent_rate, bet.sub_agent_rate) == (D(".035"), D(".02"), D(".03"))
        assert bet.sub_agent_id == uuid.UUID(sub["id"])
    again = await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/result", json={"outcome": "meron"})
    assert again.status_code in (409, 422)  # repeat cannot create another earnings posting
    assert D((await me(child))["total_earned"]) == 300
    # The existing correction path reverses every level, using the original rates.
    result = await tenant.admin.put(
        f"{ADMIN}/matches/{fight['id']}/result", json={"outcome": "draw", "reason": "Official correction"}
    )
    assert result.status_code in (200, 201), result.text
    for client in (boss, owner, child):
        assert D((await me(client))["total_earned"]) == 0
    history = (await child.get(f"{COM}/history")).json()
    assert history["total"] == 2
    assert {r["sub_agent_points"] for r in history["items"]} == {"300.00", "-300.00"}


async def test_sub_agent_permissions_limits_scope_and_history(tenant, other_tenant):
    _, [fight] = await open_card(tenant)
    master, agent, sub, boss, owner, child, player = await sub_network(tenant)
    other = await tenant.create_user("other_sub_agent", "agent", parent_id=master["id"])
    outsider = await tenant.login("other_sub_agent")
    for value in ("0.0600", "-0.0100", "0.03001"):
        response = await owner.put(f"{COM}/sub-agents/{sub['id']}", json={"rate": value})
        assert response.status_code == 422, response.text
    assert (await boss.put(f"{COM}/agents/{agent['id']}", json={"rate": "0.0200"})).status_code == 409
    assert (await outsider.put(f"{COM}/sub-agents/{sub['id']}", json={"rate": "0.0100"})).status_code == 404
    assert (await other_tenant.admin.get(f"/api/v1/admin/users/{sub['id']}")).status_code == 404
    assert (await child.put(f"{COM}/agents/{sub['id']}", json={"rate": "0"})).status_code == 403
    assert (
        await child.post(
            "/api/v1/admin/users",
            json={"username": "illegal_sub", "full_name": "Illegal", "account_type": "sub_agent", "password": PASSWORD},
        )
    ).status_code == 403
    outside_player = await tenant.create_user("outside_player", "player", parent_id=other["id"])
    assert (await child.get(f"/api/v1/admin/users/{outside_player['id']}")).status_code == 404
    for client, names in (
        (owner, {"sub_one", "sub_player"}),
        (child, {"sub_player"}),
        (boss, {"agent_sub", "sub_one", "sub_player", "other_sub_agent", "outside_player"}),
    ):
        rows = (await client.get("/api/v1/agent/users")).json()["items"]
        assert {r["username"] for r in rows} == names
    assert (await child.patch(f"/api/v1/admin/users/{outside_player['id']}", json={"parent_id": sub["id"]})).status_code == 404
    assert (await owner.get("/api/v1/agent/activity", params={"player_id": outside_player["id"]})).status_code == 404
    rate_history = (await owner.get(f"{COM}/rate-history")).json()["items"]
    assert any(r["username"] == "sub_one" for r in rate_history)
    assert (await outsider.get(f"{COM}/rate-history")).json()["total"] == 0
    await placed(player, fight["id"], "wala", 1000)
    assert (await child.get("/api/v1/agent/activity")).json()["items"][0]["amount"] == "1000.00"
    # Uplines may inspect descendants but cannot steal ownership.
    assert (await owner.patch(f"/api/v1/admin/users/{outside_player['id']}", json={"parent_id": agent["id"]})).status_code == 404


async def test_sub_agent_referrals_preserve_exact_ownership_and_no_further_agents(tenant, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "register_ip_max_per_hour", 10000)
    _, agent, sub, boss, owner, child, _ = await sub_network(tenant)
    anon = client_for(tenant.app, tenant.host)
    try:
        body = {
            "username": "invited_sub",
            "full_name": "Invited Sub",
            "password": PASSWORD,
            "ref": "agent_sub",
            "apply": "sub_agent",
        }
        r = await anon.post("/api/v1/auth/register", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["user"]["account_type"] == "sub_agent"
        assert r.json()["user"]["parent_id"] == agent["id"]
        body.update(username="invited_player", ref="sub_one")
        body.pop("apply")
        r = await anon.post("/api/v1/auth/register", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["user"]["parent_id"] == sub["id"]
        for apply in ("agent", "sub_agent"):
            assert (
                await anon.post("/api/v1/auth/register", json={**body, "username": "illegal_invite", "apply": apply})
            ).status_code == 422
    finally:
        await anon.aclose()


async def test_sub_agent_earnings_conversion_is_separate_and_idempotent(tenant):
    _, [fight] = await open_card(tenant)
    _, _, _, _, owner, child, player = await sub_network(tenant)
    await placed(player, fight["id"], "meron", 10000)
    await declare(tenant, fight["id"], "meron")
    body = {"amount": "100.00", "idempotency_key": str(uuid.uuid4())}
    results = await asyncio.gather(*(child.post(f"{COM}/earnings-moves", json=body) for _ in range(2)))
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    overview = await me(child)
    assert D(overview["total_earned"]) == 300
    assert D(overview["available_earnings"]) == 200
    assert D(overview["own_balance"]) == 100
    assert D((await me(owner))["total_earned"]) == 200


async def test_future_bets_use_new_rates_and_concurrent_allocations_cannot_overspend(tenant):
    _, [fight] = await open_card(tenant)
    _, agent, sub, boss, owner, child, player = await sub_network(tenant)
    await placed(player, fight["id"], "meron", 1000)
    assert (await owner.put(f"{COM}/sub-agents/{sub['id']}", json={"rate": "0.0100"})).status_code == 200
    await placed(player, fight["id"], "meron", 1000)
    await declare(tenant, fight["id"], "meron")
    assert D((await me(child))["total_earned"]) == 40
    assert D((await me(owner))["total_earned"]) == 60
    assert D((await me(boss))["total_earned"]) == 70
    responses = await asyncio.gather(
        boss.put(f"{COM}/agents/{agent['id']}", json={"rate": "0.0400"}),
        owner.put(f"{COM}/sub-agents/{sub['id']}", json={"rate": "0.0450"}),
    )
    assert sorted(r.status_code for r in responses) in ([200, 409], [200, 422])
    agent_rate = D((await me(owner))["rate"])
    sub_rate = D((await me(child))["rate"])
    assert sub_rate <= agent_rate
    assert D((await me(child))["total_earned"]) == 40


async def test_draw_and_cancel_do_not_credit_any_level_of_sub_agent_network(tenant):
    _, fights = await open_card(tenant, fights=2)
    _, _, _, boss, owner, child, player = await sub_network(tenant)
    await placed(player, fights[0]["id"], "meron", 1000)
    await declare(tenant, fights[0]["id"], "draw")
    assert (await tenant.admin.post(f"{ADMIN}/matches/{fights[1]['id']}/betting/open")).status_code == 200
    await placed(player, fights[1]["id"], "wala", 1000)
    assert (
        await tenant.admin.post(f"{ADMIN}/matches/{fights[1]['id']}/cancel", json={"reason": "Cancelled fight"})
    ).status_code == 200
    for client in (boss, owner, child):
        assert D((await me(client))["total_earned"]) == 0


async def test_sub_agent_wallet_transfers_and_cashout_reuse_authorized_workflows(tenant):
    _, agent, sub, _, owner, child, _ = await sub_network(tenant)
    grant = await tenant.admin.post(
        "/api/v1/admin/balance/account-grants",
        json={"target_user_id": agent["id"], "amount": "100.00", "idempotency_key": str(uuid.uuid4())},
    )
    assert grant.status_code == 201, grant.text
    send = {"method": "send", "recipient_id": sub["id"], "amount": "100.00", "idempotency_key": str(uuid.uuid4())}
    first = await owner.post(f"{COM}/loads", json=send)
    assert first.status_code == 201, first.text
    assert (await owner.post(f"{COM}/loads", json=send)).json()["replayed"] is True
    assert D((await me(child))["own_balance"]) == 100
    body = {"amount": "60.00", "purpose": "cashout", "idempotency_key": str(uuid.uuid4())}
    request = await child.post(f"{COM}/balance-requests", json=body)
    assert request.status_code == 201, request.text
    decision = await tenant.admin.post(f"{COM}/approvals/{request.json()['id']}", json={"decision": "approve"})
    assert decision.status_code == 200, decision.text
    assert D((await me(child))["own_balance"]) == 40
    ledger = (await child.get("/api/v1/portal/betting/entries")).json()["items"]
    assert {row["username"] for row in ledger} == {"sub_one"}
    assert {D(row["delta"]) for row in ledger} == {D("100"), D("-60")}

    assert D((await me(child))["total_earned"]) == 0


async def test_sub_agents_cannot_reassign_even_their_own_players(tenant):
    _, agent, sub, _, _, child, player = await sub_network(tenant)
    player_id = (await player.get("/api/v1/auth/me")).json()["id"]
    response = await child.patch(f"/api/v1/admin/users/{player_id}", json={"parent_id": agent["id"]})
    assert response.status_code == 403, response.text
    assert (await child.get(f"/api/v1/admin/users/{player_id}")).json()["parent_id"] == sub["id"]
