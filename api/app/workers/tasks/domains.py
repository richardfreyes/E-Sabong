from datetime import timedelta

from sqlalchemy import select

from app.core.database import platform_session
from app.core.security import utcnow
from app.modules.audit.service import Actor
from app.modules.tenants.models import DomainKind, DomainVerification, TenantDomain
from app.modules.tenants.service import check_domain
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async


@celery_app.task(name="domains.recheck_pending")
def recheck_pending() -> int:
    """Re-check unverified custom domains added within the last 14 days."""

    async def _run() -> int:
        async with platform_session() as db:
            rows = (
                (
                    await db.execute(
                        select(TenantDomain).where(
                            TenantDomain.kind == DomainKind.CUSTOM,
                            TenantDomain.verification_status != DomainVerification.VERIFIED,
                            TenantDomain.created_at >= utcnow() - timedelta(days=14),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                await check_domain(db, row, actor=Actor.system(), meta=None)
            return len(rows)

    return run_async(_run)
