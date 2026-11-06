"""Commission rates, settlement into the earnings ledger, and the agent / master agent read models.

Rules (all on the server, all ``Decimal``):

* A master agent has a total allocation (default 8.5%). Each agent under them gets a rate set by
  that master agent, at most the allocation. The master agent keeps ``allocation - agent rate``.
  A player directly under a master agent gives the master agent the whole allocation.
* The applicable rates are snapshotted on the bet when it is accepted (``snapshot``).
* Only an official MERON or WALA result earns commission, on the original stake, for winning and
  losing bets alike. DRAW, cancellation and no contest earn nothing. Dummy / ghost stakes are not
  bets, so they never earn.
* Rounding: each party's share of each bet is ``stake x rate`` rounded DOWN to the cent, so the
  two shares together never exceed the allocation.
* ``sync_fight`` runs inside every settlement (declaration, correction, cancellation) while the
  caller holds the fight lock. It posts only the difference between what each bet should have
  earned and what the ledger already holds, so a repeat posts nothing and a correction posts an
  audited reversal. Wallets are never touched.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.deps import CurrentUser
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.permissions import P
from app.core.security import utcnow
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.betting.models import Bet
from app.modules.commissions.models import AgentCommissionRate, CommissionEntry, CommissionStatus
from app.modules.commissions.schemas import (
    AgentRateRow,
    BreakdownRow,
    CommissionOverview,
    HistoryRow,
    MasterAllocation,
)
from app.modules.events.models import Event
from app.modules.matches.models import Match
from app.modules.realtime.publisher import publish_to_users_after_commit
from app.modules.tenants.models import TenantSettings
from app.modules.users.models import AccountType, TenantUser
from app.shared.schemas import Page, PageParams

DEFAULT_ALLOCATION = Decimal("0.0850")
MAX_ALLOCATION = Decimal("0.5000")
RATE_PLACES = Decimal("0.0001")
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
ELIGIBLE_OUTCOMES = frozenset({"meron", "wala"})


def share(amount: Decimal, rate: Decimal) -> Decimal:
    """One party's commission on one stake: rounded down to the cent (never over the allocation)."""
    return (amount * rate).quantize(CENT, rounding=ROUND_DOWN)


# ---- rates -------------------------------------------------------------------------------------


async def _rate_row(
    db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, *, lock: bool = False
) -> AgentCommissionRate | None:
    stmt = select(AgentCommissionRate).where(AgentCommissionRate.tenant_id == tenant_id, AgentCommissionRate.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def allocation(db: AsyncSession, tenant_id: uuid.UUID, master_id: uuid.UUID) -> Decimal:
    row = await _rate_row(db, tenant_id, master_id)
    return row.rate if row else DEFAULT_ALLOCATION


async def agent_rate(db: AsyncSession, tenant_id: uuid.UUID, agent_id: uuid.UUID, master_allocation: Decimal) -> Decimal:
    row = await _rate_row(db, tenant_id, agent_id)
    return min(row.rate, master_allocation) if row else Decimal("0")


async def _user(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID | None) -> TenantUser | None:
    if user_id is None:
        return None
    return (
        await db.execute(
            select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.id == user_id, TenantUser.deleted_at.is_(None))
        )
    ).scalar_one_or_none()


async def snapshot(
    db: AsyncSession, tenant_id: uuid.UUID, player: TenantUser
) -> tuple[uuid.UUID | None, uuid.UUID | None, Decimal, Decimal]:
    """(agent_id, master_agent_id, agent_rate, master_rate) for a bet the player places now."""
    parent = await _user(db, tenant_id, player.parent_id)
    if parent is None:
        return None, None, Decimal("0"), Decimal("0")
    if parent.account_type == AccountType.MASTER_AGENT:
        return None, parent.id, Decimal("0"), await allocation(db, tenant_id, parent.id)
    if parent.account_type != AccountType.AGENT:
        return None, None, Decimal("0"), Decimal("0")
    master = await _user(db, tenant_id, parent.parent_id)
    if master is None or master.account_type != AccountType.MASTER_AGENT:
        return parent.id, None, Decimal("0"), Decimal("0")
    total = await allocation(db, tenant_id, master.id)
    rate = await agent_rate(db, tenant_id, parent.id, total)
    return parent.id, master.id, rate, total - rate


def _require_agent(actor: CurrentUser) -> None:
    if actor.user.account_type not in (AccountType.MASTER_AGENT, AccountType.AGENT):
        raise Forbidden("Commission is for master agents and agents", code="commission_agents_only")


def _require_master(actor: CurrentUser) -> None:
    if actor.user.account_type != AccountType.MASTER_AGENT:
        raise Forbidden("Only a master agent sets agent commission rates", code="commission_master_only")


def _rate_value(rate: Decimal) -> Decimal:
    return rate.quantize(RATE_PLACES)


async def _direct_agents(db: AsyncSession, tenant_id: uuid.UUID, master_id: uuid.UUID) -> list[TenantUser]:
    rows = await db.execute(
        select(TenantUser)
        .where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.parent_id == master_id,
            TenantUser.account_type == AccountType.AGENT,
            TenantUser.deleted_at.is_(None),
        )
        .order_by(func.lower(TenantUser.username))
    )
    return list(rows.scalars())


async def set_agent_rate(
    db: AsyncSession, actor: CurrentUser, agent_id: uuid.UUID, rate: Decimal, meta: RequestMeta
) -> AgentRateRow:
    """A master agent sets one of their own agents' rate. Applies to bets accepted from now on."""
    _require_master(actor)
    tenant_id = actor.tenant.id
    # Lock the master's allocation row (if any) so an allocation change cannot race this one.
    await _rate_row(db, tenant_id, actor.id, lock=True)
    agent = await _user(db, tenant_id, agent_id)
    if agent is None or agent.account_type != AccountType.AGENT or agent.parent_id != actor.id:
        raise NotFound("Agent not found in your network", code="agent_not_found")
    total = await allocation(db, tenant_id, actor.id)
    rate = _rate_value(rate)
    if rate < 0 or rate > total:
        raise Unprocessable(
            f"An agent's rate must be between 0% and your allocation of {total * 100:.2f}%",
            code="rate_above_allocation",
            details={"allocation": str(total)},
        )
    row = await _rate_row(db, tenant_id, agent.id, lock=True)
    before = row.rate if row else Decimal("0")
    if row is None:
        row = AgentCommissionRate(id=uuid.uuid4(), tenant_id=tenant_id, user_id=agent.id, rate=rate, updated_by_id=actor.id)
        db.add(row)
    else:
        row.rate, row.updated_by_id = rate, actor.id
    await db.flush()
    if before != rate:
        await audit.record(
            db,
            actor=actor.actor,
            action="commission.agent_rate_changed",
            resource_type="tenant_user",
            resource_id=agent.id,
            tenant_id=tenant_id,
            changes={"agent_rate": {"from": str(before), "to": str(rate)}, "master_rate": str(total - rate)},
            meta=meta,
        )
        publish_to_users_after_commit(db, tenant_id, "commission.updated", {"reason": "rate"}, {agent.id, actor.id})
    return (await agent_rows(db, actor, only=agent.id))[0]


async def set_master_allocation(
    db: AsyncSession, actor: CurrentUser, master_id: uuid.UUID, rate: Decimal, meta: RequestMeta
) -> MasterAllocation:
    """Tenant administrators set a master agent's total allocation (never below an agent's rate)."""
    if not actor.has(P.USERS_MANAGE):
        raise Forbidden("Only an administrator sets a master agent's allocation", code="permission_denied")
    tenant_id = actor.tenant.id
    master = await _user(db, tenant_id, master_id)
    if master is None or master.account_type != AccountType.MASTER_AGENT:
        raise NotFound("Master agent not found", code="master_agent_not_found")
    rate = _rate_value(rate)
    if rate < 0 or rate > MAX_ALLOCATION:
        raise Unprocessable("The allocation must be between 0% and 50%", code="allocation_out_of_range")
    row = await _rate_row(db, tenant_id, master.id, lock=True)
    agents = await _direct_agents(db, tenant_id, master.id)
    highest = max([await agent_rate(db, tenant_id, a.id, MAX_ALLOCATION) for a in agents], default=Decimal("0"))
    if rate < highest:
        raise Conflict(
            f"An agent of this master agent has {highest * 100:.2f}%; lower that agent's rate first",
            code="allocation_below_agent_rate",
            details={"highest_agent_rate": str(highest)},
        )
    before = row.rate if row else DEFAULT_ALLOCATION
    if row is None:
        db.add(AgentCommissionRate(id=uuid.uuid4(), tenant_id=tenant_id, user_id=master.id, rate=rate, updated_by_id=actor.id))
    else:
        row.rate, row.updated_by_id = rate, actor.id
    await db.flush()
    if before != rate:
        await audit.record(
            db,
            actor=actor.actor,
            action="commission.allocation_changed",
            resource_type="tenant_user",
            resource_id=master.id,
            tenant_id=tenant_id,
            changes={"allocation": {"from": str(before), "to": str(rate)}},
            meta=meta,
        )
        publish_to_users_after_commit(db, tenant_id, "commission.updated", {"reason": "allocation"}, {master.id})
    return MasterAllocation(master_agent_id=master.id, username=master.username, allocation=rate)


# ---- settlement --------------------------------------------------------------------------------


async def sync_fight(
    db: AsyncSession,
    actor: CurrentUser,
    match_id: uuid.UUID,
    outcome: str,
    revision: int | None,
    bets: Sequence[Bet],
    meta: RequestMeta | None,
) -> None:
    """Bring the earnings ledger of one fight in line with its official result (idempotent)."""
    tenant_id = actor.tenant.id
    eligible = outcome in ELIGIBLE_OUTCOMES
    candidates = [b for b in bets if (b.agent_id or b.master_agent_id) and (b.agent_rate > 0 or b.master_rate > 0)]
    if not candidates:
        return
    held = {
        bet_id: (Decimal(agent or 0), Decimal(master or 0), int(seq))
        for bet_id, agent, master, seq in (
            await db.execute(
                select(
                    CommissionEntry.bet_id,
                    func.sum(CommissionEntry.agent_points),
                    func.sum(CommissionEntry.master_points),
                    func.max(CommissionEntry.seq),
                )
                .where(CommissionEntry.tenant_id == tenant_id, CommissionEntry.match_id == match_id)
                .group_by(CommissionEntry.bet_id)
            )
        ).all()
    }
    posted: list[CommissionEntry] = []
    for bet in candidates:
        want_agent = share(bet.amount, bet.agent_rate) if eligible and bet.agent_id else ZERO
        want_master = share(bet.amount, bet.master_rate) if eligible and bet.master_agent_id else ZERO
        have_agent, have_master, seq = held.get(bet.id, (ZERO, ZERO, 0))
        d_agent, d_master = want_agent - have_agent, want_master - have_master
        if d_agent == 0 and d_master == 0:
            continue
        entry = CommissionEntry(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            match_id=match_id,
            bet_id=bet.id,
            seq=seq + 1,
            player_id=bet.user_id,
            agent_id=bet.agent_id,
            master_agent_id=bet.master_agent_id,
            bet_amount=bet.amount,
            agent_rate=bet.agent_rate,
            master_rate=bet.master_rate,
            agent_points=d_agent,
            master_points=d_master,
            outcome=outcome,
            result_revision=revision,
            status=CommissionStatus.EARNED if d_agent + d_master > 0 else CommissionStatus.REVERSED,
        )
        db.add(entry)
        posted.append(entry)
    if not posted:
        return
    await db.flush()
    agent_total = sum((e.agent_points for e in posted), ZERO)
    master_total = sum((e.master_points for e in posted), ZERO)
    await audit.record(
        db,
        actor=actor.actor,
        action="commission.settled" if agent_total + master_total >= 0 else "commission.reversed",
        resource_type="match",
        resource_id=match_id,
        tenant_id=tenant_id,
        changes={
            "outcome": outcome,
            "revision": revision,
            "entries": len(posted),
            "agent_points": str(agent_total),
            "master_points": str(master_total),
        },
        meta=meta,
    )
    recipients = {uid for e in posted for uid in (e.agent_id, e.master_agent_id) if uid}
    publish_to_users_after_commit(db, tenant_id, "commission.updated", {"match_id": str(match_id)}, recipients)


# ---- read models -------------------------------------------------------------------------------


async def _month_starts(db: AsyncSession, tenant_id: uuid.UUID) -> tuple[datetime, datetime]:
    """Start of this month and of last month, in the tenant's timezone."""
    settings = await db.get(TenantSettings, tenant_id)
    try:
        zone = ZoneInfo(settings.timezone if settings else "Asia/Manila")
    except (KeyError, ValueError):
        zone = ZoneInfo("Asia/Manila")
    now = utcnow().astimezone(zone)
    this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    return this_month, last_month


def _scope(actor: CurrentUser):  # type: ignore[no-untyped-def]
    """Ledger rows the caller may see, and the column holding the caller's own points."""
    if actor.user.account_type == AccountType.MASTER_AGENT:
        return CommissionEntry.master_agent_id == actor.id, CommissionEntry.master_points
    return CommissionEntry.agent_id == actor.id, CommissionEntry.agent_points


