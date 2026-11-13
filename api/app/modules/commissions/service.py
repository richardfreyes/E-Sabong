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

from sqlalchemy import Select, String, case, cast, func, select, text
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
from app.modules.users.models import AGENT_TYPES, AccountType, TenantUser
from app.shared.schemas import Page, PageParams

DEFAULT_ALLOCATION = Decimal("0.0850")
MAX_ALLOCATION = Decimal("1.0000")
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
            select(TenantUser)
            .where(TenantUser.tenant_id == tenant_id, TenantUser.id == user_id, TenantUser.deleted_at.is_(None))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def rate_lock(db: AsyncSession, tenant_id: uuid.UUID, *, shared: bool = False) -> None:
    # A transaction-wide lock also covers the first allocation (when no rate row exists yet).
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    await db.execute(text(f"SELECT {function}(hashtextextended(:key, 0))"), {"key": f"commission-rates:{tenant_id}"})


async def assigned_rate(db: AsyncSession, tenant_id: uuid.UUID, user: TenantUser) -> Decimal:
    user = await _user(db, tenant_id, user.id)
    if user is None:
        return Decimal("0")
    if user.account_type == AccountType.MASTER_AGENT:
        return await allocation(db, tenant_id, user.id)
    parent = await _user(db, tenant_id, user.parent_id)
    if parent is None or user.account_type not in (AccountType.AGENT, AccountType.SUB_AGENT):
        return Decimal("0")
    return await agent_rate(db, tenant_id, user.id, await assigned_rate(db, tenant_id, parent))


async def snapshot(db: AsyncSession, tenant_id: uuid.UUID, player: TenantUser) -> dict:
    """Three snapshotted net shares. Changes to rates/ownership never rewrite accepted bets."""
    await rate_lock(db, tenant_id, shared=True)
    player = await _user(db, tenant_id, player.id)
    result = dict(
        agent_id=None,
        master_agent_id=None,
        sub_agent_id=None,
        agent_rate=Decimal("0"),
        master_rate=Decimal("0"),
        sub_agent_rate=Decimal("0"),
    )
    parent = await _user(db, tenant_id, player.parent_id) if player else None
    if parent is None:
        return result
    sub = parent if parent.account_type == AccountType.SUB_AGENT else None
    if sub:
        parent = await _user(db, tenant_id, sub.parent_id)
    if parent and parent.account_type == AccountType.MASTER_AGENT and not sub:
        result.update(master_agent_id=parent.id, master_rate=await allocation(db, tenant_id, parent.id))
        return result
    if parent is None or parent.account_type != AccountType.AGENT:
        return result
    master = await _user(db, tenant_id, parent.parent_id)
    if master is None or master.account_type != AccountType.MASTER_AGENT:
        return result
    total = await allocation(db, tenant_id, master.id)
    agent = await agent_rate(db, tenant_id, parent.id, total)
    sub_rate = await agent_rate(db, tenant_id, sub.id, agent) if sub else Decimal("0")
    result.update(
        agent_id=parent.id,
        master_agent_id=master.id,
        sub_agent_id=sub.id if sub else None,
        agent_rate=agent - sub_rate,
        master_rate=total - agent,
        sub_agent_rate=sub_rate,
    )
    return result


async def _spendable(db: AsyncSession, actor: CurrentUser, earned: Decimal) -> tuple[Decimal, Decimal]:
    """Earnings not yet moved to the play wallet, and the caller's own wallet balance."""
    from app.modules.betting.models import PlayWallet
    from app.modules.commissions.models import BalanceRequest, BalanceRequestStatus, EarningsMove

    moved = Decimal(
        await db.scalar(
            select(func.coalesce(func.sum(EarningsMove.amount), 0)).where(
                EarningsMove.tenant_id == actor.tenant.id, EarningsMove.user_id == actor.id
            )
        )
        or 0
    )
    pending = Decimal(
        await db.scalar(
            select(func.coalesce(func.sum(BalanceRequest.amount), 0)).where(
                BalanceRequest.tenant_id == actor.tenant.id,
                BalanceRequest.requester_id == actor.id,
                BalanceRequest.purpose == "earnings",
                BalanceRequest.status == BalanceRequestStatus.PENDING,
            )
        )
        or 0
    )
    balance = await db.scalar(
        select(PlayWallet.balance).where(PlayWallet.tenant_id == actor.tenant.id, PlayWallet.user_id == actor.id)
    )
    return earned - moved - pending, Decimal(balance or 0)


