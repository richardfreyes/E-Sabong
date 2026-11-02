"""Admin treasury issuance and direct distribution reuse the demo-points ledger."""

import asyncio
import contextlib
import json
import uuid

import httpx
import pytest
from httpx_ws import aconnect_ws
from sqlalchemy import func, select

from app.core.database import tenant_session
from app.modules.audit.models import AuditLog
from app.modules.demo_points import service as credit_service
from app.modules.demo_points.models import PointAccount, PointLedgerEntry, PointTransaction
from tests.test_points import key
from tests.test_websocket import _receive, ws_client

BASE = "/api/v1/admin/points"


async def test_credit_success_is_not_sent_until_balances_are_committed(app, tenant):  # type: ignore[no-untyped-def]
    player = await tenant.create_user("player", "player")
    expected = {tenant.admin_id: 100, uuid.UUID(player["id"]): 0}
    observed = []

    async def observer(scope, receive, send_response):  # type: ignore[no-untyped-def]
        async def send_checked(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start" and message["status"] == 201:
                # Independent connection: uncommitted request-session state is
                # invisible here even though the handler has already returned.
                async with tenant_session(tenant.id) as db:
                    balances = dict(
                        (
                            await db.execute(
                                select(PointAccount.user_id, PointAccount.balance).where(
                                    PointAccount.user_id.in_(expected),
                                )
                            )
                        )
                        .tuples()
                        .all()
                    )
                assert balances == expected
                observed.append(scope["path"])
            await send_response(message)

        await app(scope, receive, send_checked)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=observer), base_url=tenant.admin.base_url, headers=dict(tenant.admin.headers)
    ) as client:
        result = await client.post(
            f"{BASE}/allocations",
            json={
                "user_id": str(tenant.admin_id),
                "amount": 100,
                "memo": "Commit boundary test",
            },
            headers=key(),
        )
        assert result.status_code == 201
        expected = {tenant.admin_id: 75, uuid.UUID(player["id"]): 25}
        result = await client.post(
            f"{BASE}/transfers",
            json={
                "to_user_id": player["id"],
                "amount": 25,
                "memo": "Committed transfer",
            },
            headers=key(),
        )
        assert result.status_code == 201
    assert observed == [f"{BASE}/allocations", f"{BASE}/transfers"]


