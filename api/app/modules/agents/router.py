"""Agent workspace screens (``/agent``): My Users for master agents and agents."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUserDep, TenantDb, require_module
from app.modules.agents import users
from app.modules.agents.users import MyUserRow, MyUsersSummary
from app.shared.schemas import Page, PageDep

router = APIRouter(prefix="/agent", tags=["agent workspace"], dependencies=[Depends(require_module("events"))])


@router.get("/users/summary", response_model=MyUsersSummary)
async def my_users_summary(current: CurrentUserDep, db: TenantDb) -> MyUsersSummary:
    """Counts of the caller's players (and, for a master agent, agents) by status, and online now."""
    return await users.summary(db, current)


@router.get("/users", response_model=Page[MyUserRow])
async def my_users(
    current: CurrentUserDep,
    db: TenantDb,
    params: PageDep,
    kind: Literal["player", "agent", "sub_agent"] | None = None,
    state: Literal["active", "blocked", "banned", "online"] | None = None,
    parent_id: uuid.UUID | None = None,
) -> Page[MyUserRow]:
    """The caller's downline players and agents, newest first."""
    return await users.list_users(db, current, params, kind=kind, state=state, parent_id=parent_id)


@router.get("/activity")
async def player_activity(current: CurrentUserDep, db: TenantDb, params: PageDep, player_id: str | None = None):
    import uuid

    from sqlalchemy import func, select

    from app.core.errors import NotFound
    from app.modules.agents.service import visible_ids_select
    from app.modules.betting.models import Bet
    from app.modules.matches.models import Match
    from app.modules.users.models import TenantUser

    users._require_agent(current)
    scope = visible_ids_select(current.tenant.id, current.id, current.user.account_type)
    stmt = (
        select(Bet, Match.match_number, TenantUser.username)
        .join(Match, (Match.tenant_id == Bet.tenant_id) & (Match.id == Bet.match_id))
        .join(TenantUser, (TenantUser.tenant_id == Bet.tenant_id) & (TenantUser.id == Bet.user_id))
        .where(Bet.tenant_id == current.tenant.id, Bet.user_id.in_(scope))
    )
    if player_id:
        try:
            uid = uuid.UUID(player_id)
        except ValueError:
            raise NotFound("Player not found") from None
        if uid not in set((await db.scalars(scope)).all()):
            raise NotFound("Player not found")
        stmt = stmt.where(Bet.user_id == uid)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    data = (await db.execute(stmt.order_by(Bet.created_at.desc(), Bet.id).offset(params.offset).limit(params.page_size))).all()
    return Page(
        items=[
            dict(
                id=b.id,
                player_id=b.user_id,
                username=name,
                match_number=number,
                amount=str(b.amount),
                side=b.side.value,
                status=b.status.value,
                payout=str(b.payout),
                created_at=b.created_at,
            )
            for b, number, name in data
        ],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )
