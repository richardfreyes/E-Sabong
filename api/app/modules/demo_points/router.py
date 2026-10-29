import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status

from app.core.deps import (
    CurrentUser,
    Meta,
    TenantDb,
    require_any_permission,
    require_module,
    require_permissions,
)
from app.core.errors import NotFound
from app.core.permissions import P
from app.modules.demo_points import service
from app.modules.demo_points.models import TransactionKind
from app.modules.demo_points.schemas import (
    AccountOut,
    AdjustmentRequest,
    AllocationRequest,
    LedgerEntryOut,
    PointsSummary,
    TransactionOut,
    TransferRequest,
)
from app.modules.users import service as users_service
from app.shared.schemas import Page, PageDep

router = APIRouter(prefix="/admin/points", tags=["admin: demonstration points"], dependencies=[Depends(require_module("points"))])

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9_\-:.]+$",
        description="Unique per logical operation; retries with the same key return the original result",
    ),
]
Allocator = Annotated[CurrentUser, Depends(require_permissions(P.POINTS_ALLOCATE))]
Transferrer = Annotated[CurrentUser, Depends(require_permissions(P.POINTS_TRANSFER))]
Adjuster = Annotated[CurrentUser, Depends(require_permissions(P.POINTS_ADJUST))]
Viewer = Annotated[CurrentUser, Depends(require_any_permission(P.POINTS_VIEW_ALL, P.POINTS_VIEW_DOWNLINE))]


async def _respond(db: TenantDb, current: CurrentUser, outcome: service.Outcome, response: Response) -> TransactionOut:
    if outcome.replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replay"] = "true"
    [out] = await service.to_out(db, current.tenant.id, [outcome.transaction])
    return out


@router.post("/allocations", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
async def allocate(
    body: AllocationRequest, key: IdempotencyKey, response: Response, current: Allocator, db: TenantDb, meta: Meta
) -> TransactionOut:
    """Issue new demonstration points from the tenant's issuance account."""
    outcome = await service.allocate(
        db, current, user_id=body.user_id, amount=body.amount, memo=body.memo, idempotency_key=key, meta=meta
    )
    return await _respond(db, current, outcome, response)


@router.post("/transfers", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
async def transfer(
    body: TransferRequest, key: IdempotencyKey, response: Response, current: Transferrer, db: TenantDb, meta: Meta
) -> TransactionOut:
    """Move your own points to an account in your downline."""
    outcome = await service.transfer(
        db, current, to_user_id=body.to_user_id, amount=body.amount, memo=body.memo, idempotency_key=key, meta=meta
    )
    return await _respond(db, current, outcome, response)


@router.post("/adjustments", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
async def adjust(
    body: AdjustmentRequest, key: IdempotencyKey, response: Response, current: Adjuster, db: TenantDb, meta: Meta
) -> TransactionOut:
    """Administrative correction (credit from or debit to the issuance account)."""
    outcome = await service.adjust(
        db,
        current,
        user_id=body.user_id,
        direction=body.direction,
        amount=body.amount,
        reason=body.reason,
        idempotency_key=key,
        meta=meta,
    )
    return await _respond(db, current, outcome, response)


@router.get("/transactions", response_model=Page[TransactionOut])
async def list_transactions(
    current: Viewer, db: TenantDb, params: PageDep, user_id: uuid.UUID | None = None, kind: TransactionKind | None = None
) -> Page[TransactionOut]:
    if user_id and not current.has(P.POINTS_VIEW_ALL) and user_id != current.id:
        await users_service.ensure_in_scope(db, current, user_id)
    items, total = await service.list_transactions(db, current, params, user_id=user_id, kind=kind)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


async def _scoped_account(db: TenantDb, current: CurrentUser, user_id: uuid.UUID):  # type: ignore[no-untyped-def]
    if user_id != current.id and not current.has(P.POINTS_VIEW_ALL):
        await users_service.ensure_in_scope(db, current, user_id)
    user = await users_service.get_user(db, current.tenant.id, user_id)
    acct = await service.user_account(db, current.tenant.id, user_id)
    return user, acct


@router.get("/accounts/{user_id}", response_model=AccountOut)
async def get_account(user_id: uuid.UUID, current: Viewer, db: TenantDb) -> AccountOut:
    user, acct = await _scoped_account(db, current, user_id)
    return AccountOut(user_id=user.id, username=user.username, full_name=user.full_name, balance=acct.balance)


@router.get("/accounts/{user_id}/entries", response_model=Page[LedgerEntryOut])
async def account_entries(user_id: uuid.UUID, current: Viewer, db: TenantDb, params: PageDep) -> Page[LedgerEntryOut]:
    _, acct = await _scoped_account(db, current, user_id)
    items, total = await service.statement(db, current.tenant.id, acct, params)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.get("/summary", response_model=PointsSummary)
async def summary(current: Viewer, db: TenantDb) -> PointsSummary:
    return await service.summary(db, current)


@router.get("/me", response_model=AccountOut)
async def my_account(
    current: Annotated[
        CurrentUser, Depends(require_any_permission(P.POINTS_TRANSFER, P.POINTS_VIEW_DOWNLINE, P.POINTS_VIEW_ALL))
    ],
    db: TenantDb,
) -> AccountOut:
    try:
        acct = await service.user_account(db, current.tenant.id, current.id)
    except NotFound:
        raise NotFound("You do not have a points account") from None
    return AccountOut(user_id=current.id, username=current.user.username, full_name=current.user.full_name, balance=acct.balance)
