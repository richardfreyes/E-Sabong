"""Retirement of the transferable points module.

Writes are gone (no endpoints, no service code, and migration 0013 rejects new rows for every
role); existing history is preserved untouched and stays readable as a read-only archive; nothing
is merged into Arena Points; players see no points at all.

Legacy history is recreated the way the retired service wrote it (double-entry rows at revision
0012, before the guard existed), then the database is upgraded and checked.
"""

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import platform_session, tenant_session
from app.modules.audit.service import Actor, record
from app.modules.demo_points import archive
from app.modules.demo_points.models import AccountKind, PointAccount, PointLedgerEntry, PointTransaction, TransactionKind
from tests.conftest import legacy_points_rows, run_alembic

ADMIN = "/api/v1/admin"
PORTAL = "/api/v1/portal"


def _alembic(*args: str) -> None:
    result = run_alembic(*args)
    assert result.returncode == 0, result.stderr


async def _seed_legacy(tenant_id: uuid.UUID, admin_id: uuid.UUID, users: dict[str, uuid.UUID]) -> dict[str, uuid.UUID]:
    """At revision 0012: an issuance account, one account per user, and a realistic history, each
    operation in its own database transaction as the retired service wrote it.

    Returns the transaction ids by label. Balances afterwards: admin 600, p1 200, p2 50, issuance -850.
    """
    async with tenant_session(tenant_id) as db:
        issuance = PointAccount(tenant_id=tenant_id, kind=AccountKind.ISSUANCE, user_id=None, balance=0)
        made = {
            name: PointAccount(tenant_id=tenant_id, kind=AccountKind.USER, user_id=uid, balance=0) for name, uid in users.items()
        }
        db.add_all([issuance, *made.values()])
        await db.flush()
        ids = {"issuance": issuance.id, **{name: a.id for name, a in made.items()}}
    txs: dict[str, uuid.UUID] = {}

    async def post(
        label: str, kind: TransactionKind, src_name: str, dst_name: str, amount: int, reverses: str | None = None
    ) -> None:
        async with tenant_session(tenant_id) as db:
            src, dst = await db.get(PointAccount, ids[src_name]), await db.get(PointAccount, ids[dst_name])
            assert src is not None and dst is not None
            tx = PointTransaction(
                tenant_id=tenant_id,
                kind=kind,
                idempotency_key=f"legacy-{label}-{uuid.uuid4().hex[:8]}",
                request_hash="0" * 64,
                from_account_id=src.id,
                to_account_id=dst.id,
                amount=amount,
                memo=f"legacy {label}",
                created_by_id=admin_id,
                reverses_transaction_id=txs[reverses] if reverses else None,
            )
            db.add(tx)
            await db.flush()
            src.balance -= amount
            dst.balance += amount
            await db.flush()
            for account in (src, dst):
                delta = -amount if account is src else amount
                db.add(
                    PointLedgerEntry(
                        tenant_id=tenant_id,
                        transaction_id=tx.id,
                        account_id=account.id,
                        delta=delta,
                        balance_after=account.balance,
                    )
                )
            await db.flush()
            await record(
                db,
                actor=Actor.system(),
                tenant_id=tenant_id,
                action=f"points.{kind.value}",
                resource_type="point_transaction",
                resource_id=tx.id,
                changes={"amount": amount},
            )
            txs[label] = tx.id

    await post("issue", TransactionKind.ALLOCATION, "issuance", "admin", 900)
    await post("to-p1", TransactionKind.TRANSFER, "admin", "p1", 300)
    await post("to-p2", TransactionKind.TRANSFER, "admin", "p2", 100)
    await post("refund-p2", TransactionKind.REFUND, "p2", "admin", 100, reverses="to-p2")
    await post("correction", TransactionKind.ADJUSTMENT, "p1", "issuance", 50)
    await post("p1-to-p2", TransactionKind.TRANSFER, "p1", "p2", 50)
    return txs