async def issue(tenant, amount=100_000):  # type: ignore[no-untyped-def]
    response = await tenant.admin.post(
        f"{BASE}/allocations",
        json={
            "user_id": str(tenant.admin_id),
            "amount": amount,
            "memo": "Admin treasury funding",
        },
        headers=key(),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def send(tenant, recipient, amount, headers=None):  # type: ignore[no-untyped-def]
    return await tenant.admin.post(
        f"{BASE}/transfers",
        json={
            "to_user_id": recipient,
            "amount": amount,
            "memo": "Direct Admin distribution",
        },
        headers=headers or key(),
    )


async def test_admin_treasury_example_all_roles_and_audit(tenant):  # type: ignore[no-untyped-def]
    master = await tenant.create_user("master", "master_agent")
    agent = await tenant.create_user("agent", "agent", parent_id=master["id"])
    player = await tenant.create_user("player", "player", parent_id=agent["id"])
    desk = await tenant.create_user("desk", "declarator")
    admin2 = await tenant.create_user("admin2", "admin")
    operator = await tenant.create_user("operator", "operator")
    issued = await issue(tenant)
    assert issued["kind"] == "allocation" and issued["destination"]["user_id"] == str(tenant.admin_id)
    transactions = [issued]
    for user, amount in [(master, 20_000), (agent, 10_000), (player, 5_000), (desk, 2_000)]:
        response = await send(tenant, user["id"], amount)
        assert response.status_code == 201, response.text
        transactions.append(response.json())
        account = (await tenant.admin.get(f"{BASE}/accounts/{user['id']}")).json()
        assert account["balance"] == amount
    summary = (await tenant.admin.get(f"{BASE}/summary")).json()
    assert (summary["own_balance"], summary["total_issued"], summary["circulating"]) == (63_000, 100_000, 100_000)
    for user in (admin2, operator):
        response = await send(tenant, user["id"], 100)
        assert response.status_code == 201, response.text
        transactions.append(response.json())
    assert (await tenant.admin.get(f"{BASE}/me")).json()["balance"] == 62_800
    assert len((await tenant.admin.get(f"{BASE}/transactions?kind=allocation")).json()["items"]) == 1
    assert (await tenant.admin.get(f"{BASE}/transactions?kind=transfer")).json()["total"] == 6
    async with tenant_session(tenant.id) as db:
        assert (await db.execute(select(func.sum(PointLedgerEntry.delta)))).scalar_one() == 0
        assert (await db.execute(select(func.count()).select_from(PointLedgerEntry))).scalar_one() == 14
        audit = (await db.execute(select(AuditLog).where(AuditLog.resource_type == "point_transaction"))).scalars().all()
        assert len(audit) == 7
        for entry in audit:
            assert entry.actor_id == tenant.admin_id and entry.created_at
            assert entry.changes["administrator_id"] == str(tenant.admin_id)
            assert entry.changes["recipient_id"] and entry.changes["amount"] > 0 and entry.changes["reason"]


@pytest.mark.parametrize("role", ["master_agent", "agent", "player", "operator", "declarator"])
async def test_non_admin_cannot_mint_or_bypass_role_rules_even_with_custom_permissions(tenant, role):  # type: ignore[no-untyped-def]
    custom = await tenant.admin.post(
        "/api/v1/admin/roles",
        json={
            "name": "credit_permissions",
            "permissions": ["points.allocate", "points.adjust", "points.transfer"],
        },
    )
    assert custom.status_code == 201, custom.text
    parent = await tenant.create_user("parent", "master_agent")
    user = await tenant.create_user(
        "customuser", role, parent_id=parent["id"] if role == "agent" else None, role_ids=[custom.json()["id"]]
    )
    client = await tenant.login(user["username"])
    for endpoint, body in [
        ("allocations", {"user_id": user["id"], "amount": 10}),
        ("adjustments", {"user_id": user["id"], "amount": 10, "direction": "credit", "reason": "Bypass attempt"}),
    ]:
        assert (await client.post(f"{BASE}/{endpoint}", json=body, headers=key())).status_code == 403
    response = await client.post(f"{BASE}/transfers", json={"to_user_id": str(tenant.admin_id), "amount": 1}, headers=key())
    assert response.status_code == 403


async def test_admin_transfer_is_not_credit_creation_and_rejects_bad_recipients(tenant, other_tenant):  # type: ignore[no-untyped-def]
    player = await tenant.create_user("player", "player")
    response = await send(tenant, player["id"], 1)
    assert response.status_code == 422 and response.json()["error"]["code"] == "insufficient_points"
    await issue(tenant, 100)
    assert (await send(tenant, str(other_tenant.admin_id), 10)).status_code == 404
    assert (await send(tenant, str(tenant.admin_id), 10)).json()["error"]["code"] == "self_transfer"
    await tenant.admin.post(f"/api/v1/admin/users/{player['id']}/suspend")
    assert (await send(tenant, player["id"], 10)).json()["error"]["code"] == "account_suspended"
    assert (await tenant.admin.get(f"{BASE}/me")).json()["balance"] == 100
    assert (await tenant.admin.get(f"{BASE}/transactions")).json()["total"] == 1


async def test_concurrent_full_treasury_retries_replay_once_and_never_mint(tenant):  # type: ignore[no-untyped-def]
    player = await tenant.create_user("player", "player")
    await issue(tenant, 100)
    headers = key()
    responses = await asyncio.gather(*(send(tenant, player["id"], 100, headers) for _ in range(10)))
    assert sorted(response.status_code for response in responses) == [200] * 9 + [201]
    assert len({response.json()["id"] for response in responses}) == 1
    assert (await send(tenant, player["id"], 99, headers)).status_code == 409
    assert (await tenant.admin.get(f"{BASE}/me")).json()["balance"] == 0
    summary = (await tenant.admin.get(f"{BASE}/summary")).json()
    assert summary["circulating"] == summary["total_issued"] == 100
    async with tenant_session(tenant.id) as db:
        minimum = (await db.execute(select(func.min(PointAccount.balance)).where(PointAccount.kind == "user"))).scalar_one()
        assert minimum == 0
        assert (await db.execute(select(func.count()).select_from(PointTransaction))).scalar_one() == 2


@pytest.mark.parametrize("amount", [0, -1, 1.5, "100", True])
async def test_credit_amounts_are_strict_positive_whole_points(tenant, amount):  # type: ignore[no-untyped-def]
    response = await tenant.admin.post(
        f"{BASE}/allocations", json={"user_id": str(tenant.admin_id), "amount": amount}, headers=key()
    )
    assert response.status_code == 422


async def test_recipient_search_role_id_and_direct_assignment_filters(tenant, other_tenant):  # type: ignore[no-untyped-def]
    master = await tenant.create_user("master", "master_agent")
    agent = await tenant.create_user("agent", "agent", parent_id=master["id"])
    player = await tenant.create_user("player", "player", parent_id=agent["id"])
    desk = await tenant.create_user("desk", "declarator", full_name="Result Official")
    for query in [desk["id"], "Result Official", "declarator", "desk"]:
        response = await tenant.admin.get("/api/v1/admin/users", params={"q": query, "transfer_recipients": True})
        assert response.status_code == 200, response.text
        assert desk["id"] in {user["id"] for user in response.json()["items"]}
    assert (await tenant.admin.get("/api/v1/admin/users", params={"q": str(other_tenant.admin_id)})).json()["total"] == 0
    eligible = (await tenant.admin.get("/api/v1/admin/users?transfer_recipients=true&page_size=100")).json()["items"]
    assert {agent["id"], player["id"], master["id"], desk["id"]} <= {user["id"] for user in eligible}
    # Recipient lookup belongs to the Admin transfer workflow only.
    for username in ("master", "agent"):
        client = await tenant.login(username)
        response = await client.get("/api/v1/admin/users?transfer_recipients=true")
        assert response.status_code == 403 and response.json()["error"]["code"] == "permission_denied"


async def test_demo_credits_never_convert_into_betting_balance(tenant):  # type: ignore[no-untyped-def]
    player = await tenant.create_user("player", "player")
    await tenant.admin.put("/api/v1/admin/betting/settings", json={"enabled": True, "starting_balance": "0.00"})
    client = await tenant.login("player")
    assert (await client.get("/api/v1/portal/betting")).json()["wallet"]["balance"] == "0.00"
    await issue(tenant, 100)
    assert (await send(tenant, player["id"], 25)).status_code == 201
    assert (await client.get("/api/v1/portal/points")).json()["balance"] == 25
    assert (await client.get("/api/v1/portal/betting")).json()["wallet"]["balance"] == "0.00"


async def test_exact_balance_limit_rejects_issuance_atomically(tenant, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(credit_service, "MAX_BALANCE", 100)
    await issue(tenant, 100)
    response = await tenant.admin.post(
        f"{BASE}/allocations",
        json={
            "user_id": str(tenant.admin_id),
            "amount": 1,
            "memo": "Would exceed safe limit",
        },
        headers=key(),
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "points_balance_limit"
    assert (await tenant.admin.get(f"{BASE}/summary")).json()["total_issued"] == 100
    assert (await tenant.admin.get(f"{BASE}/me")).json()["balance"] == 100
    assert (await tenant.admin.get(f"{BASE}/transactions")).json()["total"] == 1


async def test_direct_player_transfer_notifies_ancestors_but_not_bystanders_or_other_tenants(app, tenant, other_tenant):  # type: ignore[no-untyped-def]
    master = await tenant.create_user("master", "master_agent")
    agent = await tenant.create_user("agent", "agent", parent_id=master["id"])
    player = await tenant.create_user("player", "player", parent_id=agent["id"])
    await tenant.create_user("bystander", "player")
    await issue(tenant, 100)
    clients = {name: await tenant.login(name) for name in ("master", "agent", "player", "bystander")}
    clients.update(admin=tenant.admin, other=other_tenant.admin)
    async with contextlib.AsyncExitStack() as stack:
        sockets = {}
        for name, client in clients.items():
            host = other_tenant.host if name == "other" else tenant.host
            transport = await stack.enter_async_context(ws_client(app, host))
            token = client.headers["Authorization"].split()[1]
            sockets[name] = await stack.enter_async_context(aconnect_ws(f"/api/ws?token={token}", transport))
            await _receive(sockets[name])
        await asyncio.sleep(0.2)
        assert (await send(tenant, player["id"], 25)).status_code == 201
        for name in ("admin", "master", "agent", "player"):
            for _ in range(5):
                frame = await _receive(sockets[name])
                if frame["type"] == "points.updated":
                    assert "balance" not in frame["data"] and "amount" not in frame["data"]
                    break
            else:
                pytest.fail(f"No balance invalidation for {name}")
        for name in ("bystander", "other"):
            await sockets[name].send_text(json.dumps({"type": "ping"}))
            assert (await _receive(sockets[name]))["type"] == "pong"
