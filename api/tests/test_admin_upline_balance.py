"""An administrator adds balance, transfers from their own wallet, and moves commission.

These use the existing play wallet and commission ledger. A transfer spends the administrator's
wallet. Adding balance creates it. Moving commission spends that account's available earnings.
"""

from decimal import Decimal as D

from tests.test_balance_management import BAL, add_to, admin_balance, hierarchy, key, ok
from tests.test_betting import START, declare, enable, open_card, placed
from tests.test_commissions import me, network


async def test_admin_transfers_their_wallet_and_can_add_balance(tenant, other_tenant):
    await enable(tenant)
    ids = await hierarchy(tenant)
    admin_id = str(tenant.admin_id)
    funded = await ok(await add_to(tenant.admin, admin_id, "50.00", remarks="Fund the admin wallet"), 201)
    assert funded["kind"] == "account_grant"
    summary = await ok(await tenant.admin.get(f"{BAL}/summary"))
    assert D(summary["own_balance"]) == START + D("50.00")

    short = await tenant.admin.post(
        f"{BAL}/admin-debits",
        json={"target_user_id": ids["agt"], "amount": "10050.01", "idempotency_key": key()},
    )
    assert short.status_code == 409 and short.json()["error"]["code"] == "admin_wallet_short"
    player = await tenant.admin.post(
        f"{BAL}/admin-debits",
        json={"target_user_id": ids["pl1"], "amount": "1.00", "idempotency_key": key()},
    )
    assert player.status_code == 404
    outsider = await other_tenant.admin.post(
        f"{BAL}/admin-debits",
        json={"target_user_id": ids["mast"], "amount": "1.00", "idempotency_key": key()},
    )
    assert outsider.status_code == 404
    assert (await (await tenant.login("agt")).post(
        f"{BAL}/admin-debits",
        json={"target_user_id": ids["mast"], "amount": "1.00", "idempotency_key": key()},
    )).status_code == 403

    idem = key()
    body = {"target_user_id": ids["mast"], "amount": "20.00", "idempotency_key": idem, "remarks": "Float"}
    moved = await ok(await tenant.admin.post(f"{BAL}/admin-debits", json=body), 201)
    assert moved["target_username"] == "mast" and D(moved["amount"]) == D("20.00")
    assert D(moved["sender_balance_after"]) == START + D("30.00")
    assert D(moved["target_balance_after"]) == START + D("20.00")
    assert D((await ok(await tenant.admin.get(f"{BAL}/summary")))["own_balance"]) == START + D("30.00")
    assert await admin_balance(tenant, ids["mast"]) == START + D("20.00")
    assert await admin_balance(tenant, ids["agt"]) == 0
    replay = await ok(await tenant.admin.post(f"{BAL}/admin-debits", json=body))
    assert replay["id"] == moved["id"] and replay["replayed"] is True
    assert D((await ok(await tenant.admin.get(f"{BAL}/summary")))["own_balance"]) == START + D("30.00")
    clash = await tenant.admin.post(f"{BAL}/admin-debits", json={**body, "amount": "5.00"})
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "idempotency_conflict"

    detail = await ok(await tenant.admin.get(f"{BAL}/accounts/{ids['mast']}"))
    assert D(detail["account"]["balance"]) == START + D("20.00")
    assert D(detail["available_earnings"]) == 0
    kinds = [row["kind"] for row in (await ok(await tenant.admin.get(f"{BAL}/entries", params={"user_id": ids["mast"], "page_size": 20})))["items"]]
    assert "transfer" in kinds
    added = await ok(await add_to(tenant.admin, ids["agt"], "15.00", remarks="Created for the agent"), 201)
    assert added["kind"] == "account_grant"
    assert await admin_balance(tenant, ids["agt"]) == START + D("15.00")
    assert D((await ok(await tenant.admin.get(f"{BAL}/summary")))["own_balance"]) == START + D("30.00")


