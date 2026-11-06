"""Role-based balance management over the single play-balance ledger.

Reads are scoped server-side: administrators (``points.view_all``) see the whole tenant, master
agents and agents (``points.view_downline``) only their own downline, and nobody sees another
tenant (every query filters on the tenant; RLS is the second line of defence).

Writes are administrator-only (``balance.manage`` *and* an admin account) and never move value
between accounts:

* ``account_grant`` adds an amount to one active player;
* ``uniform_grant`` credits the same amount to every active player;
* ``reset`` sets every active player to the configured starting balance (reset policy required,
  refused while any stake is open);
* ``propose_correction`` / ``approve`` / ``reject``: a correction reverses part or all of an
  earlier administrator posting (starting grant, account grant, uniform grant or reset) or applies
  a uniform grant an eligible player missed. A second administrator must approve it before it posts.

Atomicity: the caller's transaction holds everything; an exception rolls the whole operation back.
Concurrency: a per-tenant advisory lock serialises bulk operations, an advisory lock per
idempotency key makes retries wait for the first attempt, and wallets are locked ``FOR UPDATE`` in
ascending id order (the same order settlement uses), so nothing deadlocks with betting.
Idempotency: the key is unique per tenant and bound to a fingerprint of actor, action and request.
"""

import hashlib
import json
import secrets
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.deps import CurrentUser
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.permissions import P
from app.core.security import utcnow
from app.modules.agents.service import downline_ids_select
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.auth.models import AccountStatus
from app.modules.balances.models import (
    MAX_WALLET_BALANCE,
    BalanceOperation,
    BalanceOperationKind,
    BalanceOperationStatus,
    CorrectionDirection,
)
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
from app.modules.betting import reports
from app.modules.betting.models import (
    Bet,
    BetStatus,
    PlayWallet,
    PlayWalletEntry,
    ResetPolicy,
    WalletEntryKind,
)
from app.modules.betting.schemas import WalletEntryRow
from app.modules.betting.service import _post, settings_for, starting_grant, wallet_for
from app.modules.realtime.publisher import notify_staff
from app.modules.tenants.models import Tenant, TenantStatus
from app.modules.users.models import AGENT_TYPES, AccountType, TenantUser
from app.shared.schemas import Page, PageParams

ZERO = Decimal("0.00")
HIERARCHY_TYPES = (AccountType.MASTER_AGENT, AccountType.AGENT, AccountType.PLAYER)
CORRECTABLE_KINDS = (
    WalletEntryKind.GRANT,
    WalletEntryKind.ACCOUNT_GRANT,
    WalletEntryKind.UNIFORM_GRANT,
    WalletEntryKind.RESET,
)
OPEN_CORRECTION = (BalanceOperationStatus.PENDING, BalanceOperationStatus.APPLIED)


# ---- authorization ---------------------------------------------------------------------------


def can_manage(current: CurrentUser) -> bool:
    return (
        current.user.tenant_id == current.tenant.id
        and current.user.account_type == AccountType.ADMIN
        and current.user.status == AccountStatus.ACTIVE
        and current.user.deleted_at is None
        and current.has(P.BALANCE_MANAGE)
    )


def authorize_manage(current: CurrentUser) -> None:
    if not can_manage(current):
        raise Forbidden("Only a tenant administrator can manage balances", code="balance_forbidden")


def view_scope(current: CurrentUser) -> Select | None:  # type: ignore[type-arg]
    """``None`` = the whole tenant; otherwise a select of the user ids the caller may see."""
    if current.user.tenant_id != current.tenant.id:
        raise Forbidden("Balance information is not available", code="balance_forbidden")
    if current.has(P.POINTS_VIEW_ALL):
        return None
    if current.user.account_type in AGENT_TYPES and current.has(P.POINTS_VIEW_DOWNLINE):
        return downline_ids_select(current.tenant.id, current.id)
    raise Forbidden("Balance information is not available", code="balance_forbidden")


def _in_scope(stmt, column, scope):  # type: ignore[no-untyped-def]
    return stmt if scope is None else stmt.where(column.in_(scope))


