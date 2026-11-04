"""Tenant-authenticated operational resources; independent of event/gameplay modules."""

import uuid

from fastapi import APIRouter

from app.core.deps import CurrentUserDep, Meta, TenantDb
from app.core.permissions import P
from app.modules.resources import service
from app.modules.resources.models import ResourceAction, ResourceKind, ResourceTransaction
from app.modules.resources.schemas import (
    BalanceOut,
    IssuanceRequest,
    RefundRequest,
    ResourceReceipt,
    SettingsRequest,
    TargetOut,
    UsageRequest,
)
from app.shared.schemas import Page, PageDep, paginate

router = APIRouter(prefix="/resources", tags=["operational resources"])


@router.get("/settings", response_model=SettingsRequest)
async def get_settings(current: CurrentUserDep, db: TenantDb) -> SettingsRequest:
    service.authorize(current, P.RESOURCES_CONFIGURE, admin=True)
    config = await service.settings(db, current.tenant.id)
    return SettingsRequest(enabled=config.enabled if config else False)


@router.put("/settings", response_model=SettingsRequest)
async def configure(data: SettingsRequest, current: CurrentUserDep, db: TenantDb, meta: Meta) -> SettingsRequest:
    await service.configure(db, current, data.enabled, meta)
    return data


@router.get("/balance/{resource_kind}", response_model=BalanceOut)
async def balance(resource_kind: ResourceKind, current: CurrentUserDep, db: TenantDb) -> BalanceOut:
    return await service.balance(db, current, resource_kind)


@router.get("/targets", response_model=Page[TargetOut])
async def targets(current: CurrentUserDep, db: TenantDb, params: PageDep) -> Page[TargetOut]:
    """Admin-only search for accounts that can receive a direct issuance."""
    items, total = await service.targets(db, current, params)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.post("/issuances", response_model=ResourceReceipt, status_code=201)
async def issue(data: IssuanceRequest, current: CurrentUserDep, db: TenantDb) -> ResourceReceipt:
    """Admin-only direct provisioning of non-monetary operational units into one account.

    There is deliberately no allocation or transfer endpoint: units never move between accounts.
    """
    return await service.post(db, current, ResourceAction.ISSUE, data)


@router.post("/usage", response_model=ResourceReceipt, status_code=201)
async def consume(data: UsageRequest, current: CurrentUserDep, db: TenantDb) -> ResourceReceipt:
    """Record operational usage against the caller's available units."""
    return await service.post(db, current, ResourceAction.CONSUME, data)


@router.post("/refunds", response_model=ResourceReceipt, status_code=201)
async def refund(data: RefundRequest, current: CurrentUserDep, db: TenantDb) -> ResourceReceipt:
    """Admin-only full reversal of an operational usage, once. Allocations cannot be reversed here."""
    return await service.post(db, current, ResourceAction.REFUND, data)


@router.get("/transactions", response_model=Page[ResourceReceipt])
async def history(
    current: CurrentUserDep,
    db: TenantDb,
    params: PageDep,
    resource_kind: ResourceKind | None = None,
    kind: ResourceAction | None = None,
) -> Page[ResourceReceipt]:
    stmt = service.history_query(current)
    if resource_kind:
        stmt = stmt.where(ResourceTransaction.resource_kind == resource_kind)
    if kind:
        stmt = stmt.where(ResourceTransaction.kind == kind)
    items, total = await paginate(db, stmt.order_by(ResourceTransaction.created_at.desc(), ResourceTransaction.id.desc()), params)
    return Page(items=[await service.receipt(db, tx) for tx in items], total=total, page=params.page, page_size=params.page_size)


@router.get("/transactions/{transaction_id}", response_model=ResourceReceipt)
async def detail(transaction_id: uuid.UUID, current: CurrentUserDep, db: TenantDb) -> ResourceReceipt:
    return await service.detail(db, current, transaction_id)
