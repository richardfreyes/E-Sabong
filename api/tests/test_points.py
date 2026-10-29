"""Demonstration points: allocation, downline transfers, idempotency, concurrency."""

import asyncio
import uuid

from sqlalchemy import func, select

from app.core.database import tenant_session
from app.modules.demo_points.models import PointAccount, PointLedgerEntry, PointTransaction


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"test-{uuid.uuid4()}"}


async def _setup(tenant):  # type: ignore[no-untyped-def]
    master = await tenant.create_user("master", "master_agent")
    agent = await tenant.create_user("agent", "agent", parent_id=master["id"])
    player = await tenant.create_user("player", "player", parent_id=agent["id"])
    r = await tenant.admin.post(
        "/api/v1/admin/points/allocations", json={"user_id": master["id"], "amount": 10_000}, headers=key()
    )
    assert r.status_code == 201, r.text
    return master, agent, player


async def test_allocate_and_transfer_down_the_hierarchy(app, tenant) -> None:  # type: ignore[no-untyped-def]
    master, agent, player = await _setup(tenant)
    mc = await tenant.login("master")
    r = await mc.post(
        "/api/v1/admin/points/transfers", json={"to_user_id": agent["id"], "amount": 4_000, "memo": "float"}, headers=key()
    )
    assert r.status_code == 201, r.text
    tx = r.json()
    assert tx["kind"] == "transfer" and tx["source"]["username"] == "master" and tx["destination"]["username"] == "agent"
    # Masters may transfer to any account in their downline, not only direct children.
    assert (
        await mc.post("/api/v1/admin/points/transfers", json={"to_user_id": player["id"], "amount": 500}, headers=key())
    ).status_code == 201

    ac = await tenant.login("agent")
    assert (await ac.get("/api/v1/admin/points/me")).json()["balance"] == 4_000
    assert (await mc.get("/api/v1/admin/points/me")).json()["balance"] == 5_500

    pc = await tenant.login("player")
    points = (await pc.get("/api/v1/portal/points")).json()
    assert points["balance"] == 500 and "cannot be redeemed" in points["disclaimer"]
    history = (await pc.get("/api/v1/portal/points/history")).json()["items"]
    assert (
        history[0]["delta"] == 500 and history[0]["balance_after"] == 500 and history[0]["counterparty"]["username"] == "master"
    )
    notes = (await pc.get("/api/v1/portal/notifications")).json()["items"]
    assert notes[0]["type"] == "points.received"


async def test_transfers_never_flow_up_or_sideways(app, tenant) -> None:  # type: ignore[no-untyped-def]
    master, agent, player = await _setup(tenant)
    other = await tenant.create_user("othermaster", "master_agent")
    mc = await tenant.login("master")
    await mc.post("/api/v1/admin/points/transfers", json={"to_user_id": agent["id"], "amount": 100}, headers=key())
    ac = await tenant.login("agent")
    for target in (master["id"], other["id"], str(tenant.admin_id)):
        r = await ac.post("/api/v1/admin/points/transfers", json={"to_user_id": target, "amount": 10}, headers=key())
        assert r.status_code == 403, target
        assert r.json()["error"]["code"] == "transfer_not_allowed"
    r = await ac.post("/api/v1/admin/points/transfers", json={"to_user_id": agent["id"], "amount": 10}, headers=key())
    assert r.status_code == 422 and r.json()["error"]["code"] == "self_transfer"
    # Players hold points but cannot move them.
    pc = await tenant.login("player")
    r = await pc.post("/api/v1/admin/points/transfers", json={"to_user_id": agent["id"], "amount": 1}, headers=key())
    assert r.status_code == 403


async def test_insufficient_balance_and_validation(app, tenant) -> None:  # type: ignore[no-untyped-def]
    master, agent, _ = await _setup(tenant)
    mc = await tenant.login("master")
    r = await mc.post("/api/v1/admin/points/transfers", json={"to_user_id": agent["id"], "amount": 10_001}, headers=key())
    assert r.status_code == 422 and r.json()["error"]["code"] == "insufficient_points"
    assert (
        await mc.post("/api/v1/admin/points/transfers", json={"to_user_id": agent["id"], "amount": 0}, headers=key())
    ).status_code == 422
    assert (
        await mc.post("/api/v1/admin/points/transfers", json={"to_user_id": agent["id"], "amount": 5})
    ).status_code == 422  # no key
    r = await mc.post(
        "/api/v1/admin/points/transfers", json={"to_user_id": agent["id"], "amount": 5}, headers={"Idempotency-Key": "bad key!"}
    )
    assert r.status_code == 422