async def test_admin_moves_commission_into_the_agents_wallet(tenant):
    _, [fight] = await open_card(tenant)
    clients, users = await network(tenant)
    await placed(clients["ply1"], fight["id"], "meron", 1000)
    await declare(tenant, fight["id"], "meron")
    agent_id = users["agent1"]["id"]
    before = await me(clients["agent1"])
    assert D(before["available_earnings"]) == D("30.00")
    detail = await ok(await tenant.admin.get(f"{BAL}/accounts/{agent_id}"))
    assert D(detail["available_earnings"]) == D("30.00")

    too_much = await tenant.admin.post(
        f"{BAL}/commission-credits",
        json={"target_user_id": agent_id, "amount": "30.01", "remarks": "Too much commission", "idempotency_key": key()},
    )
    assert too_much.status_code == 409 and too_much.json()["error"]["code"] == "earnings_exceeded"
    base = D(detail["account"]["balance"]) if detail["account"]["has_wallet"] else START
    idem = key()
    body = {"target_user_id": agent_id, "amount": "12.50", "remarks": "Move commission", "idempotency_key": idem}
    moved = await ok(await tenant.admin.post(f"{BAL}/commission-credits", json=body), 201)
    assert (D(moved["earnings_before"]), D(moved["earnings_after"]), D(moved["amount"])) == (D("30.00"), D("17.50"), D("12.50"))
    assert D(moved["wallet_after"]) == base + D("12.50")
    replay = await ok(await tenant.admin.post(f"{BAL}/commission-credits", json=body))
    assert replay["replayed"] is True and replay["id"] == moved["id"]
    after = await me(clients["agent1"])
    assert D(after["available_earnings"]) == D("17.50")
    assert D(after["total_earned"]) == D("30.00")
    assert D(after["own_balance"]) == base + D("12.50")
    admin_before = D((await ok(await tenant.admin.get(f"{BAL}/summary")))["own_balance"])
    rest = await ok(
        await tenant.admin.post(
            f"{BAL}/commission-credits",
            json={"target_user_id": agent_id, "amount": "17.50", "remarks": "Move the rest", "idempotency_key": key()},
        ),
        201,
    )
    assert D(rest["earnings_after"]) == 0
    assert D((await me(clients["agent1"]))["available_earnings"]) == 0
    assert D((await ok(await tenant.admin.get(f"{BAL}/summary")))["own_balance"]) == admin_before
    assert (await clients["agent1"].post(
        f"{BAL}/commission-credits",
        json={"target_user_id": agent_id, "amount": "1.00", "remarks": "Not allowed", "idempotency_key": key()},
    )).status_code == 403


async def _balance(tenant, user_id) -> D:  # type: ignore[no-untyped-def]
    return D((await ok(await tenant.admin.get(f"{BAL}/accounts/{user_id}")))["account"]["balance"])


async def _own(tenant) -> D:  # type: ignore[no-untyped-def]
    return D((await ok(await tenant.admin.get(f"{BAL}/summary")))["own_balance"])


async def test_add_transfer_deduct_and_commission_persist(tenant):
    """The four administrator actions, for a master agent and an agent, survive a later read."""
    _, [fight] = await open_card(tenant)
    clients, users = await network(tenant)
    await placed(clients["ply1"], fight["id"], "meron", 2000)
    await declare(tenant, fight["id"], "meron")
    master_id, agent_id = users["boss"]["id"], users["agent1"]["id"]
    assert D((await me(clients["boss"]))["available_earnings"]) == D("110.00")
    assert D((await me(clients["agent1"]))["available_earnings"]) == D("60.00")

    for user_id in (master_id, agent_id):
        await ok(await add_to(tenant.admin, user_id, "1.00", remarks="Open the wallet"), 201)
    await ok(await add_to(tenant.admin, str(tenant.admin_id), "5000.00", remarks="Fund the admin wallet"), 201)

    for user_id, client in ((master_id, clients["boss"]), (agent_id, clients["agent1"])):
        before, own = await _balance(tenant, user_id), await _own(tenant)
        added = await ok(await add_to(tenant.admin, user_id, "1000.00", remarks="Created balance"), 201)
        assert added["kind"] == "account_grant" and D(added["total_delta"]) == D("1000.00")
        assert await _balance(tenant, user_id) == before + D("1000.00")
        assert await _own(tenant) == own

        before, own = await _balance(tenant, user_id), await _own(tenant)
        moved = await ok(
            await tenant.admin.post(
                f"{BAL}/admin-debits",
                json={"target_user_id": user_id, "amount": "1000.00", "idempotency_key": key()},
            ),
            201,
        )
        assert D(moved["sender_balance_after"]) == own - D("1000.00")
        assert D(moved["target_balance_after"]) == before + D("1000.00")
        assert await _own(tenant) == own - D("1000.00")
        assert await _balance(tenant, user_id) == before + D("1000.00")

        before, own = await _balance(tenant, user_id), await _own(tenant)
        taken = await ok(
            await tenant.admin.post(
                f"{BAL}/upline-returns",
                json={"target_user_id": user_id, "amount": "1000.00", "remarks": "Retrieve balance", "idempotency_key": key()},
            ),
            201,
        )
        assert D(taken["account_balance_after"]) == before - D("1000.00")
        assert D(taken["admin_balance_after"]) == own + D("1000.00")
        assert await _balance(tenant, user_id) == before - D("1000.00")
        assert await _own(tenant) == own + D("1000.00")

        wallet_before = await _balance(tenant, user_id)
        own = await _own(tenant)
        credited = await ok(
            await tenant.admin.post(
                f"{BAL}/commission-credits",
                json={"target_user_id": user_id, "amount": "50.00", "remarks": "Move commission", "idempotency_key": key()},
            ),
            201,
        )
        assert D(credited["earnings_before"]) - D(credited["earnings_after"]) == D("50.00")
        assert await _balance(tenant, user_id) == wallet_before + D("50.00")
        assert D((await me(client))["available_earnings"]) == D(credited["earnings_after"])
        assert await _own(tenant) == own

    again = await tenant.login("admin")
    assert await _balance(tenant, master_id) == D((await ok(await again.get(f"{BAL}/accounts/{master_id}")))["account"]["balance"])
    too_much = await tenant.admin.post(
        f"{BAL}/upline-returns",
        json={"target_user_id": master_id, "amount": "999999.99", "remarks": "Too much", "idempotency_key": key()},
    )
    assert too_much.status_code == 409 and too_much.json()["error"]["code"] == "upline_wallet_short"
    assert (await clients["agent1"].post(
        f"{BAL}/upline-returns",
        json={"target_user_id": master_id, "amount": "1.00", "remarks": "Not allowed", "idempotency_key": key()},
    )).status_code == 403


