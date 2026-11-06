"""A player asks their upline to take play points back. The agent inbox approves or denies it.

Approving debits the player and credits the direct upline by the same amount. Earnings stay put,
and a denied request moves nothing.
"""

import uuid
from decimal import Decimal as D

from tests.test_balance_management import add_to, admin_balance

AGENT = "/api/v1/agent"
PORTAL = "/api/v1/portal"


def key() -> str:
    return f"inbox-{uuid.uuid4().hex}"


async def test_inbox_request_returns_points_only_when_approved(tenant, other_tenant):
    boss = await tenant.create_user("boss", "master_agent")
    agent = await tenant.create_user("agent1", "agent", parent_id=boss["id"])
    player = await tenant.create_user("ply1", "player", parent_id=agent["id"])
    other = await tenant.create_user("ply3", "player", parent_id=boss["id"])
    await tenant.create_user("stranger", "player")
    await other_tenant.create_user("outsider", "player", parent_id=None)
    await add_to(tenant.admin, player["id"], "25.00")
    await add_to(tenant.admin, agent["id"], "10.00")
    ply, ag, master = await tenant.login("ply1"), await tenant.login("agent1"), await tenant.login("boss")
    other_player = await tenant.login("ply3")
    earned = (await ag.get("/api/v1/commissions/me")).json()["total_earned"]

    sent = await ply.post(f"{PORTAL}/inbox", json={"amount": "8.00", "idempotency_key": "inbox-ply1-once"})
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["status"] == "pending" and body["kind"] == "cashout" and body["player_username"] == "ply1"
    assert D(body["amount"]) == D("8.00") and body["upline_username"] == "agent1"
    assert await admin_balance(tenant, player["id"]) == D("25.00")
    again = await ply.post(f"{PORTAL}/inbox", json={"amount": "8.00", "idempotency_key": "inbox-ply1-once"})
    assert again.status_code == 200 and again.json()["replayed"] is True and again.json()["id"] == body["id"]
    duplicate = await ply.post(f"{PORTAL}/inbox", json={"amount": "1.00", "idempotency_key": key()})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "request_open"
    for amount in ("0", "-1", "nope"):
        assert (await other_player.post(f"{PORTAL}/inbox", json={"amount": amount, "idempotency_key": key()})).status_code == 422

    waiting = (await ag.get(f"{AGENT}/inbox")).json()
    assert [row["player_username"] for row in waiting] == ["ply1"]
    assert (await ag.get(f"{AGENT}/inbox/counts")).json() == {"cashout": 1, "commission": 0}
    assert (await master.get(f"{AGENT}/inbox/counts")).json()["cashout"] == 1
    hidden = await ag.post(f"{AGENT}/inbox/{uuid.uuid4()}/decision", json={"decision": "deny"})
    assert hidden.status_code == 404
    assert (await ply.get(f"{AGENT}/inbox")).status_code == 403

    denied_player = await other_player.post(f"{PORTAL}/inbox", json={"amount": "1.00", "idempotency_key": key()})
    # ply3 has no wallet points.
    assert denied_player.status_code == 422 and denied_player.json()["error"]["code"] == "insufficient_balance"
    await add_to(tenant.admin, other["id"], "4.00")
    opened = await other_player.post(f"{PORTAL}/inbox", json={"amount": "4.00", "idempotency_key": key()})
    assert opened.status_code == 200, opened.text
    # The direct upline is the master agent. agent1 cannot decide it.
    assert (await ag.post(f"{AGENT}/inbox/{opened.json()['id']}/decision", json={"decision": "deny"})).status_code == 404
    deny = await master.post(f"{AGENT}/inbox/{opened.json()['id']}/decision", json={"decision": "deny"})
    assert deny.status_code == 200 and deny.json()["status"] == "denied"
    assert await admin_balance(tenant, other["id"]) == D("4.00")

    approved = await ag.post(f"{AGENT}/inbox/{body['id']}/decision", json={"decision": "approve"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert await admin_balance(tenant, player["id"]) == D("17.00")
    assert await admin_balance(tenant, agent["id"]) == D("18.00")
    repeat = await master.post(f"{AGENT}/inbox/{body['id']}/decision", json={"decision": "approve"})
    assert repeat.status_code == 200 and repeat.json()["replayed"] is True
    assert await admin_balance(tenant, player["id"]) == D("17.00")
    assert (await ag.get(f"{AGENT}/inbox")).json() == []
    assert (await ag.get("/api/v1/commissions/me")).json()["total_earned"] == earned
    stranger = await tenant.login("stranger")
    assert (await stranger.get(f"{AGENT}/inbox")).status_code == 403
    far = await other_tenant.login("outsider")
    assert (await far.post(f"{PORTAL}/inbox", json={"amount": "1.00", "idempotency_key": key()})).status_code == 422
