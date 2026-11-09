"""A master agent or agent requests play balance, and moves their own earnings into their own wallet."""

from decimal import Decimal as D

from tests.test_betting import START, declare, open_card, placed
from tests.test_commissions import COM, me, network
from tests.test_loader import LOADER, _fund


async def test_balance_request_and_earnings_move(app, tenant):
    _, [fight] = await open_card(tenant)
    clients, users = await network(tenant)
    await placed(clients["ply1"], fight["id"], "meron", 1000)
    await declare(tenant, fight["id"], "meron")
    agent = clients["agent1"]
    before = await me(agent)
    assert D(before["available_earnings"]) == D("30.00")

    loader_user = await tenant.create_user("loader1", "loader")
    await _fund(tenant, loader_user["id"], key="fund-for-request")
    loader = await tenant.login("loader1")
    assert (await clients["ply1"].post(
        f"{COM}/balance-requests",
        json={"amount": "10.00", "idempotency_key": "player-cannot-ask"},
    )).status_code == 403
    named = await agent.post(
        f"{COM}/balance-requests",
        json={"approver_id": loader_user["id"], "amount": "10.00", "idempotency_key": "no-named-approver"},
    )
    assert named.status_code == 422

    body = {"amount": "25.00", "idempotency_key": "ask-loader-25"}
    created = await agent.post(f"{COM}/balance-requests", json=body)
    assert created.status_code == 201, created.text
    request = created.json()
    assert request["status"] == "pending"
    replay = await agent.post(f"{COM}/balance-requests", json=body)
    assert replay.status_code == 201 and replay.json()["id"] == request["id"] and replay.json()["replayed"] is True
    clash = await agent.post(f"{COM}/balance-requests", json={"amount": "10.00", "idempotency_key": "ask-loader-25"})
    assert clash.status_code == 409

    assert (await agent.get(f"{COM}/approvals")).status_code == 403
    assert (await tenant.admin.get(f"{COM}/approvals")).json()[0]["id"] == request["id"]
    allocation_before = D((await loader.get(f"{LOADER}/allocation")).json()["balance"])
    pending = (await loader.get(f"{COM}/approvals")).json()
    assert pending[0]["id"] == request["id"] and pending[0]["requester_username"] == "agent1"
    approved = await loader.post(f"{COM}/approvals/{request['id']}", json={"decision": "approve"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    again = await loader.post(f"{COM}/approvals/{request['id']}", json={"decision": "approve"})
    assert again.json()["replayed"] is True
    assert D((await loader.get(f"{LOADER}/allocation")).json()["balance"]) == allocation_before - D("25.00")
    funded = await me(agent)
    # The wallet is created on the first credit, so the configured starting balance is posted with it.
    assert D(funded["own_balance"]) == D(before["own_balance"]) + START + D("25.00")

    rejected_body = {"amount": "5.00", "idempotency_key": "ask-admin-5"}
    waiting = (await agent.post(f"{COM}/balance-requests", json=rejected_body)).json()
    denied = await tenant.admin.post(f"{COM}/approvals/{waiting['id']}", json={"decision": "reject"})
    assert denied.status_code == 200, denied.text
    assert denied.json()["status"] == "rejected"
    assert D((await me(agent))["own_balance"]) == D(funded["own_balance"])

    too_much = await agent.post(f"{COM}/earnings-moves", json={"amount": "30.01", "idempotency_key": "earn-too-much"})
    assert too_much.status_code == 409, too_much.text
    moved = await agent.post(f"{COM}/earnings-moves", json={"amount": "10.00", "idempotency_key": "earn-move-10"})
    assert moved.status_code == 201, moved.text
    receipt = moved.json()
    assert (D(receipt["earnings_before"]), D(receipt["earnings_after"]), receipt["status"]) == (D("30.00"), D("20.00"), "applied")
    replayed = await agent.post(f"{COM}/earnings-moves", json={"amount": "10.00", "idempotency_key": "earn-move-10"})
    assert replayed.json()["replayed"] is True
    after = await me(agent)
    assert D(after["available_earnings"]) == D("20.00")
    assert D(after["total_earned"]) == D("30.00")
    assert D(after["own_balance"]) == D(funded["own_balance"]) + D("10.00")
    assert (await agent.get(f"{COM}/earnings-moves")).json()[0]["reference"] == receipt["reference"]
    assert users["agent1"]["id"]
    wallet_before = D((await me(agent))["own_balance"])
    cashout = await agent.post(
        f"{COM}/balance-requests",
        json={"amount": "1.00", "purpose": "cashout", "idempotency_key": "cashout-needs-approval"},
    )
    assert cashout.status_code == 201, cashout.text
    assert D((await me(agent))["own_balance"]) == wallet_before - D("1.00")
    admin_notes = (await tenant.admin.get("/api/v1/portal/notifications")).json()["items"]
    loader_notes = (await loader.get("/api/v1/portal/notifications")).json()["items"]
    assert any(n["type"] == "balance.approval_required" and n["title"].startswith("Cashout from agent1") for n in admin_notes)
    assert any(n["type"] == "balance.approval_required" and n["title"].startswith("Cashout from agent1") for n in loader_notes)
    again = await agent.post(
        f"{COM}/balance-requests",
        json={"amount": "1.00", "purpose": "cashout", "idempotency_key": "cashout-needs-approval"},
    )
    assert again.json()["replayed"] is True
    replayed = (await tenant.admin.get("/api/v1/portal/notifications")).json()["items"]
    assert sum(1 for n in replayed if n["title"].startswith("Cashout from agent1")) == 1
    before_cashout = D((await me(agent))["own_balance"])
    too_big = await agent.post(
        f"{COM}/balance-requests",
        json={"amount": "999999.00", "purpose": "cashout", "idempotency_key": "cashout-too-big"},
    )
    assert too_big.status_code == 409, too_big.text
    approved_cashout = await tenant.admin.post(f"{COM}/approvals/{cashout.json()['id']}", json={"decision": "approve"})
    assert approved_cashout.status_code == 200, approved_cashout.text
    assert approved_cashout.json()["purpose"] == "cashout"
    assert D((await me(agent))["own_balance"]) == before_cashout

    earn_before = D((await me(agent))["available_earnings"])
    withdraw = await agent.post(
        f"{COM}/balance-requests",
        json={"amount": "5.00", "purpose": "earnings", "idempotency_key": "withdraw-earnings-5"},
    )
    assert withdraw.status_code == 201, withdraw.text
    held = await me(agent)
    assert D(held["available_earnings"]) == earn_before - D("5.00")
    approved_withdraw = await tenant.admin.post(f"{COM}/approvals/{withdraw.json()['id']}", json={"decision": "approve"})
    assert approved_withdraw.status_code == 200, approved_withdraw.text
    after_withdraw = await me(agent)
    assert D(after_withdraw["available_earnings"]) == D(held["available_earnings"])
    assert D(after_withdraw["own_balance"]) == D(held["own_balance"])

    declined_wallet = D(after_withdraw["own_balance"])
    declined = await agent.post(
        f"{COM}/balance-requests",
        json={"amount": "2.00", "purpose": "cashout", "idempotency_key": "cashout-declined"},
    )
    assert declined.status_code == 201, declined.text
    assert D((await me(agent))["own_balance"]) == declined_wallet - D("2.00")
    denied_cashout = await tenant.admin.post(f"{COM}/approvals/{declined.json()['id']}", json={"decision": "reject"})
    assert denied_cashout.status_code == 200, denied_cashout.text
    assert D((await me(agent))["own_balance"]) == declined_wallet
    log = (await agent.get(f"{COM}/balance-requests")).json()
    assert any(row["purpose"] == "cashout" and row["status"] == "rejected" for row in log)


async def test_loading_form_sends_and_withdraws_play_balance(tenant):
    master = await tenant.create_user("boss", "master_agent")
    player = await tenant.create_user("ply", "player", parent_id=master["id"])
    funded = await tenant.admin.post(
        "/api/v1/admin/balance/account-grants",
        json={"target_user_id": master["id"], "amount": "40.00", "idempotency_key": "fund-boss-load", "remarks": "Load"},
    )
    assert funded.status_code == 201, funded.text
    boss = await tenant.login("boss")
    before = D((await me(boss))["own_balance"])
    sent = await boss.post(
        f"{COM}/loads",
        json={"method": "send", "recipient_id": player["id"], "amount": "15.00", "idempotency_key": "load-send-15"},
    )
    assert sent.status_code == 201, sent.text
    assert D((await me(boss))["own_balance"]) == before - D("15.00")
    back = await boss.post(
        f"{COM}/loads",
        json={"method": "withdraw", "recipient_id": player["id"], "amount": "5.00", "idempotency_key": "load-back-5"},
    )
    assert back.status_code == 201, back.text
    assert D((await me(boss))["own_balance"]) == before - D("10.00")
    outsider = await tenant.create_user("other", "player")
    refused = await boss.post(
        f"{COM}/loads",
        json={"method": "send", "recipient_id": outsider["id"], "amount": "1.00", "idempotency_key": "load-outsider"},
    )
    assert refused.status_code == 404, refused.text
