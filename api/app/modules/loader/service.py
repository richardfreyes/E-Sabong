"""Loader allocations over the single play-balance wallet.

An administrator funds a Loader with the existing account grant. The Loader can then move
points from that wallet to a master agent, agent or player in the same tenant. The two
wallet rows are a transfer (net zero): a Loader never mints points. Every grant is one
database transaction, idempotent, and append-only.
"""

import hashlib
import json
import secrets
import uuid
from decimal import Decimal

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.deps import CurrentUser
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.permissions import P
from app.core.security import utcnow
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.auth.models import AccountStatus
from app.modules.balances.models import MAX_WALLET_BALANCE
from app.modules.betting.models import PlayWallet, WalletEntryKind
from app.modules.betting.service import _post, wallet_for
from app.modules.loader.models import LoaderGrant
from app.modules.loader.schemas import AccountOut, AllocationOut, GrantIn, GrantOut
from app.modules.tenants.models import Tenant, TenantStatus
from app.modules.users.models import AccountType, TenantUser
from app.shared.schemas import Page, PageParams

RECIPIENTS = (AccountType.MASTER_AGENT, AccountType.AGENT, AccountType.PLAYER)
ZERO = Decimal("0.00")


def _require(current: CurrentUser, permission: P) -> None:
    if (
        current.user.tenant_id != current.tenant.id
        or current.user.account_type != AccountType.LOADER
        or current.user.status != AccountStatus.ACTIVE
        or current.user.deleted_at is not None
        or not current.has(permission)
    ):
        raise Forbidden("Only a Loader can do that", code="loader_forbidden")


