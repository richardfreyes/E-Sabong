"""Publishes tenant-scoped realtime events through Redis pub/sub.

Every API replica's WebSocket endpoint subscribes to the channels of its
connected clients, so events reach clients regardless of which replica handled
the originating request.
"""

import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import on_commit
from app.core.redis import get_redis


def tenant_channel(tenant_id: uuid.UUID) -> str:
    return f"rt:tenant:{tenant_id}"


def user_channel(tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return f"rt:tenant:{tenant_id}:user:{user_id}"


async def publish(tenant_id: uuid.UUID, event: str, data: dict[str, Any], user_id: uuid.UUID | None = None) -> None:
    channel = user_channel(tenant_id, user_id) if user_id else tenant_channel(tenant_id)
    message = json.dumps({"type": event, "data": data}, default=str)
    await get_redis().publish(channel, message)


def publish_after_commit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    event: str,
    data: dict[str, Any],
    user_id: uuid.UUID | None = None,
) -> None:
    async def _send() -> None:
        await publish(tenant_id, event, data, user_id)

    on_commit(db, _send)
