"""Admin points management, scenario by scenario: issue, credit, transfer, history, refunds,
adjustments, permissions, tenant isolation, idempotency, concurrency, rollback, ledger integrity
and migration safety. Every balance is read back from PostgreSQL, never from the client."""

import asyncio
import csv
import io
import os
import uuid

import httpx
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.database import platform_session, tenant_session
from app.modules.audit.models import AuditLog
from app.modules.demo_points import service as points_service
from app.modules.demo_points.models import PointAccount, PointLedgerEntry, PointTransaction
from app.modules.subscriptions.models import SubscriptionPlan
from tests.conftest import PASSWORD, client_for, run_alembic, unique
from tests.test_points import key

BASE = "/api/v1/admin/points"


async def issue(tenant, amount: int, user_id=None, memo: str = "Approved issuance"):  # type: ignore[no-untyped-def]
    r = await tenant.admin.post(
        f"{BASE}/allocations", json={"user_id": str(user_id or tenant.admin_id), "amount": amount, "memo": memo}, headers=key()
    )
    assert r.status_code == 201, r.text
    return r.json()


async def transfer(tenant, to_user_id, amount, *, from_user_id=None, memo=None, headers=None):  # type: ignore[no-untyped-def]
    body = {"to_user_id": str(to_user_id), "amount": amount, "memo": memo}
    if from_user_id:
        body["from_user_id"] = str(from_user_id)
    return await tenant.admin.post(f"{BASE}/transfers", json=body, headers=headers or key())


async def balances(tenant) -> dict[uuid.UUID, int]:  # type: ignore[no-untyped-def]
    async with tenant_session(tenant.id) as db:
        rows = await db.execute(select(PointAccount.user_id, PointAccount.balance).where(PointAccount.kind == "user"))
        return {uid: int(bal) for uid, bal in rows.tuples().all()}


async def counts(tenant) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    async with tenant_session(tenant.id) as db:
        txs = (await db.execute(select(func.count()).select_from(PointTransaction))).scalar_one()
        entries = (await db.execute(select(func.count()).select_from(PointLedgerEntry))).scalar_one()
    return txs, entries


async def assert_consistent(tenant) -> dict:  # type: ignore[no-untyped-def]
    report = (await tenant.admin.get(f"{BASE}/integrity")).json()
    assert report["consistent"], report
    assert (
        report["ledger_net"] == report["balance_net"] == 0
        and not report["mismatched_accounts"]
        and not report["unbalanced_transactions"]
    )
    return report


@pytest.fixture
async def owner_db():  # type: ignore[no-untyped-def]
    """Connection as the schema owner, the most privileged role the deployment uses."""
    engine = create_async_engine(os.environ["DATABASE_URL_MIGRATIONS"])
    yield engine
    await engine.dispose()


# 1 ------------------------------------------------------------------------------------------


async def test_new_accounts_start_with_zero_points(app, tenant) -> None:  # type: ignore[no-untyped-def]
    player = await tenant.create_user("fresh", "player")
    account = (await tenant.admin.get(f"{BASE}/accounts/{player['id']}")).json()
    assert account["balance"] == 0
    assert (await tenant.admin.get(f"{BASE}/me")).json()["balance"] == 0
    assert (await tenant.admin.put("/api/v1/admin/settings", json={"allow_self_registration": True})).status_code == 200
    username = unique("reg")
    async with client_for(app, tenant.host) as c:
        r = await c.post("/api/v1/auth/register", json={"username": username, "full_name": "Registrant", "password": PASSWORD})
        assert r.status_code == 201, r.text
    registrant = await tenant.login(username)
    assert (await registrant.get("/api/v1/portal/points")).json()["balance"] == 0
    assert (await registrant.get("/api/v1/portal/points/history")).json()["total"] == 0
    assert await counts(tenant) == (0, 0)
    # The database itself refuses a wallet that opens with a balance.
    with pytest.raises(DBAPIError, match="zero balance"):
        async with tenant_session(tenant.id) as db:
            await db.execute(
                text("INSERT INTO point_accounts (tenant_id, kind, user_id, balance) VALUES (:t, 'user', :u, 5)"),
                {"t": tenant.id, "u": uuid.uuid4()},
            )


# 2, 3 ---------------------------------------------------------------------------------------