async def _cash_out(client, user_id, amount, remarks, idem=None):  # type: ignore[no-untyped-def]
    return await client.post(
        f"{BAL}/upline-returns",
        json={"target_user_id": user_id, "amount": str(amount), "remarks": remarks, "idempotency_key": idem or key()},
    )


async def test_cash_out_credits_the_admin_from_the_selected_account(tenant, other_tenant):
    """1,000 from a 50,000 master leaves 49,000 and raises the admin from 5,000 to 6,000. Same for an agent."""
    await enable(tenant)
    ids = await hierarchy(tenant)
    await ok(await add_to(tenant.admin, ids["mast"], "40000.00", remarks="Set the master balance"), 201)
    assert await admin_balance(tenant, ids["mast"]) == D("50000.00")
    # The administrator has no wallet yet, so this credit is exactly 5,000 and does not add a starting balance.
    await ok(await _cash_out(tenant.admin, ids["mast"], "5000.00", "Open the admin wallet"), 201)
    await ok(await add_to(tenant.admin, ids["mast"], "5000.00", remarks="Restore the master balance"), 201)
    assert await admin_balance(tenant, ids["mast"]) == D("50000.00")
    assert D((await ok(await tenant.admin.get(f"{BAL}/summary")))["own_balance"]) == D("5000.00")
    earnings = D((await ok(await tenant.admin.get(f"{BAL}/accounts/{ids['mast']}")))["available_earnings"])

    short = await _cash_out(tenant.admin, ids["mast"], "50000.01", "Too much")
    assert short.status_code == 409 and short.json()["error"]["code"] == "upline_wallet_short"
    assert (await _cash_out(tenant.admin, ids["pl1"], "1.00", "Not an upline")).status_code == 404
    assert (await _cash_out(other_tenant.admin, ids["mast"], "1.00", "Other tenant")).status_code == 404
    assert (await _cash_out(await tenant.login("agt"), ids["mast"], "1.00", "Not an admin")).status_code == 403

    idem = key()
    posted = await ok(await _cash_out(tenant.admin, ids["mast"], "1000.00", "Weekly settlement", idem), 201)
    assert D(posted["account_balance_after"]) == D("49000.00")
    assert D(posted["admin_balance_after"]) == D("6000.00")
    assert await admin_balance(tenant, ids["mast"]) == D("49000.00")
    assert D((await ok(await tenant.admin.get(f"{BAL}/summary")))["own_balance"]) == D("6000.00")
    assert D((await ok(await tenant.admin.get(f"{BAL}/accounts/{ids['mast']}")))["available_earnings"]) == earnings
    assert await admin_balance(tenant, ids["agt"]) == 0 and await admin_balance(tenant, ids["agt2"]) == 0
    replay = await ok(await _cash_out(tenant.admin, ids["mast"], "1000.00", "Weekly settlement", idem))
    assert replay["replayed"] is True and await admin_balance(tenant, ids["mast"]) == D("49000.00")

    await ok(await add_to(tenant.admin, ids["agt"], "40000.00", remarks="Set the agent balance"), 201)
    assert await admin_balance(tenant, ids["agt"]) == D("50000.00")
    agent_earnings = D((await ok(await tenant.admin.get(f"{BAL}/accounts/{ids['agt']}")))["available_earnings"])
    agent = await ok(await _cash_out(tenant.admin, ids["agt"], "1000.00", "Agent settlement"), 201)
    assert D(agent["account_balance_after"]) == D("49000.00")
    assert D(agent["admin_balance_after"]) == D("7000.00")
    assert await admin_balance(tenant, ids["agt"]) == D("49000.00")
    assert await admin_balance(tenant, ids["mast"]) == D("49000.00")
    assert await admin_balance(tenant, ids["agt2"]) == 0
    assert D((await ok(await tenant.admin.get(f"{BAL}/accounts/{ids['agt']}")))["available_earnings"]) == agent_earnings

    again = await tenant.login("admin")
    assert await admin_balance(tenant, ids["mast"]) == D("49000.00")
    assert D((await ok(await again.get(f"{BAL}/summary")))["own_balance"]) == D("7000.00")
    history = (await ok(await again.get(f"{BAL}/upline-returns")))["items"]
    posted_rows = [row for row in history if D(row["amount"]) == D("1000.00")]
    assert {row["account_username"] for row in posted_rows} == {"mast", "agt"}
    assert {row["remarks"] for row in posted_rows} == {"Weekly settlement", "Agent settlement"}
    assert {row["admin_username"] for row in posted_rows} == {"admin"}