async def _snapshot(tenant_id: uuid.UUID) -> tuple:  # type: ignore[type-arg]
    async with platform_session() as db:
        q = lambda sql: db.execute(text(sql), {"t": tenant_id})  # noqa: E731
        snapshots = []
        for table in (
            "point_accounts",
            "point_transactions",
            "point_ledger_entries",
            "audit_logs",
            "play_wallets",
            "play_wallet_entries",
        ):
            sql = f"SELECT row_to_json(a)::text FROM {table} a WHERE tenant_id = :t ORDER BY id"  # noqa: S608 -- fixed tables
            snapshots.append((await q(sql)).all())
    return tuple(snapshots)


# ------------------------------------------------------------------------------------------ writes


@pytest.mark.parametrize(
    "path",
    ["/points/allocations", "/points/transfers", "/points/adjustments", f"/points/transactions/{uuid.uuid4()}/refund"],
)
async def test_points_write_endpoints_are_gone(tenant, path) -> None:  # type: ignore[no-untyped-def]
    r = await tenant.admin.post(
        ADMIN + path,
        json={"user_id": str(tenant.admin_id), "amount": 10, "memo": "x", "reason": "x"},
        headers={"Idempotency-Key": f"k-{uuid.uuid4()}"},
    )
    assert r.status_code in (404, 405), r.text


async def test_new_tenants_and_users_get_no_points_account(tenant) -> None:  # type: ignore[no-untyped-def]
    await tenant.create_user("fresh-agent", "master_agent")
    await tenant.create_user("fresh-player", "player")
    assert await legacy_points_rows(tenant.id) == (0, 0, 0)


async def test_the_database_rejects_any_new_points_row_for_every_role(tenant) -> None:  # type: ignore[no-untyped-def]
    # The application role (what the API uses) ...
    with pytest.raises(DBAPIError, match="retired"):
        async with tenant_session(tenant.id) as db:
            db.add(PointAccount(tenant_id=tenant.id, kind=AccountKind.USER, user_id=tenant.admin_id, balance=0))
            await db.flush()
    # ... and the schema owner (migrations) alike.
    engine = create_async_engine(os.environ["DATABASE_URL_MIGRATIONS"])
    try:
        for table in ("point_accounts", "point_transactions", "point_ledger_entries"):
            async with engine.connect() as conn:
                with pytest.raises(DBAPIError, match="retired"):
                    await conn.execute(text(f"INSERT INTO {table} DEFAULT VALUES"))
    finally:
        await engine.dispose()


async def test_retired_permissions_are_no_longer_offered(tenant) -> None:  # type: ignore[no-untyped-def]
    codes = {p["code"]: p["description"] for p in (await tenant.admin.get(f"{ADMIN}/permissions")).json()}
    assert not {"points.allocate", "points.transfer", "points.adjust"} & set(codes)
    assert "read-only" in codes["points.view_all"]


async def test_players_see_no_points_anywhere(tenant) -> None:  # type: ignore[no-untyped-def]
    await tenant.create_user("viewer", "player")
    player = await tenant.login("viewer")
    assert (await player.get(f"{PORTAL}/points")).status_code == 404
    assert (await player.get(f"{PORTAL}/points/history")).status_code == 404
    dashboard = (await player.get(f"{PORTAL}/dashboard")).json()
    assert "balance" not in dashboard and "points_disclaimer" not in dashboard


async def test_arena_points_history_is_the_players_own_ledger(tenant) -> None:  # type: ignore[no-untyped-def]
    r = await tenant.admin.put(f"{ADMIN}/betting/settings", json={"enabled": True, "starting_balance": "500.00"})
    assert r.status_code == 200, r.text
    ana_user = await tenant.create_user("ana-ap", "player")
    ben_user = await tenant.create_user("ben-ap", "player")
    ana, ben = await tenant.login("ana-ap"), await tenant.login("ben-ap")
    for c in (ana, ben):
        assert (await c.get(f"{PORTAL}/betting")).status_code == 200  # opens the wallet with the configured grant
    # A user_id for someone else is ignored: the endpoint is always scoped to the caller.
    rows = (await ana.get(f"{PORTAL}/betting/entries", params={"user_id": ben_user["id"]})).json()["items"]
    assert [(r["user_id"], r["kind"], r["delta"], r["balance_after"]) for r in rows] == [
        (ana_user["id"], "grant", "500.00", "500.00")
    ]
    assert rows[0]["memo"] == "Starting Arena Points (configured by an administrator)"
    config = (await ana.get(f"{PORTAL}/betting")).json()
    assert "Arena Points" in config["wallet"]["disclaimer"] and "no cash value" in config["wallet"]["disclaimer"]
    # Betting never creates legacy points.
    assert await legacy_points_rows(tenant.id) == (0, 0, 0)