async def _gate(db: AsyncSession, current: CurrentUser, permission: P) -> None:
    status = (await db.execute(select(Tenant.status).where(Tenant.id == current.tenant.id))).scalar_one_or_none()
    if status != TenantStatus.ACTIVE:
        raise Forbidden("Workspace is unavailable", code="tenant_suspended")
    actor = (
        await db.execute(
            select(TenantUser)
            .where(TenantUser.tenant_id == current.tenant.id, TenantUser.id == current.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if actor is None:
        raise Forbidden("Only a Loader can do that", code="loader_forbidden")
    current.user = actor
    _require(current, permission)


async def _lock(db: AsyncSession, tenant_id: uuid.UUID, key: str) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"loader:{tenant_id}:{key}"}
    )


def _fingerprint(current: CurrentUser, data: GrantIn) -> str:
    body = data.model_dump(mode="json", exclude={"idempotency_key"})
    raw = json.dumps({"actor": str(current.id), "action": "loader_grant", "request": body}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _reference() -> str:
    return f"LDR-{utcnow():%Y%m%d}-{secrets.token_hex(4).upper()}"


async def allocation(db: AsyncSession, current: CurrentUser) -> AllocationOut:
    _require(current, P.LOADER_VIEW)
    wallet = await wallet_for(db, current.tenant.id, current.id)
    return AllocationOut(balance=wallet.balance, username=current.user.username)


async def accounts(
    db: AsyncSession,
    current: CurrentUser,
    params: PageParams,
    *,
    account_type: AccountType | None = None,
    status: AccountStatus | None = None,
) -> Page[AccountOut]:
    _require(current, P.LOADER_VIEW)
    if account_type is not None and account_type not in RECIPIENTS:
        raise Unprocessable("Choose a master agent, agent or player", code="invalid_account_type")
    parent = aliased(TenantUser)
    stmt = (
        select(TenantUser, parent.username, PlayWallet.balance)
        .outerjoin(parent, (parent.id == TenantUser.parent_id) & (parent.tenant_id == current.tenant.id))
        .outerjoin(PlayWallet, (PlayWallet.user_id == TenantUser.id) & (PlayWallet.tenant_id == current.tenant.id))
        .where(
            TenantUser.tenant_id == current.tenant.id,
            TenantUser.deleted_at.is_(None),
            TenantUser.account_type.in_(RECIPIENTS if account_type is None else (account_type,)),
        )
    )
    if status is not None:
        stmt = stmt.where(TenantUser.status == status)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(TenantUser.username.ilike(like), TenantUser.full_name.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    ordered = stmt.order_by(TenantUser.account_type, TenantUser.username, TenantUser.id)
    rows = (await db.execute(ordered.offset(params.offset).limit(params.page_size))).all()
    return Page(
        items=[
            AccountOut(
                id=user.id,
                username=user.username,
                full_name=user.full_name,
                account_type=user.account_type.value,
                status=user.status.value,
                parent_id=user.parent_id,
                parent_username=parent_username,
                balance=balance if balance is not None else ZERO,
            )
            for user, parent_username, balance in rows
        ],
        total=int(total),
        page=params.page,
        page_size=params.page_size,
    )


def _grant_out(row: LoaderGrant, username: str, account_type: str, *, replayed: bool = False) -> GrantOut:
    return GrantOut(
        id=row.id,
        reference=row.reference,
        loader_id=row.loader_id,
        recipient_id=row.recipient_id,
        recipient_username=username,
        recipient_type=account_type,
        amount=row.amount,
        reason=row.reason,
        loader_balance_after=row.loader_balance_after,
        recipient_balance_after=row.recipient_balance_after,
        created_at=row.created_at,
        replayed=replayed,
    )


async def _named(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> tuple[str, str]:
    row = (
        await db.execute(
            select(TenantUser.username, TenantUser.account_type).where(
                TenantUser.tenant_id == tenant_id, TenantUser.id == user_id
            )
        )
    ).one()
    return row[0], row[1].value


async def history(db: AsyncSession, current: CurrentUser, params: PageParams) -> Page[GrantOut]:
    _require(current, P.LOADER_HISTORY)
    base = select(LoaderGrant).where(LoaderGrant.tenant_id == current.tenant.id, LoaderGrant.loader_id == current.id)
    total = (await db.execute(select(func.count()).select_from(base.order_by(None).subquery()))).scalar_one()
    ordered = base.order_by(LoaderGrant.created_at.desc(), LoaderGrant.id.desc())
    rows = (await db.execute(ordered.offset(params.offset).limit(params.page_size))).scalars().all()
    items = []
    for row in rows:
        username, account_type = await _named(db, current.tenant.id, row.recipient_id)
        items.append(_grant_out(row, username, account_type))
    return Page(items=items, total=int(total), page=params.page, page_size=params.page_size)


async def grant(db: AsyncSession, current: CurrentUser, data: GrantIn, meta: RequestMeta) -> GrantOut:
    await _gate(db, current, P.LOADER_GRANT)
    tid = current.tenant.id
    fingerprint = _fingerprint(current, data)
    await _lock(db, tid, "key:" + data.idempotency_key)
    existing = (
        await db.execute(
            select(LoaderGrant).where(LoaderGrant.tenant_id == tid, LoaderGrant.idempotency_key == data.idempotency_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != fingerprint or existing.loader_id != current.id:
            raise Conflict("This idempotency key was already used for a different request", code="idempotency_conflict")
        username, account_type = await _named(db, tid, existing.recipient_id)
        return _grant_out(existing, username, account_type, replayed=True)

    await _lock(db, tid, f"wallet:{current.id}")
    target = (
        await db.execute(
            select(TenantUser)
            .where(TenantUser.tenant_id == tid, TenantUser.id == data.target_user_id, TenantUser.deleted_at.is_(None))
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if target is None or target.account_type not in RECIPIENTS:
        raise NotFound("Account not found")
    if target.status != AccountStatus.ACTIVE:
        raise Conflict("Only an active account can receive a balance", code="not_active")

    source = await wallet_for(db, tid, current.id)
    dest = await wallet_for(db, tid, target.id)
    for wallet in sorted((source, dest), key=lambda w: w.id.int):
        locked = await wallet_for(db, tid, wallet.user_id, lock=True)
        if locked.user_id == current.id:
            source = locked
        else:
            dest = locked
    if source.balance < data.amount:
        raise Conflict(
            "That amount is more than the allocation an administrator has assigned to you",
            code="allocation_exceeded",
            details={"available": str(source.balance)},
        )
    if dest.balance + data.amount > MAX_WALLET_BALANCE:
        raise Conflict("The grant would exceed the maximum balance", code="balance_limit")

    grant_id = uuid.uuid4()
    reason = data.reason[:160]
    posted_out = await _post(
        db, source, WalletEntryKind.TRANSFER, -data.amount, f"lgrant:{grant_id}:out",
        memo=f"Allocated to {target.username}: {reason}"[:200],
    )
    posted_in = await _post(
        db, dest, WalletEntryKind.TRANSFER, data.amount, f"lgrant:{grant_id}:in",
        memo=f"Allocation from {current.user.username}: {reason}"[:200],
    )
    if not posted_out or not posted_in:
        raise Conflict("This allocation was already posted", code="duplicate_grant")
    row = LoaderGrant(
        id=grant_id,
        tenant_id=tid,
        reference=_reference(),
        loader_id=current.id,
        recipient_id=target.id,
        amount=data.amount,
        reason=data.reason,
        idempotency_key=data.idempotency_key,
        request_hash=fingerprint,
        loader_balance_after=source.balance,
        recipient_balance_after=dest.balance,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    await audit.record(
        db,
        actor=current.actor,
        action="loader.balance_grant",
        resource_type="tenant_user",
        resource_id=target.id,
        tenant_id=tid,
        changes={
            "loader_id": str(current.id),
            "recipient_id": str(target.id),
            "recipient_username": target.username,
            "amount": str(data.amount),
            "reason": data.reason,
            "reference": row.reference,
        },
        meta=meta,
    )
    return _grant_out(row, target.username, target.account_type.value)
