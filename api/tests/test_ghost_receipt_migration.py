"""Migration rollback/upgrade preserves legacy ghost idempotency receipts and pool values."""

import asyncio
import uuid

from sqlalchemy import select

from app.core.database import dispose_engines, tenant_session
from app.modules.betting.models import DummyStakeReceipt, FightBook
from tests.conftest import run_alembic
from tests.test_declarator_betting import DESK, desk, switch


async def test_receipt_backfill_and_downgrade_preserve_retries(tenant):
    _, [fight], judge = await desk(tenant)
    await switch(judge, fight["id"], "open")
    body = {"side": "wala", "amount": "5000.00", "request_id": str(uuid.uuid4())}
    path = f"{DESK}/matches/{fight['id']}/dummy-bets"
    assert (await judge.post(path, json=body)).status_code == 200
    await dispose_engines()
    try:
        result = await asyncio.to_thread(run_alembic, "downgrade", "0039")
        assert result.returncode == 0, result.stderr
        async with tenant_session(tenant.id) as db:
            book = (await db.execute(select(FightBook).where(FightBook.match_id == uuid.UUID(fight["id"])))).scalar_one()
            assert book.dummy_request_ids == [uuid.UUID(body["request_id"])]
    finally:
        await dispose_engines()
        result = await asyncio.to_thread(run_alembic, "upgrade", "head")
        assert result.returncode == 0, result.stderr
    replay = await judge.post(path, json=body)
    assert replay.status_code == 200 and replay.json()["pools"]["wala"] == "5000.00"
    async with tenant_session(tenant.id) as db:
        book = (await db.execute(select(FightBook).where(FightBook.match_id == uuid.UUID(fight["id"])))).scalar_one()
        assert book.dummy_request_ids == []
        assert await db.scalar(
            select(DummyStakeReceipt.request_id).where(DummyStakeReceipt.match_id == book.match_id)
        ) == uuid.UUID(body["request_id"])
