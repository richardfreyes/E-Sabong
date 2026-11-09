"""Commission and earnings for master agents and agents (``/commissions``).

Every route scopes to the caller from the session: a master agent sees and sets their own
network, an agent sees their own earnings. Administrators set a master agent's allocation.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentUserDep, Meta, TenantDb, require_module
from app.modules.commissions import moves, service
from app.modules.commissions.schemas import (
    AgentRateIn,
    AgentRateRow,
    ApproverOut,
    BalanceRequestIn,
    BalanceRequestOut,
    BreakdownRow,
    CommissionOverview,
    DecisionIn,
    EarningsMoveIn,
    EarningsMoveOut,
    LoadIn,
    LoadOut,
    HistoryRow,
    MasterAllocation,
    MasterAllocationIn,
)
from app.shared.schemas import Page, PageDep

router = APIRouter(
    prefix="/commissions",
    tags=["commissions"],
    dependencies=[Depends(require_module("events")), Depends(require_module("matches"))],
)


@router.get("/me", response_model=CommissionOverview)
async def overview(current: CurrentUserDep, db: TenantDb) -> CommissionOverview:
    """The caller's rate and earnings: total, this month and last month (tenant timezone)."""
    return await service.overview(db, current)


@router.get("/agents", response_model=list[AgentRateRow])
async def agents(current: CurrentUserDep, db: TenantDb) -> list[AgentRateRow]:
    """Master agent: their agents with each agent's rate, the master agent's remainder and earnings."""
    return await service.agent_rows(db, current)


@router.put("/agents/{agent_id}", response_model=AgentRateRow)
async def set_agent_rate(
    agent_id: uuid.UUID, body: AgentRateIn, current: CurrentUserDep, db: TenantDb, meta: Meta
) -> AgentRateRow:
    """Master agent: set one of their agents' rate. Applies to bets accepted from now on."""
    return await service.set_agent_rate(db, current, agent_id, body.rate, meta)


@router.get("/history", response_model=Page[HistoryRow])
async def history(
    current: CurrentUserDep,
    db: TenantDb,
    params: PageDep,
    agent_id: uuid.UUID | None = None,
    match_id: uuid.UUID | None = None,
) -> Page[HistoryRow]:
    """The earnings ledger visible to the caller, newest first (reversals included)."""
    return await service.history(db, current, params, agent_id=agent_id, match_id=match_id)


@router.get("/breakdown", response_model=list[BreakdownRow])
async def breakdown(
    current: CurrentUserDep,
    db: TenantDb,
    by: Literal["player", "fight"] = Query("player"),
    agent_id: uuid.UUID | None = None,
) -> list[BreakdownRow]:
    """Net commission per player or per fight."""
    return await service.breakdown(db, current, by, agent_id=agent_id)


@router.get("/approvers", response_model=list[ApproverOut])
async def list_approvers(current: CurrentUserDep, db: TenantDb) -> list[ApproverOut]:
    """Active Loaders and administrators the caller may ask for play balance."""
    return await moves.approvers(db, current)


@router.get("/balance-requests", response_model=list[BalanceRequestOut])
async def my_balance_requests(current: CurrentUserDep, db: TenantDb) -> list[BalanceRequestOut]:
    """Balance requests this master agent or agent has sent."""
    return await moves.list_requests(db, current)


@router.post("/balance-requests", response_model=BalanceRequestOut, status_code=201)
async def request_balance(
    body: BalanceRequestIn, current: CurrentUserDep, db: TenantDb, meta: Meta
) -> BalanceRequestOut:
    """Ask every Loader and administrator for a play-balance amount. Nothing is posted until one of them approves."""
    return await moves.create_request(db, current, body, meta)


@router.get("/approvals", response_model=list[BalanceRequestOut])
async def approval_inbox(current: CurrentUserDep, db: TenantDb) -> list[BalanceRequestOut]:
    """Requests waiting on this Loader or administrator, plus the ones they already decided."""
    return await moves.inbox(db, current)


@router.post("/approvals/{request_id}", response_model=BalanceRequestOut)
async def decide_balance_request(
    request_id: uuid.UUID, body: DecisionIn, current: CurrentUserDep, db: TenantDb, meta: Meta
) -> BalanceRequestOut:
    """Approve or reject a request addressed to the caller."""
    return await moves.decide(db, current, request_id, body, meta)


@router.get("/earnings-moves", response_model=list[EarningsMoveOut])
async def my_earnings_moves(current: CurrentUserDep, db: TenantDb) -> list[EarningsMoveOut]:
    """Commission this account has moved into its own play wallet."""
    return await moves.list_moves(db, current)


@router.post("/loads", response_model=LoadOut, status_code=201)
async def load_funds(body: LoadIn, current: CurrentUserDep, db: TenantDb, meta: Meta) -> LoadOut:
    """Send play balance to a direct account, withdraw it back, or move commission into that account."""
    return await moves.load_funds(db, current, body, meta)


@router.post("/earnings-moves", response_model=EarningsMoveOut, status_code=201)
async def move_earnings(body: EarningsMoveIn, current: CurrentUserDep, db: TenantDb, meta: Meta) -> EarningsMoveOut:
    """Move available commission into this account's play wallet. Other accounts are unchanged."""
    return await moves.move_earnings(db, current, body, meta)


@router.get("/masters/{master_id}", response_model=MasterAllocation)
async def master_allocation(master_id: uuid.UUID, current: CurrentUserDep, db: TenantDb) -> MasterAllocation:
    """Administrators: a master agent's commission, and whether it is still the 8.5% default."""
    return await service.master_allocation(db, current, master_id)


@router.put("/masters/{master_id}", response_model=MasterAllocation)
async def set_master_allocation(
    master_id: uuid.UUID, body: MasterAllocationIn, current: CurrentUserDep, db: TenantDb, meta: Meta
) -> MasterAllocation:
    """Administrators: a master agent's total allocation (default 8.5%)."""
    return await service.set_master_allocation(db, current, master_id, body.allocation, meta)
