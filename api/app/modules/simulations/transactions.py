"""Serializable mutations with bounded retry and a per-worker admission mutex.

PostgreSQL locks remain authoritative across replicas. No callbacks are published from
an aborted attempt, and no session used for authentication is repurposed mid-transaction.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import TypeVar
from weakref import WeakValueDictionary

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import _run_after_commit, get_session_factory
from app.core.errors import Conflict

T = TypeVar("T")
_locks: WeakValueDictionary[tuple[uuid.UUID, uuid.UUID], asyncio.Lock] = WeakValueDictionary()


async def serializable(tenant_id: uuid.UUID, resource_id: uuid.UUID, action: Callable[[AsyncSession], Awaitable[T]]) -> T:
    key = tenant_id, resource_id
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        for attempt in range(8):
            async with get_session_factory("app")() as db:
                try:
                    # Set isolation before the first query/tenant-context hook starts a snapshot.
                    await db.connection(execution_options={"isolation_level": "SERIALIZABLE"})
                    db.info["tenant_id"] = tenant_id
                    await db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
                    result = await action(db)
                    await db.commit()
                except DBAPIError as exc:
                    await db.rollback()
                    code = getattr(exc.orig, "sqlstate", None)
                    if code not in ("40001", "40P01"):
                        raise
                    if attempt == 7:
                        raise Conflict("Please retry this request with the same request ID", code="serialization_retry") from exc
                    await asyncio.sleep(min(0.01 * 2**attempt, 0.2))
                    continue
                await _run_after_commit(db)
                return result
    raise AssertionError("Unreachable retry loop")
