import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.security import utcnow
from app.modules.notifications.models import Notification
from app.modules.realtime.publisher import publish_after_commit


async def notify(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    type: str,
    title: str,
    body: str | None = None,
    data: dict[str, Any] | None = None,
) -> Notification:
    row = Notification(tenant_id=tenant_id, user_id=user_id, type=type, title=title, body=body, data=data or {})
    db.add(row)
    await db.flush()
    publish_after_commit(
        db,
        tenant_id,
        "notification.created",
        {"id": row.id, "type": type, "title": title, "body": body},
        user_id=user_id,
    )
    return row


async def unread_count(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.tenant_id == tenant_id, Notification.user_id == user_id, Notification.read_at.is_(None))
            )
        ).scalar_one()
    )


async def mark_read(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, notification_id: uuid.UUID) -> Notification:
    row = (
        await db.execute(
            select(Notification).where(
                Notification.tenant_id == tenant_id, Notification.user_id == user_id, Notification.id == notification_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Notification not found")
    if row.read_at is None:
        row.read_at = utcnow()
        await db.flush()
    return row


async def mark_all_read(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> int:
    result = await db.execute(
        update(Notification)
        .where(Notification.tenant_id == tenant_id, Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
