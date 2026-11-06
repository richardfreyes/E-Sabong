"""Master agents and agents move existing wallet points to players in their hierarchy.

A transfer debits the sender's play wallet and credits the player's by the same amount. Commission
earnings are never posted, a repeated request does not post twice, and a player outside the
caller's hierarchy or tenant is not found.
"""

import uuid
from decimal import Decimal as D

from httpx_ws import aconnect_ws

from tests.test_betting_staff import drain, token
from tests.test_websocket import _receive, ws_client

AGENT = "/api/v1/agent"
ADMIN = "/api/v1/admin"
BAL = f"{ADMIN}/balance"


def key() -> str:
    return f"xfr-{uuid.uuid4().hex}"


async def grant(admin, user_id: str, amount: str) -> None:
    response = await admin.post(
        f"{BAL}/account-grants",
        json={"target_user_id": user_id, "amount": amount, "idempotency_key": key()},
    )
    assert response.status_code == 201, response.text


async def balance_of(admin, user_id: str) -> D:
    response = await admin.get(f"{BAL}/accounts/{user_id}")
    assert response.status_code == 200, response.text
    return D(response.json()["account"]["balance"])


async def test_hierarchy_transfer_moves_wallet_points_once(app, tenant, other_tenant):
    boss_user = await tenant.create_user("boss", "master_agent")
    agent_user = await tenant.create_user("agent1", "agent", parent_id=boss_user["id"])
    ply1 = await tenant.create_user("ply1", "player", parent_id=agent_user["id"])
    ply3 = await tenant.create_user("ply3", "player", parent_id=boss_user["id"])
    stranger = await tenant.create_user("stranger", "player")
    blocked = await tenant.create_user("blocked1", "player", parent_id=agent_user["id"])
    outsider = await other_tenant.create_user("outsider", "player")
    await grant(tenant.admin, boss_user["id"], "100.00")
    await grant(tenant.admin, agent_user["id"], "30.00")
    boss, agent = await tenant.login("boss"), await tenant.login("agent1")
    player = await tenant.login("ply1")

    earned = (await agent.get("/api/v1/commissions/me")).json()["total_earned"]
    preview = (await agent.get(f"{AGENT}/users/{ply1['id']}/transfer")).json()
    assert preview["player_username"] == "ply1"
    assert (D(preview["player_balance"]), D(preview["sender_balance"])) == (D("0.00"), D("30.00"))
    assert D((await agent.get(f"{AGENT}/wallet")).json()["balance"]) == D("30.00")

    # An agent funds only a player assigned directly to them.
    async with ws_client(app, tenant.host) as socket, aconnect_ws(f"/api/ws?token={token(player)}", socket) as ws:
        assert (await _receive(ws))["type"] == "connected"
        first = await agent.post(
            f"{AGENT}/users/{ply1['id']}/transfers",
            json={"amount": "12.50", "idempotency_key": "agent-ply1-once"},
        )
        assert first.status_code == 200, first.text
        body = first.json()
        frames = await drain(ws, "wallet.updated")
    assert body["replayed"] is False and body["status"] == "applied"
    assert (body["sender_username"], body["sender_role"], body["player_username"]) == ("agent1", "agent", "ply1")
    assert (D(body["amount"]), D(body["sender_balance_before"]), D(body["sender_balance_after"])) == (
        D("12.50"),
        D("30.00"),
        D("17.50"),
    )
    assert (D(body["player_balance_before"]), D(body["player_balance_after"])) == (D("0.00"), D("12.50"))
    assert D(frames[-1]["data"]["balance"]) == D("12.50")
    assert await balance_of(tenant.admin, agent_user["id"]) == D("17.50")
    assert await balance_of(tenant.admin, ply1["id"]) == D("12.50")

    again = await agent.post(
        f"{AGENT}/users/{ply1['id']}/transfers",
        json={"amount": "12.50", "idempotency_key": "agent-ply1-once"},
    )
    assert again.status_code == 200 and again.json()["id"] == body["id"] and again.json()["replayed"] is True
    assert await balance_of(tenant.admin, agent_user["id"]) == D("17.50")
    conflict = await agent.post(
        f"{AGENT}/users/{ply1['id']}/transfers",
        json={"amount": "1.00", "idempotency_key": "agent-ply1-once"},
    )
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"

    short = await agent.post(f"{AGENT}/users/{ply1['id']}/transfers", json={"amount": "31.00", "idempotency_key": key()})
    assert short.status_code == 422 and short.json()["error"]["code"] == "insufficient_balance"
    for amount in ("0", "-1", "1.234", "nope"):
        bad = await agent.post(f"{AGENT}/users/{ply1['id']}/transfers", json={"amount": amount, "idempotency_key": key()})
        assert bad.status_code == 422, amount
    for hidden in (ply3, stranger, outsider):
        refused = await agent.post(
            f"{AGENT}/users/{hidden['id']}/transfers", json={"amount": "1.00", "idempotency_key": key()}
        )
        assert refused.status_code == 404
    player_call = await player.post(
        f"{AGENT}/users/{ply1['id']}/transfers", json={"amount": "1.00", "idempotency_key": key()}
    )
    assert player_call.status_code == 403
    assert (await agent.get("/api/v1/commissions/me")).json()["total_earned"] == earned

    # A master agent funds a direct player and a player under a subordinate agent.
    direct = await boss.post(f"{AGENT}/users/{ply3['id']}/transfers", json={"amount": "40.00", "idempotency_key": key()})
    assert direct.status_code == 200, direct.text
    nested = await boss.post(f"{AGENT}/users/{ply1['id']}/transfers", json={"amount": "7.50", "idempotency_key": key()})
    assert nested.status_code == 200, nested.text
    assert await balance_of(tenant.admin, boss_user["id"]) == D("52.50")
    assert await balance_of(tenant.admin, ply3["id"]) == D("40.00")
    assert await balance_of(tenant.admin, ply1["id"]) == D("20.00")

    # A master agent also funds an agent in the hierarchy. An agent cannot fund an agent.
    peer = await tenant.create_user("peeragent", "agent", parent_id=boss_user["id"])
    to_agent = await boss.post(
        f"{AGENT}/users/{agent_user['id']}/transfers", json={"amount": "10.00", "idempotency_key": key()}
    )
    assert to_agent.status_code == 200, to_agent.text
    assert to_agent.json()["player_username"] == "agent1"
    assert (D(to_agent.json()["sender_balance_after"]), D(to_agent.json()["player_balance_after"])) == (
        D("42.50"),
        D("27.50"),
    )
    to_peer = await boss.post(
        f"{AGENT}/users/{peer['id']}/transfers", json={"amount": "5.00", "idempotency_key": key()}
    )
    assert to_peer.status_code == 200, to_peer.text
    assert await balance_of(tenant.admin, boss_user["id"]) == D("37.50")
    assert await balance_of(tenant.admin, agent_user["id"]) == D("27.50")
    assert await balance_of(tenant.admin, peer["id"]) == D("5.00")
    for hidden_agent in (agent_user, peer, boss_user):
        refused_agent = await agent.post(
            f"{AGENT}/users/{hidden_agent['id']}/transfers", json={"amount": "1.00", "idempotency_key": key()}
        )
        assert refused_agent.status_code == 404
    assert (await tenant.admin.post(f"{ADMIN}/users/{peer['id']}/suspend")).status_code == 200
    inactive_agent = await boss.post(
        f"{AGENT}/users/{peer['id']}/transfers", json={"amount": "1.00", "idempotency_key": key()}
    )
    assert inactive_agent.status_code == 409 and inactive_agent.json()["error"]["code"] == "not_active"
    outside = await boss.post(
        f"{AGENT}/users/{stranger['id']}/transfers", json={"amount": "1.00", "idempotency_key": key()}
    )
    assert outside.status_code == 404
    assert (await tenant.admin.post(f"{ADMIN}/users/{blocked['id']}/suspend")).status_code == 200
    inactive = await agent.post(f"{AGENT}/users/{blocked['id']}/transfers", json={"amount": "1.00", "idempotency_key": key()})
    assert inactive.status_code == 409 and inactive.json()["error"]["code"] == "not_active"

    mine = (await agent.get(f"{AGENT}/balance-transfers")).json()["items"]
    assert [row["player_username"] for row in mine] == ["ply1"]
    network = (await boss.get(f"{AGENT}/balance-transfers")).json()["items"]
    assert {row["player_username"] for row in network} == {"ply1", "ply3", "agent1", "peeragent"}
    sent = next(row for row in network if row["reference"] == body["reference"])
    assert sent["sender_username"] == "agent1" and sent["sender_role"] == "agent" and sent["status"] == "applied"
    assert D(sent["sender_balance_after"]) == D("17.50") and D(sent["player_balance_after"]) == D("12.50")

    logs = (await tenant.admin.get(f"{ADMIN}/audit-logs", params={"action": "balance.transferred"})).json()["items"]
    assert len(logs) == 5
    rec = (await tenant.admin.get(f"{ADMIN}/betting/reconciliation")).json()
    assert rec["balanced"] is True and rec["checks"]["wallet_mismatches"] == 0
    # Both legs posted, so the transfer adds nothing to the tenant total.
    assert D(rec["in_wallets"]) == D("100.00") + D("30.00")