# ------------------------------------------------------------------------------- the archive itself


async def test_migration_keeps_legacy_history_intact_and_readable(tenant, other_tenant) -> None:  # type: ignore[no-untyped-def]
    p1 = await tenant.create_user("legacy-p1", "player")
    p2 = await tenant.create_user("legacy-p2", "player")
    users = {"admin": tenant.admin_id, "p1": uuid.UUID(p1["id"]), "p2": uuid.UUID(p2["id"])}
    _alembic("downgrade", "0012")  # before retirement: the guard does not exist yet
    try:
        txs = await _seed_legacy(tenant.id, tenant.admin_id, users)
        before = await _snapshot(tenant.id)
        async with platform_session() as db:
            report_before = archive.fingerprint([r for r in await archive.report(db) if r.slug == tenant.slug])
    finally:
        _alembic("upgrade", "head")

    # Every row identical after the migration: nothing moved, merged, converted or deleted.
    assert await _snapshot(tenant.id) == before
    async with platform_session() as db:
        [after] = [r for r in await archive.report(db) if r.slug == tenant.slug]
    assert archive.fingerprint([after]) == report_before
    assert after.consistent and after.transactions == 6 and after.ledger_entries == 12
    assert after.user_balances == 850 and after.net_balance == 0  # admin 600 + p1 200 + p2 50; issuance -850

    # Read-only archive endpoints still answer from the preserved rows.
    a = tenant.admin
    listed = (await a.get(f"{ADMIN}/points/transactions", params={"page_size": 50})).json()
    assert listed["total"] == 6
    detail = (await a.get(f"{ADMIN}/points/transactions/{txs['refund-p2']}")).json()
    assert detail["kind"] == "refund" and detail["amount"] == 100
    export = await a.get(f"{ADMIN}/points/transactions/export")
    assert export.status_code == 200 and export.text.count("\n") >= 7  # header + 6 rows
    balances = {r["username"]: r["balance"] for r in (await a.get(f"{ADMIN}/points/accounts")).json()["items"]}
    assert (balances["legacy-p1"], balances["legacy-p2"]) == (200, 50)
    entries = (await a.get(f"{ADMIN}/points/accounts/{p1['id']}/entries")).json()["items"]
    assert [e["balance_after"] for e in entries] == [200, 250, 300]  # newest first
    summary = (await a.get(f"{ADMIN}/points/summary")).json()
    assert summary["total_issued"] == 850 and summary["circulating"] == 850 and "read-only" in summary["disclaimer"]
    integrity = (await a.get(f"{ADMIN}/points/integrity")).json()
    assert integrity["consistent"] is True and integrity["transactions_checked"] == 6
    assert integrity["ledger_net"] == 0 and integrity["balance_net"] == 0 and not integrity["mismatched_accounts"]

    # Writes stay impossible over existing data, through the API and in the database.
    r = await a.post(
        f"{ADMIN}/points/transfers", json={"to_user_id": p1["id"], "amount": 1}, headers={"Idempotency-Key": "k-retired-1"}
    )
    assert r.status_code in (404, 405)
    with pytest.raises(DBAPIError):  # 0011: a balance may only change with a new ledger row, which 0013 forbids
        async with tenant_session(tenant.id) as db:
            await db.execute(text("UPDATE point_accounts SET balance = balance + 1 WHERE user_id = :u"), {"u": p1["id"]})
            await db.commit()
    for table in ("point_accounts", "point_transactions", "point_ledger_entries"):
        for verb in ("UPDATE", "DELETE"):
            sql = (
                f"UPDATE {table} SET created_at = created_at WHERE tenant_id = :t"  # noqa: S608 -- fixed tables
                if verb == "UPDATE"
                else f"DELETE FROM {table} WHERE tenant_id = :t"  # noqa: S608
            )
            with pytest.raises(DBAPIError):
                async with tenant_session(tenant.id) as db:
                    await db.execute(text(sql), {"t": tenant.id})
    assert await _snapshot(tenant.id) == before

    # Existing audit trails and read permission checks survive retirement.
    audit = (await a.get(f"{ADMIN}/audit-logs", params={"resource_type": "point_transaction"})).json()
    assert len([row for row in audit["items"] if row["resource_id"] in {str(t) for t in txs.values()}]) == 6
    for role in ("player", "operator"):
        await tenant.create_user(f"no-archive-{role}", role)
        denied = await tenant.login(f"no-archive-{role}")
        for path in ("/summary", "/transactions", "/accounts", "/integrity"):
            assert (await denied.get(f"{ADMIN}/points{path}")).status_code == 403

    # The archive stays tenant-isolated.
    o = other_tenant.admin
    assert (await o.get(f"{ADMIN}/points/transactions/{txs['issue']}")).status_code == 404
    assert (await o.get(f"{ADMIN}/points/transactions")).json()["total"] == 0
    # Players never see it, and their Arena Points are not affected by it.
    player = await tenant.login("legacy-p1")
    assert (await player.get(f"{PORTAL}/points")).status_code == 404


