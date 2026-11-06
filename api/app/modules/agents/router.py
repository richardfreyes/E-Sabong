"""Agent workspace screens (``/agent``): My Users for master agents and agents."""

from typing import Literal

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUserDep, TenantDb, require_module
from app.modules.agents import users
from app.modules.agents.users import MyUserRow, MyUsersSummary
from app.shared.schemas import Page, PageDep

router = APIRouter(prefix="/agent", tags=["agent workspace"], dependencies=[Depends(require_module("events"))])


@router.get("/users/summary", response_model=MyUsersSummary)
async def my_users_summary(current: CurrentUserDep, db: TenantDb) -> MyUsersSummary:
    """Counts of the caller's players and agents by status, and online now."""
    return await users.summary(db, current)


@router.get("/users", response_model=Page[MyUserRow])
async def my_users(
    current: CurrentUserDep,
    db: TenantDb,
    params: PageDep,
    kind: Literal["player", "agent"] | None = None,
    state: Literal["active", "blocked", "banned", "online"] | None = None,
) -> Page[MyUserRow]:
    """The caller's downline players and agents, newest first."""
    return await users.list_users(db, current, params, kind=kind, state=state)
