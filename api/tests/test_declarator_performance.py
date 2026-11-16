"""Repeatable real DB measurements; output path is opt-in, no production credentials."""

import asyncio
import json
import os
import resource
import time
from pathlib import Path

from sqlalchemy import event as sql_event

from app.core.database import get_engine
from tests.test_declarator import proposal
from tests.test_declarator_betting import DESK, desk, switch


async def measure(judge, path):
    statements = []

    def count(*args):
        statements.append(1)

    engine = get_engine("app").sync_engine
    sql_event.listen(engine, "before_cursor_execute", count)
    started, cpu = time.perf_counter(), time.process_time()
    try:
        response = await judge.get(path)
    finally:
        sql_event.remove(engine, "before_cursor_execute", count)
    assert response.status_code == 200, response.text
    return {
        "ms": round((time.perf_counter() - started) * 1000, 2),
        "cpu_ms": round((time.process_time() - cpu) * 1000, 2),
        "sql": len(statements),
        "bytes": len(response.content),
        "process_max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


async def test_monitor_scaling_measurement(tenant):
    event, card, judge = await desk(tenant, fights=20)
    metrics = []
    for i, fight in enumerate(card):
        await switch(judge, fight["id"], "open")
        if i in (0, 19):
            samples = [await measure(judge, f"{DESK}/events/{event['id']}/monitor") for _ in range(5)]
            live_samples = [await measure(judge, f"{DESK}/matches/{fight['id']}/book") for _ in range(5)]
            metrics.append({"books": i + 1, "samples": samples, "live_samples": live_samples})
        await switch(judge, fight["id"], "close")
        response = await judge.post(f"{DESK}/matches/{fight['id']}/declarations", json=proposal("meron"))
        assert response.status_code == 200, response.text
    assert metrics[0]["samples"][-1]["sql"] == metrics[-1]["samples"][-1]["sql"]
    assert metrics[0]["live_samples"][-1]["sql"] == metrics[-1]["live_samples"][-1]["sql"]
    if path := os.environ.get("DECLARATOR_METRICS"):
        await asyncio.to_thread(Path(path).write_text, json.dumps(metrics, indent=2))
    print(json.dumps(metrics))


async def test_live_book_is_scoped_and_ghost_receipts_stay_off_hot_row(tenant, other_tenant):
    import asyncio
    import uuid

    from sqlalchemy import func, select

    from app.core.database import tenant_session
    from app.modules.betting.models import DummyStakeReceipt, FightBook
    from tests.test_betting import players

    event, [fight], judge = await desk(tenant)
    await switch(judge, fight["id"], "open")
    body = {"request_id": str(uuid.uuid4()), "side": "meron", "amount": "1000.00"}
    path = f"{DESK}/matches/{fight['id']}/dummy-bets"
    # Concurrent retries must create one pool increment and one receipt.
    replies = await asyncio.gather(*(judge.post(path, json=body) for _ in range(12)))
    assert all(r.status_code == 200 for r in replies)
    for _ in range(100):
        body["request_id"] = str(uuid.uuid4())
        assert (await judge.post(path, json=body)).status_code == 200
    live = await judge.get(f"{DESK}/matches/{fight['id']}/book")
    assert live.status_code == 200 and live.json()["dummy_meron"] == "101000.00"
    compact = await judge.get(f"{DESK}/events/{event['id']}/monitor", params={"compact": True})
    assert compact.status_code == 200 and compact.json()["books"][0] == live.json()
    async with tenant_session(tenant.id) as db:
        book = (await db.execute(select(FightBook).where(FightBook.match_id == uuid.UUID(fight["id"])))).scalar_one()
        assert book.dummy_request_ids == []
        assert await db.scalar(select(func.count()).select_from(DummyStakeReceipt)) == 101
    async with tenant_session(other_tenant.id) as db:
        assert await db.scalar(select(func.count()).select_from(DummyStakeReceipt)) == 0
    [player] = await players(tenant, "book-player")
    assert (await player.get(f"{DESK}/matches/{fight['id']}/book")).status_code == 403
    outsider = await other_tenant.login("admin")
    assert (await outsider.get(f"{DESK}/matches/{fight['id']}/book")).status_code == 404


async def test_repeated_manual_auto_close_declare_and_retry_cycles(tenant):
    import uuid

    from app.modules.declarator.desk import run_due_jobs
    from tests.test_betting import placed, players

    cycles = int(os.environ.get("DECLARATOR_LOAD_CYCLES", "20"))
    event, _, judge = await desk(tenant)
    ana, ben = await players(tenant, "load-ana", "load-ben")
    prefix = f"{DESK}/events/{event['id']}"
    latencies = []
    for i in range(cycles):
        opened = await judge.post(f"{prefix}/betting/open")
        assert opened.status_code == 200, opened.text
        mid = opened.json()["match_id"]
        await placed(ana, mid, "meron", 10)
        await placed(ben, mid, "wala", 10)
        started = await judge.post(
            f"{prefix}/ghost-auto/start", json={"meron_amount": "100.00", "wala_amount": "200.00", "interval_seconds": 1}
        )
        assert started.status_code == 200, started.text
        await asyncio.gather(run_due_jobs(), run_due_jobs())
        auto = (await judge.get(f"{prefix}/ghost-auto")).json()
        assert auto["runs"] == 1 and auto["added_meron"] == "100.00" and auto["added_wala"] == "200.00"
        replies = await asyncio.gather(
            *(
                judge.post(
                    f"{DESK}/matches/{mid}/dummy-bets",
                    json={"side": "meron", "amount": "100.00", "request_id": str(uuid.uuid4())},
                )
                for _ in range(8)
            )
        )
        assert all(r.status_code == 200 for r in replies)
        started_at = time.perf_counter()
        await switch(judge, mid, "close")
        outcome = ["meron", "wala", "draw", "cancelled", "no_contest"][i % 5]
        body = proposal(outcome, reason="Load test official decision")
        declarations = await asyncio.gather(*(judge.post(f"{DESK}/matches/{mid}/declarations", json=body) for _ in range(3)))
        assert all(r.status_code == 200 for r in declarations), [r.text for r in declarations]
        assert len({r.json()["id"] for r in declarations}) == 1
        latencies.append(round((time.perf_counter() - started_at) * 1000, 2))
        assert (await judge.get(f"{prefix}/ghost-auto")).json()["active"] is False
        book = (await judge.get(f"{DESK}/matches/{mid}/book")).json()
        assert book["state"] == "settled" and book["dummy_meron"] == "900.00" and book["dummy_wala"] == "200.00"
    reconciliation = await tenant.admin.get("/api/v1/admin/betting/reconciliation")
    assert reconciliation.status_code == 200, reconciliation.text
    assert reconciliation.json()["balanced"] is True
    assert reconciliation.json()["checks"]["pool_mismatches"] == 0
    print(json.dumps({"cycles": cycles, "close_and_declare_ms": latencies}))