async def test_admin_issues_into_own_wallet_and_credits_another_account(tenant) -> None:  # type: ignore[no-untyped-def]
    player = await tenant.create_user("player", "player")
    issued = await issue(tenant, 1_000, memo="Treasury funding")
    assert issued["type"] == "issue" and issued["kind"] == "allocation" and issued["status"] == "completed"
    assert issued["destination"]["user_id"] == str(tenant.admin_id) and issued["source"]["kind"] == "issuance"
    assert (issued["destination_balance_before"], issued["destination_balance_after"]) == (0, 1_000)
    assert issued["reference"] == issued["id"] and issued["memo"] == "Treasury funding"

    credited = await issue(tenant, 250, user_id=player["id"], memo="Direct credit")
    assert credited["type"] == "credit" and credited["destination"]["user_id"] == player["id"]
    assert (credited["destination_balance_before"], credited["destination_balance_after"]) == (0, 250)
    assert await balances(tenant) == {tenant.admin_id: 1_000, uuid.UUID(player["id"]): 250}

    summary = (await tenant.admin.get(f"{BASE}/summary")).json()
    assert summary["total_issued"] == summary["gross_issued"] == summary["circulating"] == 1_250
    assert summary["own_balance"] == 1_000 and summary["total_distributed"] == 250
    # A reason is mandatory for creating points.
    r = await tenant.admin.post(
        f"{BASE}/allocations", json={"user_id": str(tenant.admin_id), "amount": 5, "memo": " "}, headers=key()
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "reason_required"
    async with tenant_session(tenant.id) as db:
        actions = (await db.execute(select(AuditLog.action).where(AuditLog.resource_type == "point_transaction"))).scalars().all()
    assert sorted(actions) == ["points.allocated", "points.allocated"]


# 4, 5, 6, 7 ---------------------------------------------------------------------------------


async def test_transfer_moves_points_and_history_records_both_sides(tenant) -> None:  # type: ignore[no-untyped-def]
    master = await tenant.create_user("master", "master_agent")
    agent = await tenant.create_user("agent", "agent", parent_id=master["id"])
    await issue(tenant, 500)
    r = await transfer(tenant, agent["id"], 120, memo="Float for agent")
    assert r.status_code == 201, r.text
    tx = r.json()
    assert tx["type"] == "transfer" and tx["source"]["user_id"] == str(tenant.admin_id)
    assert (tx["source_balance_before"], tx["source_balance_after"]) == (500, 380)
    assert (tx["destination_balance_before"], tx["destination_balance_after"]) == (0, 120)
    assert tx["created_by_username"] == "admin"
    assert await balances(tenant) == {tenant.admin_id: 380, uuid.UUID(master["id"]): 0, uuid.UUID(agent["id"]): 120}

    sender = (await tenant.admin.get(f"{BASE}/accounts/{tenant.admin_id}/entries")).json()["items"]
    recipient = (await tenant.admin.get(f"{BASE}/accounts/{agent['id']}/entries")).json()["items"]
    assert (sender[0]["entry_type"], sender[0]["delta"], sender[0]["balance_before"], sender[0]["balance_after"]) == (
        "debit",
        -120,
        500,
        380,
    )
    assert (recipient[0]["entry_type"], recipient[0]["delta"], recipient[0]["balance_after"]) == ("credit", 120, 120)
    assert sender[0]["transaction_id"] == recipient[0]["transaction_id"] == tx["id"]
    assert recipient[0]["counterparty"]["username"] == "admin"

    by_sender = (
        await tenant.admin.get(f"{BASE}/transactions", params={"sender_id": str(tenant.admin_id), "type": "transfer"})
    ).json()
    by_recipient = (await tenant.admin.get(f"{BASE}/transactions", params={"recipient_id": agent["id"]})).json()
    assert [t["id"] for t in by_sender["items"]] == [t["id"] for t in by_recipient["items"]] == [tx["id"]]
    detail = (await tenant.admin.get(f"{BASE}/transactions/{tx['id']}")).json()
    assert detail == tx
    agent_client = await tenant.login("agent")
    assert (await agent_client.get("/api/v1/portal/points")).json()["balance"] == 120
    await assert_consistent(tenant)


async def test_admin_selects_another_sender(tenant) -> None:  # type: ignore[no-untyped-def]
    master = await tenant.create_user("master", "master_agent")
    player = await tenant.create_user("player", "player")
    await issue(tenant, 300, user_id=master["id"])
    tx = (await transfer(tenant, player["id"], 100, from_user_id=master["id"])).json()
    assert tx["source"]["user_id"] == master["id"] and tx["created_by_id"] == str(tenant.admin_id)
    assert await balances(tenant) == {tenant.admin_id: 0, uuid.UUID(master["id"]): 200, uuid.UUID(player["id"]): 100}


# 8, 9, 10, 11 -------------------------------------------------------------------------------


async def test_invalid_transfers_change_nothing(tenant) -> None:  # type: ignore[no-untyped-def]
    player = await tenant.create_user("player", "player")
    await issue(tenant, 50)
    before = (await balances(tenant), await counts(tenant))

    r = await transfer(tenant, player["id"], 51)
    assert r.status_code == 422 and r.json()["error"]["code"] == "insufficient_points"
    assert r.json()["error"]["details"] == {"balance": 50, "requested": 51}
    for amount in (0, -5, 1.5, "10", None):
        assert (await transfer(tenant, player["id"], amount)).status_code == 422, amount
        r = await tenant.admin.post(
            f"{BASE}/allocations", json={"user_id": str(tenant.admin_id), "amount": amount, "memo": "Reasoned"}, headers=key()
        )
        assert r.status_code == 422, amount
        r = await tenant.admin.post(
            f"{BASE}/adjustments",
            json={"user_id": player["id"], "direction": "credit", "amount": amount, "reason": "Fix"},
            headers=key(),
        )
        assert r.status_code == 422, amount
    r = await transfer(tenant, tenant.admin_id, 10)
    assert r.status_code == 422 and r.json()["error"]["code"] == "self_transfer"
    r = await transfer(tenant, player["id"], 1, from_user_id=player["id"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "self_transfer"
    assert (await balances(tenant), await counts(tenant)) == before


# 12 -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["player", "agent", "master_agent", "declarator", "operator"])
async def test_non_admins_cannot_create_or_move_points(tenant, role) -> None:  # type: ignore[no-untyped-def]
    other = await tenant.create_user("someone", "player")
    parent = (await tenant.create_user("parent", "master_agent"))["id"] if role == "agent" else None
    await tenant.create_user("actor", role, parent_id=parent)
    await issue(tenant, 100)
    client = await tenant.login("actor")
    attempts = [
        ("allocations", {"user_id": other["id"], "amount": 10, "memo": "Mint attempt"}),
        ("transfers", {"to_user_id": other["id"], "amount": 10}),
        ("transfers", {"from_user_id": str(tenant.admin_id), "to_user_id": other["id"], "amount": 10}),
        ("adjustments", {"user_id": other["id"], "direction": "credit", "amount": 10, "reason": "Mint attempt"}),
    ]
    for path, body in attempts:
        r = await client.post(f"{BASE}/{path}", json=body, headers=key())
        assert r.status_code == 403, (path, r.text)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=tenant.app), base_url=tenant.admin.base_url) as anon:
        assert (await anon.post(f"{BASE}/transfers", json=attempts[1][1], headers=key())).status_code == 401
    final = await balances(tenant)
    assert final.pop(tenant.admin_id) == 100 and set(final.values()) == {0}
    assert (await counts(tenant))[0] == 1


# 13 -----------------------------------------------------------------------------------------


async def test_tenant_isolation(tenant, other_tenant) -> None:  # type: ignore[no-untyped-def]
    foreign = await other_tenant.create_user("foreign", "player")
    await issue(tenant, 100)
    await issue(other_tenant, 70)
    for r in (
        await transfer(tenant, foreign["id"], 10),
        await transfer(tenant, tenant.admin_id, 10, from_user_id=other_tenant.admin_id),
        await tenant.admin.post(
            f"{BASE}/allocations", json={"user_id": foreign["id"], "amount": 5, "memo": "Cross-tenant"}, headers=key()
        ),
        await tenant.admin.get(f"{BASE}/accounts/{foreign['id']}"),
    ):
        assert r.status_code == 404, r.text
    other_tx = (await other_tenant.admin.get(f"{BASE}/transactions")).json()["items"][0]
    assert (await tenant.admin.get(f"{BASE}/transactions/{other_tx['id']}")).status_code == 404
    assert (await tenant.admin.get(f"{BASE}/transactions", params={"q": other_tx["id"]})).json()["total"] == 0
    # The other tenant's token is not valid on this tenant's host.
    async with client_for(tenant.app, tenant.host) as c:
        c.headers["Authorization"] = other_tenant.admin.headers["Authorization"]
        assert (
            await c.post(f"{BASE}/transfers", json={"to_user_id": str(tenant.admin_id), "amount": 1}, headers=key())
        ).status_code == 401
    assert (await balances(tenant))[tenant.admin_id] == 100
    assert (await balances(other_tenant))[other_tenant.admin_id] == 70
    assert (await tenant.admin.get(f"{BASE}/summary")).json()["total_issued"] == 100


# 14, 15 -------------------------------------------------------------------------------------


async def test_duplicate_requests_post_once(tenant) -> None:  # type: ignore[no-untyped-def]
    player = await tenant.create_user("player", "player")
    await issue(tenant, 100)
    headers = key()
    first = await transfer(tenant, player["id"], 40, headers=headers)
    retries = await asyncio.gather(*(transfer(tenant, player["id"], 40, headers=headers) for _ in range(5)))
    assert first.status_code == 201 and {r.status_code for r in retries} == {200}
    assert {r.headers.get("Idempotent-Replay") for r in retries} == {"true"}
    assert {r.json()["id"] for r in retries} == {first.json()["id"]}
    r = await transfer(tenant, player["id"], 41, headers=headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_conflict"
    assert await balances(tenant) == {tenant.admin_id: 60, uuid.UUID(player["id"]): 40}
    allocation_key = key()
    for _ in range(3):
        await tenant.admin.post(
            f"{BASE}/allocations", json={"user_id": player["id"], "amount": 5, "memo": "Once"}, headers=allocation_key
        )
    assert (await balances(tenant))[uuid.UUID(player["id"])] == 45
    assert (await counts(tenant))[0] == 3


async def test_concurrent_transfers_cannot_overspend(tenant) -> None:  # type: ignore[no-untyped-def]
    recipients = [await tenant.create_user(f"player{i}", "player") for i in range(10)]
    await issue(tenant, 100)
    responses = await asyncio.gather(*(transfer(tenant, r["id"], 30) for r in recipients))
    codes = sorted(r.status_code for r in responses)
    assert codes == [201] * 3 + [422] * 7
    assert {r.json()["error"]["code"] for r in responses if r.status_code == 422} == {"insufficient_points"}
    final = await balances(tenant)
    assert final[tenant.admin_id] == 10 and sum(final.values()) == 100
    await assert_consistent(tenant)


# 16 -----------------------------------------------------------------------------------------


async def test_failure_after_debit_rolls_back_completely(tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    player = await tenant.create_user("player", "player")
    await issue(tenant, 100)
    before = (await balances(tenant), await counts(tenant))

    async def fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("audit store unavailable")

    # Fails after both balances and ledger entries were written in the request transaction.
    monkeypatch.setattr(points_service.audit, "record", fail)
    transport = httpx.ASGITransport(app=tenant.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=tenant.admin.base_url, headers=dict(tenant.admin.headers)) as c:
        headers = key()
        r = await c.post(f"{BASE}/transfers", json={"to_user_id": player["id"], "amount": 60}, headers=headers)
    assert r.status_code == 500
    assert (await balances(tenant), await counts(tenant)) == before
    monkeypatch.undo()
    # Nothing was stored under the key either: the same request now posts normally, once.
    assert (await transfer(tenant, player["id"], 60, headers=headers)).status_code == 201
    assert await balances(tenant) == {tenant.admin_id: 40, uuid.UUID(player["id"]): 60}


# 17 -----------------------------------------------------------------------------------------


async def test_history_cannot_be_modified(tenant, owner_db) -> None:  # type: ignore[no-untyped-def]
    player = await tenant.create_user("player", "player")
    await issue(tenant, 100)
    tx = (await transfer(tenant, player["id"], 10, memo="Original remark")).json()
    for method in ("put", "patch", "delete"):
        r = await getattr(tenant.admin, method)(f"{BASE}/transactions/{tx['id']}")
        assert r.status_code == 405, method
    statements = [
        "UPDATE point_transactions SET memo = 'edited', amount = 1",
        "DELETE FROM point_transactions",
        "UPDATE point_ledger_entries SET delta = delta * 2",
        "DELETE FROM point_ledger_entries",
        "DELETE FROM point_accounts",
        "UPDATE point_accounts SET user_id = NULL, kind = 'issuance'",
    ]
    for statement in statements:
        # Even the schema owner, with tenant context, is refused by the database guards.
        with pytest.raises(DBAPIError, match="append-only|identity cannot change"):
            async with owner_db.begin() as conn:
                await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.id)})
                await conn.execute(text(statement))
    assert (await tenant.admin.get(f"{BASE}/transactions/{tx['id']}")).json() == tx


