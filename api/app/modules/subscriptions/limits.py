import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PlanLimitReached
from app.modules.events.models import Event
from app.modules.subscriptions.models import LIVE_SUBSCRIPTION_STATUSES, TenantSubscription
from app.modules.users.models import TenantUser


async def live_subscription(db: AsyncSession, tenant_id: uuid.UUID) -> TenantSubscription | None:
    return (
        await db.execute(
            select(TenantSubscription).where(
                TenantSubscription.tenant_id == tenant_id,
                TenantSubscription.status.in_(LIVE_SUBSCRIPTION_STATUSES),
            )
        )
    ).scalar_one_or_none()


async def plan_limit(db: AsyncSession, tenant_id: uuid.UUID, key: str) -> int | None:
    """Numeric limit from the live plan; ``None`` means unlimited (or a licensed install)."""
    sub = await live_subscription(db, tenant_id)
    if sub is None:
        return None
    value = (sub.plan.limits or {}).get(key)
    return int(value) if value is not None else None


async def ensure_user_capacity(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    limit = await plan_limit(db, tenant_id, "max_users")
    if limit is None:
        return
    count = (
        await db.execute(
            select(func.count()).select_from(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.deleted_at.is_(None))
        )
    ).scalar_one()
    if count >= limit:
        raise PlanLimitReached(
            f"Your plan allows {limit} accounts. Upgrade to add more.",
            details={"limit": "max_users", "value": limit},
        )


async def ensure_event_capacity(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    limit = await plan_limit(db, tenant_id, "max_events_per_month")
    if limit is None:
        return
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    count = (
        await db.execute(
            select(func.count())
            .select_from(Event)
            .where(
                Event.tenant_id == tenant_id,
                Event.deleted_at.is_(None),
                Event.created_at >= month_start,
            )
        )
    ).scalar_one()
    if count >= limit:
        raise PlanLimitReached(
            f"Your plan allows {limit} events per month. Upgrade to add more.",
            details={"limit": "max_events_per_month", "value": limit},
        )
