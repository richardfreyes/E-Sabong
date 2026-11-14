from sqlalchemy import select

from app.core.database import platform_session
from app.modules.audit.service import Actor
from app.modules.tenants.models import DomainKind, TenantDomain
from app.modules.tenants.service import check_domain
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async


@celery_app.task(name="domains.recheck_pending")
def recheck_pending() -> int:
    """Re-check enabled custom domains, including certificate renewal health."""

    async def _run() -> int:
        async with platform_session() as db:
            ids = list(
                await db.scalars(
                    select(TenantDomain.id).where(TenantDomain.kind == DomainKind.CUSTOM, TenantDomain.enabled.is_(True))
                )
            )
        count = 0
        for domain_id in ids:
            # Commit approval and release the lock after each host; a slow DNS check must
            # not hold every tenant's domain settings in one transaction. Skip owner edits.
            async with platform_session() as db:
                row = await db.scalar(
                    select(TenantDomain)
                    .where(TenantDomain.id == domain_id, TenantDomain.kind == DomainKind.CUSTOM, TenantDomain.enabled.is_(True))
                    .with_for_update(skip_locked=True)
                )
                if row is not None:
                    await check_domain(db, row, actor=Actor.system(), meta=None)
                    count += 1
        return count

    return run_async(_run)
