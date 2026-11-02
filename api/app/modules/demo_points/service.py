"""Points ledger operations. The only code that changes a wallet balance.

Concurrency model (READ COMMITTED):

* Both accounts are locked with ``SELECT ... FOR UPDATE`` in ascending id order,
  so two transfers touching the same pair can never deadlock.
* Balances are re-read under the lock (``populate_existing``) and changed with
  ``UPDATE ... SET balance = balance + :delta RETURNING balance``; the CHECK
  constraint ``balance >= 0`` is the final guard against overdraft.
* ``UNIQUE (tenant_id, idempotency_key)`` makes retries safe: a concurrent
  duplicate blocks on the unique index until the first commits, then fails and
  replays the stored transaction.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import String, and_, cast, exists, func, not_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.deps import CurrentUser
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.permissions import P
from app.core.security import utcnow
from app.modules.agents.service import MAX_DEPTH, downline_ids_select
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.auth.models import AccountStatus
from app.modules.demo_points.models import (
    AccountKind,
    PointAccount,
    PointLedgerEntry,
    PointTransaction,
    TransactionKind,
    TransactionType,
)
from app.modules.demo_points.schemas import (
    MAX_AMOUNT,
    MAX_BALANCE,
    AccountMismatch,
    LedgerEntryOut,
    LedgerIntegrity,
    Party,
    PointsSummary,
    TransactionOut,
)
from app.modules.notifications.service import notify
from app.modules.realtime.publisher import publish_after_commit, publish_to_users_after_commit, users_with_permission
from app.modules.users.models import AccountType, TenantUser
from app.shared.schemas import PageParams, paginate


@dataclass(frozen=True)
class Outcome:
    transaction: PointTransaction
    replayed: bool


def request_hash(**fields: object) -> str:
    canonical = json.dumps({k: str(v) if v is not None else None for k, v in sorted(fields.items())}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _existing(db: AsyncSession, tenant_id: uuid.UUID, key: str) -> PointTransaction | None:
    return (
        await db.execute(
            select(PointTransaction).where(PointTransaction.tenant_id == tenant_id, PointTransaction.idempotency_key == key)
        )
    ).scalar_one_or_none()


def _replay_or_conflict(existing: PointTransaction, req_hash: str) -> Outcome:
    if existing.request_hash != req_hash:
        raise Conflict("This Idempotency-Key was already used for a different request", code="idempotency_conflict")
    return Outcome(existing, replayed=True)


async def issuance_account(db: AsyncSession, tenant_id: uuid.UUID) -> PointAccount:
    acct = (
        await db.execute(
            select(PointAccount).where(PointAccount.tenant_id == tenant_id, PointAccount.kind == AccountKind.ISSUANCE)
        )
    ).scalar_one_or_none()
    if acct is None:
        raise NotFound("Issuance account missing", code="ledger_not_initialised")
    return acct


async def user_account(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> PointAccount:
    acct = (
        await db.execute(select(PointAccount).where(PointAccount.tenant_id == tenant_id, PointAccount.user_id == user_id))
    ).scalar_one_or_none()
    if acct is None:
        raise NotFound("Account not found")
    return acct


async def _active_user(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> TenantUser:
    user = (
        await db.execute(
            select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.id == user_id, TenantUser.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if user is None:
        raise NotFound("Account not found")
    if user.status != AccountStatus.ACTIVE:
        raise Unprocessable("The recipient account is suspended", code="account_suspended")
    return user


async def _post(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: TransactionKind,
    source_id: uuid.UUID,
    dest_id: uuid.UUID,
    amount: int,
    memo: str | None,
    created_by: uuid.UUID,
    idempotency_key: str,
    req_hash: str,
    reverses_transaction_id: uuid.UUID | None = None,
) -> Outcome:
    if type(amount) is not int or not 0 < amount <= MAX_AMOUNT:
        raise Unprocessable("Amount must be a positive whole number of points", code="invalid_points_amount")
    if source_id == dest_id:
        raise Unprocessable("Source and destination must differ", code="self_transfer")
    existing = await _existing(db, tenant_id, idempotency_key)
    if existing is not None:
        return _replay_or_conflict(existing, req_hash)

    # Lock both accounts in a global order to avoid deadlocks.
    locked = {
        a.id: a
        for a in (
            await db.execute(
                select(PointAccount)
                .where(PointAccount.tenant_id == tenant_id, PointAccount.id.in_([source_id, dest_id]))
                .order_by(PointAccount.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    source, dest = locked.get(source_id), locked.get(dest_id)
    if source is None or dest is None:
        raise NotFound("Account not found")
    # A duplicate may have waited for the first posting's account locks. Replay
    # before checking funds, even when that posting spent the sender's last point.
    existing = await _existing(db, tenant_id, idempotency_key)
    if existing is not None:
        return _replay_or_conflict(existing, req_hash)
    if (
        reverses_transaction_id
        and (
            await db.execute(
                select(PointTransaction.id).where(
                    PointTransaction.tenant_id == tenant_id, PointTransaction.reverses_transaction_id == reverses_transaction_id
                )
            )
        ).first()
    ):
        raise Conflict("This transfer has already been refunded", code="already_refunded")
    if source.kind == AccountKind.USER and source.balance < amount:
        raise Unprocessable(
            "Insufficient points",
            code="insufficient_points",
            details={"balance": int(source.balance), "requested": amount},
        )
    if dest.balance + amount > MAX_BALANCE or (source.kind == AccountKind.ISSUANCE and amount - source.balance > MAX_BALANCE):
        raise Unprocessable("The points balance limit would be exceeded", code="points_balance_limit")

    tx = PointTransaction(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        kind=kind,
        idempotency_key=idempotency_key,
        request_hash=req_hash,
        from_account_id=source.id,
        to_account_id=dest.id,
        amount=amount,
        memo=memo,
        created_by_id=created_by,
        reverses_transaction_id=reverses_transaction_id,
    )
    try:
        async with db.begin_nested():
            db.add(tx)
            await db.flush()
    except IntegrityError:
        # A concurrent request with the same key committed first.
        existing = await _existing(db, tenant_id, idempotency_key)
        if existing is None:
            raise
        return _replay_or_conflict(existing, req_hash)

    for acct, delta in ((source, -amount), (dest, amount)):
        new_balance = (
            await db.execute(
                update(PointAccount)
                .where(PointAccount.id == acct.id, PointAccount.tenant_id == tenant_id)
                .values(balance=PointAccount.balance + delta, updated_at=func.now())
                .returning(PointAccount.balance)
            )
        ).scalar_one()
        db.add(
            PointLedgerEntry(
                tenant_id=tenant_id, transaction_id=tx.id, account_id=acct.id, delta=delta, balance_after=new_balance
            )
        )
        acct.balance = new_balance
    await db.flush()
    return Outcome(tx, replayed=False)


async def _after_credit(db: AsyncSession, tenant_id: uuid.UUID, recipient: TenantUser, outcome: Outcome, label: str) -> None:
    if outcome.replayed:
        return
    tx = outcome.transaction
    await notify(
        db,
        tenant_id=tenant_id,
        user_id=recipient.id,
        type="points.received",
        title=f"You received {tx.amount:,} points",
        body=f"{label}. Points have no monetary value.",
        data={"transaction_id": str(tx.id), "amount": tx.amount},
    )
    balance = (await user_account(db, tenant_id, recipient.id)).balance
    publish_after_commit(
        db,
        tenant_id,
        "points.received",
        {"transaction_id": tx.id, "amount": tx.amount, "balance": balance},
        user_id=recipient.id,
    )


async def _announce(db: AsyncSession, tenant_id: uuid.UUID, outcome: Outcome) -> None:
    """``points.updated`` to everyone whose dashboard shows a changed balance: both parties, their
    direct parents (whose downline lists show them) and staff who see every account. Personal
    channels only; the frame carries no amounts, each dashboard refetches what it may see."""
    if outcome.replayed:
        return
    tx = outcome.transaction
    parties = (
        await db.execute(
            select(TenantUser.id, TenantUser.parent_id)
            .join(PointAccount, (PointAccount.user_id == TenantUser.id) & (PointAccount.tenant_id == tenant_id))
            .where(TenantUser.tenant_id == tenant_id, PointAccount.id.in_([tx.from_account_id, tx.to_account_id]))
        )
    ).all()
    audience = {uid for row in parties for uid in row if uid is not None}
    # Admin-to-player transfers also affect every ancestor's downline totals.
    parents = {parent for _, parent in parties if parent is not None}
    for _ in range(MAX_DEPTH):
        if not parents:
            break
        ancestors = (
            (
                await db.execute(
                    select(TenantUser.parent_id).where(
                        TenantUser.tenant_id == tenant_id,
                        TenantUser.id.in_(parents),
                        TenantUser.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        parents = {uid for uid in ancestors if uid is not None and uid not in audience}
        audience |= parents
    audience |= await users_with_permission(db, tenant_id, P.POINTS_VIEW_ALL)
    publish_to_users_after_commit(
        db, tenant_id, "points.updated", {"transaction_id": str(tx.id), "kind": tx.kind.value}, audience
    )


async def allocate(
    db: AsyncSession,
    actor: CurrentUser,
    *,
    user_id: uuid.UUID,
    amount: int,
    memo: str | None,
    idempotency_key: str,
    meta: RequestMeta,
) -> Outcome:
    if actor.user.account_type != AccountType.ADMIN or not actor.has(P.POINTS_ALLOCATE):
        raise Forbidden("Only tenant Admins can issue points", code="permission_denied")
    tenant_id = actor.tenant.id
    req_hash = request_hash(op="allocation", actor=actor.id, user=user_id, amount=amount, memo=memo)
    existing = await _existing(db, tenant_id, idempotency_key)
    if existing is not None:
        return _replay_or_conflict(existing, req_hash)
    if not memo or len(memo.strip()) < 3:
        raise Unprocessable("Issuing points requires a reason of at least 3 characters", code="reason_required")
    recipient = await _active_user(db, tenant_id, user_id)
    source = await issuance_account(db, tenant_id)
    dest = await user_account(db, tenant_id, recipient.id)
    outcome = await _post(
        db,
        tenant_id=tenant_id,
        kind=TransactionKind.ALLOCATION,
        source_id=source.id,
        dest_id=dest.id,
        amount=amount,
        memo=memo,
        created_by=actor.id,
        idempotency_key=idempotency_key,
        req_hash=req_hash,
    )
    if not outcome.replayed:
        await audit.record(
            db,
            actor=actor.actor,
            action="points.allocated",
            resource_type="point_transaction",
            resource_id=outcome.transaction.id,
            tenant_id=tenant_id,
            changes={
                "to": recipient.username,
                "recipient_id": recipient.id,
                "amount": amount,
                "memo": memo,
                "reason": memo,
                "administrator_id": actor.id,
                "sender_id": source.user_id,
                "source_account_id": source.id,
                "destination_account_id": dest.id,
            },
            meta=meta,
        )
    await _after_credit(db, tenant_id, recipient, outcome, f"Issued by {actor.user.username}")
    await _announce(db, tenant_id, outcome)
    return outcome


async def transfer(
    db: AsyncSession,
    actor: CurrentUser,
    *,
    to_user_id: uuid.UUID,
    from_user_id: uuid.UUID | None = None,
    amount: int,
    memo: str | None,
    idempotency_key: str,
    meta: RequestMeta,
) -> Outcome:
    if actor.user.account_type != AccountType.ADMIN or not actor.has(P.POINTS_TRANSFER):
        raise Forbidden("Only authorized Admins can transfer points", code="permission_denied")
    tenant_id = actor.tenant.id
    sender_id = from_user_id or actor.id
    if sender_id != actor.id and not actor.has(P.POINTS_VIEW_ALL):
        raise Forbidden("Managing another sender requires points.view_all", code="permission_denied")
    # Preserve hashes for existing own-wallet requests and their retries.
    fields = dict(op="transfer", actor=actor.id, user=to_user_id, amount=amount, memo=memo)
    if sender_id != actor.id:
        fields["sender"] = sender_id
    req_hash = request_hash(**fields)
    existing = await _existing(db, tenant_id, idempotency_key)
    if existing is not None:
        return _replay_or_conflict(existing, req_hash)
    if to_user_id == sender_id:
        raise Unprocessable("Source and destination must differ", code="self_transfer")
    sender = await _active_user(db, tenant_id, sender_id)
    recipient = await _active_user(db, tenant_id, to_user_id)
    source = await user_account(db, tenant_id, sender.id)
    dest = await user_account(db, tenant_id, recipient.id)
    outcome = await _post(
        db,
        tenant_id=tenant_id,
        kind=TransactionKind.TRANSFER,
        source_id=source.id,
        dest_id=dest.id,
        amount=amount,
        memo=memo,
        created_by=actor.id,
        idempotency_key=idempotency_key,
        req_hash=req_hash,
    )
    if not outcome.replayed:
        await audit.record(
            db,
            actor=actor.actor,
            action="points.transferred",
            resource_type="point_transaction",
            resource_id=outcome.transaction.id,
            tenant_id=tenant_id,
            changes={
                "to": recipient.username,
                "recipient_id": recipient.id,
                "sender_id": sender.id,
                "administrator_id": actor.id,
                "amount": amount,
                "memo": memo,
                "reason": memo or "Assigned-account credit transfer",
                "source_account_id": source.id,
                "destination_account_id": dest.id,
            },
            meta=meta,
        )
    await _after_credit(db, tenant_id, recipient, outcome, f"Transferred by {actor.user.username}")
    await _announce(db, tenant_id, outcome)
    return outcome


async def adjust(
    db: AsyncSession,
    actor: CurrentUser,
    *,
    user_id: uuid.UUID,
    direction: str,
    amount: int,
    reason: str,
    idempotency_key: str,
    meta: RequestMeta,
) -> Outcome:
    if actor.user.account_type != AccountType.ADMIN or not actor.has(P.POINTS_ADJUST):
        raise Forbidden("Only tenant Admins can adjust points", code="permission_denied")
    tenant_id = actor.tenant.id
    req_hash = request_hash(op="adjustment", actor=actor.id, user=user_id, amount=amount, memo=reason, direction=direction)
    existing = await _existing(db, tenant_id, idempotency_key)
    if existing is not None:
        return _replay_or_conflict(existing, req_hash)
    target = (
        await db.execute(
            select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.id == user_id, TenantUser.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if target is None:
        raise NotFound("Account not found")
    issuance = await issuance_account(db, tenant_id)
    acct = await user_account(db, tenant_id, target.id)
    source, dest = (issuance, acct) if direction == "credit" else (acct, issuance)
    outcome = await _post(
        db,
        tenant_id=tenant_id,
        kind=TransactionKind.ADJUSTMENT,
        source_id=source.id,
        dest_id=dest.id,
        amount=amount,
        memo=reason,
        created_by=actor.id,
        idempotency_key=idempotency_key,
        req_hash=req_hash,
    )
    if not outcome.replayed:
        await audit.record(
            db,
            actor=actor.actor,
            action=f"points.adjusted_{direction}",
            resource_type="point_transaction",
            resource_id=outcome.transaction.id,
            tenant_id=tenant_id,
            changes={
                "user": target.username,
                "recipient_id": target.id,
                "administrator_id": actor.id,
                "amount": amount,
                "reason": reason,
                "source_account_id": source.id,
                "destination_account_id": dest.id,
            },
            meta=meta,
        )
        if direction == "credit":
            await _after_credit(db, tenant_id, target, outcome, "Administrative adjustment")
    await _announce(db, tenant_id, outcome)
    return outcome


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


async def refund(
    db: AsyncSession, actor: CurrentUser, *, transaction_id: uuid.UUID, reason: str, idempotency_key: str, meta: RequestMeta
) -> Outcome:
    if actor.user.account_type != AccountType.ADMIN or not actor.has(P.POINTS_ADJUST) or not actor.has(P.POINTS_TRANSFER):
        raise Forbidden("Only authorized Admins can refund transfers", code="permission_denied")
    req_hash = request_hash(op="refund", actor=actor.id, transaction=transaction_id, reason=reason)
    existing = await _existing(db, actor.tenant.id, idempotency_key)
    if existing is not None:
        return _replay_or_conflict(existing, req_hash)
    original = (
        await db.execute(
            select(PointTransaction).where(PointTransaction.tenant_id == actor.tenant.id, PointTransaction.id == transaction_id)
        )
    ).scalar_one_or_none()
    if original is None:
        raise NotFound("Transaction not found")
    if original.kind != TransactionKind.TRANSFER:
        raise Unprocessable("Only complete transfers can be refunded; correct issuance with an adjustment", code="not_refundable")
    outcome = await _post(
        db,
        tenant_id=actor.tenant.id,
        kind=TransactionKind.REFUND,
        source_id=original.to_account_id,
        dest_id=original.from_account_id,
        amount=original.amount,
        memo=reason,
        created_by=actor.id,
        idempotency_key=idempotency_key,
        req_hash=req_hash,
        reverses_transaction_id=original.id,
    )
    if not outcome.replayed:
        await audit.record(
            db,
            actor=actor.actor,
            tenant_id=actor.tenant.id,
            action="points.refunded",
            resource_type="point_transaction",
            resource_id=outcome.transaction.id,
            changes={
                "original_transaction_id": original.id,
                "amount": original.amount,
                "reason": reason,
                "administrator_id": actor.id,
                "source_account_id": original.to_account_id,
                "destination_account_id": original.from_account_id,
            },
            meta=meta,
        )
        dest = await user_account(db, actor.tenant.id, (await transaction_detail(db, actor, original.id)).source.user_id)
        recipient = (
            await db.execute(select(TenantUser).where(TenantUser.tenant_id == actor.tenant.id, TenantUser.id == dest.user_id))
        ).scalar_one()
        await _after_credit(db, actor.tenant.id, recipient, outcome, "Transfer refund")
    await _announce(db, actor.tenant.id, outcome)
    return outcome


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


async def summary(db: AsyncSession, actor: CurrentUser) -> PointsSummary:
    tenant_id = actor.tenant.id
    since = utcnow() - timedelta(days=30)
    own = await user_account(db, tenant_id, actor.id)
    tx = PointTransaction
    movement = tx.kind.in_([TransactionKind.TRANSFER, TransactionKind.REFUND])
    sums = (
        await db.execute(
            select(
                func.coalesce(func.sum(tx.amount).filter(tx.kind == TransactionKind.ALLOCATION), 0),
                func.coalesce(func.sum(tx.amount).filter(movement & (tx.from_account_id == own.id)), 0),
                func.coalesce(func.sum(tx.amount).filter(movement & (tx.to_account_id == own.id)), 0),
                func.coalesce(
                    func.sum(tx.amount).filter(
                        ((tx.kind == TransactionKind.TRANSFER) & (tx.from_account_id == own.id))
                        | (
                            (tx.kind == TransactionKind.ALLOCATION)
                            & (tx.created_by_id == actor.id)
                            & (tx.to_account_id != own.id)
                        )
                    ),
                    0,
                ),
                func.count(),
            ).where(
                tx.tenant_id == tenant_id,
                *([] if actor.has(P.POINTS_VIEW_ALL) else [or_(tx.from_account_id == own.id, tx.to_account_id == own.id)]),
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
        issuance = await issuance_account(db, tenant_id)
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
            total_issued=-int(issuance.balance),
            circulating=int(circulating),
            own_balance=int((await user_account(db, tenant_id, actor.id)).balance),
            accounts=int(accounts),
            transactions_30d={k.value: int(v) for k, v in by_kind.items()},
        )
    own = await user_account(db, tenant_id, actor.id)
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
                    or_(sent.from_account_id == own.id, sent.to_account_id == own.id),
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
        own_balance=int(own.balance),
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
