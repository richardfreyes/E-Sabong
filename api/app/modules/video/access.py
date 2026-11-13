"""Live-video entitlement reads the available play wallet; watching never posts a debit."""

import uuid
from decimal import Decimal

from sqlalchemy import and_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import platform_session
from app.core.deps import CurrentUser
from app.core.errors import Forbidden
from app.modules.auth.models import AccountStatus
from app.modules.betting.models import PlayWallet
from app.modules.tenants.models import Tenant, TenantStatus
from app.modules.users.models import AccountType, TenantUser

MIN_STREAM_BALANCE = Decimal("100.00")
BALANCE_MESSAGE = "Your balance must be at least 100 points to watch the live stream. Please contact your Agent to add balance."


async def require_balance(db: AsyncSession, current: CurrentUser) -> None:
    if current.user.account_type != AccountType.PLAYER:
        return
    balance = await db.scalar(
        select(PlayWallet.balance).where(PlayWallet.tenant_id == current.tenant.id, PlayWallet.user_id == current.id)
    )
    if balance is None or balance < MIN_STREAM_BALANCE:
        raise Forbidden(BALANCE_MESSAGE, code="stream_insufficient_balance")


async def allowed_viewers(viewers: set[tuple[uuid.UUID, uuid.UUID]]) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """Batch active-session checks, using both tenant and account IDs from signed claims."""
    if not viewers:
        return set()
    async with platform_session() as db:
        rows = (
            await db.execute(
                select(TenantUser.tenant_id, TenantUser.id, TenantUser.account_type, PlayWallet.balance)
                .join(Tenant, Tenant.id == TenantUser.tenant_id)
                .outerjoin(PlayWallet, and_(PlayWallet.tenant_id == TenantUser.tenant_id, PlayWallet.user_id == TenantUser.id))
                .where(
                    tuple_(TenantUser.tenant_id, TenantUser.id).in_(viewers),
                    TenantUser.status == AccountStatus.ACTIVE,
                    TenantUser.deleted_at.is_(None),
                    Tenant.status == TenantStatus.ACTIVE,
                    Tenant.deleted_at.is_(None),
                )
            )
        ).all()
    return {
        (tid, uid)
        for tid, uid, role, balance in rows
        if role != AccountType.PLAYER or (balance is not None and balance >= MIN_STREAM_BALANCE)
    }