def _require_agent(actor: CurrentUser) -> None:
    if actor.user.account_type not in AGENT_TYPES:
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


def _require_rate_manager(actor: CurrentUser) -> None:
    if actor.user.account_type not in (AccountType.MASTER_AGENT, AccountType.AGENT):
        raise Forbidden("Sub-Agents cannot distribute commission", code="commission_manager_only")


async def highest_child_rate(db: AsyncSession, tenant_id: uuid.UUID, parent_id: uuid.UUID) -> Decimal:
    return Decimal(
        await db.scalar(
            select(func.max(AgentCommissionRate.rate))
            .join(
                TenantUser,
                (TenantUser.tenant_id == AgentCommissionRate.tenant_id) & (TenantUser.id == AgentCommissionRate.user_id),
            )
            .where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.parent_id == parent_id,
                TenantUser.account_type == AccountType.SUB_AGENT,
                TenantUser.deleted_at.is_(None),
            )
        )
        or 0
    )


async def set_agent_rate(
    db: AsyncSession, actor: CurrentUser, agent_id: uuid.UUID, rate: Decimal, meta: RequestMeta
) -> AgentRateRow:
    """A master agent sets one of their own agents' rate. Applies to bets accepted from now on."""
    _require_rate_manager(actor)
    tenant_id = actor.tenant.id
    await rate_lock(db, tenant_id)
    # Lock the master's allocation row (if any) so an allocation change cannot race this one.
    await _rate_row(db, tenant_id, actor.id, lock=True)
    agent = await _user(db, tenant_id, agent_id)
    if (
        agent is None
        or agent.account_type
        != (AccountType.AGENT if actor.user.account_type == AccountType.MASTER_AGENT else AccountType.SUB_AGENT)
        or agent.parent_id != actor.id
    ):
        raise NotFound("Agent not found in your network", code="agent_not_found")
    total = await assigned_rate(db, tenant_id, actor.user)
    rate = _rate_value(rate)
    if rate < 0 or rate > total:
        raise Unprocessable(
            f"An agent's rate must be between 0% and your allocation of {total * 100:.2f}%",
            code="rate_above_allocation",
            details={"allocation": str(total)},
        )
    highest = await highest_child_rate(db, tenant_id, agent.id)
    if rate < highest:
        raise Conflict(
            "Lower this account's Sub-Agent allocations first",
            code="allocation_below_sub_agent_rate",
            details={"highest_sub_agent_rate": str(highest)},
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
        from app.modules.agents.service import downline_ids_select

        recipients = set(
            await db.scalars(
                select(TenantUser.id).where(
                    TenantUser.tenant_id == tenant_id,
                    TenantUser.id.in_(downline_ids_select(tenant_id, agent.id)),
                    TenantUser.account_type.in_(AGENT_TYPES),
                )
            )
        )
        recipients.update({agent.id, actor.id})
        if actor.user.account_type == AccountType.AGENT and actor.user.parent_id:
            recipients.add(actor.user.parent_id)
        publish_to_users_after_commit(db, tenant_id, "commission.updated", {"reason": "rate"}, recipients)
    return (await agent_rows(db, actor, only=agent.id))[0]


def _require_tenant_admin(actor: CurrentUser) -> None:
    # A master agent's own rate is an administrator decision; uplines and agents only read it.
    if actor.user.account_type != AccountType.ADMIN or not actor.has(P.USERS_MANAGE):
        raise Forbidden("Only an administrator sets a master agent's commission", code="permission_denied")


async def _master(db: AsyncSession, tenant_id: uuid.UUID, master_id: uuid.UUID) -> TenantUser:
    master = await _user(db, tenant_id, master_id)
    if master is None or master.account_type != AccountType.MASTER_AGENT:
        raise NotFound("Master agent not found", code="master_agent_not_found")
    return master


async def _highest_agent_rate(db: AsyncSession, tenant_id: uuid.UUID, master_id: uuid.UUID) -> Decimal:
    agents = await _direct_agents(db, tenant_id, master_id)
    return max([await agent_rate(db, tenant_id, a.id, MAX_ALLOCATION) for a in agents], default=Decimal("0"))


async def master_allocation(db: AsyncSession, actor: CurrentUser, master_id: uuid.UUID) -> MasterAllocation:
    """Administrators: the master agent's current commission (the 8.5% default until one is saved)."""
    _require_tenant_admin(actor)
    tenant_id = actor.tenant.id
    master = await _master(db, tenant_id, master_id)
    row = await _rate_row(db, tenant_id, master.id)
    return MasterAllocation(
        master_agent_id=master.id,
        username=master.username,
        allocation=row.rate if row else DEFAULT_ALLOCATION,
        is_default=row is None,
        highest_agent_rate=await _highest_agent_rate(db, tenant_id, master.id),
    )


async def set_master_allocation(
    db: AsyncSession, actor: CurrentUser, master_id: uuid.UUID, rate: Decimal, meta: RequestMeta
) -> MasterAllocation:
    """Tenant administrators set a master agent's total allocation (never below an agent's rate).

    Only bets accepted afterwards use the new rate: each bet keeps the rates snapshotted when it
    was placed, and posted commission entries are never rewritten.
    """
    _require_tenant_admin(actor)
    tenant_id = actor.tenant.id
    await rate_lock(db, tenant_id)
    master = await _master(db, tenant_id, master_id)
    rate = _rate_value(rate)
    if rate < 0 or rate > MAX_ALLOCATION:
        raise Unprocessable("The commission must be between 0% and 100%", code="allocation_out_of_range")
    row = await _rate_row(db, tenant_id, master.id, lock=True)
    highest = await _highest_agent_rate(db, tenant_id, master.id)
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
        from app.modules.agents.service import downline_ids_select

        recipients = set(
            await db.scalars(
                select(TenantUser.id).where(
                    TenantUser.tenant_id == tenant_id,
                    TenantUser.id.in_(downline_ids_select(tenant_id, master.id)),
                    TenantUser.account_type.in_(AGENT_TYPES),
                )
            )
        )
        publish_to_users_after_commit(db, tenant_id, "commission.updated", {"reason": "allocation"}, recipients | {master.id})
    return MasterAllocation(master_agent_id=master.id, username=master.username, allocation=rate, highest_agent_rate=highest)


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
    candidates = [
        b
        for b in bets
        if (b.agent_id or b.master_agent_id or b.sub_agent_id) and (b.agent_rate > 0 or b.master_rate > 0 or b.sub_agent_rate > 0)
    ]
    if not candidates:
        return
    held = {
        bet_id: (Decimal(agent or 0), Decimal(master or 0), Decimal(sub or 0), int(seq))
        for bet_id, agent, master, sub, seq in (
            await db.execute(
                select(
                    CommissionEntry.bet_id,
                    func.sum(CommissionEntry.agent_points),
                    func.sum(CommissionEntry.master_points),
                    func.sum(CommissionEntry.sub_agent_points),
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
        want_sub = share(bet.amount, bet.sub_agent_rate) if eligible and bet.sub_agent_id else ZERO
        have_agent, have_master, have_sub, seq = held.get(bet.id, (ZERO, ZERO, ZERO, 0))
        d_sub = want_sub - have_sub
        d_agent, d_master = want_agent - have_agent, want_master - have_master
        if d_agent == 0 and d_master == 0 and d_sub == 0:
            continue
        entry = CommissionEntry(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            match_id=match_id,
            bet_id=bet.id,
            seq=seq + 1,
            player_id=bet.user_id,
            sub_agent_id=bet.sub_agent_id,
            sub_agent_rate=bet.sub_agent_rate,
            sub_agent_points=d_sub,
            agent_id=bet.agent_id,
            master_agent_id=bet.master_agent_id,
            bet_amount=bet.amount,
            agent_rate=bet.agent_rate,
            master_rate=bet.master_rate,
            agent_points=d_agent,
            master_points=d_master,
            outcome=outcome,
            result_revision=revision,
            status=CommissionStatus.EARNED if d_agent + d_master + d_sub > 0 else CommissionStatus.REVERSED,
        )
        db.add(entry)
        posted.append(entry)
    if not posted:
        return
    await db.flush()
    agent_total = sum((e.agent_points for e in posted), ZERO)
    master_total = sum((e.master_points for e in posted), ZERO)
    sub_total = sum((e.sub_agent_points for e in posted), ZERO)
    await audit.record(
        db,
        actor=actor.actor,
        action="commission.settled" if agent_total + master_total + sub_total >= 0 else "commission.reversed",
        resource_type="match",
        resource_id=match_id,
        tenant_id=tenant_id,
        changes={
            "outcome": outcome,
            "revision": revision,
            "entries": len(posted),
            "agent_points": str(agent_total),
            "master_points": str(master_total),
            "sub_agent_points": str(sub_total),
        },
        meta=meta,
    )
    recipients = {uid for e in posted for uid in (e.agent_id, e.master_agent_id, e.sub_agent_id) if uid}
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
    if actor.user.account_type == AccountType.SUB_AGENT:
        return CommissionEntry.sub_agent_id == actor.id, CommissionEntry.sub_agent_points
    return CommissionEntry.agent_id == actor.id, CommissionEntry.agent_points


async def overview(db: AsyncSession, actor: CurrentUser) -> CommissionOverview:
    _require_agent(actor)
    tenant_id = actor.tenant.id
    this_month, last_month = await _month_starts(db, tenant_id)
    where, mine = _scope(actor)
    network = (
        CommissionEntry.agent_points + CommissionEntry.master_points + CommissionEntry.sub_agent_points
        if actor.user.account_type == AccountType.MASTER_AGENT
        else CommissionEntry.agent_points + CommissionEntry.sub_agent_points
        if actor.user.account_type == AccountType.AGENT
        else CommissionEntry.sub_agent_points
    )
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
    total = await assigned_rate(db, tenant_id, actor.user)
    rate, upline_rate = total, None
    if actor.user.account_type == AccountType.SUB_AGENT:
        parent = await _user(db, tenant_id, actor.user.parent_id)
        upline_rate = await assigned_rate(db, tenant_id, parent) if parent else None
    available, own_balance = await _spendable(db, actor, Decimal(row[0]))
    return CommissionOverview(
        role=actor.user.account_type.value,
        rate=_rate_value(rate),
        allocation=_rate_value(total) if master else None,
        upline_rate=upline_rate,
        total_earned=Decimal(row[0]),
        this_month=Decimal(row[1]),
        last_month=Decimal(row[2]),
        network_total=Decimal(row[3]),
        network_this_month=Decimal(row[4]),
        network_last_month=Decimal(row[5]),
        available_earnings=available,
        own_balance=own_balance,
    )


async def agent_rows(db: AsyncSession, actor: CurrentUser, *, only: uuid.UUID | None = None) -> list[AgentRateRow]:
    from app.modules.agents.service import downline_ids_select

    _require_rate_manager(actor)
    tenant_id = actor.tenant.id
    total = await assigned_rate(db, tenant_id, actor.user)
    child_type = AccountType.AGENT if actor.user.account_type == AccountType.MASTER_AGENT else AccountType.SUB_AGENT
    children = (
        await db.scalars(
            select(TenantUser)
            .where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.parent_id == actor.id,
                TenantUser.account_type == child_type,
                TenantUser.deleted_at.is_(None),
            )
            .order_by(TenantUser.username)
        )
    ).all()
    out = []
    for child in children:
        if only is not None and only != child.id:
            continue
        rate = await agent_rate(db, tenant_id, child.id, total)
        child_id = CommissionEntry.agent_id if child_type == AccountType.AGENT else CommissionEntry.sub_agent_id
        child_points = CommissionEntry.agent_points if child_type == AccountType.AGENT else CommissionEntry.sub_agent_points
        _, own_points = _scope(actor)
        earned, own = (
            await db.execute(
                select(func.coalesce(func.sum(child_points), 0), func.coalesce(func.sum(own_points), 0)).where(
                    CommissionEntry.tenant_id == tenant_id, child_id == child.id
                )
            )
        ).one()
        players = await db.scalar(
            select(func.count())
            .select_from(TenantUser)
            .where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.id.in_(downline_ids_select(tenant_id, child.id)),
                TenantUser.account_type == AccountType.PLAYER,
            )
        )
        out.append(
            AgentRateRow(
                agent_id=child.id,
                username=child.username,
                full_name=child.full_name,
                status=child.status.value,
                players=int(players or 0),
                agent_rate=_rate_value(rate),
                master_rate=_rate_value(total - rate),
                allocation=_rate_value(total),
                agent_earned=Decimal(earned),
                master_earned=Decimal(own),
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
        stmt = stmt.where(
            (CommissionEntry.sub_agent_id if actor.user.account_type == AccountType.AGENT else CommissionEntry.agent_id)
            == agent_id
        )
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
            sub_agent_id=e.sub_agent_id,
            sub_agent_rate=e.sub_agent_rate,
            sub_agent_points=e.sub_agent_points,
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
        func.sum(CommissionEntry.sub_agent_points),
    )
    base = [CommissionEntry.tenant_id == actor.tenant.id, where]
    if agent_id:
        base.append(
            (CommissionEntry.sub_agent_id if actor.user.account_type == AccountType.AGENT else CommissionEntry.agent_id)
            == agent_id
        )
    if by == "player" and master:
        # A master agent's own players one by one; each agent's players as one row for that agent
        # (same totals, no individual player records of the agent's network).
        owner = aliased(TenantUser)
        owner_id = func.coalesce(CommissionEntry.agent_id, CommissionEntry.player_id)
        via_agent = CommissionEntry.agent_id.is_not(None)
        rows = (
            await db.execute(
                select(owner_id, via_agent, owner.username, *measures)
                .join(owner, (owner.tenant_id == CommissionEntry.tenant_id) & (owner.id == owner_id))
                .where(*base)
                .group_by(owner_id, via_agent, owner.username)
                .order_by(func.sum(CommissionEntry.agent_points + CommissionEntry.master_points).desc(), owner.username)
                .limit(200)
            )
        ).all()
        keyed = [
            (f"agent:{k}" if is_agent else str(k), f"{label}'s players" if is_agent else label, *rest)
            for k, is_agent, label, *rest in rows
        ]
    elif by == "player":
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
            sub_agent_points=Decimal(sub_pts or 0),
        )
        for key, label, bets, amount, agent, master_pts, sub_pts in keyed
    ]


async def rate_history(db: AsyncSession, actor: CurrentUser, params: PageParams) -> Page:
    from app.modules.agents.service import downline_ids_select
    from app.modules.audit.models import AuditLog

    _require_agent(actor)
    ids = downline_ids_select(actor.tenant.id, actor.id)
    stmt = (
        select(AuditLog, TenantUser.username)
        .join(TenantUser, (TenantUser.tenant_id == AuditLog.tenant_id) & (cast(TenantUser.id, String) == AuditLog.resource_id))
        .where(
            AuditLog.tenant_id == actor.tenant.id,
            (
                AuditLog.resource_id.in_(
                    select(cast(TenantUser.id, String)).where(TenantUser.tenant_id == actor.tenant.id, TenantUser.id.in_(ids))
                )
            )
            | (AuditLog.resource_id == str(actor.id)),
            AuditLog.action.in_(("commission.agent_rate_changed", "commission.allocation_changed")),
        )
    )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await db.execute(stmt.order_by(AuditLog.created_at.desc(), AuditLog.id).offset(params.offset).limit(params.page_size))
    ).all()
    return Page(
        items=[
            dict(id=entry.id, created_at=entry.created_at, username=name, action=entry.action, changes=entry.changes)
            for entry, name in rows
        ],
        total=int(total or 0),
        page=params.page,
        page_size=params.page_size,
    )
