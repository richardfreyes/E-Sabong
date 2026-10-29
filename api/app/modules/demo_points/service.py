"""Demonstration points ledger operations.

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
from datetime import timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.deps import CurrentUser
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.permissions import P
from app.core.security import utcnow
from app.modules.agents.service import downline_ids_select, is_in_downline
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.auth.models import AccountStatus
from app.modules.demo_points.models import (
    AccountKind,
    PointAccount,
    PointLedgerEntry,
    PointTransaction,
    TransactionKind,
)
from app.modules.demo_points.schemas import LedgerEntryOut, Party, PointsSummary, TransactionOut
from app.modules.notifications.service import notify
from app.modules.realtime.publisher import publish_after_commit
from app.modules.users.models import TenantUser
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
) -> Outcome:
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
    if source.kind == AccountKind.USER and source.balance < amount:
        raise Unprocessable(
            "Insufficient demonstration points",
            code="insufficient_points",
            details={"balance": int(source.balance), "requested": amount},
        )

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
        title=f"You received {tx.amount:,} demo points",
        body=f"{label}. Demonstration points have no monetary value.",
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
    tenant_id = actor.tenant.id
    req_hash = request_hash(op="allocation", actor=actor.id, user=user_id, amount=amount, memo=memo)
    existing = await _existing(db, tenant_id, idempotency_key)
    if existing is not None:
        return _replay_or_conflict(existing, req_hash)
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
            changes={"to": recipient.username, "amount": amount, "memo": memo},
            meta=meta,
        )
    await _after_credit(db, tenant_id, recipient, outcome, f"Issued by {actor.user.username}")
    return outcome


async def transfer(
    db: AsyncSession,
    actor: CurrentUser,
    *,
    to_user_id: uuid.UUID,
    amount: int,
    memo: str | None,
    idempotency_key: str,
    meta: RequestMeta,
) -> Outcome:
    tenant_id = actor.tenant.id
    req_hash = request_hash(op="transfer", actor=actor.id, user=to_user_id, amount=amount, memo=memo)
    existing = await _existing(db, tenant_id, idempotency_key)
    if existing is not None:
        return _replay_or_conflict(existing, req_hash)
    if to_user_id == actor.id:
        raise Unprocessable("You cannot transfer points to yourself", code="self_transfer")
    # Points only flow down the hierarchy; upward/lateral transfers are refused.
    if not await is_in_downline(db, tenant_id, actor.id, to_user_id):
        raise Forbidden("Points can only be transferred to accounts in your downline", code="transfer_not_allowed")
    recipient = await _active_user(db, tenant_id, to_user_id)
    source = await user_account(db, tenant_id, actor.id)
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
            changes={"to": recipient.username, "amount": amount, "memo": memo},
            meta=meta,
        )
    await _after_credit(db, tenant_id, recipient, outcome, f"Transferred by {actor.user.username}")
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
            changes={"user": target.username, "amount": amount, "reason": reason},
            meta=meta,
        )
        if direction == "credit":
            await _after_credit(db, tenant_id, target, outcome, "Administrative adjustment")
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
            amount=t.amount,
            memo=t.memo,
            source=parties[t.from_account_id],
            destination=parties[t.to_account_id],
            created_by_id=t.created_by_id,
            created_by_username=creators.get(t.created_by_id),
            created_at=t.created_at,
        )
        for t in txs
    ]


def _visible_account_ids(actor: CurrentUser):  # type: ignore[no-untyped-def]
    tenant_id = actor.tenant.id
    ids = select(TenantUser.id).where(
        TenantUser.tenant_id == tenant_id,
        or_(TenantUser.id == actor.id, TenantUser.id.in_(downline_ids_select(tenant_id, actor.id))),
    )
    return select(PointAccount.id).where(PointAccount.tenant_id == tenant_id, PointAccount.user_id.in_(ids))


async def list_transactions(
    db: AsyncSession,
    actor: CurrentUser,
    params: PageParams,
    *,
    user_id: uuid.UUID | None = None,
    kind: TransactionKind | None = None,
) -> tuple[list[TransactionOut], int]:
    tenant_id = actor.tenant.id
    stmt = select(PointTransaction).where(PointTransaction.tenant_id == tenant_id)
    if not actor.has(P.POINTS_VIEW_ALL):
        visible = _visible_account_ids(actor)
        stmt = stmt.where(or_(PointTransaction.from_account_id.in_(visible), PointTransaction.to_account_id.in_(visible)))
    if user_id:
        acct = await user_account(db, tenant_id, user_id)
        stmt = stmt.where(or_(PointTransaction.from_account_id == acct.id, PointTransaction.to_account_id == acct.id))
    if kind:
        stmt = stmt.where(PointTransaction.kind == kind)
    if params.q:
        stmt = stmt.where(PointTransaction.memo.ilike(f"%{params.q}%"))
    stmt = stmt.order_by(PointTransaction.created_at.desc(), PointTransaction.id)
    txs, total = await paginate(db, stmt, params)
    return await to_out(db, tenant_id, txs), total


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
                delta=e.delta,
                balance_after=e.balance_after,
                memo=t.memo,
                counterparty=party,
                created_at=e.created_at,
            )
        )
    return out, total


async def summary(db: AsyncSession, actor: CurrentUser) -> PointsSummary:
    tenant_id = actor.tenant.id
    since = utcnow() - timedelta(days=30)
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
            scope="tenant",
            total_issued=-int(issuance.balance),
            circulating=int(circulating),
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
        scope="downline",
        circulating=int(circulating),
        own_balance=int(own.balance),
        accounts=int(accounts),
        transactions_30d={k.value: int(v) for k, v in by_kind.items()},
    )