async def overview(db: AsyncSession, actor: CurrentUser) -> CommissionOverview:
    _require_agent(actor)
    tenant_id = actor.tenant.id
    this_month, last_month = await _month_starts(db, tenant_id)
    where, mine = _scope(actor)
    network = CommissionEntry.agent_points + CommissionEntry.master_points
    in_month = CommissionEntry.created_at >= this_month
    in_last = (CommissionEntry.created_at >= last_month) & (CommissionEntry.created_at < this_month)
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(mine), 0),
                func.coalesce(func.sum(case((in_month, mine), else_=0)), 0),
                func.coalesce(func.sum(case((in_last, mine), else_=0)), 0),
                func.coalesce(func.sum(network), 0),
                func.coalesce(func.sum(case((in_month, network), else_=0)), 0),
                func.coalesce(func.sum(case((in_last, network), else_=0)), 0),
            ).where(CommissionEntry.tenant_id == tenant_id, where)
        )
    ).one()
    master = actor.user.account_type == AccountType.MASTER_AGENT
    if master:
        total = await allocation(db, tenant_id, actor.id)
        rate, upline_rate = total, None
    else:
        parent = await _user(db, tenant_id, actor.user.parent_id)
        total = (
            await allocation(db, tenant_id, parent.id)
            if parent and parent.account_type == AccountType.MASTER_AGENT
            else Decimal("0")
        )
        rate, upline_rate = await agent_rate(db, tenant_id, actor.id, total), None
    return CommissionOverview(
        role="master_agent" if master else "agent",
        rate=_rate_value(rate),
        allocation=_rate_value(total) if master else None,
        upline_rate=upline_rate,
        total_earned=Decimal(row[0]),
        this_month=Decimal(row[1]),
        last_month=Decimal(row[2]),
        network_total=Decimal(row[3]) if master else Decimal(row[0]),
        network_this_month=Decimal(row[4]) if master else Decimal(row[1]),
        network_last_month=Decimal(row[5]) if master else Decimal(row[2]),
    )


async def agent_rows(db: AsyncSession, actor: CurrentUser, *, only: uuid.UUID | None = None) -> list[AgentRateRow]:
    _require_master(actor)
    tenant_id = actor.tenant.id
    total = await allocation(db, tenant_id, actor.id)
    agents = [a for a in await _direct_agents(db, tenant_id, actor.id) if only is None or a.id == only]
    ids = [a.id for a in agents]
    earned = (
        {
            agent_id: (Decimal(a), Decimal(m))
            for agent_id, a, m in (
                await db.execute(
                    select(
                        CommissionEntry.agent_id, func.sum(CommissionEntry.agent_points), func.sum(CommissionEntry.master_points)
                    )
                    .where(
                        CommissionEntry.tenant_id == tenant_id,
                        CommissionEntry.master_agent_id == actor.id,
                        CommissionEntry.agent_id.in_(ids),
                    )
                    .group_by(CommissionEntry.agent_id)
                )
            ).all()
        }
        if ids
        else {}
    )
    players = (
        {
            parent_id: int(n)
            for parent_id, n in (
                await db.execute(
                    select(TenantUser.parent_id, func.count())
                    .where(
                        TenantUser.tenant_id == tenant_id,
                        TenantUser.parent_id.in_(ids),
                        TenantUser.account_type == AccountType.PLAYER,
                        TenantUser.deleted_at.is_(None),
                    )
                    .group_by(TenantUser.parent_id)
                )
            ).all()
        }
        if ids
        else {}
    )
    out = []
    for a in agents:
        rate = await agent_rate(db, tenant_id, a.id, total)
        agent_earned, master_earned = earned.get(a.id, (ZERO, ZERO))
        out.append(
            AgentRateRow(
                agent_id=a.id,
                username=a.username,
                full_name=a.full_name,
                status=a.status.value,
                players=players.get(a.id, 0),
                agent_rate=_rate_value(rate),
                master_rate=_rate_value(total - rate),
                allocation=_rate_value(total),
                agent_earned=agent_earned,
                master_earned=master_earned,
            )
        )
    return out