async def _write_gate(db: AsyncSession, current: CurrentUser) -> None:
    status = (await db.execute(select(Tenant.status).where(Tenant.id == current.tenant.id))).scalar_one_or_none()
    if status != TenantStatus.ACTIVE:
        raise Forbidden("Workspace is unavailable", code="tenant_suspended")
    # Lock the acting administrator and re-read it: a concurrent suspension wins.
    actor = (
        await db.execute(
            select(TenantUser)
            .where(TenantUser.tenant_id == current.tenant.id, TenantUser.id == current.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if actor is None:
        raise Forbidden("Only a tenant administrator can manage balances", code="balance_forbidden")
    current.user = actor
    authorize_manage(current)


async def _lock(db: AsyncSession, tenant_id: uuid.UUID, key: str) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"balance-management:{tenant_id}:{key}"}
    )


async def _check_deferred(db: AsyncSession) -> None:
    """Run the deferred ledger guard now, so a violation fails this request instead of the commit."""
    await db.flush()
    await db.execute(text("SET CONSTRAINTS balance_operations_posted IMMEDIATE"))


def _fingerprint(current: CurrentUser, action: str, data: object) -> str:
    body = data.model_dump(mode="json", exclude={"idempotency_key"})  # type: ignore[attr-defined]
    raw = json.dumps({"actor": str(current.id), "action": action, "request": body}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


async def _replay(db: AsyncSession, current: CurrentUser, key: str, fingerprint: str) -> BalanceOperation | None:
    await _lock(db, current.tenant.id, "key:" + key)
    existing = (
        await db.execute(
            select(BalanceOperation).where(
                BalanceOperation.tenant_id == current.tenant.id, BalanceOperation.idempotency_key == key
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.request_hash != fingerprint:
        raise Conflict("This idempotency key was already used for a different request", code="idempotency_conflict")
    return existing


def _reference() -> str:
    return f"BAL-{utcnow():%Y%m%d}-{secrets.token_hex(4).upper()}"


# ---- reads -----------------------------------------------------------------------------------


def _accounts_query(current: CurrentUser, scope):  # type: ignore[no-untyped-def]
    parent = aliased(TenantUser)
    stmt = (
        select(TenantUser, parent.username.label("parent_username"), PlayWallet)
        .outerjoin(parent, (parent.id == TenantUser.parent_id) & (parent.tenant_id == current.tenant.id))
        .outerjoin(PlayWallet, (PlayWallet.user_id == TenantUser.id) & (PlayWallet.tenant_id == current.tenant.id))
        .where(
            TenantUser.tenant_id == current.tenant.id,
            TenantUser.deleted_at.is_(None),
            TenantUser.account_type.in_(HIERARCHY_TYPES),
        )
    )
    return _in_scope(stmt, TenantUser.id, scope)


def _account_row(user: TenantUser, parent_username: str | None, wallet: PlayWallet | None) -> AccountRow:
    return AccountRow(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        account_type=user.account_type.value,
        status=user.status.value,
        parent_id=user.parent_id,
        parent_username=parent_username,
        balance=wallet.balance if wallet else ZERO,
        has_wallet=wallet is not None,
        updated_at=wallet.updated_at if wallet else None,
    )


async def accounts(
    db: AsyncSession,
    current: CurrentUser,
    params: PageParams,
    *,
    account_type: AccountType | None = None,
    parent_id: uuid.UUID | None = None,
    status: AccountStatus | None = None,
) -> Page[AccountRow]:
    scope = view_scope(current)
    stmt = _accounts_query(current, scope)
    if account_type:
        stmt = stmt.where(TenantUser.account_type == account_type)
    if parent_id:
        # Everything below the chosen account (still inside the caller's own scope).
        stmt = stmt.where(TenantUser.id.in_(downline_ids_select(current.tenant.id, parent_id, "parent_downline")))
    if status:
        stmt = stmt.where(TenantUser.status == status)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(TenantUser.username.ilike(like), TenantUser.full_name.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    order = params.order_by(
        {"username": TenantUser.username, "balance": PlayWallet.balance, "account_type": TenantUser.account_type},
        TenantUser.username.asc(),
    )
    rows = (await db.execute(stmt.order_by(order, TenantUser.id).offset(params.offset).limit(params.page_size))).all()
    return Page(
        items=[_account_row(u, pu, w) for u, pu, w in rows], total=int(total), page=params.page, page_size=params.page_size
    )


async def account_detail(
    db: AsyncSession, current: CurrentUser, user_id: uuid.UUID, *, date_from: date | None = None, date_to: date | None = None
) -> AccountDetail:
    scope = view_scope(current)
    row = (await db.execute(_accounts_query(current, scope).where(TenantUser.id == user_id))).first()
    if row is None:  # outside the caller's scope looks exactly like a missing account
        raise NotFound("Account not found")
    statement = await reports.statement_summary(db, current.tenant.id, user_id, date_from=date_from, date_to=date_to)
    return AccountDetail(account=_account_row(*row), statement=statement)


async def summary(db: AsyncSession, current: CurrentUser) -> BalanceSummary:
    scope = view_scope(current)
    base = _accounts_query(current, scope).subquery()
    accounts_n, participants, total = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(base.c.account_type == AccountType.PLAYER.value),
                func.coalesce(func.sum(base.c.balance), 0),
            ).select_from(base)
        )
    ).one()
    pending = None
    if scope is None:
        pending = (
            await db.execute(
                select(func.count()).where(
                    BalanceOperation.tenant_id == current.tenant.id,
                    BalanceOperation.status == BalanceOperationStatus.PENDING,
                )
            )
        ).scalar_one()
    settings = await settings_for(db, current.tenant.id)
    return BalanceSummary(
        accounts=int(accounts_n),
        participants=int(participants),
        total_balance=Decimal(total).quantize(Decimal("0.01")),
        pending_corrections=None if pending is None else int(pending),
        reset_policy=settings.reset_policy.value,
        starting_balance=settings.starting_balance,
        can_manage=can_manage(current),
    )


async def entries(
    db: AsyncSession,
    current: CurrentUser,
    params: PageParams,
    *,
    kind: WalletEntryKind | None = None,
    user_id: uuid.UUID | None = None,
    operation_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[WalletEntryRow]:
    return await reports.entries(
        db,
        current.tenant.id,
        params,
        kind=kind,
        user_id=user_id,
        operation_id=operation_id,
        date_from=date_from,
        date_to=date_to,
        user_scope=view_scope(current),
    )


async def entry_detail(db: AsyncSession, current: CurrentUser, entry_id: uuid.UUID) -> WalletEntryRow:
    page = await reports.entries(
        db, current.tenant.id, PageParams(page=1, page_size=1), entry_id=entry_id, user_scope=view_scope(current)
    )
    if not page.items:
        raise NotFound("Transaction not found")
    return page.items[0]


def _operations_query(current: CurrentUser):  # type: ignore[no-untyped-def]
    target, creator, reviewer = aliased(TenantUser), aliased(TenantUser), aliased(TenantUser)
    tid = current.tenant.id
    return (
        select(BalanceOperation, target.username, creator.username, reviewer.username)
        .outerjoin(target, (target.id == BalanceOperation.target_user_id) & (target.tenant_id == tid))
        .join(creator, (creator.id == BalanceOperation.created_by_id) & (creator.tenant_id == tid))
        .outerjoin(reviewer, (reviewer.id == BalanceOperation.reviewed_by_id) & (reviewer.tenant_id == tid))
        .where(BalanceOperation.tenant_id == tid)
    ), target


def _operation_out(op: BalanceOperation, target: str | None, creator: str | None, reviewer: str | None, **kw) -> OperationOut:  # type: ignore[no-untyped-def]
    out = OperationOut.model_validate(op)
    return out.model_copy(
        update={"target_username": target, "created_by_username": creator, "reviewed_by_username": reviewer, **kw}
    )


def _require_admin_view(current: CurrentUser) -> None:
    # Operations span the whole tenant; downline viewers see their accounts' entries instead.
    if view_scope(current) is not None or current.user.account_type != AccountType.ADMIN:
        raise Forbidden("Only tenant administrators can review balance operations", code="balance_forbidden")


async def operations(
    db: AsyncSession,
    current: CurrentUser,
    params: PageParams,
    *,
    kind: BalanceOperationKind | None = None,
    status: BalanceOperationStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[OperationOut]:
    _require_admin_view(current)
    stmt, target = _operations_query(current)
    stmt = reports._in_range(stmt, BalanceOperation.created_at, date_from, date_to)
    if kind:
        stmt = stmt.where(BalanceOperation.kind == kind)
    if status:
        stmt = stmt.where(BalanceOperation.status == status)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(
            or_(BalanceOperation.reference.ilike(like), BalanceOperation.remarks.ilike(like), target.username.ilike(like))
        )
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(BalanceOperation.created_at.desc(), BalanceOperation.id.desc())
            .offset(params.offset)
            .limit(params.page_size)
        )
    ).all()
    return Page(items=[_operation_out(*r) for r in rows], total=int(total), page=params.page, page_size=params.page_size)


async def operation_detail(db: AsyncSession, current: CurrentUser, operation_id: uuid.UUID, **kw) -> OperationOut:  # type: ignore[no-untyped-def]
    stmt, _ = _operations_query(current)
    stmt = stmt.where(BalanceOperation.id == operation_id).execution_options(populate_existing=True)
    row = (await db.execute(stmt)).first()
    if row is None:
        raise NotFound("Balance operation not found")
    return _operation_out(*row, **kw)


# ---- uniform operations ----------------------------------------------------------------------


def _eligible_players(tenant_id: uuid.UUID) -> Select:  # type: ignore[type-arg]
    return select(TenantUser.id).where(
        TenantUser.tenant_id == tenant_id,
        TenantUser.account_type == AccountType.PLAYER,
        TenantUser.status == AccountStatus.ACTIVE,
        TenantUser.deleted_at.is_(None),
    )


async def _lock_wallets(db: AsyncSession, tenant_id: uuid.UUID, user_ids: Select) -> list[PlayWallet]:  # type: ignore[type-arg]
    return list(
        (
            await db.execute(
                select(PlayWallet)
                .where(PlayWallet.tenant_id == tenant_id, PlayWallet.user_id.in_(user_ids))
                .order_by(PlayWallet.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def _finish(
    db: AsyncSession, current: CurrentUser, op: BalanceOperation, action: str, meta: RequestMeta, changes: dict[str, object]
) -> OperationOut:
    await audit.record(
        db,
        actor=current.actor,
        tenant_id=current.tenant.id,
        action=action,
        resource_type="balance_operation",
        resource_id=op.id,
        changes={"reference": op.reference, "kind": op.kind.value, "status": op.status.value, **changes},
        meta=meta,
    )
    await _check_deferred(db)
    await notify_staff(db, current.tenant.id, P.POINTS_VIEW_ALL, "betting.ledger_updated", {"kind": op.kind.value})
    return await operation_detail(db, current, op.id)


async def uniform_grant(db: AsyncSession, current: CurrentUser, data: UniformGrantIn, meta: RequestMeta) -> OperationOut:
    authorize_manage(current)
    await _write_gate(db, current)
    tid = current.tenant.id
    await _lock(db, tid, "bulk")
    fingerprint = _fingerprint(current, "uniform_grant", data)
    if existing := await _replay(db, current, data.idempotency_key, fingerprint):
        return await operation_detail(db, current, existing.id, replayed=True)

    players = list((await db.execute(_eligible_players(tid).order_by(TenantUser.id))).scalars())
    if not players:
        raise Conflict("There are no active players to receive a uniform grant", code="no_participants")
    await db.execute(
        insert(PlayWallet)
        .values([{"id": uuid.uuid4(), "tenant_id": tid, "user_id": uid, "balance": ZERO, "version": 0} for uid in players])
        .on_conflict_do_nothing(constraint="uq_play_wallets_tenant_user")
    )
    wallets = await _lock_wallets(db, tid, _eligible_players(tid))
    for wallet in wallets:
        await starting_grant(db, wallet)  # a never-used wallet first receives its starting balance
    over = [w.user_id for w in wallets if w.balance + data.amount > MAX_WALLET_BALANCE]
    if over:
        raise Conflict("The grant would exceed the maximum balance", code="balance_limit", details={"accounts": len(over)})
    op = BalanceOperation(
        tenant_id=tid,
        reference=_reference(),
        kind=BalanceOperationKind.UNIFORM_GRANT,
        status=BalanceOperationStatus.APPLIED,
        amount=data.amount,
        remarks=data.remarks,
        created_by_id=current.id,
        accounts_affected=len(wallets),
        total_delta=data.amount * len(wallets),
        idempotency_key=data.idempotency_key,
        request_hash=fingerprint,
    )
    db.add(op)
    await db.flush()
    memo = (data.remarks or "Uniform grant to every active player")[:200]
    for wallet in wallets:
        await _post(
            db,
            wallet,
            WalletEntryKind.UNIFORM_GRANT,
            data.amount,
            f"ugrant:{op.id}:{wallet.user_id}",
            memo=memo,
            operation_id=op.id,
        )
    return await _finish(
        db, current, op, "balance.uniform_grant", meta, {"amount": str(op.amount), "accounts": op.accounts_affected}
    )


async def account_grant(db: AsyncSession, current: CurrentUser, data: AccountGrantIn, meta: RequestMeta) -> OperationOut:
    """Add ``amount`` to one active player, agent or master agent. No other account is debited.

    This is how an administrator puts transferable wallet points onto an upline. It is not a
    transfer: nothing is deducted from the administrator."""
    authorize_manage(current)
    await _write_gate(db, current)
    tid = current.tenant.id
    fingerprint = _fingerprint(current, "account_grant", data)
    if existing := await _replay(db, current, data.idempotency_key, fingerprint):
        return await operation_detail(db, current, existing.id, replayed=True)

    await _lock(db, tid, f"player:{data.target_user_id}")
    player = await _fundable_account(db, tid, data.target_user_id)
    if player.status != AccountStatus.ACTIVE:
        raise Conflict("Only an active account can receive a balance", code="not_active")
    wallet = await wallet_for(db, tid, player.id, lock=True)
    if wallet.balance + data.amount > MAX_WALLET_BALANCE:
        raise Conflict("The grant would exceed the maximum balance", code="balance_limit")
    op = BalanceOperation(
        tenant_id=tid,
        reference=_reference(),
        kind=BalanceOperationKind.ACCOUNT_GRANT,
        status=BalanceOperationStatus.APPLIED,
        amount=data.amount,
        target_user_id=player.id,
        remarks=data.remarks,
        created_by_id=current.id,
        accounts_affected=1,
        total_delta=data.amount,
        idempotency_key=data.idempotency_key,
        request_hash=fingerprint,
    )
    db.add(op)
    await db.flush()
    memo = (data.remarks or f"Balance added to {player.username}")[:200]
    await _post(
        db,
        wallet,
        WalletEntryKind.ACCOUNT_GRANT,
        data.amount,
        f"agrant:{op.id}:{wallet.user_id}",
        memo=memo,
        operation_id=op.id,
    )
    return await _finish(
        db,
        current,
        op,
        "balance.account_grant",
        meta,
        {"target_user_id": str(player.id), "amount": str(op.amount)},
    )


async def _open_stakes(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    from app.modules.simulations.models import SimulationMatch, SimulationStake, SimulationState

    bets = await db.scalar(select(func.count()).where(Bet.tenant_id == tenant_id, Bet.status == BetStatus.ACCEPTED))
    simulated = await db.scalar(
        select(func.count())
        .select_from(SimulationStake)
        .join(
            SimulationMatch,
            (SimulationMatch.id == SimulationStake.match_id) & (SimulationMatch.tenant_id == SimulationStake.tenant_id),
        )
        .where(SimulationStake.tenant_id == tenant_id, SimulationMatch.state != SimulationState.SETTLED)
    )
    return int(bets or 0) + int(simulated or 0)


async def reset(db: AsyncSession, current: CurrentUser, data: ResetIn, meta: RequestMeta) -> OperationOut:
    authorize_manage(current)
    await _write_gate(db, current)
    tid = current.tenant.id
    await _lock(db, tid, "bulk")
    fingerprint = _fingerprint(current, "reset", data)
    if existing := await _replay(db, current, data.idempotency_key, fingerprint):
        return await operation_detail(db, current, existing.id, replayed=True)

    settings = await settings_for(db, tid)
    if settings.reset_policy != ResetPolicy.STARTING_BALANCE:
        raise Conflict("Balance resets are disabled by the reset policy", code="reset_disabled")
    target = settings.starting_balance
    # Lock first, then look for open stakes: a stake needs its wallet lock, so none can slip in.
    wallets = await _lock_wallets(db, tid, _eligible_players(tid))
    if open_count := await _open_stakes(db, tid):
        raise Conflict(
            "Balances cannot be reset while stakes are open; settle or cancel them first",
            code="open_stakes",
            details={"open_stakes": open_count},
        )
    moves = [(w, target - w.balance) for w in wallets if w.balance != target]
    op = BalanceOperation(
        tenant_id=tid,
        reference=_reference(),
        kind=BalanceOperationKind.RESET,
        status=BalanceOperationStatus.APPLIED,
        amount=target,
        remarks=data.remarks,
        created_by_id=current.id,
        accounts_affected=len(moves),
        total_delta=sum((d for _, d in moves), ZERO),
        idempotency_key=data.idempotency_key,
        request_hash=fingerprint,
    )
    db.add(op)
    await db.flush()
    memo = (data.remarks or f"Reset to the starting balance of {target}")[:200]
    for wallet, delta in moves:
        await _post(db, wallet, WalletEntryKind.RESET, delta, f"reset:{op.id}:{wallet.user_id}", memo=memo, operation_id=op.id)
    return await _finish(
        db,
        current,
        op,
        "balance.reset",
        meta,
        {"target": str(target), "accounts": op.accounts_affected, "net": str(op.total_delta)},
    )


# ---- corrections (maker-checker) -------------------------------------------------------------


async def _fundable_account(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> TenantUser:
    """A player, agent or master agent in this tenant. An administrator grant credits this wallet
    and debits nobody. Agents then transfer from that wallet to their players."""
    user = (
        await db.execute(
            select(TenantUser)
            .where(TenantUser.tenant_id == tenant_id, TenantUser.id == user_id, TenantUser.deleted_at.is_(None))
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if user is None or user.account_type not in (AccountType.PLAYER, AccountType.AGENT, AccountType.MASTER_AGENT):
        raise NotFound("Account not found")
    return user


async def _target_player(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> TenantUser:
    user = (
        await db.execute(
            select(TenantUser)
            .where(TenantUser.tenant_id == tenant_id, TenantUser.id == user_id, TenantUser.deleted_at.is_(None))
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if user is None or user.account_type != AccountType.PLAYER:
        raise NotFound("Player not found")
    return user


async def _entry_remaining(
    db: AsyncSession, tenant_id: uuid.UUID, entry: PlayWalletEntry, *, excluding: uuid.UUID | None = None
) -> Decimal:
    """How much of an administrator posting is still uncorrected (pending corrections count)."""
    stmt = select(func.coalesce(func.sum(BalanceOperation.amount), 0)).where(
        BalanceOperation.tenant_id == tenant_id,
        BalanceOperation.corrects_entry_id == entry.id,
        BalanceOperation.status.in_(OPEN_CORRECTION),
    )
    if excluding is not None:
        stmt = stmt.where(BalanceOperation.id != excluding)
    return abs(entry.delta) - Decimal(await db.scalar(stmt) or 0)


async def _missed_grant_open(
    db: AsyncSession, tenant_id: uuid.UUID, grant: BalanceOperation, user_id: uuid.UUID, *, excluding: uuid.UUID | None = None
) -> bool:
    """The player has not received this uniform grant and no correction for it is pending or applied."""
    received = await db.scalar(
        select(func.count())
        .select_from(PlayWalletEntry)
        .join(PlayWallet, (PlayWallet.id == PlayWalletEntry.wallet_id) & (PlayWallet.tenant_id == tenant_id))
        .where(
            PlayWalletEntry.tenant_id == tenant_id,
            PlayWalletEntry.operation_id == grant.id,
            PlayWallet.user_id == user_id,
        )
    )
    stmt = select(func.count()).where(
        BalanceOperation.tenant_id == tenant_id,
        BalanceOperation.corrects_operation_id == grant.id,
        BalanceOperation.target_user_id == user_id,
        BalanceOperation.status.in_(OPEN_CORRECTION),
    )
    if excluding is not None:
        stmt = stmt.where(BalanceOperation.id != excluding)
    return not received and not await db.scalar(stmt)


async def _correctable_entry(db: AsyncSession, tenant_id: uuid.UUID, entry_id: uuid.UUID, user_id: uuid.UUID) -> PlayWalletEntry:
    entry = (
        await db.execute(
            select(PlayWalletEntry)
            .join(PlayWallet, (PlayWallet.id == PlayWalletEntry.wallet_id) & (PlayWallet.tenant_id == tenant_id))
            .where(PlayWalletEntry.tenant_id == tenant_id, PlayWalletEntry.id == entry_id, PlayWallet.user_id == user_id)
        )
    ).scalar_one_or_none()
    if entry is None:
        raise NotFound("Transaction not found for this player")
    if entry.kind not in CORRECTABLE_KINDS:
        raise Conflict(
            "Only starting grants, account grants, uniform grants and resets can be corrected; "
            "play results are corrected through results",
            code="not_correctable",
        )
    return entry


async def _uniform_grant_op(db: AsyncSession, tenant_id: uuid.UUID, operation_id: uuid.UUID) -> BalanceOperation:
    grant = (
        await db.execute(
            select(BalanceOperation).where(
                BalanceOperation.tenant_id == tenant_id,
                BalanceOperation.id == operation_id,
                BalanceOperation.kind == BalanceOperationKind.UNIFORM_GRANT,
                BalanceOperation.status == BalanceOperationStatus.APPLIED,
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        raise NotFound("Uniform grant not found")
    return grant


async def propose_correction(db: AsyncSession, current: CurrentUser, data: CorrectionIn, meta: RequestMeta) -> OperationOut:
    authorize_manage(current)
    await _write_gate(db, current)
    tid = current.tenant.id
    fingerprint = _fingerprint(current, "correction", data)
    if existing := await _replay(db, current, data.idempotency_key, fingerprint):
        return await operation_detail(db, current, existing.id, replayed=True)
    player = await _target_player(db, tid, data.target_user_id)

    if data.entry_id is not None:
        await _lock(db, tid, f"entry:{data.entry_id}")
        entry = await _correctable_entry(db, tid, data.entry_id, player.id)
        remaining = await _entry_remaining(db, tid, entry)
        amount = data.amount if data.amount is not None else remaining
        if remaining <= 0 or amount > remaining:
            raise Conflict(
                "The correction exceeds the uncorrected part of that transaction",
                code="correction_exceeds_entry",
                details={"remaining": str(max(remaining, ZERO))},
            )
        direction = CorrectionDirection.DEBIT if entry.delta > 0 else CorrectionDirection.CREDIT
    else:
        assert data.operation_id is not None
        grant = await _uniform_grant_op(db, tid, data.operation_id)
        await _lock(db, tid, f"missed:{grant.id}:{player.id}")
        if player.created_at > grant.created_at:
            raise Conflict("The player did not exist when this grant was made", code="not_eligible")
        if not await _missed_grant_open(db, tid, grant, player.id):
            raise Conflict("The player already received this grant or a correction for it exists", code="already_received")
        amount, direction = grant.amount, CorrectionDirection.CREDIT

    op = BalanceOperation(
        tenant_id=tid,
        reference=_reference(),
        kind=BalanceOperationKind.CORRECTION,
        status=BalanceOperationStatus.PENDING,
        amount=amount,
        direction=direction,
        target_user_id=player.id,
        corrects_entry_id=data.entry_id,
        corrects_operation_id=data.operation_id,
        remarks=data.remarks,
        created_by_id=current.id,
        idempotency_key=data.idempotency_key,
        request_hash=fingerprint,
    )
    db.add(op)
    await db.flush()
    return await _finish(
        db,
        current,
        op,
        "balance.correction_proposed",
        meta,
        {"target_user_id": str(player.id), "amount": str(amount), "direction": direction.value},
    )


async def _lock_correction(db: AsyncSession, current: CurrentUser, operation_id: uuid.UUID) -> BalanceOperation:
    op = (
        await db.execute(
            select(BalanceOperation)
            .where(
                BalanceOperation.tenant_id == current.tenant.id,
                BalanceOperation.id == operation_id,
                BalanceOperation.kind == BalanceOperationKind.CORRECTION,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if op is None:
        raise NotFound("Correction not found")
    return op


def _require_second_admin(current: CurrentUser, op: BalanceOperation) -> None:
    if op.created_by_id == current.id:
        raise Forbidden("A correction must be reviewed by a different administrator", code="second_reviewer_required")


async def approve(
    db: AsyncSession, current: CurrentUser, operation_id: uuid.UUID, data: ReviewIn, meta: RequestMeta
) -> OperationOut:
    authorize_manage(current)
    await _write_gate(db, current)
    tid = current.tenant.id
    op = await _lock_correction(db, current, operation_id)
    if op.status == BalanceOperationStatus.APPLIED:
        if op.reviewed_by_id == current.id:  # a retried approval
            return await operation_detail(db, current, op.id, replayed=True)
        raise Conflict("This correction was already approved", code="already_reviewed")
    if op.status == BalanceOperationStatus.REJECTED:
        raise Conflict("This correction was rejected", code="already_reviewed")
    _require_second_admin(current, op)
    assert op.target_user_id is not None
    player = await _target_player(db, tid, op.target_user_id)

    # Re-validate against the current ledger: other corrections may have been applied meanwhile.
    if op.corrects_entry_id is not None:
        await _lock(db, tid, f"entry:{op.corrects_entry_id}")
        entry = await _correctable_entry(db, tid, op.corrects_entry_id, player.id)
        if op.amount > await _entry_remaining(db, tid, entry, excluding=op.id):
            raise Conflict("The correction now exceeds the uncorrected part of that transaction", code="correction_exceeds_entry")
    else:
        assert op.corrects_operation_id is not None
        await _lock(db, tid, f"missed:{op.corrects_operation_id}:{player.id}")
        grant = await _uniform_grant_op(db, tid, op.corrects_operation_id)
        if not await _missed_grant_open(db, tid, grant, player.id, excluding=op.id):
            raise Conflict("The player already received this grant", code="already_received")

    delta = op.amount if op.direction == CorrectionDirection.CREDIT else -op.amount
    wallet = await wallet_for(db, tid, player.id, lock=True)
    if wallet.balance + delta > MAX_WALLET_BALANCE:
        raise Conflict("The correction would exceed the maximum balance", code="balance_limit")
    # Raises insufficient_balance (and rolls everything back) if the player has since spent it.
    await _post(
        db,
        wallet,
        WalletEntryKind.CORRECTION,
        delta,
        f"correction:{op.id}",
        memo=op.remarks[:200] if op.remarks else None,
        operation_id=op.id,
    )
    op.status = BalanceOperationStatus.APPLIED
    op.reviewed_by_id = current.id
    op.reviewed_at = utcnow()
    op.review_remarks = data.remarks
    op.accounts_affected = 1
    op.total_delta = delta
    return await _finish(
        db, current, op, "balance.correction_approved", meta, {"delta": str(delta), "balance_after": str(wallet.balance)}
    )


async def reject(
    db: AsyncSession, current: CurrentUser, operation_id: uuid.UUID, data: RejectIn, meta: RequestMeta
) -> OperationOut:
    authorize_manage(current)
    await _write_gate(db, current)
    op = await _lock_correction(db, current, operation_id)
    if op.status == BalanceOperationStatus.REJECTED and op.reviewed_by_id == current.id:
        return await operation_detail(db, current, op.id, replayed=True)
    if op.status != BalanceOperationStatus.PENDING:
        raise Conflict("This correction was already reviewed", code="already_reviewed")
    _require_second_admin(current, op)
    op.status = BalanceOperationStatus.REJECTED
    op.reviewed_by_id = current.id
    op.reviewed_at = utcnow()
    op.review_remarks = data.remarks
    return await _finish(db, current, op, "balance.correction_rejected", meta, {"remarks": data.remarks})