async def test_archive_filters_exports_and_downline_scope(tenant) -> None:  # type: ignore[no-untyped-def]
    master = await tenant.create_user("archive-master", "master_agent")
    agent = await tenant.create_user("archive-agent", "agent", parent_id=master["id"])
    child = await tenant.create_user("archive-child", "player", parent_id=agent["id"])
    outside = await tenant.create_user("archive-outside", "player")
    _alembic("downgrade", "0012")
    try:
        txs = await _seed_legacy(
            tenant.id,
            tenant.admin_id,
            {"admin": tenant.admin_id, "p1": uuid.UUID(child["id"]), "p2": uuid.UUID(outside["id"])},
        )
    finally:
        _alembic("upgrade", "head")
    a = tenant.admin
    for filters, expected in [
        ({"type": "refund"}, {str(txs["refund-p2"])}),
        ({"status": "reversed"}, {str(txs["to-p2"])}),
        ({"q": str(txs["correction"])}, {str(txs["correction"])}),
        ({"sender_id": child["id"]}, {str(txs["correction"]), str(txs["p1-to-p2"])}),
        ({"date_from": "2000-01-01", "date_to": "2000-01-01"}, set()),
        ({"status": "pending"}, set()),
    ]:
        result = await a.get(f"{ADMIN}/points/transactions", params=filters)
        assert result.status_code == 200, result.text
        assert {row["id"] for row in result.json()["items"]} == expected
    assert (
        await a.get(f"{ADMIN}/points/transactions", params={"date_from": "2030-01-02", "date_to": "2030-01-01"})
    ).status_code == 422
    first = (await a.get(f"{ADMIN}/points/transactions", params={"page_size": 2})).json()
    second = (await a.get(f"{ADMIN}/points/transactions", params={"page_size": 2, "page": 2})).json()
    assert first["total"] == second["total"] == 6
    assert not {row["id"] for row in first["items"]} & {row["id"] for row in second["items"]}
    reader = await tenant.login("archive-master")
    accounts = (await reader.get(f"{ADMIN}/points/accounts")).json()["items"]
    assert {row["user_id"] for row in accounts} == {child["id"]}
    assert (await reader.get(f"{ADMIN}/points/accounts/{outside['id']}")).status_code == 404
    assert (await reader.get(f"{ADMIN}/points/transactions/{txs['to-p2']}")).status_code == 404
    assert (await reader.get(f"{ADMIN}/points/transactions/export", params={"user_id": outside["id"]})).status_code == 404
    visible = (await reader.get(f"{ADMIN}/points/transactions")).json()
    assert visible["total"] == 3
    csv = await reader.get(f"{ADMIN}/points/transactions/export")
    assert csv.status_code == 200 and str(txs["to-p2"]) not in csv.text
    assert str(txs["to-p1"]) in csv.text
