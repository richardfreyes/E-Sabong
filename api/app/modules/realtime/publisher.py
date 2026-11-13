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
    if user_id is None and event in {"video.source_changed", "video.stream_status"}:
        from app.modules.tenants.rental import settings

        config = await settings()
        if (
            config
            and config.shared_domain_enabled
            and config.broadcast_tenant_id == tenant_id
            and data.get("stream_id") == (str(config.global_stream_id) if config.global_stream_id else None)
        ):
            # Explicit allowlist: never promote wallet, pool, commission or account messages.
            await get_redis().publish("rt:global:stream", message)


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


async def users_with_permission(db: AsyncSession, tenant_id: uuid.UUID, permission: str) -> set[uuid.UUID]:
    from sqlalchemy import select

    from app.modules.users.models import RolePermission, UserRole

    rows = await db.execute(
        select(UserRole.user_id)
        .join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .where(UserRole.tenant_id == tenant_id, RolePermission.permission == str(permission))
        .distinct()
    )
    return set(rows.scalars())


def publish_to_users_after_commit(
    db: AsyncSession, tenant_id: uuid.UUID, event: str, data: dict[str, Any], user_ids: set[uuid.UUID]
) -> None:
    for user_id in sorted(user_ids):
        publish_after_commit(db, tenant_id, event, data, user_id=user_id)


async def notify_staff(db: AsyncSession, tenant_id: uuid.UUID, permission: str, event: str, data: dict[str, Any]) -> None:
    """Send ``event`` after commit to every account holding ``permission`` (e.g. administrators),
    on their personal channels: data for staff dashboards never goes out tenant-wide."""
    publish_to_users_after_commit(db, tenant_id, event, data, await users_with_permission(db, tenant_id, permission))