def _history_stmt(actor: CurrentUser, agent_id: uuid.UUID | None, match_id: uuid.UUID | None) -> Select:  # type: ignore[type-arg]
    player, agent = aliased(TenantUser), aliased(TenantUser)
    where, _mine = _scope(actor)
    stmt = (
        select(CommissionEntry, Match.match_number, Event.name, player.username, agent.username, Bet.side)
        .join(Match, (Match.tenant_id == CommissionEntry.tenant_id) & (Match.id == CommissionEntry.match_id))
        .join(Event, (Event.tenant_id == Match.tenant_id) & (Event.id == Match.event_id))
        .join(Bet, (Bet.tenant_id == CommissionEntry.tenant_id) & (Bet.id == CommissionEntry.bet_id))
        .join(player, (player.tenant_id == CommissionEntry.tenant_id) & (player.id == CommissionEntry.player_id))
        .outerjoin(agent, (agent.tenant_id == CommissionEntry.tenant_id) & (agent.id == CommissionEntry.agent_id))
        .where(CommissionEntry.tenant_id == actor.tenant.id, where)
    )
    if agent_id:
        stmt = stmt.where(CommissionEntry.agent_id == agent_id)
    if match_id:
        stmt = stmt.where(CommissionEntry.match_id == match_id)
    return stmt


async def history(
    db: AsyncSession, actor: CurrentUser, params: PageParams, *, agent_id: uuid.UUID | None, match_id: uuid.UUID | None
) -> Page[HistoryRow]:
    _require_agent(actor)
    master = actor.user.account_type == AccountType.MASTER_AGENT
    stmt = _history_stmt(actor, agent_id, match_id)
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(CommissionEntry.created_at.desc(), CommissionEntry.id.desc())
            .offset(params.offset)
            .limit(params.page_size)
        )
    ).all()
    items = [
        HistoryRow(
            id=e.id,
            created_at=e.created_at,
            match_id=e.match_id,
            match_number=number,
            event_name=event_name,
            player_id=e.player_id,
            player_username=player_name,
            agent_id=e.agent_id,
            agent_username=agent_name,
            side=str(side),
            bet_amount=e.bet_amount,
            agent_rate=e.agent_rate,
            agent_points=e.agent_points,
            # An agent sees their own share only; the master agent's cut stays with the master agent.
            master_rate=e.master_rate if master else None,
            master_points=e.master_points if master else None,
            outcome=e.outcome,
            result_revision=e.result_revision,
            status=e.status.value,
        )
        for e, number, event_name, player_name, agent_name, side in rows
    ]
    return Page(items=items, total=int(total), page=params.page, page_size=params.page_size)


async def breakdown(
    db: AsyncSession, actor: CurrentUser, by: Literal["player", "fight"], *, agent_id: uuid.UUID | None
) -> list[BreakdownRow]:
    """Net commission per player or per fight. ``eligible_points`` nets reversals out."""
    _require_agent(actor)
    master = actor.user.account_type == AccountType.MASTER_AGENT
    where, _mine = _scope(actor)
    signed_amount = case(
        (CommissionEntry.status == CommissionStatus.EARNED, CommissionEntry.bet_amount), else_=-CommissionEntry.bet_amount
    )
    measures = (
        func.count(func.distinct(CommissionEntry.bet_id)),
        func.sum(signed_amount),
        func.sum(CommissionEntry.agent_points),
        func.sum(CommissionEntry.master_points),
    )
    base = [CommissionEntry.tenant_id == actor.tenant.id, where]
    if agent_id:
        base.append(CommissionEntry.agent_id == agent_id)
    if by == "player":
        player = aliased(TenantUser)
        rows = (
            await db.execute(
                select(CommissionEntry.player_id, player.username, *measures)
                .join(player, (player.tenant_id == CommissionEntry.tenant_id) & (player.id == CommissionEntry.player_id))
                .where(*base)
                .group_by(CommissionEntry.player_id, player.username)
                .order_by(func.sum(CommissionEntry.agent_points + CommissionEntry.master_points).desc(), player.username)
                .limit(200)
            )
        ).all()
        keyed = [(str(k), label, *rest) for k, label, *rest in rows]
    else:
        rows = (
            await db.execute(
                select(CommissionEntry.match_id, Match.match_number, Event.name, *measures)
                .join(Match, (Match.tenant_id == CommissionEntry.tenant_id) & (Match.id == CommissionEntry.match_id))
                .join(Event, (Event.tenant_id == Match.tenant_id) & (Event.id == Match.event_id))
                .where(*base)
                .group_by(CommissionEntry.match_id, Match.match_number, Event.name)
                .order_by(func.max(CommissionEntry.created_at).desc())
                .limit(200)
            )
        ).all()
        keyed = [(str(k), f"{event} · Fight #{number}", *rest) for k, number, event, *rest in rows]
    return [
        BreakdownRow(
            key=key,
            label=label,
            bets=int(bets),
            eligible_points=Decimal(amount or 0),
            agent_points=Decimal(agent or 0),
            master_points=Decimal(master_pts or 0) if master else None,
        )
        for key, label, bets, amount, agent, master_pts in keyed
    ]
