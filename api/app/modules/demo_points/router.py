import uuid
from datetime import date
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Response

from app.core.deps import (
    CurrentUser,
    TenantDb,
    require_any_permission,
    require_module,
    require_permissions,
)
from app.core.errors import NotFound
from app.core.permissions import P
from app.modules.demo_points import service
from app.modules.demo_points.models import TransactionKind, TransactionType
from app.modules.demo_points.schemas import (
    AccountOut,
    LedgerEntryOut,
    LedgerIntegrity,
    PointsSummary,
    TransactionOut,
)
from app.modules.users import service as users_service
from app.shared.schemas import Page, PageDep

# Read-only archive of the retired transferable points module. There are no write endpoints:
# allocations, transfers, adjustments and refunds were removed, and migration 0013 rejects any new
# points row in the database. Arena Points (the betting module) is the only gameplay balance.
router = APIRouter(
    prefix="/admin/points", tags=["admin: legacy points (read-only)"], dependencies=[Depends(require_module("points"))]
)

Viewer = Annotated[CurrentUser, Depends(require_any_permission(P.POINTS_VIEW_ALL, P.POINTS_VIEW_DOWNLINE))]


class HistoryQuery:
    def __init__(
        self,
        kind: TransactionKind | None = None,
        type: TransactionType | None = None,
        status: Literal["completed", "reversed", "failed", "pending"] | None = None,
        sender_id: uuid.UUID | None = None,
        recipient_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ):
        self.filters = service.HistoryFilters(
            kind=kind,
            type=type,
            status=status,
            sender_id=sender_id,
            recipient_id=recipient_id,
            date_from=date_from,
            date_to=date_to,
        )


HistoryDep = Annotated[HistoryQuery, Depends()]


@router.get("/transactions", response_model=Page[TransactionOut])
async def list_transactions(
    current: Viewer, db: TenantDb, params: PageDep, query: HistoryDep, user_id: uuid.UUID | None = None
) -> Page[TransactionOut]:
    """Committed ledger transactions, with inclusive Asia/Manila calendar-day filters.

    ``type`` separates issue (into the issuing Admin's wallet) from credit (directly into another
    account). ``reversed`` transfers have a posted refund. Failed or pending requests never enter
    the ledger, so those status filters return no rows. UUID is the permanent transaction reference.
    Search accepts users, wallet IDs, references and remarks.
    """
    if user_id and not current.has(P.POINTS_VIEW_ALL) and user_id != current.id:
        await users_service.ensure_in_scope(db, current, user_id)
    items, total = await service.list_transactions(db, current, params, user_id=user_id, filters=query.filters)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.get("/transactions/export")
async def export_transactions(
    current: Viewer, db: TenantDb, params: PageDep, query: HistoryDep, user_id: uuid.UUID | None = None
) -> Response:
    """CSV of the same authorized, filtered history, up to 10,000 rows; narrow filters for larger exports."""
    from app.core.errors import Unprocessable
    from app.modules.reports.router import _ensure_csv_allowed
    from app.modules.reports.service import to_csv

    await _ensure_csv_allowed(db, current.tenant.id)
    if user_id and not current.has(P.POINTS_VIEW_ALL) and user_id != current.id:
        await users_service.ensure_in_scope(db, current, user_id)
    stmt = service.transaction_query(current, params, user_id=user_id, filters=query.filters)
    txs = list((await db.execute(stmt.limit(10_001))).scalars())
    if len(txs) > 10_000:
        raise Unprocessable("Export exceeds 10,000 records. Narrow the date range or filters.", code="export_too_large")
    rows = []
    for tx in await service.to_out(db, current.tenant.id, txs):
        row = tx.model_dump(mode="json", exclude={"source", "destination"})
        row["direction"] = row["direction"] or ""
        row["reverses_transaction_id"] = row["reverses_transaction_id"] or ""
        row["refund_transaction_id"] = row["refund_transaction_id"] or ""
        row["created_at"] = tx.created_at.astimezone(ZoneInfo("Asia/Manila")).isoformat()
        for name, party in (("sender", tx.source), ("recipient", tx.destination)):
            row[f"{name}_account_id"] = str(party.account_id)
            row[f"{name}_user_id"] = str(party.user_id) if party.user_id else ""
            row[f"{name}_username"] = party.username or "Issuance"
        rows.append(row)
    return Response(
        content=to_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="points-history.csv"'},
    )


@router.get("/transactions/{transaction_id}", response_model=TransactionOut)
async def transaction_details(transaction_id: uuid.UUID, current: Viewer, db: TenantDb) -> TransactionOut:
    return await service.transaction_detail(db, current, transaction_id)


async def _scoped_account(db: TenantDb, current: CurrentUser, user_id: uuid.UUID):  # type: ignore[no-untyped-def]
    if user_id != current.id and not current.has(P.POINTS_VIEW_ALL):
        await users_service.ensure_in_scope(db, current, user_id)
    user = await users_service.get_user(db, current.tenant.id, user_id)
    acct = await service.user_account(db, current.tenant.id, user_id)
    return user, acct


@router.get("/accounts", response_model=Page[AccountOut])
async def list_accounts(current: Viewer, db: TenantDb, params: PageDep) -> Page[AccountOut]:
    items, total = await service.list_accounts(db, current, params)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.get("/accounts/{user_id}", response_model=AccountOut)
async def get_account(user_id: uuid.UUID, current: Viewer, db: TenantDb) -> AccountOut:
    user, acct = await _scoped_account(db, current, user_id)
    return AccountOut(user_id=user.id, username=user.username, full_name=user.full_name, balance=acct.balance, account_id=acct.id)


@router.get("/accounts/{user_id}/entries", response_model=Page[LedgerEntryOut])
async def account_entries(user_id: uuid.UUID, current: Viewer, db: TenantDb, params: PageDep) -> Page[LedgerEntryOut]:
    _, acct = await _scoped_account(db, current, user_id)
    items, total = await service.statement(db, current.tenant.id, acct, params)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.get("/summary", response_model=PointsSummary)
async def summary(current: Viewer, db: TenantDb) -> PointsSummary:
    return await service.summary(db, current)


@router.get("/integrity", response_model=LedgerIntegrity)
async def integrity(
    current: Annotated[CurrentUser, Depends(require_permissions(P.POINTS_VIEW_ALL))], db: TenantDb
) -> LedgerIntegrity:
    """Recompute every tenant balance from the append-only ledger and report any difference. Read-only."""
    return await service.integrity(db, current.tenant.id)


@router.get("/me", response_model=AccountOut)
async def my_account(
    current: Annotated[CurrentUser, Depends(require_any_permission(P.POINTS_VIEW_DOWNLINE, P.POINTS_VIEW_ALL))],
    db: TenantDb,
) -> AccountOut:
    try:
        acct = await service.user_account(db, current.tenant.id, current.id)
    except NotFound:
        raise NotFound("You do not have a points account") from None
    return AccountOut(
        user_id=current.id,
        username=current.user.username,
        full_name=current.user.full_name,
        balance=acct.balance,
        account_id=acct.id,
    )