# 18 -----------------------------------------------------------------------------------------


async def test_balances_match_ledger_and_cannot_change_without_an_entry(tenant, owner_db) -> None:  # type: ignore[no-untyped-def]
    master = await tenant.create_user("master", "master_agent")
    users = [
        await tenant.create_user(f"user{i}", kind, parent_id=master["id"] if kind == "agent" else None)
        for i, kind in enumerate(["player", "agent", "declarator"])
    ]
    await issue(tenant, 1_000)
    await issue(tenant, 75, user_id=users[2]["id"])
    for u, amount in zip(users, (100, 200, 50), strict=True):
        assert (await transfer(tenant, u["id"], amount)).status_code == 201
    await transfer(tenant, users[0]["id"], 30, from_user_id=users[1]["id"])
    await tenant.admin.post(
        f"{BASE}/adjustments",
        json={"user_id": users[0]["id"], "direction": "debit", "amount": 5, "reason": "Correction"},
        headers=key(),
    )
    report = await assert_consistent(tenant)
    assert report["accounts_checked"] == 6 and report["transactions_checked"] == 7
    async with tenant_session(tenant.id) as db:
        ledger = dict(
            (
                await db.execute(
                    select(PointLedgerEntry.account_id, func.sum(PointLedgerEntry.delta)).group_by(PointLedgerEntry.account_id)
                )
            )
            .tuples()
            .all()
        )
        accounts = dict((await db.execute(select(PointAccount.id, PointAccount.balance))).tuples().all())
    assert all(accounts[a] == ledger.get(a, 0) for a in accounts)

    # A balance edit outside the wallet service (no matching ledger entry) fails at commit.
    with pytest.raises(DBAPIError, match="without a matching ledger entry"):
        async with tenant_session(tenant.id) as db:
            await db.execute(update_balance(), {"u": users[0]["id"]})
    # Integrity reporting detects drift if the guard were ever bypassed by a superuser.
    async with owner_db.connect() as conn:
        trans = await conn.begin()
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.id)})
        await conn.execute(text("ALTER TABLE point_accounts DISABLE TRIGGER point_accounts_balance_posted"))
        await conn.execute(update_balance(), {"u": users[0]["id"]})
        report = await points_service.integrity(AsyncSession(bind=conn), tenant.id)
        await trans.rollback()
    assert not report.consistent and report.ledger_net == 0 and report.balance_net == 1_000_000
    assert [m.user_id for m in report.mismatched_accounts] == [uuid.UUID(users[0]["id"])]
    await assert_consistent(tenant)


