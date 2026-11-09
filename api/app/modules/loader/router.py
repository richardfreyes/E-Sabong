"""Loader dashboard API. Every query is limited to the caller's tenant and Loader account."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import CurrentUserDep, Meta, TenantDb
from app.modules.auth.models import AccountStatus
from app.modules.loader import service
from app.modules.loader.schemas import AccountOut, AllocationOut, GrantIn, GrantOut
from app.modules.users.models import AccountType
from app.shared.schemas import Page, PageDep

router = APIRouter(prefix="/loader", tags=["loader"])


@router.get("/allocation", response_model=AllocationOut)
async def allocation(current: CurrentUserDep, db: TenantDb) -> AllocationOut:
    return await service.allocation(db, current)


@router.get("/accounts", response_model=Page[AccountOut])
async def accounts(
    current: CurrentUserDep,
    db: TenantDb,
    params: PageDep,
    account_type: Annotated[AccountType | None, Query()] = None,
    status: Annotated[AccountStatus | None, Query()] = None,
) -> Page[AccountOut]:
    return await service.accounts(db, current, params, account_type=account_type, status=status)


@router.get("/history", response_model=Page[GrantOut])
async def history(current: CurrentUserDep, db: TenantDb, params: PageDep) -> Page[GrantOut]:
    return await service.history(db, current, params)


@router.post("/grants", response_model=GrantOut)
async def grant(body: GrantIn, current: CurrentUserDep, db: TenantDb, meta: Meta) -> GrantOut:
    return await service.grant(db, current, body, meta)
