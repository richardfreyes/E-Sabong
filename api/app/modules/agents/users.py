"""My Users: the players and agents below a master agent or agent, with status, presence,
play balance, commission earnings and (for agents) the commission rate.

Read-only. The scope is always the caller's own downline, resolved on the server. Blocking uses
the existing ``/admin/users/{id}/suspend|activate`` endpoints (downline-scoped); bans are
administrator-only. Nothing here moves a balance.
"""

import uuid
from decimal import Decimal
from typing import Literal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import Forbidden
from app.modules.agents.service import downline_ids_select
from app.modules.auth.models import AccountStatus
from app.modules.betting.models import PlayWallet
from app.modules.commissions import service as commissions
from app.modules.commissions.models import CommissionEntry
from app.modules.realtime import presence
from app.modules.users.models import AccountType, TenantUser
from app.shared.schemas import Page, PageParams, Schema

Kind = Literal["player", "agent"]
State = Literal["active", "blocked", "banned", "online"]
ZERO = Decimal("0.00")


class MyUserRow(Schema):
    id: uuid.UUID
    code: str
    username: str
    full_name: str
    account_type: str
    parent_username: str | None
    direct: bool
    status: str
    online: bool
    via_referral: bool
    balance: Decimal | None
    earnings: Decimal
    rate: Decimal | None
    created_at: str
    last_login_at: str | None


class StateCounts(Schema):
    active: int
    blocked: int
    banned: int
    online: int


class MyUsersSummary(Schema):
    players: StateCounts
    agents: StateCounts | None


def _require_agent(actor: CurrentUser) -> None:
    if actor.user.account_type not in (AccountType.MASTER_AGENT, AccountType.AGENT):
        raise Forbidden("My Users is for master agents and agents", code="agents_only")


def _base(actor: CurrentUser) -> Select:  # type: ignore[type-arg]
    tenant_id = actor.tenant.id
    return select(TenantUser).where(
        TenantUser.tenant_id == tenant_id,
        TenantUser.id.in_(downline_ids_select(tenant_id, actor.id)),
        TenantUser.account_type.in_((AccountType.PLAYER, AccountType.AGENT)),
        TenantUser.deleted_at.is_(None),
    )


_STATUS = {"active": AccountStatus.ACTIVE, "blocked": AccountStatus.SUSPENDED, "banned": AccountStatus.BANNED}


async def summary(db: AsyncSession, actor: CurrentUser) -> MyUsersSummary:
    _require_agent(actor)
    online = set(await presence.online_player_ids(actor.tenant.id, "accounts"))
    rows = (await db.execute(_base(actor).with_only_columns(TenantUser.id, TenantUser.account_type, TenantUser.status))).all()
    counts = {kind: {"active": 0, "blocked": 0, "banned": 0, "online": 0} for kind in ("player", "agent")}
    for user_id, account_type, status in rows:
        bucket = counts[account_type.value]
        bucket[{AccountStatus.ACTIVE: "active", AccountStatus.SUSPENDED: "blocked", AccountStatus.BANNED: "banned"}[status]] += 1
        if user_id in online:
            bucket["online"] += 1
    # Same shape for master agents and agents (an agent simply has no agents below them).
    return MyUsersSummary(players=StateCounts(**counts["player"]), agents=StateCounts(**counts["agent"]))


async def list_users(
    db: AsyncSession, actor: CurrentUser, params: PageParams, *, kind: Kind | None, state: State | None
) -> Page[MyUserRow]:
    _require_agent(actor)
    tenant_id = actor.tenant.id
    online = await presence.online_player_ids(tenant_id, "accounts")
    stmt = _base(actor)
    if kind:
        stmt = stmt.where(TenantUser.account_type == AccountType(kind))
    if state == "online":
        stmt = stmt.where(TenantUser.id.in_(list(online) or [uuid.UUID(int=0)]))
    elif state:
        stmt = stmt.where(TenantUser.status == _STATUS[state])
    if params.q:
        term = f"%{params.q.strip()}%"
        stmt = stmt.where(or_(TenantUser.username.ilike(term), TenantUser.full_name.ilike(term)))
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    users = list(
        (
            await db.execute(
                stmt.order_by(TenantUser.created_at.desc(), TenantUser.id).offset(params.offset).limit(params.page_size)
            )
        )
        .scalars()
        .all()
    )
    ids = [u.id for u in users]
    parents = (
        dict(
            (
                await db.execute(
                    select(TenantUser.id, TenantUser.username).where(
                        TenantUser.tenant_id == tenant_id, TenantUser.id.in_({u.parent_id for u in users if u.parent_id})
                    )
                )
            )
            .tuples()
            .all()
        )
        if users
        else {}
    )
    balances = (
        dict(
            (
                await db.execute(
                    select(PlayWallet.user_id, PlayWallet.balance).where(
                        PlayWallet.tenant_id == tenant_id, PlayWallet.user_id.in_(ids)
                    )
                )
            )
            .tuples()
            .all()
        )
        if ids
        else {}
    )
    master = actor.user.account_type == AccountType.MASTER_AGENT
    mine = CommissionEntry.master_points if master else CommissionEntry.agent_points
    scope = CommissionEntry.master_agent_id == actor.id if master else CommissionEntry.agent_id == actor.id
    # Players: the commission each one generated for the caller. Agents: what the agent earned.
    from_players = (
        dict(
            (
                await db.execute(
                    select(CommissionEntry.player_id, func.sum(mine))
                    .where(CommissionEntry.tenant_id == tenant_id, scope, CommissionEntry.player_id.in_(ids))
                    .group_by(CommissionEntry.player_id)
                )
            )
            .tuples()
            .all()
        )
        if ids
        else {}
    )
    agent_ids = [u.id for u in users if u.account_type == AccountType.AGENT]
    agents_earned = (
        dict(
            (
                await db.execute(
                    select(CommissionEntry.agent_id, func.sum(CommissionEntry.agent_points))
                    .where(CommissionEntry.tenant_id == tenant_id, CommissionEntry.agent_id.in_(agent_ids))
                    .group_by(CommissionEntry.agent_id)
                )
            )
            .tuples()
            .all()
        )
        if agent_ids
        else {}
    )
    allocation = await commissions.allocation(db, tenant_id, actor.id) if master else None
    items = []
    for u in users:
        is_agent = u.account_type == AccountType.AGENT
        rate = None
        if is_agent and allocation is not None and u.parent_id == actor.id:
            rate = await commissions.agent_rate(db, tenant_id, u.id, allocation)
        items.append(
            MyUserRow(
                id=u.id,
                code=u.id.hex[:8].upper(),
                username=u.username,
                full_name=u.full_name,
                account_type=u.account_type.value,
                parent_username=parents.get(u.parent_id) if u.parent_id else None,
                direct=u.parent_id == actor.id,
                status=u.status.value,
                online=u.id in online,
                via_referral=u.account_type == AccountType.PLAYER and u.parent_id is not None and u.created_by_id is None,
                balance=balances.get(u.id, ZERO),
                earnings=Decimal(agents_earned.get(u.id, 0) if is_agent else from_players.get(u.id, 0)),
                rate=rate,
                created_at=u.created_at.isoformat(),
                last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
            )
        )
    return Page(items=items, total=int(total), page=params.page, page_size=params.page_size)
