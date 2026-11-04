"""Balance Management: role-scoped views of the play-balance ledger and administrator operations.

Reads: administrators see the tenant, master agents and agents their downline (server-side scope).
Players use their own ``/portal/betting/entries`` and ``/portal/betting/statement``.

Writes (tenant administrators only): a grant to one player, uniform grants, uniform resets and
maker-checker corrections. There is no transfer or loading endpoint, and no request names a
sender, tenant or balance: those always come from the authenticated session and the database.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.deps import CurrentUser, Meta, TenantDb, require_any_permission, require_module
from app.core.permissions import P
from app.modules.auth.models import AccountStatus
from app.modules.balances import service
from app.modules.balances.models import BalanceOperationKind, BalanceOperationStatus
from app.modules.balances.schemas import (
    AccountDetail,
    AccountGrantIn,
    AccountRow,
    BalanceSummary,
    CorrectionIn,
    OperationOut,
    RejectIn,
    ResetIn,
    ReviewIn,
    UniformGrantIn,
)
from app.modules.betting.models import WalletEntryKind
from app.modules.betting.schemas import WalletEntryRow
from app.modules.users.models import AccountType
from app.shared.schemas import Page, PageDep

router = APIRouter(
    prefix="/admin/balance",
    tags=["admin: balance management"],
    dependencies=[Depends(require_module("events")), Depends(require_module("matches"))],
)

Viewer = Annotated[CurrentUser, Depends(require_any_permission(P.POINTS_VIEW_ALL, P.POINTS_VIEW_DOWNLINE))]
Manager = Annotated[CurrentUser, Depends(require_any_permission(P.BALANCE_MANAGE))]


def _created(response: Response, op: OperationOut) -> OperationOut:
    if op.replayed:
        response.status_code = status.HTTP_200_OK
    return op


@router.get("/summary", response_model=BalanceSummary)
async def summary(current: Viewer, db: TenantDb) -> BalanceSummary:
    return await service.summary(db, current)


@router.get("/accounts", response_model=Page[AccountRow])
async def accounts(
    current: Viewer,
    db: TenantDb,
    params: PageDep,
    account_type: AccountType | None = None,
    parent_id: uuid.UUID | None = None,
    account_status: AccountStatus | None = None,
) -> Page[AccountRow]:
    """Accounts the caller may see (master agents, agents, players) with their balances."""
    return await service.accounts(db, current, params, account_type=account_type, parent_id=parent_id, status=account_status)


@router.get("/accounts/{user_id}", response_model=AccountDetail)
async def account(
    user_id: uuid.UUID, current: Viewer, db: TenantDb, date_from: date | None = None, date_to: date | None = None
) -> AccountDetail:
    return await service.account_detail(db, current, user_id, date_from=date_from, date_to=date_to)


@router.get("/entries", response_model=Page[WalletEntryRow])
async def entries(
    current: Viewer,
    db: TenantDb,
    params: PageDep,
    kind: WalletEntryKind | None = None,
    user_id: uuid.UUID | None = None,
    operation_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[WalletEntryRow]:
    """Balance history (play activity and administrator operations) of the accounts in scope."""
    return await service.entries(
        db, current, params, kind=kind, user_id=user_id, operation_id=operation_id, date_from=date_from, date_to=date_to
    )


@router.get("/entries/{entry_id}", response_model=WalletEntryRow)
async def entry(entry_id: uuid.UUID, current: Viewer, db: TenantDb) -> WalletEntryRow:
    return await service.entry_detail(db, current, entry_id)


@router.get("/operations", response_model=Page[OperationOut])
async def operations(
    current: Viewer,
    db: TenantDb,
    params: PageDep,
    kind: BalanceOperationKind | None = None,
    operation_status: BalanceOperationStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[OperationOut]:
    """Uniform grants, resets and corrections with their review status (administrators)."""
    return await service.operations(db, current, params, kind=kind, status=operation_status, date_from=date_from, date_to=date_to)


@router.get("/operations/{operation_id}", response_model=OperationOut)
async def operation(operation_id: uuid.UUID, current: Viewer, db: TenantDb) -> OperationOut:
    service._require_admin_view(current)
    return await service.operation_detail(db, current, operation_id)


@router.post("/account-grants", response_model=OperationOut, status_code=status.HTTP_201_CREATED)
async def account_grant(body: AccountGrantIn, current: Manager, db: TenantDb, meta: Meta, response: Response) -> OperationOut:
    """Add the amount to one active player. Replaying the key returns the original (200)."""
    return _created(response, await service.account_grant(db, current, body, meta))


@router.post("/grants", response_model=OperationOut, status_code=status.HTTP_201_CREATED)
async def uniform_grant(body: UniformGrantIn, current: Manager, db: TenantDb, meta: Meta, response: Response) -> OperationOut:
    """Credit the same amount to every active player. Replaying the key returns the original (200)."""
    return _created(response, await service.uniform_grant(db, current, body, meta))


@router.post("/resets", response_model=OperationOut, status_code=status.HTTP_201_CREATED)
async def reset(body: ResetIn, current: Manager, db: TenantDb, meta: Meta, response: Response) -> OperationOut:
    """Set every active player to the starting balance (reset policy required, no open stakes)."""
    return _created(response, await service.reset(db, current, body, meta))


@router.post("/corrections", response_model=OperationOut, status_code=status.HTTP_201_CREATED)
async def propose_correction(body: CorrectionIn, current: Manager, db: TenantDb, meta: Meta, response: Response) -> OperationOut:
    """Propose a correction of an administrator posting; it posts only after another admin approves."""
    return _created(response, await service.propose_correction(db, current, body, meta))


@router.post("/corrections/{operation_id}/approve", response_model=OperationOut)
async def approve_correction(
    operation_id: uuid.UUID, current: Manager, db: TenantDb, meta: Meta, body: ReviewIn | None = None
) -> OperationOut:
    return await service.approve(db, current, operation_id, body or ReviewIn(), meta)


@router.post("/corrections/{operation_id}/reject", response_model=OperationOut)
async def reject_correction(operation_id: uuid.UUID, body: RejectIn, current: Manager, db: TenantDb, meta: Meta) -> OperationOut:
    return await service.reject(db, current, operation_id, body, meta)
