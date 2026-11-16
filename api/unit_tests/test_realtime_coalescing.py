"""Only aggregate snapshots can be replaced inside the same uncommitted transaction."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.database import _run_after_commit
from app.modules.realtime import publisher


async def test_auto_ghost_sides_publish_one_final_snapshot_without_losing_critical_events(monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(publisher, "publish", send)
    db = SimpleNamespace(info={})
    tenant = uuid.uuid4()
    publisher.publish_after_commit(db, tenant, "book.updated", {"match_id": "m", "version": 1})
    publisher.publish_after_commit(db, tenant, "book.updated", {"match_id": "m", "version": 2})
    publisher.publish_after_commit(db, tenant, "fight.settled", {"match_id": "m"})
    publisher.publish_after_commit(db, tenant, "bets.settled", {"match_id": "m"}, user_id=uuid.uuid4())
    assert len(db.info["after_commit"]) == 3
    await _run_after_commit(db)
    assert send.await_count == 3
    assert send.await_args_list[0].args[2]["version"] == 2
    assert not db.info
