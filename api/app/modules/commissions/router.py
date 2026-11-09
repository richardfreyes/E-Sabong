"""Commission and earnings for master agents and agents (``/commissions``).

Every route scopes to the caller from the session: a master agent sees and sets their own
network, an agent sees their own earnings. Administrators set a master agent's allocation.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentUserDep, Meta, TenantDb, require_module
from app.modules.commissions import service
from app.modules.commissions.schemas import (
    AgentRateIn,
    AgentRateRow,
    BreakdownRow,
    CommissionOverview,
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
