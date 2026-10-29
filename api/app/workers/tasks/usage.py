"""Per-tenant daily usage metrics.

Request counts accumulate in Redis (``usage:<tenant>:<date>:api_requests``) and
are written here as absolute daily values, so re-running is idempotent. Other
metrics are computed from the database for today and yesterday.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.core.database import platform_session
from app.core.redis import get_redis
from app.modules.demo_points.models import PointTransaction
from app.modules.events.models import Event
from app.modules.matches.models import Match, MatchStatus
from app.modules.tenants.models import Tenant, TenantUsage
from app.modules.users.models import TenantUser
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async


async def _upsert(db, tenant_id: uuid.UUID, metric: str, day: date, value: int) -> None:  # type: ignore[no-untyped-def]
    stmt = insert(TenantUsage).values(tenant_id=tenant_id, metric=metric, period_date=day, value=value)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[TenantUsage.tenant_id, TenantUsage.metric, TenantUsage.period_date],
            set_={"value": stmt.excluded.value, "updated_at": func.now()},
        )
    )


async def flush_usage() -> int:
    redis = get_redis()
    written = 0
    async with platform_session() as db:
        async for key in redis.scan_iter(match="usage:*", count=500):
            try:
                _, tenant, day, metric = key.split(":", 3)
                tenant_id, period = uuid.UUID(tenant), date.fromisoformat(day)
            except ValueError:
                continue
            value = int(await redis.get(key) or 0)
            await _upsert(db, tenant_id, metric, period, value)
            written += 1

        today = datetime.now(UTC).date()
        tenants = (await db.execute(select(Tenant.id).where(Tenant.deleted_at.is_(None)))).scalars().all()
        for day in (today - timedelta(days=1), today):
            lo = datetime.combine(day, time.min, UTC)
            hi = lo + timedelta(days=1)
            metrics = {
                "accounts_total": select(TenantUser.tenant_id, func.count()).where(
                    TenantUser.deleted_at.is_(None), TenantUser.created_at < hi
                ),
                "active_accounts": select(TenantUser.tenant_id, func.count()).where(
                    TenantUser.last_login_at >= lo, TenantUser.last_login_at < hi
                ),
                "events_created": select(Event.tenant_id, func.count()).where(Event.created_at >= lo, Event.created_at < hi),
                "matches_completed": select(Match.tenant_id, func.count()).where(
                    Match.status == MatchStatus.COMPLETED, Match.ended_at >= lo, Match.ended_at < hi
                ),
                "point_transactions": select(PointTransaction.tenant_id, func.count()).where(
                    PointTransaction.created_at >= lo, PointTransaction.created_at < hi
                ),
            }
            for metric, stmt in metrics.items():
                col = stmt.selected_columns[0]
                counts = dict((await db.execute(stmt.group_by(col))).tuples().all())
                for tid in tenants:
                    await _upsert(db, tid, metric, day, int(counts.get(tid, 0)))
                    written += 1
    return written


@celery_app.task(name="usage.flush")
def flush() -> int:
    return run_async(flush_usage)
