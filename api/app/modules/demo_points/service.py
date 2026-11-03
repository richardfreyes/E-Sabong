"""Read-only access to the retired points ledger (history, statements, summaries, integrity).

The transferable points module is retired: allocations, transfers, adjustments and refunds were
removed and migration 0013 rejects new points rows. Nothing here changes a balance. The only
gameplay balance is Arena Points (``app.modules.betting``).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import String, and_, cast, exists, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.deps import CurrentUser
from app.core.errors import NotFound, Unprocessable
from app.core.permissions import P
from app.core.security import utcnow
from app.modules.agents.service import downline_ids_select
from app.modules.demo_points.models import (
    AccountKind,
    PointAccount,
    PointLedgerEntry,
    PointTransaction,
    TransactionKind,
    TransactionType,
)
from app.modules.demo_points.schemas import (
    AccountMismatch,
    LedgerEntryOut,
    LedgerIntegrity,
    Party,
    PointsSummary,
    TransactionOut,
)
from app.modules.users.models import TenantUser
from app.shared.schemas import PageParams, paginate


async def user_account(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> PointAccount:
    acct = (
        await db.execute(select(PointAccount).where(PointAccount.tenant_id == tenant_id, PointAccount.user_id == user_id))
    ).scalar_one_or_none()
    if acct is None:
        raise NotFound("Account not found")
    return acct


# ---------------------------------------------------------------- reads


async def _parties(db: AsyncSession, tenant_id: uuid.UUID, account_ids: set[uuid.UUID]) -> dict[uuid.UUID, Party]:
    if not account_ids:
        return {}
    rows = await db.execute(
        select(PointAccount.id, PointAccount.kind, PointAccount.user_id, TenantUser.username, TenantUser.full_name)
        .outerjoin(TenantUser, TenantUser.id == PointAccount.user_id)
        .where(PointAccount.tenant_id == tenant_id, PointAccount.id.in_(account_ids))
    )
    return {
        aid: Party(account_id=aid, kind=kind, user_id=uid, username=uname, full_name=fname)
        for aid, kind, uid, uname, fname in rows.all()
    }


async def to_out(db: AsyncSession, tenant_id: uuid.UUID, txs: list[PointTransaction]) -> list[TransactionOut]:
    parties = await _parties(db, tenant_id, {t.from_account_id for t in txs} | {t.to_account_id for t in txs})
    tx_ids = {t.id for t in txs}
    entries = (
        {
            (e.transaction_id, e.account_id): e
            for e in (
                await db.execute(
                    select(PointLedgerEntry).where(
                        PointLedgerEntry.tenant_id == tenant_id, PointLedgerEntry.transaction_id.in_(tx_ids)
                    )
                )
            ).scalars()
        }
        if txs
        else {}
    )
    refunds = (
        dict(
            (
                await db.execute(
                    select(PointTransaction.reverses_transaction_id, PointTransaction.id).where(
                        PointTransaction.tenant_id == tenant_id, PointTransaction.reverses_transaction_id.in_(tx_ids)
                    )
                )
            ).all()
        )
        if txs
        else {}
    )
    creators = {}
    if txs:
        creators = dict(
            (
                await db.execute(
                    select(TenantUser.id, TenantUser.username).where(
                        TenantUser.tenant_id == tenant_id, TenantUser.id.in_({t.created_by_id for t in txs})
                    )
                )
            )
            .tuples()
            .all()
        )
    return [
        TransactionOut(
            id=t.id,
            kind=t.kind,
            type=_type(t, parties[t.to_account_id]),
            direction=(
                ("credit" if parties[t.from_account_id].kind == AccountKind.ISSUANCE else "debit")
                if t.kind == TransactionKind.ADJUSTMENT
                else None
            ),
            status="reversed" if t.id in refunds else "completed",
            amount=t.amount,
            memo=t.memo,
            source=parties[t.from_account_id],
            destination=parties[t.to_account_id],
            created_by_id=t.created_by_id,
            created_by_username=creators.get(t.created_by_id),
            created_at=t.created_at,
            tenant_id=t.tenant_id,
            reference=str(t.id),
            source_balance_before=entries[(t.id, t.from_account_id)].balance_after + t.amount,
            source_balance_after=entries[(t.id, t.from_account_id)].balance_after,
            destination_balance_before=entries[(t.id, t.to_account_id)].balance_after - t.amount,
            destination_balance_after=entries[(t.id, t.to_account_id)].balance_after,
            reverses_transaction_id=t.reverses_transaction_id,
            refund_transaction_id=refunds.get(t.id),
        )
        for t in txs
    ]


def _type(tx: PointTransaction, destination: Party) -> TransactionType:
    if tx.kind == TransactionKind.ALLOCATION:
        return TransactionType.ISSUE if destination.user_id == tx.created_by_id else TransactionType.CREDIT
    return TransactionType(tx.kind.value)


def _visible_account_ids(actor: CurrentUser):  # type: ignore[no-untyped-def]
    tenant_id = actor.tenant.id
    ids = select(TenantUser.id).where(
        TenantUser.tenant_id == tenant_id,
        or_(TenantUser.id == actor.id, TenantUser.id.in_(downline_ids_select(tenant_id, actor.id))),
    )
    return select(PointAccount.id).where(PointAccount.tenant_id == tenant_id, PointAccount.user_id.in_(ids))


@dataclass(frozen=True)
class HistoryFilters:
    kind: TransactionKind | None = None
    type: TransactionType | None = None
    status: str | None = None
    sender_id: uuid.UUID | None = None
    recipient_id: uuid.UUID | None = None
    date_from: date | None = None
    date_to: date | None = None


def transaction_query(
    actor: CurrentUser, params: PageParams, *, user_id: uuid.UUID | None = None, filters: HistoryFilters | None = None
):
    filters = filters or HistoryFilters()
    tenant_id = actor.tenant.id
    stmt = select(PointTransaction).where(PointTransaction.tenant_id == tenant_id)
    if not actor.has(P.POINTS_VIEW_ALL):
        visible = _visible_account_ids(actor)
        stmt = stmt.where(or_(PointTransaction.from_account_id.in_(visible), PointTransaction.to_account_id.in_(visible)))

    def account_ids(uid):
        return select(PointAccount.id).where(
            PointAccount.tenant_id == tenant_id, or_(PointAccount.user_id == uid, PointAccount.id == uid)
        )

    if user_id:
        stmt = stmt.where(
            or_(
                PointTransaction.from_account_id.in_(account_ids(user_id)),
                PointTransaction.to_account_id.in_(account_ids(user_id)),
            )
        )
    if filters.sender_id:
        stmt = stmt.where(PointTransaction.from_account_id.in_(account_ids(filters.sender_id)))
    if filters.recipient_id:
        stmt = stmt.where(PointTransaction.to_account_id.in_(account_ids(filters.recipient_id)))
    if filters.kind:
        stmt = stmt.where(PointTransaction.kind == filters.kind)
    if filters.type:
        into_own_wallet = exists().where(
            PointAccount.id == PointTransaction.to_account_id, PointAccount.user_id == PointTransaction.created_by_id
        )
        if filters.type == TransactionType.ISSUE:
            stmt = stmt.where(PointTransaction.kind == TransactionKind.ALLOCATION, into_own_wallet)
        elif filters.type == TransactionType.CREDIT:
            stmt = stmt.where(PointTransaction.kind == TransactionKind.ALLOCATION, not_(into_own_wallet))
        else:
            stmt = stmt.where(PointTransaction.kind == TransactionKind(filters.type.value))
    # Transactions exist only after an atomic commit; failed requests never become ledger entries.
    reversal = aliased(PointTransaction)
    reversed_ = exists().where(reversal.tenant_id == tenant_id, reversal.reverses_transaction_id == PointTransaction.id)
    if filters.status == "completed":
        stmt = stmt.where(not_(reversed_))
    elif filters.status == "reversed":
        stmt = stmt.where(reversed_)
    elif filters.status:
        stmt = stmt.where(False)
    if filters.date_from and filters.date_to and filters.date_to < filters.date_from:
        raise Unprocessable("End date must be on or after start date", code="invalid_date_range")
    zone = ZoneInfo("Asia/Manila")
    if filters.date_from:
        stmt = stmt.where(PointTransaction.created_at >= datetime.combine(filters.date_from, time.min, zone))
    if filters.date_to:
        stmt = stmt.where(PointTransaction.created_at < datetime.combine(filters.date_to + timedelta(days=1), time.min, zone))
    if params.q:
        like = f"%{params.q}%"
        users = select(TenantUser.id).where(
            TenantUser.tenant_id == tenant_id,
            or_(
                TenantUser.username.ilike(like),
                TenantUser.email.ilike(like),
                TenantUser.full_name.ilike(like),
                cast(TenantUser.id, String).ilike(like),
            ),
        )
        accounts = select(PointAccount.id).where(
            PointAccount.tenant_id == tenant_id, or_(PointAccount.user_id.in_(users), cast(PointAccount.id, String).ilike(like))
        )
        stmt = stmt.where(
            or_(
                PointTransaction.memo.ilike(like),
                cast(PointTransaction.id, String).ilike(like),
                PointTransaction.from_account_id.in_(accounts),
                PointTransaction.to_account_id.in_(accounts),
            )
        )
    return stmt.order_by(PointTransaction.created_at.desc(), PointTransaction.id)


async def list_transactions(
    db: AsyncSession,
    actor: CurrentUser,
    params: PageParams,
    *,
    user_id: uuid.UUID | None = None,
    kind: TransactionKind | None = None,
    filters: HistoryFilters | None = None,
) -> tuple[list[TransactionOut], int]:
    txs, total = await paginate(
        db, transaction_query(actor, params, user_id=user_id, filters=filters or HistoryFilters(kind=kind)), params
    )
    return await to_out(db, actor.tenant.id, txs), total


async def transaction_detail(db: AsyncSession, actor: CurrentUser, transaction_id: uuid.UUID) -> TransactionOut:
    tx = (
        await db.execute(transaction_query(actor, PageParams()).where(PointTransaction.id == transaction_id))
    ).scalar_one_or_none()
    if tx is None:
        raise NotFound("Transaction not found")
    return (await to_out(db, actor.tenant.id, [tx]))[0]


async def statement(
    db: AsyncSession, tenant_id: uuid.UUID, account: PointAccount, params: PageParams
) -> tuple[list[LedgerEntryOut], int]:
    stmt = (
        select(PointLedgerEntry)
        .where(PointLedgerEntry.tenant_id == tenant_id, PointLedgerEntry.account_id == account.id)
        .order_by(PointLedgerEntry.created_at.desc(), PointLedgerEntry.id)
    )
    entries, total = await paginate(db, stmt, params)
    tx_ids = {e.transaction_id for e in entries}
    txs = (
        {
            t.id: t
            for t in (
                await db.execute(
                    select(PointTransaction).where(PointTransaction.tenant_id == tenant_id, PointTransaction.id.in_(tx_ids))
                )
            ).scalars()
        }
        if tx_ids
        else {}
    )
    counter_ids = {(t.to_account_id if t.from_account_id == account.id else t.from_account_id) for t in txs.values()}
    parties = await _parties(db, tenant_id, counter_ids)
    out = []
    for e in entries:
        t = txs[e.transaction_id]
        other = t.to_account_id if t.from_account_id == account.id else t.from_account_id
        party = parties.get(other)
        if party and party.kind == AccountKind.ISSUANCE:
            party = Party(account_id=party.account_id, kind=party.kind, username=None, full_name="Platform issuance")
        out.append(
            LedgerEntryOut(
                id=e.id,
                transaction_id=t.id,
                kind=t.kind,
                entry_type="credit" if e.delta > 0 else "debit",
                delta=e.delta,
                balance_after=e.balance_after,
                balance_before=e.balance_after - e.delta,
                memo=t.memo,
                counterparty=party,
                created_at=e.created_at,
            )
        )
    return out, total


async def _account_or_none(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> PointAccount | None:
    return (
        await db.execute(select(PointAccount).where(PointAccount.tenant_id == tenant_id, PointAccount.user_id == user_id))
    ).scalar_one_or_none()


async def summary(db: AsyncSession, actor: CurrentUser) -> PointsSummary:
    tenant_id = actor.tenant.id
    since = utcnow() - timedelta(days=30)
    # Users created after the module was retired have no legacy account: their figures are absent, not zero.
    own = await _account_or_none(db, tenant_id, actor.id)
    own_id = own.id if own else uuid.UUID(int=0)  # matches no row: aggregates over "your wallet" stay empty
    tx = PointTransaction
    movement = tx.kind.in_([TransactionKind.TRANSFER, TransactionKind.REFUND])
    sums = (
        await db.execute(
            select(
                func.coalesce(func.sum(tx.amount).filter(tx.kind == TransactionKind.ALLOCATION), 0),
                func.coalesce(func.sum(tx.amount).filter(movement & (tx.from_account_id == own_id)), 0),
                func.coalesce(func.sum(tx.amount).filter(movement & (tx.to_account_id == own_id)), 0),
                func.coalesce(
                    func.sum(tx.amount).filter(
                        ((tx.kind == TransactionKind.TRANSFER) & (tx.from_account_id == own_id))
                        | (
                            (tx.kind == TransactionKind.ALLOCATION)
                            & (tx.created_by_id == actor.id)
                            & (tx.to_account_id != own_id)
                        )
                    ),
                    0,
                ),
                func.count(),
            ).where(
                tx.tenant_id == tenant_id,
                *([] if actor.has(P.POINTS_VIEW_ALL) else [or_(tx.from_account_id == own_id, tx.to_account_id == own_id)]),
            )
        )
    ).one()
    totals = dict(
        gross_issued=int(sums[0]),
        total_transferred=int(sums[1]),
        total_received=int(sums[2]),
        total_distributed=int(sums[3]),
        completed_transactions=int(sums[4]),
    )
    if actor.has(P.POINTS_VIEW_ALL):
        issuance = (
            await db.execute(
                select(PointAccount).where(PointAccount.tenant_id == tenant_id, PointAccount.kind == AccountKind.ISSUANCE)
            )
        ).scalar_one_or_none()
        circulating, accounts = (
            await db.execute(
                select(func.coalesce(func.sum(PointAccount.balance), 0), func.count()).where(
                    PointAccount.tenant_id == tenant_id, PointAccount.kind == AccountKind.USER
                )
            )
        ).one()
        by_kind = dict(
            (
                await db.execute(
                    select(PointTransaction.kind, func.count())
                    .where(PointTransaction.tenant_id == tenant_id, PointTransaction.created_at >= since)
                    .group_by(PointTransaction.kind)
                )
            )
            .tuples()
            .all()
        )
        return PointsSummary(
            **totals,
            scope="tenant",
            total_issued=-int(issuance.balance) if issuance else None,
            circulating=int(circulating),
            own_balance=int(own.balance) if own else None,
            accounts=int(accounts),
            transactions_30d={k.value: int(v) for k, v in by_kind.items()},
        )
    downline_accounts = (
        select(PointAccount)
        .where(PointAccount.tenant_id == tenant_id, PointAccount.user_id.in_(downline_ids_select(tenant_id, actor.id)))
        .subquery()
    )
    circulating, accounts = (
        await db.execute(
            select(func.coalesce(func.sum(downline_accounts.c.balance), 0), func.count()).select_from(downline_accounts)
        )
    ).one()
    sent = aliased(PointTransaction)
    by_kind = dict(
        (
            await db.execute(
                select(sent.kind, func.count())
                .where(
                    sent.tenant_id == tenant_id,
                    sent.created_at >= since,
                    or_(sent.from_account_id == own_id, sent.to_account_id == own_id),
                )
                .group_by(sent.kind)
            )
        )
        .tuples()
        .all()
    )
    return PointsSummary(
        **totals,
        scope="downline",
        circulating=int(circulating),
        own_balance=int(own.balance) if own else None,
        accounts=int(accounts),
        transactions_30d={k.value: int(v) for k, v in by_kind.items()},
    )


async def list_accounts(db: AsyncSession, actor: CurrentUser, params: PageParams) -> tuple[list, int]:
    from app.modules.demo_points.schemas import AccountOut

    stmt = (
        select(PointAccount)
        .join(TenantUser, TenantUser.id == PointAccount.user_id)
        .where(
            PointAccount.tenant_id == actor.tenant.id,
            TenantUser.tenant_id == actor.tenant.id,
            PointAccount.kind == AccountKind.USER,
        )
    )
    if not actor.has(P.POINTS_VIEW_ALL):
        stmt = stmt.where(PointAccount.id.in_(_visible_account_ids(actor)))
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(
            or_(
                TenantUser.username.ilike(like),
                TenantUser.email.ilike(like),
                TenantUser.full_name.ilike(like),
                cast(TenantUser.id, String).ilike(like),
                cast(PointAccount.id, String).ilike(like),
            )
        )
    accounts, total = await paginate(db, stmt.order_by(PointAccount.id), params)
    parties = await _parties(db, actor.tenant.id, {a.id for a in accounts})
    return [
        AccountOut(
            user_id=a.user_id,
            account_id=a.id,
            username=parties[a.id].username,
            full_name=parties[a.id].full_name,
            balance=int(a.balance),
        )
        for a in accounts
    ], total


async def integrity(db: AsyncSession, tenant_id: uuid.UUID) -> LedgerIntegrity:
    """Recompute every balance from the append-only ledger. Read-only; never corrects anything."""
    entries = (
        select(PointLedgerEntry.account_id, func.sum(PointLedgerEntry.delta).label("total"))
        .where(PointLedgerEntry.tenant_id == tenant_id)
        .group_by(PointLedgerEntry.account_id)
        .subquery()
    )
    ledger_balance = func.coalesce(entries.c.total, 0)
    accounts_checked, ledger_net, balance_net = (
        await db.execute(
            select(
                func.count(PointAccount.id),
                func.coalesce(func.sum(ledger_balance), 0),
                func.coalesce(func.sum(PointAccount.balance), 0),
            )
            .select_from(PointAccount)
            .outerjoin(entries, entries.c.account_id == PointAccount.id)
            .where(PointAccount.tenant_id == tenant_id)
        )
    ).one()
    mismatched = (
        await db.execute(
            select(
                PointAccount.id,
                PointAccount.kind,
                PointAccount.user_id,
                TenantUser.username,
                PointAccount.balance,
                ledger_balance,
            )
            .outerjoin(entries, entries.c.account_id == PointAccount.id)
            .outerjoin(TenantUser, and_(TenantUser.id == PointAccount.user_id, TenantUser.tenant_id == tenant_id))
            .where(PointAccount.tenant_id == tenant_id, PointAccount.balance != ledger_balance)
            .order_by(PointAccount.id)
            .limit(100)
        )
    ).all()
    per_tx = (
        select(
            PointTransaction.id,
            func.count(PointLedgerEntry.id).label("n"),
            func.coalesce(func.sum(PointLedgerEntry.delta), 0).label("net"),
            func.coalesce(func.max(PointLedgerEntry.delta), 0).label("credit"),
            PointTransaction.amount,
        )
        .outerjoin(
            PointLedgerEntry,
            and_(PointLedgerEntry.transaction_id == PointTransaction.id, PointLedgerEntry.tenant_id == tenant_id),
        )
        .where(PointTransaction.tenant_id == tenant_id)
        .group_by(PointTransaction.id)
        .subquery()
    )
    transactions_checked = (await db.execute(select(func.count()).select_from(per_tx))).scalar_one()
    unbalanced = list(
        (
            await db.execute(
                select(per_tx.c.id)
                .where(or_(per_tx.c.n != 2, per_tx.c.net != 0, per_tx.c.credit != per_tx.c.amount))
                .order_by(per_tx.c.id)
                .limit(100)
            )
        ).scalars()
    )
    return LedgerIntegrity(
        consistent=not mismatched and not unbalanced and int(ledger_net) == 0 and int(balance_net) == 0,
        accounts_checked=int(accounts_checked),
        transactions_checked=int(transactions_checked),
        ledger_net=int(ledger_net),
        balance_net=int(balance_net),
        mismatched_accounts=[
            AccountMismatch(account_id=aid, kind=kind, user_id=uid, username=uname, balance=int(bal), ledger_balance=int(lb))
            for aid, kind, uid, uname, bal, lb in mismatched
        ],
        unbalanced_transactions=unbalanced,
        checked_at=utcnow(),
    )
