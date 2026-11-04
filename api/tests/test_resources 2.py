"""Real PostgreSQL resource-ledger tests; use the isolated conftest database only."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.database import platform_session, tenant_session
from app.modules.resources.models import ResourceLedgerEntry, ResourceTransaction, ResourceWallet
from tests.conftest import unique

ROOT = "/api/v1/resources"


def request(*, amount=10, kind="compute", target=None, key=None):
    body = {
        "amount": amount,
        "resource_kind": kind,
        "reason": "Operational compute quota",
        "idempotency_key": key or str(uuid.uuid4()),
    }
    if target:
        body["target_user_id"] = str(target)
    return body


async def enable(tenant):
    response = await tenant.admin.put(ROOT + "/settings", json={"enabled": True})
    assert response.status_code == 200, response.text


async def issue(tenant, uid, amount=100, kind="compute"):
    response = await tenant.admin.post(ROOT + "/issuances", json=request(amount=amount, kind=kind, target=uid))
    assert response.status_code == 201, response.text
    return response.json()


async def own_balance(client, kind="compute"):
    response = await client.get(ROOT + "/balance/" + kind)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["balance"] == result["available_balance"]
    return result["balance"]


async def assert_consistent(tid):
    async with tenant_session(tid) as db:
        wallets = list((await db.execute(select(ResourceWallet).where(ResourceWallet.tenant_id == tid))).scalars())
        for wallet in wallets:
            total = (
                await db.execute(
                    select(func.coalesce(func.sum(ResourceLedgerEntry.delta), 0)).where(
                        ResourceLedgerEntry.tenant_id == tid,
                        ResourceLedgerEntry.wallet_id == wallet.id,
                    )
                )
            ).scalar_one()
            assert wallet.balance == total
            assert wallet.balance >= 0


async def test_default_disabled_and_read_has_no_side_effects(tenant):
    assert (await tenant.admin.get(ROOT + "/settings")).json() == {"enabled": False}
    assert await own_balance(tenant.admin) == 0
    async with tenant_session(tenant.id) as db:
        assert (await db.execute(select(func.count()).select_from(ResourceWallet))).scalar_one() == 0
    response = await tenant.admin.post(ROOT + "/issuances", json=request(target=tenant.admin_id))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "resources_disabled"


async def test_receipts_idempotency_usage_and_refund(tenant):
    await enable(tenant)
    player = await tenant.create_user(unique("user"), "player")
    client = await tenant.login(player["username"])
    body = request(target=player["id"], amount=40)
    issued = await tenant.admin.post(ROOT + "/issuances", json=body)
    assert issued.status_code == 201, issued.text
    tx = issued.json()
    # Direct provisioning: nothing is debited from the issuing admin or any other account.
    assert tx["source_user_id"] is None and tx["source_balance_after"] is None
    assert tx["target_user_id"] == player["id"] and tx["target_balance_after"] == 40
    assert await own_balance(tenant.admin) == 0
    used = await client.post(ROOT + "/usage", json=request(amount=20))
    assert used.status_code == 201, used.text
    replay = await tenant.admin.post(ROOT + "/issuances", json=body)
    assert replay.status_code == 201
    assert replay.json()["id"] == tx["id"] and replay.json()["replayed"]
    assert replay.json()["target_balance_after"] == 40  # immutable original receipt
    assert await own_balance(client) == 20
    body["amount"] = 41
    assert (await tenant.admin.post(ROOT + "/issuances", json=body)).status_code == 409
    refund = {"transaction_id": used.json()["id"], "reason": "Unused compute", "idempotency_key": str(uuid.uuid4())}
    response = await tenant.admin.post(ROOT + "/refunds", json=refund)
    assert response.status_code == 201, response.text
    assert await own_balance(client) == 40
    assert (await tenant.admin.post(ROOT + "/refunds", json=refund)).json()["replayed"]
    refund["idempotency_key"] = str(uuid.uuid4())
    assert (await tenant.admin.post(ROOT + "/refunds", json=refund)).status_code == 409
    refund["transaction_id"] = tx["id"]
    assert (await tenant.admin.post(ROOT + "/refunds", json=refund)).status_code == 409
    # Admin audit sees all; the end user sees the same three rows because each touches only them.
    assert (await tenant.admin.get(ROOT + "/transactions")).json()["total"] == 3
    assert (await client.get(ROOT + "/transactions")).json()["total"] == 3
    await assert_consistent(tenant.id)


async def test_no_account_can_move_units_down_the_hierarchy(tenant):
    await enable(tenant)
    master = await tenant.create_user(unique("master"), "master_agent")
    agent = await tenant.create_user(unique("agent"), "agent", master["id"])
    player = await tenant.create_user(unique("player"), "player", agent["id"])
    mc = await tenant.login(master["username"])
    ac = await tenant.login(agent["username"])
    pc = await tenant.login(player["username"])
    for uid in (master["id"], agent["id"], player["id"]):
        await issue(tenant, uid)
    # Admin search for issuance recipients covers the whole tenant; nobody else may search.
    found = {u["id"] for u in (await tenant.admin.get(ROOT + "/targets")).json()["items"]}
    assert {master["id"], agent["id"], player["id"]} <= found
    assert (await tenant.admin.get(ROOT + "/targets", params={"q": player["username"]})).json()["total"] == 1
    for client, target in ((mc, agent["id"]), (ac, player["id"]), (pc, master["id"])):
        assert (await client.get(ROOT + "/targets")).status_code == 403
        # There is no allocation or transfer endpoint at all.
        for path in ("/allocations", "/transfers"):
            assert (await client.post(ROOT + path, json=request(target=target))).status_code in (404, 405)
        # Issuance is admin provisioning only, even to one's own downline.
        assert (await client.post(ROOT + "/issuances", json=request(target=target))).status_code == 403
        # Usage cannot name a recipient: the schema forbids extra fields.
        assert (await client.post(ROOT + "/usage", json=request(target=target))).status_code == 422
    for client in (mc, ac, pc):
        assert await own_balance(client) == 100
    assert (await ac.put(ROOT + "/settings", json={"enabled": True})).status_code == 403
    async with tenant_session(tenant.id) as db:
        both = select(func.count()).where(
            ResourceTransaction.source_wallet_id.is_not(None), ResourceTransaction.target_wallet_id.is_not(None)
        )
        assert (await db.execute(both)).scalar_one() == 0
    await assert_consistent(tenant.id)


async def test_insufficient_kind_isolation_and_disable(tenant):
    await enable(tenant)
    player = await tenant.create_user(unique("player"), "player")
    await issue(tenant, tenant.admin_id, amount=50, kind="seats")
    assert await own_balance(tenant.admin, "seats") == 50
    assert await own_balance(tenant.admin, "compute") == 0
    assert (await tenant.admin.post(ROOT + "/usage", json=request(amount=1, kind="compute"))).status_code == 409
    assert (await tenant.admin.post(ROOT + "/usage", json=request(amount=51, kind="seats"))).status_code == 409
    await issue(tenant, player["id"], amount=5, kind="seats")
    assert await own_balance(tenant.admin, "seats") == 50  # issuing to another account debits nobody
    assert await own_balance(tenant.admin, "seats") == 50
    await tenant.admin.put(ROOT + "/settings", json={"enabled": False})
    assert (await tenant.admin.post(ROOT + "/usage", json=request(amount=1, kind="seats"))).status_code == 403
    await assert_consistent(tenant.id)


async def test_tenant_isolation(tenant, other_tenant):
    await enable(tenant)
    await enable(other_tenant)
    tx = await issue(tenant, tenant.admin_id)
    assert (await other_tenant.admin.get(ROOT + "/transactions/" + tx["id"])).status_code == 404
    assert (await tenant.admin.post(ROOT + "/issuances", json=request(target=other_tenant.admin_id))).status_code == 404
    assert (
        await other_tenant.admin.post(
            ROOT + "/refunds",
            json={
                "transaction_id": tx["id"],
                "reason": "Not ours",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
    ).status_code == 404
    async with tenant_session(other_tenant.id) as db:
        assert (await db.execute(select(ResourceWallet).where(ResourceWallet.tenant_id == tenant.id))).scalars().all() == []
        assert (
            await db.execute(select(ResourceTransaction).where(ResourceTransaction.tenant_id == tenant.id))
        ).scalars().all() == []
    assert await own_balance(other_tenant.admin) == 0


async def test_concurrent_usage_cannot_double_spend(tenant):
    await enable(tenant)
    await issue(tenant, tenant.admin_id, 100)
    results = await asyncio.gather(*[tenant.admin.post(ROOT + "/usage", json=request(amount=10)) for _ in range(24)])
    codes = [r.status_code for r in results]
    assert codes.count(201) == 10, [(r.status_code, r.text) for r in results]
    assert codes.count(409) == 14
    assert await own_balance(tenant.admin) == 0
    await assert_consistent(tenant.id)


async def test_concurrent_duplicate_first_wallet_creation(tenant):
    await enable(tenant)
    player = await tenant.create_user(unique("player"), "player")
    body = request(target=player["id"], amount=70)
    results = await asyncio.gather(*[tenant.admin.post(ROOT + "/issuances", json=body) for _ in range(12)])
    assert all(r.status_code == 201 for r in results), [(r.status_code, r.text) for r in results]
    assert len({r.json()["id"] for r in results}) == 1
    assert sum(not r.json()["replayed"] for r in results) == 1
    client = await tenant.login(player["username"])
    assert await own_balance(client) == 70
    await assert_consistent(tenant.id)


async def test_refund_competes_safely_with_usage(tenant):
    await enable(tenant)
    await issue(tenant, tenant.admin_id, 100)
    used = await tenant.admin.post(ROOT + "/usage", json=request(amount=80))
    refund = {"transaction_id": used.json()["id"], "reason": "Compute job cancelled", "idempotency_key": str(uuid.uuid4())}
    results = await asyncio.gather(
        tenant.admin.post(ROOT + "/refunds", json=refund),
        tenant.admin.post(ROOT + "/refunds", json={**refund, "idempotency_key": str(uuid.uuid4())}),
        tenant.admin.post(ROOT + "/usage", json=request(amount=20)),
        tenant.admin.post(ROOT + "/usage", json=request(amount=20)),
    )
    assert sorted(r.status_code for r in results[:2]) == [201, 409]
    assert all(r.status_code in (201, 409) for r in results[2:])
    debited = 20 * sum(r.status_code == 201 for r in results[2:])
    assert await own_balance(tenant.admin) == 100 - debited
    await assert_consistent(tenant.id)


async def test_database_guards(tenant):
    await enable(tenant)
    tx = await issue(tenant, tenant.admin_id)
    for statement in (
        "UPDATE resource_ledger_entries SET delta = 9 WHERE transaction_id = :id",
        "DELETE FROM resource_transactions WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with tenant_session(tenant.id) as db:
                await db.execute(text(statement), {"id": uuid.UUID(tx["id"])})
    # Even a direct wallet update cannot commit without matching ledger postings.
    with pytest.raises(DBAPIError):
        async with tenant_session(tenant.id) as db:
            await db.execute(
                text("UPDATE resource_wallets SET balance = balance + 1 WHERE user_id = :id"), {"id": tenant.admin_id}
            )
    assert await own_balance(tenant.admin) == 100


async def test_resource_operations_leave_unrelated_tables_unchanged(tenant):
    # No imports of gameplay services/models: read-only snapshot all unrelated public tables.
    # Ignore generic API telemetry/audit/auth rows; compare immutable/history and balance tables.
    async def snapshot():
        async with platform_session() as db:
            tables = (
                (
                    await db.execute(
                        text("""
                SELECT tablename FROM pg_tables WHERE schemaname = 'public'
                AND (tablename LIKE 'play_%' OR tablename LIKE 'point_%' OR tablename = 'bets')
                ORDER BY tablename
            """)
                    )
                )
                .scalars()
                .all()
            )
            return {
                table: (
                    await db.execute(
                        text(
                            "SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY id), '[]'::jsonb) "  # noqa: S608
                            f'FROM "{table}" t WHERE tenant_id = :tid'
                        ),
                        {"tid": tenant.id},
                    )
                ).scalar_one()
                for table in tables
            }

    before = await snapshot()
    await enable(tenant)
    player = await tenant.create_user(unique("resourceuser"), "player")
    await issue(tenant, tenant.admin_id)
    await issue(tenant, player["id"])
    usage = await tenant.admin.post(ROOT + "/usage", json=request())
    await tenant.admin.post(
        ROOT + "/refunds",
        json={
            "transaction_id": usage.json()["id"],
            "reason": "Cancelled utility",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert await snapshot() == before


@pytest.fixture(scope="module", autouse=True)
async def discard_isolated_resource_test_rows():
    """Leave the shared disposable DB ready for existing empty-ledger migration rehearsals."""
    yield
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    url = get_settings().database_url_migrations
    assert url.rsplit("/", 1)[-1].split("?")[0].startswith("esabong_test")
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("TRUNCATE resource_ledger_entries, resource_transactions, resource_wallets, resource_settings")
            )
    finally:
        await engine.dispose()


async def test_self_service_resource_permission_does_not_grant_admin_portal(tenant, app):
    from tests.conftest import PASSWORD, client_for

    player = await tenant.create_user(unique("ordinary"), "player")
    async with client_for(app, tenant.host) as client:
        response = await client.post(
            "/api/v1/auth/login", headers={"X-Portal": "admin"}, json={"username": player["username"], "password": PASSWORD}
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "portal_not_permitted"


async def test_permissions_and_account_status_rechecked_on_retry(tenant):
    await enable(tenant)
    agent = await tenant.create_user(unique("master"), "master_agent")
    player = await tenant.create_user(unique("descendant"), "player", agent["id"])
    client = await tenant.login(agent["username"])
    await issue(tenant, agent["id"])
    body = request()
    assert (await client.post(ROOT + "/usage", json=body)).status_code == 201
    # Removing usage permission from the role must block an otherwise identical retry.
    async with tenant_session(tenant.id) as db:
        await db.execute(
            text("""
            DELETE FROM role_permissions WHERE tenant_id = :tid AND permission = 'resources.consume'
            AND role_id IN (SELECT role_id FROM user_roles WHERE user_id = :uid AND tenant_id = :tid)
        """),
            {"tid": tenant.id, "uid": uuid.UUID(agent["id"])},
        )
    assert (await client.post(ROOT + "/usage", json=body)).status_code == 403
    assert (await tenant.admin.post(f"/api/v1/admin/users/{player['id']}/suspend")).status_code == 200
    assert (await tenant.admin.post(ROOT + "/issuances", json=request(target=player["id"]))).status_code == 404


async def test_issuance_rolls_back_as_a_unit(tenant):
    from app.core.deps import CurrentUser
    from app.core.permissions import ALL_PERMISSIONS
    from app.core.tenancy import TenantContext
    from app.modules.resources.models import ResourceAction
    from app.modules.resources.schemas import IssuanceRequest
    from app.modules.resources.service import post
    from app.modules.tenants.models import TenantStatus
    from app.modules.users.models import TenantUser

    await enable(tenant)
    player = await tenant.create_user(unique("rollback"), "player")
    await issue(tenant, tenant.admin_id)
    key = str(uuid.uuid4())
    with pytest.raises(RuntimeError, match="downstream failure"):
        async with tenant_session(tenant.id) as db:
            current = CurrentUser(
                user=await db.get(TenantUser, tenant.admin_id),
                permissions=ALL_PERMISSIONS,
                tenant=TenantContext(id=tenant.id, slug=tenant.slug, name="Test", status=TenantStatus.ACTIVE),
                session_family=uuid.uuid4(),
            )
            await post(db, current, ResourceAction.ISSUE, IssuanceRequest(**request(target=player["id"], key=key)))
            raise RuntimeError("downstream failure")
    assert await own_balance(tenant.admin) == 100
    client = await tenant.login(player["username"])
    assert await own_balance(client) == 0
    async with tenant_session(tenant.id) as db:
        assert (
            await db.execute(select(ResourceTransaction.id).where(ResourceTransaction.idempotency_key == key))
        ).first() is None
    await assert_consistent(tenant.id)


async def test_database_rejects_cross_tenant_and_cross_resource_references(tenant, other_tenant):
    await enable(tenant)
    await enable(other_tenant)
    await issue(tenant, tenant.admin_id)
    await issue(other_tenant, other_tenant.admin_id)
    async with tenant_session(other_tenant.id) as db:
        foreign_id = (await db.execute(select(ResourceWallet.id))).scalar_one()
    with pytest.raises(DBAPIError):
        async with tenant_session(tenant.id) as db:
            db.add(ResourceWallet(tenant_id=tenant.id, user_id=other_tenant.admin_id, resource_kind="seats", balance=0))
            await db.flush()
    for target_id, kind in ((foreign_id, "compute"), (None, "seats")):
        with pytest.raises(DBAPIError):
            async with tenant_session(tenant.id) as db:
                wallet_id = target_id or (await db.execute(select(ResourceWallet.id))).scalar_one()
                db.add(
                    ResourceTransaction(
                        tenant_id=tenant.id,
                        kind="issue",
                        resource_kind=kind,
                        amount=1,
                        target_wallet_id=wallet_id,
                        source_wallet_id=None,
                        created_by_id=tenant.admin_id,
                        idempotency_key=str(uuid.uuid4()),
                        request_hash="0" * 64,
                        reason="Invalid boundary",
                        refund_of_id=None,
                    )
                )
                await db.flush()


async def test_concurrent_distinct_issuance_keys_create_only_one_wallet(tenant):
    await enable(tenant)
    player = await tenant.create_user(unique("firstwallet"), "player")
    results = await asyncio.gather(
        *[tenant.admin.post(ROOT + "/issuances", json=request(target=player["id"], amount=5)) for _ in range(12)]
    )
    assert all(r.status_code == 201 for r in results), [(r.status_code, r.text) for r in results]
    client = await tenant.login(player["username"])
    assert await own_balance(client) == 60
    async with tenant_session(tenant.id) as db:
        assert (await db.execute(select(func.count()).select_from(ResourceWallet))).scalar_one() == 1
    await assert_consistent(tenant.id)


async def test_database_rejects_incomplete_posting_and_negative_balance(tenant):
    await enable(tenant)
    await issue(tenant, tenant.admin_id)
    async with tenant_session(tenant.id) as db:
        wallet_id = (await db.execute(select(ResourceWallet.id))).scalar_one()
    with pytest.raises(DBAPIError):
        async with tenant_session(tenant.id) as db:
            db.add(
                ResourceTransaction(
                    tenant_id=tenant.id,
                    kind="issue",
                    resource_kind="compute",
                    amount=1,
                    target_wallet_id=wallet_id,
                    source_wallet_id=None,
                    created_by_id=tenant.admin_id,
                    idempotency_key=str(uuid.uuid4()),
                    request_hash="0" * 64,
                    reason="Missing posting",
                    refund_of_id=None,
                )
            )
            await db.flush()
    with pytest.raises(DBAPIError):
        async with tenant_session(tenant.id) as db:
            await db.execute(text("UPDATE resource_wallets SET balance = -1 WHERE id = :wid"), {"wid": wallet_id})
    assert await own_balance(tenant.admin) == 100


async def test_owner_cannot_rewrite_resource_audit_history(tenant):
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    await enable(tenant)
    tx = await issue(tenant, tenant.admin_id)
    engine = create_async_engine(get_settings().database_url_migrations)
    try:
        for statement in (
            "UPDATE resource_ledger_entries SET balance_after = 1 WHERE transaction_id = :id",
            "DELETE FROM resource_transactions WHERE id = :id",
        ):
            with pytest.raises(DBAPIError, match="append-only"):
                async with engine.begin() as conn:
                    await conn.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant.id)})
                    await conn.execute(text(statement), {"id": uuid.UUID(tx["id"])})
    finally:
        await engine.dispose()


async def test_database_rejects_false_receipt_balance(tenant):
    await enable(tenant)
    await issue(tenant, tenant.admin_id)
    with pytest.raises(DBAPIError, match="snapshot differs"):
        async with tenant_session(tenant.id) as db:
            wallet = (await db.execute(select(ResourceWallet).with_for_update())).scalar_one()
            tx = ResourceTransaction(
                tenant_id=tenant.id,
                kind="issue",
                resource_kind="compute",
                amount=1,
                target_wallet_id=wallet.id,
                source_wallet_id=None,
                created_by_id=tenant.admin_id,
                idempotency_key=str(uuid.uuid4()),
                request_hash="0" * 64,
                reason="False receipt",
                refund_of_id=None,
            )
            db.add(tx)
            wallet.balance += 1
            await db.flush()
            db.add(
                ResourceLedgerEntry(
                    tenant_id=tenant.id,
                    transaction_id=tx.id,
                    wallet_id=wallet.id,
                    resource_kind="compute",
                    delta=1,
                    balance_after=999,
                )
            )
            await db.flush()
    assert await own_balance(tenant.admin) == 100


async def test_populated_resource_history_blocks_migration_downgrade(tenant):
    from tests.conftest import run_alembic

    await enable(tenant)
    await issue(tenant, tenant.admin_id)
    result = await asyncio.to_thread(run_alembic, "downgrade", "0016")
    assert result.returncode != 0
    assert "Cannot downgrade a populated operational resource ledger" in result.stderr
    assert await own_balance(tenant.admin) == 100
    async with platform_session() as db:
        assert (
            await db.execute(text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'resource_transactions'"))
        ).scalar_one() is True


@pytest.mark.parametrize("kind", ["allocate", "consume", "issue", "transfer"])
async def test_database_rejects_any_account_to_account_movement(tenant, kind):
    """Even a direct insert bypassing the API cannot debit one account and credit another."""
    await enable(tenant)
    player = await tenant.create_user(unique("nomove"), "player")
    await issue(tenant, tenant.admin_id)
    await issue(tenant, player["id"])
    async with tenant_session(tenant.id) as db:
        wallets = {w.user_id: w.id for w in (await db.execute(select(ResourceWallet))).scalars()}
    # Raw SQL so the ORM's enum validation cannot mask the database's own CHECK constraints.
    with pytest.raises(DBAPIError, match="no_transfer|resourceaction|resource_action_parties"):
        async with tenant_session(tenant.id) as db:
            await db.execute(
                text("""
                INSERT INTO resource_transactions (tenant_id, kind, resource_kind, amount, source_wallet_id,
                    target_wallet_id, created_by_id, idempotency_key, request_hash, reason)
                VALUES (:tid, :kind, 'compute', 1, :src, :dst, :uid, :key, :hash, 'Attempted transfer')
            """),
                {
                    "tid": tenant.id,
                    "kind": kind,
                    "src": wallets[tenant.admin_id],
                    "dst": wallets[uuid.UUID(player["id"])],
                    "uid": tenant.admin_id,
                    "key": str(uuid.uuid4()),
                    "hash": "0" * 64,
                },
            )
    assert await own_balance(tenant.admin) == 100
