"""Loader role: an administrator funds one wallet, and the Loader can only give that balance out."""

from decimal import Decimal as D

from tests.test_betting import ADMIN

BAL = f"{ADMIN}/balance"
LOADER = "/api/v1/loader"


async def _fund(tenant, loader_id, amount="100.00", key="fund-loader"):
    r = await tenant.admin.post(
        f"{BAL}/account-grants",
        json={"target_user_id": loader_id, "amount": amount, "idempotency_key": key, "remarks": "Approved allocation"},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_admin_creates_a_loader_and_only_an_admin_can(app, tenant):
    loader = await tenant.create_user("loader1", "loader")
    assert loader["account_type"] == "loader"
    roles = (await tenant.admin.get(f"{ADMIN}/roles")).json()
    role = next(row for row in roles if row["name"] == "loader")
    assert role["is_system"] is True
    assert role["description"] == "Manages balance allocation for Master Agents, Agents, and Players."
    assert set(role["permissions"]) == {"loader.view", "loader.grant", "loader.history"}

    master = await tenant.create_user("boss", "master_agent")
    boss = await tenant.login("boss")
    refused = await boss.post(
        f"{ADMIN}/users",
        json={"username": "loader2", "full_name": "Loader Two", "account_type": "loader", "password": "Str0ng!Passw0rd"},
    )
    assert refused.status_code == 403, refused.text
    # The Loader role cannot be stuck on another account type.
    stuck = await tenant.admin.post(
        f"{ADMIN}/users",
        json={
            "username": "notloader",
            "full_name": "Not Loader",
            "account_type": "player",
            "password": "Str0ng!Passw0rd",
            "role_ids": [role["id"]],
        },
    )
    assert stuck.status_code == 422, stuck.text
    assert master["id"]


async def test_loader_allocates_only_its_funded_balance(app, tenant, other_tenant):
    loader_user = await tenant.create_user("loader1", "loader")
    master = await tenant.create_user("boss", "master_agent")
    player = await tenant.create_user("ply1", "player", parent_id=master["id"])
    outsider = await other_tenant.create_user("outsider", "player")
    await _fund(tenant, loader_user["id"])

    loader = await tenant.login("loader1")
    assert D((await loader.get(f"{LOADER}/allocation")).json()["balance"]) == D("100.00")
    listed = (await loader.get(f"{LOADER}/accounts", params={"q": "ply"})).json()["items"]
    assert [row["username"] for row in listed] == ["ply1"]
    assert (await loader.get(f"{ADMIN}/roles")).status_code == 403
    assert (await loader.get(f"{ADMIN}/users")).status_code == 403
    visible = (await loader.get(f"{BAL}/accounts")).json()["items"]
    assert {row["username"] for row in visible} >= {"boss", "ply1"}
    assert "loader1" not in {row["username"] for row in visible}
    assert (await loader.get(f"{BAL}/operations")).status_code == 403
    minted = await loader.post(
        f"{BAL}/account-grants",
        json={"target_user_id": player["id"], "amount": "1.00", "idempotency_key": "loader-must-not-mint"},
    )
    assert minted.status_code == 403, minted.text

    body = {
        "target_user_id": player["id"],
        "amount": "40.00",
        "reason": "Weekly allocation",
        "idempotency_key": "loader-grant-1",
    }
    first = await loader.post(f"{LOADER}/grants", json=body)
    assert first.status_code == 200, first.text
    grant = first.json()
    assert grant["replayed"] is False
    assert grant["reference"].startswith("LDR-")
    assert (D(grant["loader_balance_after"]), D(grant["recipient_balance_after"])) == (D("60.00"), D("40.00"))
    listed_after = (await loader.get(f"{LOADER}/accounts", params={"q": "ply1"})).json()["items"]
    assert D(listed_after[0]["balance"]) == D("40.00")

    again = await loader.post(f"{LOADER}/grants", json=body)
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert D((await loader.get(f"{LOADER}/allocation")).json()["balance"]) == D("60.00")

    changed = {**body, "amount": "10.00", "idempotency_key": "loader-grant-1"}
    assert (await loader.post(f"{LOADER}/grants", json=changed)).status_code == 409

    too_much = {**body, "amount": "60.01", "idempotency_key": "loader-grant-2"}
    assert (await loader.post(f"{LOADER}/grants", json=too_much)).status_code == 409

    foreign = {**body, "target_user_id": outsider["id"], "amount": "1.00", "idempotency_key": "loader-grant-3"}
    assert (await loader.post(f"{LOADER}/grants", json=foreign)).status_code == 404

    history = (await loader.get(f"{LOADER}/history")).json()["items"]
    assert len(history) == 1
    assert history[0]["reason"] == "Weekly allocation"
    assert (await loader.put(f"{LOADER}/history")).status_code == 405
    # A master agent still cannot mint or move balances through the Loader API.
    boss = await tenant.login("boss")
    assert (await boss.get(f"{LOADER}/accounts")).status_code == 403


async def test_loader_withdrawals_stay_in_own_allocation_and_preserve_admin_boundaries(tenant, other_tenant):
    from tests.test_balance_management import key, ok

    loader_user = await tenant.create_user("loader_moves", "loader")
    other_loader_user = await tenant.create_user("loader_other", "loader")
    master = await tenant.create_user("master_moves", "master_agent")
    outsider = await other_tenant.create_user("foreign_master", "master_agent")
    await _fund(tenant, loader_user["id"], "100.00", key())
    loader = await tenant.login("loader_moves")
    other_loader = await tenant.login("loader_other")
    grant = {"target_user_id": master["id"], "amount": "40.00", "reason": "Transfer Balance", "idempotency_key": key()}
    await ok(await loader.post(f"{LOADER}/grants", json=grant))
    body = {"target_user_id": master["id"], "amount": "10.00", "remarks": "Withdrawal", "idempotency_key": key()}
    row = await ok(await loader.post(f"{BAL}/upline-returns", json=body), 201)
    assert D(row["account_balance_after"]) == D("30.00")
    assert D(row["admin_balance_after"]) == D("70.00")
    assert D((await ok(await loader.get(f"{LOADER}/allocation")))["balance"]) == D("70.00")
    assert (await ok(await loader.get(f"{BAL}/summary")))["can_manage"] is False
    replay = await ok(await loader.post(f"{BAL}/upline-returns", json=body))
    assert replay["replayed"] is True and replay["id"] == row["id"]
    assert D((await ok(await loader.get(f"{LOADER}/allocation")))["balance"]) == D("70.00")
    own_logs = await ok(await loader.get(f"{BAL}/upline-returns"))
    assert [r["id"] for r in own_logs["items"]] == [row["id"]]
    assert (await ok(await other_loader.get(f"{BAL}/upline-returns")))["items"] == []
    assert row["id"] in [r["id"] for r in (await ok(await tenant.admin.get(f"{BAL}/upline-returns")))["items"]]
    assert (
        await loader.post(f"{BAL}/upline-returns", json={**body, "amount": "30.01", "idempotency_key": key()})
    ).status_code == 409
    for route in ("upline-returns", "commission-credits"):
        assert (
            await loader.post(f"{BAL}/{route}", json={**body, "target_user_id": outsider["id"], "idempotency_key": key()})
        ).status_code == 404
    for route in ("account-grants", "grants", "resets"):
        assert (await loader.post(f"{BAL}/{route}", json={**body, "idempotency_key": key()})).status_code == 403
    assert (await loader.get(f"{BAL}/operations")).status_code == 403
    assert other_loader_user["id"]


async def test_loader_moves_real_commission_without_minting_or_changing_its_allocation(tenant):
    from tests.test_balance_management import key, ok
    from tests.test_betting import declare, open_card, placed
    from tests.test_commissions import network

    _, [fight] = await open_card(tenant)
    clients, users = await network(tenant)
    await placed(clients["ply1"], fight["id"], "meron", 1000)
    await declare(tenant, fight["id"], "meron")
    await tenant.create_user("loader_commission", "loader")
    loader = await tenant.login("loader_commission")
    body = {
        "target_user_id": users["agent1"]["id"],
        "amount": "12.50",
        "remarks": "Withdrawal Commission",
        "idempotency_key": key(),
    }
    allocation_before = D((await ok(await loader.get(f"{LOADER}/allocation")))["balance"])
    before = await ok(await loader.get(f"{BAL}/accounts/{users['agent1']['id']}"))
    row = await ok(await loader.post(f"{BAL}/commission-credits", json=body), 201)
    assert D(row["earnings_before"]) == D("30.00")
    assert D(row["earnings_after"]) == D("17.50")
    after = await ok(await loader.get(f"{BAL}/accounts/{users['agent1']['id']}"))
    # A previously absent wallet receives the existing starting grant before the commission move.
    from tests.test_betting import START

    base = D(before["account"]["balance"]) if before["account"]["has_wallet"] else START
    assert D(after["account"]["balance"]) == base + D("12.50")
    assert D((await ok(await loader.get(f"{LOADER}/allocation")))["balance"]) == allocation_before
    replay = await ok(await loader.post(f"{BAL}/commission-credits", json=body))
    assert replay["replayed"] is True
    assert (
        await loader.post(f"{BAL}/commission-credits", json={**body, "amount": "17.51", "idempotency_key": key()})
    ).status_code == 409
