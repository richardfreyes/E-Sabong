"""Manual and auto ghost amounts run concurrently: no event or fight lock, the book row orders them.

These pin the lock design in ``betting.add_dummy_stakes``: a ghost click is never blocked by a
transaction holding the event row (Stop, Close, Declare, a player bet's share lock), a Close always
freezes exactly the clicks that committed before it, replays are answered and never refused or added
twice, and the auto runner rolls a run back when the job changed under it.
"""

import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import func, select, text

from app.core.database import get_engine, tenant_session
from app.modules.betting.models import DummyStakeReceipt, FightBook
from tests.test_declarator import proposal
from tests.test_declarator_betting import DESK, desk, switch


def click(side: str = "meron", amount: str = "5000.00") -> dict:
    return {"request_id": str(uuid.uuid4()), "side": side, "amount": amount}


async def receipts(tenant, match_id: str) -> int:
    async with tenant_session(tenant.id) as db:
        return await db.scalar(
            select(func.count()).select_from(DummyStakeReceipt).where(DummyStakeReceipt.match_id == uuid.UUID(match_id))
        )


async def test_ghost_click_is_not_blocked_by_a_transaction_holding_the_event_row(tenant):
    event, [fight], judge = await desk(tenant)
    await switch(judge, fight["id"], "open")
    async with get_engine("app").connect() as holder:
        await holder.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.id)})
        # What Stop, Close, Declare and auto start hold for their whole transaction.
        await holder.execute(text("SELECT id FROM events WHERE id = :e FOR UPDATE"), {"e": event["id"]})
        reply = await asyncio.wait_for(judge.post(f"{DESK}/matches/{fight['id']}/dummy-bets", json=click()), timeout=5)
        await holder.rollback()
    assert reply.status_code == 200, reply.text
    body = reply.json()
    # The Declarator's answer splits ghost money out; players' book.updated never does.
    assert body["dummy_meron"] == "5000.00" and body["dummy_wala"] == "0.00" and body["pools"]["meron"] == "5000.00"
    assert "db;dur=" in reply.headers["server-timing"]


async def test_close_during_concurrent_clicks_freezes_exactly_the_accepted_ones(tenant):
    _, [fight], judge = await desk(tenant)
    await switch(judge, fight["id"], "open")
    path = f"{DESK}/matches/{fight['id']}/dummy-bets"
    bodies = [click("meron" if i % 2 else "wala", f"{100 + i}.00") for i in range(40)]
    tasks = [asyncio.create_task(judge.post(path, json=b)) for b in bodies]
    await asyncio.sleep(0.01)
    closed = await judge.post(f"{DESK}/matches/{fight['id']}/betting/close")
    replies = await asyncio.gather(*tasks)
    assert closed.status_code == 200, closed.text
    accepted = [b for b, r in zip(bodies, replies, strict=True) if r.status_code == 200]
    refused = [r for r in replies if r.status_code != 200]
    assert all(r.status_code == 409 and r.json()["error"]["code"] == "betting_closed" for r in refused)
    book = (await judge.get(f"{DESK}/matches/{fight['id']}/book")).json()
    assert book["state"] == "closed"
    for side in ("meron", "wala"):
        assert Decimal(book[f"dummy_{side}"]) == sum((Decimal(b["amount"]) for b in accepted if b["side"] == side), Decimal(0))
    assert await receipts(tenant, fight["id"]) == len(accepted)
    # Nothing moves after the close, and an accepted click replayed now is answered, not refused or re-added.
    if accepted:
        replay = await judge.post(path, json=accepted[0])
        assert replay.status_code == 200, replay.text
    late = await judge.post(path, json=click())
    assert late.status_code == 409 and late.json()["error"]["code"] == "betting_closed"
    again = (await judge.get(f"{DESK}/matches/{fight['id']}/book")).json()
    assert (again["dummy_meron"], again["dummy_wala"]) == (book["dummy_meron"], book["dummy_wala"])
    assert await receipts(tenant, fight["id"]) == len(accepted)
    declared = await judge.post(f"{DESK}/matches/{fight['id']}/declarations", json=proposal("meron"))
    assert declared.status_code == 200, declared.text
    settled = (await judge.get(f"{DESK}/matches/{fight['id']}/book")).json()
    assert settled["state"] == "settled" and settled["dummy_meron"] == book["dummy_meron"]


async def test_refused_click_leaves_no_receipt_or_audit(tenant):
    _, [fight], judge = await desk(tenant)
    await switch(judge, fight["id"], "open")
    await switch(judge, fight["id"], "close")
    body = click()
    reply = await judge.post(f"{DESK}/matches/{fight['id']}/dummy-bets", json=body)
    assert reply.status_code == 409 and reply.json()["error"]["code"] == "betting_closed"
    assert await receipts(tenant, fight["id"]) == 0
    logs = await tenant.admin.get("/api/v1/admin/audit-logs", params={"action": "betting.dummy_added"})
    assert all(body["request_id"] not in str(item["changes"]) for item in logs.json()["items"])


async def test_auto_run_is_rolled_back_when_the_job_changed_under_it(tenant, monkeypatch):
    from app.modules.declarator import desk as desk_module
    from app.modules.declarator.models import GhostAutoBet

    event, [fight], judge = await desk(tenant)
    await switch(judge, fight["id"], "open")
    started = await judge.post(
        f"{DESK}/events/{event['id']}/ghost-auto/start",
        json={"meron_amount": "100.00", "wala_amount": "200.00", "interval_seconds": 1},
    )
    assert started.status_code == 200, started.text
    job_id = uuid.UUID(started.json()["id"])
    original = desk_module.betting.add_dummy_stakes

    async def stake_then_bump(db, *args, **kwargs):  # an overlapping tick ran the same job meanwhile
        result = await original(db, *args, **kwargs)
        async with get_engine("app").connect() as other:
            await other.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.id)})
            await other.execute(text("UPDATE ghost_auto_bets SET runs = runs + 1 WHERE id = :j"), {"j": job_id})
            await other.commit()
        return result

    monkeypatch.setattr(desk_module.betting, "add_dummy_stakes", stake_then_bump)
    assert await desk_module.run_due_jobs() == 1
    monkeypatch.setattr(desk_module.betting, "add_dummy_stakes", original)
    async with tenant_session(tenant.id) as db:
        book = (await db.execute(select(FightBook).where(FightBook.match_id == uuid.UUID(fight["id"])))).scalar_one()
        job = await db.get(GhostAutoBet, job_id)
        # The superseded run added nothing and the job keeps running.
        assert (book.dummy_meron, book.dummy_wala) == (Decimal("0.00"), Decimal("0.00"))
        assert job.status == "active" and job.runs == 1 and job.added_meron == Decimal("0.00")
    assert await receipts(tenant, fight["id"]) == 0
    # A normal run applies both sides once.
    async with tenant_session(tenant.id) as db:
        await db.execute(text("UPDATE ghost_auto_bets SET next_run_at = now() WHERE id = :j"), {"j": job_id})
    assert await desk_module.run_due_jobs() == 1
    book = (await judge.get(f"{DESK}/matches/{fight['id']}/book")).json()
    assert (book["dummy_meron"], book["dummy_wala"]) == ("100.00", "200.00")
    # Leave no running job behind: the runner picks up due jobs of every tenant.
    assert (await judge.post(f"{DESK}/events/{event['id']}/ghost-auto/stop")).status_code == 200