def update_balance():  # type: ignore[no-untyped-def]
    return text("UPDATE point_accounts SET balance = balance + 1000000 WHERE user_id = :u")


async def test_integrity_report_requires_view_all(tenant) -> None:  # type: ignore[no-untyped-def]
    master = await tenant.create_user("master", "master_agent")
    await tenant.create_user("agent", "agent", parent_id=master["id"])
    agent = await tenant.login("agent")
    assert (await agent.get(f"{BASE}/integrity")).status_code == 403


# 19 -----------------------------------------------------------------------------------------


async def test_refunds_and_adjustments_are_recorded(tenant) -> None:  # type: ignore[no-untyped-def]
    player = await tenant.create_user("player", "player")
    await issue(tenant, 200)
    original = (await transfer(tenant, player["id"], 80, memo="Sent in error")).json()
    r = await tenant.admin.post(f"{BASE}/transactions/{original['id']}/refund", json={"reason": "Wrong recipient"}, headers=key())
    assert r.status_code == 201, r.text
    refund = r.json()
    assert refund["type"] == refund["kind"] == "refund" and refund["reverses_transaction_id"] == original["id"]
    assert refund["source"]["user_id"] == player["id"] and refund["destination"]["user_id"] == str(tenant.admin_id)
    assert (refund["source_balance_before"], refund["source_balance_after"]) == (80, 0)
    assert await balances(tenant) == {tenant.admin_id: 200, uuid.UUID(player["id"]): 0}
    reversed_ = (await tenant.admin.get(f"{BASE}/transactions/{original['id']}")).json()
    assert reversed_["status"] == "reversed" and reversed_["refund_transaction_id"] == refund["id"]
    again = await tenant.admin.post(f"{BASE}/transactions/{original['id']}/refund", json={"reason": "Twice"}, headers=key())
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_refunded"
    not_transfer = (await tenant.admin.get(f"{BASE}/transactions", params={"type": "issue"})).json()["items"][0]
    r = await tenant.admin.post(f"{BASE}/transactions/{not_transfer['id']}/refund", json={"reason": "Nope"}, headers=key())
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_refundable"

    credit = await tenant.admin.post(
        f"{BASE}/adjustments",
        json={"user_id": player["id"], "direction": "credit", "amount": 15, "reason": "Goodwill"},
        headers=key(),
    )
    debit = await tenant.admin.post(
        f"{BASE}/adjustments",
        json={"user_id": player["id"], "direction": "debit", "amount": 5, "reason": "Correction"},
        headers=key(),
    )
    assert (credit.json()["type"], credit.json()["direction"]) == ("adjustment", "credit")
    assert (debit.json()["type"], debit.json()["direction"]) == ("adjustment", "debit")
    over = await tenant.admin.post(
        f"{BASE}/adjustments",
        json={"user_id": player["id"], "direction": "debit", "amount": 11, "reason": "Too much"},
        headers=key(),
    )
    assert over.status_code == 422 and over.json()["error"]["code"] == "insufficient_points"
    assert (await balances(tenant))[uuid.UUID(player["id"])] == 10
    assert (await tenant.admin.get(f"{BASE}/summary")).json()["total_issued"] == 210
    async with tenant_session(tenant.id) as db:
        actions = set((await db.execute(select(AuditLog.action).where(AuditLog.resource_type == "point_transaction"))).scalars())
    assert {"points.refunded", "points.adjusted_credit", "points.adjusted_debit"} <= actions
    await assert_consistent(tenant)


