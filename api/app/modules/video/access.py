"""Live-video entitlement: the available play wallet plus the player's open fight stakes.

A stake is debited from the wallet when the bet is accepted, so ``balance + open stakes`` counts
every point exactly once: a player who bets their whole balance keeps watching until the fight
settles. Watching never posts a debit.
"""

import uuid
from decimal import Decimal

from sqlalchemy import and_, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import platform_session
from app.core.deps import CurrentUser
from app.core.errors import Forbidden
from app.modules.auth.models import AccountStatus
from app.modules.betting.models import Bet, BetStatus, PlayWallet
from app.modules.tenants.models import Tenant, TenantStatus
from app.modules.users.models import AccountType, TenantUser

MIN_STREAM_BALANCE = Decimal("100.00")
BALANCE_MESSAGE = (
    "Your balance and active bets must total at least 100 points to watch the live stream. "
    "Please contact your Agent to add balance."
)
ZERO = Decimal("0.00")


def eligible(balance: Decimal | None, open_stakes: Decimal | None) -> bool:
    return (balance or ZERO) + (open_stakes or ZERO) >= MIN_STREAM_BALANCE


def open_stakes_query(tenant_id: uuid.UUID, user_ids: set[uuid.UUID] | None = None):
    """Unsettled fight stakes per player. Won, lost and refunded bets are no longer open."""
    stmt = (
        select(Bet.user_id, func.sum(Bet.amount).label("open_stakes"))
        .where(Bet.tenant_id == tenant_id, Bet.status == BetStatus.ACCEPTED)
        .group_by(Bet.user_id)
    )
    return stmt.where(Bet.user_id.in_(user_ids)) if user_ids is not None else stmt


async def open_stakes_for(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Decimal:
    row = (await db.execute(open_stakes_query(tenant_id, {user_id}))).first()
    return row.open_stakes if row else ZERO


async def open_stakes_by_user(db: AsyncSession, tenant_id: uuid.UUID, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    if not user_ids:
        return {}
    found = {row.user_id: row.open_stakes for row in await db.execute(open_stakes_query(tenant_id, user_ids))}
    return {user_id: found.get(user_id, ZERO) for user_id in user_ids}


async def stream_funds(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> tuple[Decimal, Decimal, int]:
    """(available balance, open stakes, wallet version) of one player, as the client shows them."""
    wallet = (
        await db.execute(
            select(PlayWallet.balance, PlayWallet.version).where(PlayWallet.tenant_id == tenant_id, PlayWallet.user_id == user_id)
        )
    ).first()
    balance, version = (wallet.balance, wallet.version) if wallet else (ZERO, 0)
    return balance, await open_stakes_for(db, tenant_id, user_id), version


async def require_balance(db: AsyncSession, current: CurrentUser) -> None:
    if current.user.account_type != AccountType.PLAYER:
        return
    balance, stakes, _ = await stream_funds(db, current.tenant.id, current.id)
    if not eligible(balance, stakes):
        raise Forbidden(BALANCE_MESSAGE, code="stream_insufficient_balance")


async def allowed_viewers(viewers: set[tuple[uuid.UUID, uuid.UUID]]) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """Batch active-session checks, using both tenant and account IDs from signed claims."""
    if not viewers:
        return set()
    # Open stakes per (tenant, player), joined on both columns so no tenant sees another's bets.
    stakes = (
        select(Bet.tenant_id, Bet.user_id, func.sum(Bet.amount).label("open_stakes"))
        .where(tuple_(Bet.tenant_id, Bet.user_id).in_(viewers), Bet.status == BetStatus.ACCEPTED)
        .group_by(Bet.tenant_id, Bet.user_id)
        .subquery()
    )
    async with platform_session() as db:
        rows = (
            await db.execute(
                select(TenantUser.tenant_id, TenantUser.id, TenantUser.account_type, PlayWallet.balance, stakes.c.open_stakes)
                .join(Tenant, Tenant.id == TenantUser.tenant_id)
                .outerjoin(PlayWallet, and_(PlayWallet.tenant_id == TenantUser.tenant_id, PlayWallet.user_id == TenantUser.id))
                .outerjoin(stakes, and_(stakes.c.tenant_id == TenantUser.tenant_id, stakes.c.user_id == TenantUser.id))
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
        for tid, uid, role, balance, open_stakes in rows
        if role != AccountType.PLAYER or (balance is not None and eligible(balance, open_stakes))
    }
