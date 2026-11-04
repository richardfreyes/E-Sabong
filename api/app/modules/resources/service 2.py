"""Atomic operational accounting. Caller owns the transaction; never commits here.

Units are provisioned only by a tenant admin directly into an account; no account can move units
to another account (no allocation, transfer or loading down the agent hierarchy).
"""

import hashlib
import json
import uuid

from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.permissions import P
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.auth.models import AccountStatus
from app.modules.resources.models import (
    MAX_BALANCE,
    ResourceAction,
    ResourceKind,
    ResourceLedgerEntry,
    ResourceSettings,
    ResourceTransaction,
    ResourceWallet,
)
from app.modules.resources.schemas import BalanceOut, IssuanceRequest, RefundRequest, ResourceReceipt, UsageRequest
from app.modules.tenants.models import Tenant, TenantStatus
from app.modules.users.models import AccountType, TenantUser
from app.shared.schemas import PageParams, paginate


def authorize(current: CurrentUser, permission: P, *, admin: bool = False) -> None:
    if (
        current.user.tenant_id != current.tenant.id
        or not current.tenant.is_operational
        or current.user.status != AccountStatus.ACTIVE
        or current.user.deleted_at is not None
        or not current.has(permission)
        or (admin and current.user.account_type != AccountType.ADMIN)
    ):
        raise Forbidden("Operational resource action is not permitted", code="resource_forbidden")