# history filters and export --------------------------------------------------------------


async def test_history_filters_search_pagination_and_export(tenant) -> None:  # type: ignore[no-untyped-def]
    player = await tenant.create_user("filterplayer", "player", email="filter.player@example.com")
    await issue(tenant, 500, memo="Opening float")
    await issue(tenant, 20, user_id=player["id"], memo="Welcome credit")
    t1 = (await transfer(tenant, player["id"], 10)).json()
    t2 = (await transfer(tenant, player["id"], 11, memo="Second")).json()
    await tenant.admin.post(f"{BASE}/transactions/{t1['id']}/refund", json={"reason": "Reverse"}, headers=key())

    async def ids(**params):  # type: ignore[no-untyped-def]
        r = await tenant.admin.get(f"{BASE}/transactions", params=params)
        assert r.status_code == 200, r.text
        return [t["id"] for t in r.json()["items"]], r.json()["total"]

    assert (await ids(type="issue"))[1] == 1 and (await ids(type="credit"))[1] == 1
    assert (await ids(kind="allocation"))[1] == 2 and (await ids(type="refund"))[1] == 1
    assert await ids(status="reversed") == ([t1["id"]], 1)
    assert t1["id"] not in (await ids(status="completed"))[0] and (await ids(status="completed"))[1] == 4
    assert (await ids(status="failed"))[1] == 0
    assert (await ids(q=t2["id"]))[0] == [t2["id"]]
    assert (await ids(q="filter.player@example"))[1] == 4  # credit, two transfers and the refund
    assert (await ids(q=player["id"]))[1] == 4
    assert (await ids(date_from="2000-01-01", date_to="2000-01-02"))[1] == 0
    page_one, total = await ids(page=1, page_size=2)
    page_two, _ = await ids(page=2, page_size=2)
    assert total == 5 and len(page_one) == len(page_two) == 2 and not set(page_one) & set(page_two)
    r = await tenant.admin.get(f"{BASE}/transactions", params={"date_from": "2026-02-02", "date_to": "2026-02-01"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_date_range"

    r = await tenant.admin.get(f"{BASE}/transactions/export", params={"recipient_id": player["id"]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert {row["id"] for row in rows} == {t1["id"], t2["id"]} | {(await ids(type="credit"))[0][0]}
    row = next(row for row in rows if row["id"] == t1["id"])
    assert row["type"] == "transfer" and row["status"] == "reversed" and row["created_at"].endswith("+08:00")
    assert row["sender_username"] == "admin" and row["recipient_username"] == "filterplayer"
    assert (row["source_balance_before"], row["destination_balance_after"]) == ("500", "30")


# end-to-end workflow -------------------------------------------------------------------------


async def test_issue_transfer_receive_workflow(tenant) -> None:  # type: ignore[no-untyped-def]
    player = await tenant.create_user("workflow", "player")
    issued = await issue(tenant, 1_000, memo="Workflow issuance")
    sent = (await transfer(tenant, player["id"], 400, memo="Workflow transfer")).json()
    client = await tenant.login("workflow")
    wallet = (await client.get("/api/v1/portal/points")).json()
    history = (await client.get("/api/v1/portal/points/history")).json()["items"]
    notes = (await client.get("/api/v1/portal/notifications")).json()["items"]
    assert wallet["balance"] == 400 and history[0]["transaction_id"] == sent["id"] and history[0]["entry_type"] == "credit"
    assert notes[0]["type"] == "points.received" and "demo" not in notes[0]["body"].lower()
    assert (await tenant.admin.get(f"{BASE}/me")).json()["balance"] == 600
    summary = (await tenant.admin.get(f"{BASE}/summary")).json()
    assert summary["total_transferred"] == summary["total_distributed"] == 400 and summary["completed_transactions"] == 2
    listed = (await tenant.admin.get(f"{BASE}/transactions")).json()["items"]
    assert [t["id"] for t in listed] == [sent["id"], issued["id"]]
    await assert_consistent(tenant)


# 20 -----------------------------------------------------------------------------------------


async def test_migrations_preserve_existing_balances_and_ledger(tenant) -> None:  # type: ignore[no-untyped-def]
    player = await tenant.create_user("legacy", "player")
    await issue(tenant, 900)
    sent = (await transfer(tenant, player["id"], 300)).json()
    await tenant.admin.post(f"{BASE}/transactions/{sent['id']}/refund", json={"reason": "Legacy refund"}, headers=key())
    await transfer(tenant, player["id"], 120)

    async def snapshot():  # type: ignore[no-untyped-def]
        async with tenant_session(tenant.id) as db:
            accounts = (await db.execute(select(PointAccount.id, PointAccount.balance).order_by(PointAccount.id))).all()
            txs = (
                await db.execute(
                    select(PointTransaction.id, PointTransaction.amount, PointTransaction.reverses_transaction_id).order_by(
                        PointTransaction.id
                    )
                )
            ).all()
            entries = (
                await db.execute(
                    select(PointLedgerEntry.id, PointLedgerEntry.delta, PointLedgerEntry.balance_after).order_by(
                        PointLedgerEntry.id
                    )
                )
            ).all()
        return accounts, txs, entries

    async def plan_approvals() -> dict:  # type: ignore[type-arg]
        async with platform_session() as db:
            rows = (await db.execute(select(SubscriptionPlan.id, SubscriptionPlan.prices_approved_at))).all()
        return dict(rows)

    async def restore_plan_approvals(saved: dict) -> None:  # type: ignore[type-arg]
        # Downgrading past 0012 drops plan price approvals by design (the column goes away);
        # put the session's fixture approvals back so later tests can still provision tenants.
        async with platform_session() as db:
            for plan_id, approved_at in saved.items():
                if approved_at is not None:
                    await db.execute(
                        update(SubscriptionPlan).where(SubscriptionPlan.id == plan_id).values(prices_approved_at=approved_at)
                    )

    approvals = await plan_approvals()
    before = await snapshot()
    # Re-applying the guards migration over live data changes no row.
    for args in (("downgrade", "0010"), ("upgrade", "head")):
        result = run_alembic(*args)
        assert result.returncode == 0, result.stderr
    assert await snapshot() == before
    # The plan rows survive the round trip; only the 0012 approval stamps are dropped.
    assert set(await plan_approvals()) == set(approvals)
    await restore_plan_approvals(approvals)
    # Rolling back the refund schema is refused while refunds exist, leaving everything intact.
    result = run_alembic("downgrade", "0009")
    assert result.returncode != 0 and "Cannot downgrade after refunds" in result.stderr
    assert run_alembic("upgrade", "head").returncode == 0
    head = run_alembic("heads").stdout.split()[0]
    assert f"{head} (head)" in run_alembic("current").stdout
    await restore_plan_approvals(approvals)
    assert await snapshot() == before
    await assert_consistent(tenant)