async def test_idempotent_replay_and_conflict(app, tenant) -> None:  # type: ignore[no-untyped-def]
    master, agent, _ = await _setup(tenant)
    mc = await tenant.login("master")
    headers = key()
    body = {"to_user_id": agent["id"], "amount": 250}
    first = await mc.post("/api/v1/admin/points/transfers", json=body, headers=headers)
    second = await mc.post("/api/v1/admin/points/transfers", json=body, headers=headers)
    assert first.status_code == 201 and second.status_code == 200
    assert second.headers["Idempotent-Replay"] == "true"
    assert first.json()["id"] == second.json()["id"]
    assert (await mc.get("/api/v1/admin/points/me")).json()["balance"] == 9_750
    r = await mc.post("/api/v1/admin/points/transfers", json={**body, "amount": 251}, headers=headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_conflict"


async def test_adjustments(app, tenant) -> None:  # type: ignore[no-untyped-def]
    master, _, _ = await _setup(tenant)
    a = tenant.admin
    r = await a.post(
        "/api/v1/admin/points/adjustments",
        json={"user_id": master["id"], "direction": "debit", "amount": 1_000, "reason": "Correction"},
        headers=key(),
    )
    assert r.status_code == 201 and r.json()["destination"]["kind"] == "issuance"
    r = await a.post(
        "/api/v1/admin/points/adjustments",
        json={"user_id": master["id"], "direction": "debit", "amount": 99_999, "reason": "Too much"},
        headers=key(),
    )
    assert r.status_code == 422
    summary = (await a.get("/api/v1/admin/points/summary")).json()
    assert summary["scope"] == "tenant" and summary["total_issued"] == 9_000 and summary["circulating"] == 9_000


async def test_concurrent_transfers_never_overdraw(app, tenant) -> None:  # type: ignore[no-untyped-def]
    master, agent, player = await _setup(tenant)  # master holds 10,000
    mc = await tenant.login("master")
    targets = [agent["id"], player["id"]]

    async def send(i: int):  # type: ignore[no-untyped-def]
        return await mc.post("/api/v1/admin/points/transfers", json={"to_user_id": targets[i % 2], "amount": 300}, headers=key())

    responses = await asyncio.gather(*(send(i) for i in range(50)))
    ok = [r for r in responses if r.status_code == 201]
    refused = [r for r in responses if r.status_code == 422]
    assert len(ok) + len(refused) == 50, [r.text for r in responses if r.status_code not in (201, 422)]
    assert len(ok) == 33  # floor(10000 / 300)
    assert all(r.json()["error"]["code"] == "insufficient_points" for r in refused)

    async with tenant_session(tenant.id) as db:
        balances = dict((await db.execute(select(PointAccount.user_id, PointAccount.balance))).tuples().all())
        unbalanced = (
            await db.execute(
                select(PointLedgerEntry.transaction_id)
                .group_by(PointLedgerEntry.transaction_id)
                .having(func.sum(PointLedgerEntry.delta) != 0)
            )
        ).all()
        issuance = (await db.execute(select(PointAccount.balance).where(PointAccount.kind == "issuance"))).scalar_one()
    assert balances[uuid.UUID(master["id"])] == 10_000 - 33 * 300
    assert sum(b for uid, b in balances.items() if uid) == -issuance == 10_000
    assert unbalanced == []


async def test_concurrent_duplicate_key_posts_once(app, tenant) -> None:  # type: ignore[no-untyped-def]
    master, agent, _ = await _setup(tenant)
    mc = await tenant.login("master")
    headers = key()
    responses = await asyncio.gather(
        *(
            mc.post("/api/v1/admin/points/transfers", json={"to_user_id": agent["id"], "amount": 100}, headers=headers)
            for _ in range(10)
        )
    )
    assert sorted(r.status_code for r in responses) == [200] * 9 + [201]
    assert len({r.json()["id"] for r in responses}) == 1
    async with tenant_session(tenant.id) as db:
        n = (
            await db.execute(
                select(func.count())
                .select_from(PointTransaction)
                .where(PointTransaction.idempotency_key == headers["Idempotency-Key"])
            )
        ).scalar_one()
    assert n == 1
    assert (await mc.get("/api/v1/admin/points/me")).json()["balance"] == 9_900


async def test_downline_visibility_of_transactions(app, tenant) -> None:  # type: ignore[no-untyped-def]
    master, agent, player = await _setup(tenant)
    other = await tenant.create_user("othermaster", "master_agent")
    await tenant.admin.post("/api/v1/admin/points/allocations", json={"user_id": other["id"], "amount": 77}, headers=key())
    mc = await tenant.login("master")
    await mc.post("/api/v1/admin/points/transfers", json={"to_user_id": agent["id"], "amount": 10}, headers=key())
    seen = (await mc.get("/api/v1/admin/points/transactions")).json()["items"]
    assert {t["amount"] for t in seen} == {10_000, 10}
    assert (await mc.get(f"/api/v1/admin/points/accounts/{other['id']}")).status_code == 404
    assert len((await tenant.admin.get("/api/v1/admin/points/transactions")).json()["items"]) == 3