async def settings(db: AsyncSession, tenant_id: uuid.UUID, *, lock: bool = False) -> ResourceSettings | None:
    stmt = select(ResourceSettings).where(ResourceSettings.tenant_id == tenant_id)
    if lock:
        stmt = stmt.with_for_update(read=True).execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def configure(db: AsyncSession, current: CurrentUser, enabled: bool, meta: RequestMeta) -> None:
    authorize(current, P.RESOURCES_CONFIGURE, admin=True)
    await db.execute(insert(ResourceSettings).values(tenant_id=current.tenant.id, enabled=False).on_conflict_do_nothing())
    row = (
        await db.execute(
            select(ResourceSettings)
            .where(ResourceSettings.tenant_id == current.tenant.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    before = row.enabled
    row.enabled = enabled
    await audit.record(
        db,
        actor=current.actor,
        tenant_id=current.tenant.id,
        action="resources.configured",
        resource_type="resource_settings",
        resource_id=current.tenant.id,
        changes={"enabled_before": before, "enabled": enabled},
        meta=meta,
    )
    await db.flush()


async def _write_gate(db: AsyncSession, current: CurrentUser) -> None:
    # Recheck database state rather than relying exclusively on cached host resolution.
    status = (await db.execute(select(Tenant.status).where(Tenant.id == current.tenant.id))).scalar_one_or_none()
    if status != TenantStatus.ACTIVE:
        raise Forbidden("Workspace is unavailable", code="tenant_suspended")
    config = await settings(db, current.tenant.id, lock=True)
    if config is None or not config.enabled:
        raise Forbidden("Operational resource ledger is disabled", code="resources_disabled")


async def _lock_key(db: AsyncSession, tenant_id: uuid.UUID, key: str) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"operational-resources:{tenant_id}:{key}"},
    )


async def _active_user(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> TenantUser:
    user = (
        await db.execute(
            select(TenantUser)
            .where(TenantUser.tenant_id == tenant_id, TenantUser.id == user_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if user is None or user.deleted_at is not None or user.status != AccountStatus.ACTIVE:
        raise NotFound("Active resource account not found")
    return user


def targets_query(current: CurrentUser):  # type: ignore[no-untyped-def]
    """Accounts an admin may provision directly. Only admins issue; nobody else has targets."""
    authorize(current, P.RESOURCES_ISSUE, admin=True)
    return select(TenantUser).where(
        TenantUser.tenant_id == current.tenant.id,
        TenantUser.status == AccountStatus.ACTIVE,
        TenantUser.deleted_at.is_(None),
    )


async def targets(db: AsyncSession, current: CurrentUser, params: PageParams):  # type: ignore[no-untyped-def]
    stmt = targets_query(current)
    if params.q:
        q = f"%{params.q}%"
        stmt = stmt.where(or_(TenantUser.username.ilike(q), TenantUser.full_name.ilike(q)))
    return await paginate(db, stmt.order_by(TenantUser.username, TenantUser.id), params)


async def balance(db: AsyncSession, current: CurrentUser, kind: ResourceKind) -> BalanceOut:
    amount = (
        await db.execute(
            select(ResourceWallet.balance).where(
                ResourceWallet.tenant_id == current.tenant.id,
                ResourceWallet.user_id == current.id,
                ResourceWallet.resource_kind == kind,
            )
        )
    ).scalar_one_or_none() or 0
    return BalanceOut(user_id=current.id, resource_kind=kind, balance=amount, available_balance=amount)


async def receipt(db: AsyncSession, tx: ResourceTransaction, *, replayed: bool = False) -> ResourceReceipt:
    rows = (
        await db.execute(
            select(ResourceLedgerEntry, ResourceWallet.user_id)
            .join(
                ResourceWallet,
                (ResourceWallet.id == ResourceLedgerEntry.wallet_id)
                & (ResourceWallet.tenant_id == ResourceLedgerEntry.tenant_id),
            )
            .where(ResourceLedgerEntry.tenant_id == tx.tenant_id, ResourceLedgerEntry.transaction_id == tx.id)
        )
    ).all()
    source = next(((e, uid) for e, uid in rows if e.delta < 0), None)
    target = next(((e, uid) for e, uid in rows if e.delta > 0), None)
    return ResourceReceipt(
        id=tx.id,
        kind=tx.kind,
        resource_kind=tx.resource_kind,
        amount=tx.amount,
        source_user_id=source[1] if source else None,
        target_user_id=target[1] if target else None,
        source_balance_after=source[0].balance_after if source else None,
        target_balance_after=target[0].balance_after if target else None,
        created_by_id=tx.created_by_id,
        created_at=tx.created_at,
        reason=tx.reason,
        refund_of_id=tx.refund_of_id,
        replayed=replayed,
    )


def history_query(current: CurrentUser):  # type: ignore[no-untyped-def]
    stmt = select(ResourceTransaction).where(ResourceTransaction.tenant_id == current.tenant.id)
    if not (current.user.account_type == AccountType.ADMIN and current.has(P.RESOURCES_AUDIT)):
        own = select(ResourceWallet.id).where(ResourceWallet.tenant_id == current.tenant.id, ResourceWallet.user_id == current.id)
        stmt = stmt.where(or_(ResourceTransaction.source_wallet_id.in_(own), ResourceTransaction.target_wallet_id.in_(own)))
    return stmt


async def detail(db: AsyncSession, current: CurrentUser, transaction_id: uuid.UUID) -> ResourceReceipt:
    tx = (await db.execute(history_query(current).where(ResourceTransaction.id == transaction_id))).scalar_one_or_none()
    if tx is None:
        raise NotFound("Resource transaction not found")
    return await receipt(db, tx)


async def post(
    db: AsyncSession,
    current: CurrentUser,
    action: ResourceAction,
    data: IssuanceRequest | UsageRequest | RefundRequest,
) -> ResourceReceipt:
    permission = {
        ResourceAction.ISSUE: P.RESOURCES_ISSUE,
        ResourceAction.CONSUME: P.RESOURCES_CONSUME,
        ResourceAction.REFUND: P.RESOURCES_ISSUE,
    }[action]
    authorize(current, permission, admin=action in {ResourceAction.ISSUE, ResourceAction.REFUND})
    await _write_gate(db, current)
    tenant_id = current.tenant.id
    await _lock_key(db, tenant_id, "request:" + data.idempotency_key)
    # Actor/action are part of the fingerprint; neither a different actor nor endpoint can replay it.
    fingerprint = hashlib.sha256(
        json.dumps(
            {"actor": str(current.id), "action": action.value, "request": data.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    # Lock the actor and refresh the identity used by hierarchy checks.
    current.user = await _active_user(db, tenant_id, current.id)
    authorize(current, permission, admin=action in {ResourceAction.ISSUE, ResourceAction.REFUND})
    existing = (
        await db.execute(
            select(ResourceTransaction).where(
                ResourceTransaction.tenant_id == tenant_id,
                ResourceTransaction.idempotency_key == data.idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing:
        if existing.request_hash != fingerprint:
            raise Conflict("Idempotency key is bound to a different request", code="idempotency_conflict")
        return await receipt(db, existing, replayed=True)

    # Every action touches exactly one account: issue and refund credit it, usage debits the caller.
    source_id = current.id if action == ResourceAction.CONSUME else None
    target_id = data.target_user_id if action == ResourceAction.ISSUE and isinstance(data, IssuanceRequest) else None
    refund_of_id = None
    if isinstance(data, RefundRequest):
        await _lock_key(db, tenant_id, "refund:" + str(data.transaction_id))
        original = (
            await db.execute(
                select(ResourceTransaction).where(
                    ResourceTransaction.tenant_id == tenant_id,
                    ResourceTransaction.id == data.transaction_id,
                )
            )
        ).scalar_one_or_none()
        if original is None:
            raise NotFound("Resource usage transaction not found")
        if original.kind != ResourceAction.CONSUME:
            raise Conflict("Only operational usage can be refunded", code="invalid_resource_refund")
        refunded = (
            await db.execute(
                select(ResourceTransaction.id).where(
                    ResourceTransaction.tenant_id == tenant_id,
                    ResourceTransaction.refund_of_id == original.id,
                )
            )
        ).scalar_one_or_none()
        if refunded:
            raise Conflict("Operational usage was already refunded", code="resource_already_refunded")
        target_id = (
            await db.execute(
                select(ResourceWallet.user_id).where(
                    ResourceWallet.tenant_id == tenant_id,
                    ResourceWallet.id == original.source_wallet_id,
                )
            )
        ).scalar_one()
        kind, amount, refund_of_id = original.resource_kind, original.amount, original.id
    else:
        kind, amount = data.resource_kind, data.amount
    if target_id:
        await _active_user(db, tenant_id, target_id)
    if source_id is not None and target_id is not None:  # defence in depth; also a database CHECK
        raise Forbidden("Operational resources cannot move between accounts", code="resource_transfer_forbidden")

    users = {uid for uid in (source_id, target_id) if uid is not None}
    # Insert in deterministic user order too, so simultaneous first use cannot deadlock on unique keys.
    for uid in sorted(users):
        await db.execute(
            insert(ResourceWallet)
            .values(
                tenant_id=tenant_id,
                user_id=uid,
                resource_kind=kind,
                balance=0,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "user_id", "resource_kind"])
        )
    wallets = list(
        (
            await db.execute(
                select(ResourceWallet)
                .where(
                    ResourceWallet.tenant_id == tenant_id,
                    ResourceWallet.user_id.in_(users),
                    ResourceWallet.resource_kind == kind,
                )
                .order_by(ResourceWallet.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    by_user = {wallet.user_id: wallet for wallet in wallets}
    source = by_user.get(source_id)
    target_wallet = by_user.get(target_id)
    if source and source.balance < amount:
        raise Conflict("Insufficient available resource units", code="insufficient_resource_balance")
    if target_wallet and target_wallet.balance > MAX_BALANCE - amount:
        raise Conflict("Resource balance limit exceeded", code="resource_balance_limit")
    tx = ResourceTransaction(
        tenant_id=tenant_id,
        kind=action,
        resource_kind=kind,
        amount=amount,
        source_wallet_id=source.id if source else None,
        target_wallet_id=target_wallet.id if target_wallet else None,
        created_by_id=current.id,
        refund_of_id=refund_of_id,
        idempotency_key=data.idempotency_key,
        request_hash=fingerprint,
        reason=data.reason,
    )
    db.add(tx)
    await db.flush()
    for wallet, delta in ((source, -amount), (target_wallet, amount)):
        if wallet is not None:
            wallet.balance += delta
    # Persist locked balances before inserting their immutable posting snapshots.
    # Deferred database checks still require matching entries before commit.
    await db.flush()
    for wallet, delta in ((source, -amount), (target_wallet, amount)):
        if wallet is not None:
            db.add(
                ResourceLedgerEntry(
                    tenant_id=tenant_id,
                    transaction_id=tx.id,
                    wallet_id=wallet.id,
                    resource_kind=kind,
                    delta=delta,
                    balance_after=wallet.balance,
                )
            )
    await db.flush()
    return await receipt(db, tx)
