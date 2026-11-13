"""Role-based balance management over the single play-balance ledger.

Reads are scoped server-side: administrators (``points.view_all``) see the whole tenant, master
agents and agents (``points.view_downline``) only the accounts they own directly (a master agent's
agents and its own players, never its agents' players), and nobody sees another
tenant (every query filters on the tenant; RLS is the second line of defence).

Administrator writes require ``balance.manage`` *and* an admin account:

* ``account_grant`` adds an amount to one active account and debits nobody;
* ``wallet_transfer`` moves play balance from the acting administrator's wallet to a master agent or agent;
* ``wallet_recall`` moves play balance from one selected master agent or agent to the acting administrator;
* ``earnings_to_wallet`` moves that account's available commission into its own wallet;
* ``uniform_grant`` credits the same amount to every active player;
* ``reset`` sets every active player to the configured starting balance (reset policy required,
  refused while any stake is open);
* ``propose_correction`` / ``approve`` / ``reject``: a correction reverses part or all of an
  earlier administrator posting (starting grant, account grant, uniform grant or reset) or applies
  a uniform grant an eligible player missed. A second administrator must approve it before it posts.

Loaders with ``loader.view`` and ``loader.grant`` may also use ``wallet_recall`` to return
upline balance to their allocation and ``earnings_to_wallet`` to move upline commission.
Their existing Loader grant API transfers allocated funds. Creating balance, resets and
correction reviews remain administrator-only; Loader withdrawal history shows only their receipts.

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
from app.modules.agents.models import BalanceTransfer
from app.modules.agents.service import downline_ids_select, visible_ids_select
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
    EarningsToWalletIn,
    EarningsToWalletOut,
    OperationOut,
    RejectIn,
    ResetIn,
    ReviewIn,
    UniformGrantIn,
    UplineReturnRow,
    WalletRecallIn,
    WalletRecallOut,
    WalletTransferIn,
    WalletTransferOut,
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
from app.modules.realtime.publisher import notify_staff, publish_to_users_after_commit
from app.modules.tenants.models import Tenant, TenantStatus
from app.modules.users.models import AGENT_TYPES, AccountType, TenantUser
from app.shared.schemas import Page, PageParams

ZERO = Decimal("0.00")
HIERARCHY_TYPES = (AccountType.MASTER_AGENT, AccountType.AGENT, AccountType.SUB_AGENT, AccountType.PLAYER)
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


def can_operate_wallet(current: CurrentUser) -> bool:
    """Loaders may move funded balances, but cannot mint, reset or review balances."""
    return can_manage(current) or (
        current.user.tenant_id == current.tenant.id
        and current.user.account_type == AccountType.LOADER
        and current.user.status == AccountStatus.ACTIVE
        and current.user.deleted_at is None
        and current.has(P.LOADER_VIEW)
        and current.has(P.LOADER_GRANT)
    )


def authorize_wallet_operator(current: CurrentUser) -> None:
    if not can_operate_wallet(current):
        raise Forbidden("Only an authorized administrator or Loader can move balances", code="balance_forbidden")


def view_scope(current: CurrentUser) -> Select | None:  # type: ignore[type-arg]
    """``None`` = the whole tenant; otherwise a select of the user ids the caller may see."""
    if current.user.tenant_id != current.tenant.id:
        raise Forbidden("Balance information is not available", code="balance_forbidden")
    if current.has(P.POINTS_VIEW_ALL):
        return None
    if current.user.account_type in AGENT_TYPES and current.has(P.POINTS_VIEW_DOWNLINE):
        return visible_ids_select(current.tenant.id, current.id, current.user.account_type)
    # A Loader sees every master agent, agent and player in the tenant, and no other account type.
    if current.user.account_type == AccountType.LOADER and current.has(P.LOADER_VIEW):
        return None
    raise Forbidden("Balance information is not available", code="balance_forbidden")


def _in_scope(stmt, column, scope):  # type: ignore[no-untyped-def]
    return stmt if scope is None else stmt.where(column.in_(scope))


async def _write_gate(db: AsyncSession, current: CurrentUser, *, wallet_operator: bool = False) -> None:
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
    if wallet_operator:
        authorize_wallet_operator(current)
    else:
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
    # Administrators also see Loader accounts so they can fund that account's allocation.
    # Master agents and agents still see only the hierarchy they already could.
    types = (*HIERARCHY_TYPES, AccountType.LOADER) if can_manage(current) else HIERARCHY_TYPES
    parent = aliased(TenantUser)
    stmt = (
        select(TenantUser, parent.username.label("parent_username"), PlayWallet)
        .outerjoin(parent, (parent.id == TenantUser.parent_id) & (parent.tenant_id == current.tenant.id))
        .outerjoin(PlayWallet, (PlayWallet.user_id == TenantUser.id) & (PlayWallet.tenant_id == current.tenant.id))
        .where(
            TenantUser.tenant_id == current.tenant.id,
            TenantUser.deleted_at.is_(None),
            TenantUser.account_type.in_(types),
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
    user = row[0]
    earnings = None
    if user.account_type in AGENT_TYPES:
        from app.modules.commissions.moves import _available_earnings

        earnings = await _available_earnings(db, current.tenant.id, user.id, user.account_type)
    return AccountDetail(account=_account_row(*row), statement=statement, available_earnings=earnings)


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
    own_balance = None
    if can_operate_wallet(current):
        own = await db.scalar(
            select(PlayWallet.balance).where(PlayWallet.tenant_id == current.tenant.id, PlayWallet.user_id == current.id)
        )
        own_balance = Decimal(own or 0).quantize(Decimal("0.01"))
    return BalanceSummary(
        accounts=int(accounts_n),
        participants=int(participants),
        total_balance=Decimal(total).quantize(Decimal("0.01")),
        pending_corrections=None if pending is None else int(pending),
        reset_policy=settings.reset_policy.value,
        starting_balance=settings.starting_balance,
        can_manage=can_manage(current),
        own_balance=own_balance,
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
    """A player, agent, master agent or loader in this tenant. An administrator grant credits this
    wallet and debits nobody. A Loader's balance is the allocation they may give out."""
    user = (
        await db.execute(
            select(TenantUser)
            .where(TenantUser.tenant_id == tenant_id, TenantUser.id == user_id, TenantUser.deleted_at.is_(None))
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if user is None or user.account_type not in (
        AccountType.PLAYER,
        AccountType.AGENT,
        AccountType.SUB_AGENT,
        AccountType.MASTER_AGENT,
        AccountType.LOADER,
        AccountType.ADMIN,
    ):
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


UPLINE = (AccountType.MASTER_AGENT, AccountType.AGENT, AccountType.SUB_AGENT)


async def _wallet_row(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, *, lock: bool) -> PlayWallet | None:
    stmt = select(PlayWallet).where(PlayWallet.tenant_id == tenant_id, PlayWallet.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _upline(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> TenantUser:
    user = (
        await db.execute(
            select(TenantUser)
            .where(TenantUser.tenant_id == tenant_id, TenantUser.id == user_id, TenantUser.deleted_at.is_(None))
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if user is None or user.account_type not in UPLINE:
        raise NotFound("Account not found")
    if user.status != AccountStatus.ACTIVE:
        raise Conflict("Only an active account can receive a balance", code="not_active")
    return user


def _transfer_out(row: BalanceTransfer, username: str, *, replayed: bool) -> WalletTransferOut:
    return WalletTransferOut(
        id=row.id,
        reference=row.reference,
        target_user_id=row.player_id,
        target_username=username,
        amount=row.amount,
        sender_balance_after=row.sender_balance_after,
        target_balance_after=row.player_balance_after,
        replayed=replayed,
    )


async def wallet_transfer(db: AsyncSession, current: CurrentUser, data: WalletTransferIn, meta: RequestMeta) -> WalletTransferOut:
    """Debit the acting administrator and credit one master agent or agent. Nothing is created."""
    authorize_manage(current)
    await _write_gate(db, current)
    tid = current.tenant.id
    if data.target_user_id == current.id:
        raise NotFound("Account not found")
    fingerprint = _fingerprint(current, "wallet_transfer", data)
    await _lock(db, tid, "key:" + data.idempotency_key)
    existing = (
        await db.execute(
            select(BalanceTransfer).where(
                BalanceTransfer.tenant_id == tid, BalanceTransfer.idempotency_key == data.idempotency_key
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != fingerprint or existing.sender_id != current.id:
            raise Conflict("This idempotency key was already used for a different request", code="idempotency_conflict")
        username = await db.scalar(
            select(TenantUser.username).where(TenantUser.tenant_id == tid, TenantUser.id == existing.player_id)
        )
        return _transfer_out(existing, username or "", replayed=True)

    target = await _upline(db, tid, data.target_user_id)
    admin = await _wallet_row(db, tid, current.id, lock=False)
    if admin is None:
        raise Conflict("Your wallet does not have enough balance", code="admin_wallet_short", details={"available": "0.00"})
    await db.execute(
        insert(PlayWallet)
        .values(tenant_id=tid, user_id=target.id, balance=ZERO, version=0)
        .on_conflict_do_nothing(constraint="uq_play_wallets_tenant_user")
    )
    dest = await _wallet_row(db, tid, target.id, lock=False)
    assert dest is not None
    first, second = sorted((admin, dest), key=lambda wallet: wallet.id.int)
    locked = {row.user_id: await _wallet_row(db, tid, row.user_id, lock=True) for row in (first, second)}
    admin_w = locked[current.id]
    dest_w = locked[target.id]
    assert admin_w is not None and dest_w is not None
    if dest_w.version == 0:
        await starting_grant(db, dest_w)
    if admin_w.balance < data.amount:
        raise Conflict(
            "Your wallet does not have enough balance",
            code="admin_wallet_short",
            details={"available": str(admin_w.balance)},
        )
    if dest_w.balance + data.amount > MAX_WALLET_BALANCE:
        raise Conflict("The transfer would exceed the maximum balance", code="balance_limit")
    transfer_id = uuid.uuid4()
    sender_before = admin_w.balance
    dest_before = dest_w.balance
    memo = (data.remarks or f"Wallet transfer to {target.username}")[:200]
    if not await _post(db, admin_w, WalletEntryKind.TRANSFER, -data.amount, f"xfer:{transfer_id}:out", memo=memo):
        raise Conflict("This transfer was already posted", code="duplicate_grant")
    if not await _post(db, dest_w, WalletEntryKind.TRANSFER, data.amount, f"xfer:{transfer_id}:in", memo=memo):
        raise Conflict("This transfer was already posted", code="duplicate_grant")
    row = BalanceTransfer(
        id=transfer_id,
        tenant_id=tid,
        reference=_reference(),
        sender_id=current.id,
        player_id=target.id,
        amount=data.amount,
        sender_balance_before=sender_before,
        sender_balance_after=admin_w.balance,
        player_balance_before=dest_before,
        player_balance_after=dest_w.balance,
        status="applied",
        idempotency_key=data.idempotency_key,
        request_hash=fingerprint,
    )
    db.add(row)
    await db.flush()
    await audit.record(
        db,
        actor=current.actor,
        tenant_id=tid,
        action="balance.wallet_transfer",
        resource_type="balance_transfer",
        resource_id=row.id,
        changes={
            "admin_id": str(current.id),
            "recipient_id": str(target.id),
            "amount": str(data.amount),
            "sender_balance": [str(sender_before), str(admin_w.balance)],
            "recipient_balance": [str(dest_before), str(dest_w.balance)],
        },
        meta=meta,
    )
    publish_to_users_after_commit(
        db,
        tid,
        "balance.updated",
        {"purpose": "transfer", "status": "applied", "amount": str(data.amount)},
        {current.id, target.id},
    )
    await notify_staff(db, tid, P.POINTS_VIEW_ALL, "betting.ledger_updated", {"kind": "transfer"})
    return _transfer_out(row, target.username, replayed=False)


async def wallet_recall(db: AsyncSession, current: CurrentUser, data: WalletRecallIn, meta: RequestMeta) -> WalletRecallOut:
    """Debit a master agent or agent and credit the authorized administrator or Loader."""
    authorize_wallet_operator(current)
    await _write_gate(db, current, wallet_operator=True)
    tid = current.tenant.id
    fingerprint = _fingerprint(current, "wallet_recall", data)
    await _lock(db, tid, "key:" + data.idempotency_key)
    existing = (
        await db.execute(
            select(BalanceTransfer).where(
                BalanceTransfer.tenant_id == tid, BalanceTransfer.idempotency_key == data.idempotency_key
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != fingerprint or existing.sender_id != data.target_user_id or existing.player_id != current.id:
            raise Conflict("This idempotency key was already used for a different request", code="idempotency_conflict")
        username = await db.scalar(
            select(TenantUser.username).where(TenantUser.tenant_id == tid, TenantUser.id == existing.sender_id)
        )
        return WalletRecallOut(
            id=existing.id,
            reference=existing.reference,
            target_user_id=existing.sender_id,
            target_username=username or "",
            amount=existing.amount,
            account_balance_after=existing.sender_balance_after,
            admin_balance_after=existing.player_balance_after,
            replayed=True,
        )

    target = await _upline(db, tid, data.target_user_id)
    source = await _wallet_row(db, tid, target.id, lock=False)
    if source is None:
        raise Conflict("That account does not have enough balance", code="upline_wallet_short", details={"available": "0.00"})
    await db.execute(
        insert(PlayWallet)
        .values(tenant_id=tid, user_id=current.id, balance=ZERO, version=0)
        .on_conflict_do_nothing(constraint="uq_play_wallets_tenant_user")
    )
    admin = await _wallet_row(db, tid, current.id, lock=False)
    assert admin is not None
    first, second = sorted((source, admin), key=lambda wallet: wallet.id.int)
    locked = {row.user_id: await _wallet_row(db, tid, row.user_id, lock=True) for row in (first, second)}
    source_w = locked[target.id]
    admin_w = locked[current.id]
    assert source_w is not None and admin_w is not None
    if source_w.balance < data.amount:
        raise Conflict(
            "That account does not have enough balance",
            code="upline_wallet_short",
            details={"available": str(source_w.balance)},
        )
    if admin_w.balance + data.amount > MAX_WALLET_BALANCE:
        raise Conflict("The transfer would exceed the maximum balance", code="balance_limit")
    recall_id = uuid.uuid4()
    source_before = source_w.balance
    admin_before = admin_w.balance
    memo = data.remarks[:200]
    if not await _post(db, source_w, WalletEntryKind.TRANSFER, -data.amount, f"recall:{recall_id}:out", memo=memo):
        raise Conflict("This transfer was already posted", code="duplicate_grant")
    if not await _post(db, admin_w, WalletEntryKind.TRANSFER, data.amount, f"recall:{recall_id}:in", memo=memo):
        raise Conflict("This transfer was already posted", code="duplicate_grant")
    row = BalanceTransfer(
        id=recall_id,
        tenant_id=tid,
        reference=_reference(),
        sender_id=target.id,
        player_id=current.id,
        amount=data.amount,
        sender_balance_before=source_before,
        sender_balance_after=source_w.balance,
        player_balance_before=admin_before,
        player_balance_after=admin_w.balance,
        status="applied",
        idempotency_key=data.idempotency_key,
        request_hash=fingerprint,
    )
    db.add(row)
    await db.flush()
    await audit.record(
        db,
        actor=current.actor,
        tenant_id=tid,
        action="balance.wallet_recall",
        resource_type="balance_transfer",
        resource_id=row.id,
        changes={
            "admin_id": str(current.id),
            "recipient_id": str(target.id),
            "amount": str(data.amount),
            "account_balance": [str(source_before), str(source_w.balance)],
            "admin_balance": [str(admin_before), str(admin_w.balance)],
        },
        meta=meta,
    )
    publish_to_users_after_commit(
        db,
        tid,
        "balance.updated",
        {"purpose": "transfer", "status": "applied", "amount": str(data.amount)},
        {current.id, target.id},
    )
    await notify_staff(db, tid, P.POINTS_VIEW_ALL, "betting.ledger_updated", {"kind": "transfer"})
    return WalletRecallOut(
        id=row.id,
        reference=row.reference,
        target_user_id=target.id,
        target_username=target.username,
        amount=row.amount,
        account_balance_after=source_w.balance,
        admin_balance_after=admin_w.balance,
        replayed=False,
    )



async def upline_returns(db: AsyncSession, current: CurrentUser, params: PageParams) -> Page[UplineReturnRow]:
    """Posted withdrawals; Loaders see only receipts into their own allocation."""
    authorize_wallet_operator(current)
    if current.user.account_type == AccountType.LOADER and not current.has(P.LOADER_HISTORY):
        raise Forbidden("Loader history is not available", code="balance_forbidden")
    tid = current.tenant.id
    account = aliased(TenantUser)
    admin = aliased(TenantUser)
    stmt = (
        select(BalanceTransfer, account.username, admin.username)
        .join(account, (account.id == BalanceTransfer.sender_id) & (account.tenant_id == tid))
        .join(admin, (admin.id == BalanceTransfer.player_id) & (admin.tenant_id == tid))
        .where(
            BalanceTransfer.tenant_id == tid,
            account.account_type.in_(UPLINE),
            admin.account_type.in_((AccountType.ADMIN, AccountType.LOADER)),
        )
    )
    if current.user.account_type == AccountType.LOADER:
        stmt = stmt.where(BalanceTransfer.player_id == current.id)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(BalanceTransfer.reference.ilike(like), account.username.ilike(like), admin.username.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(BalanceTransfer.created_at.desc(), BalanceTransfer.id.desc())
            .offset(params.offset)
            .limit(params.page_size)
        )
    ).all()
    keys = [f"recall:{row.id}:out" for row, _, _ in rows]
    memos: dict[str, str | None] = {}
    if keys:
        memos = {
            key: memo
            for key, memo in (
                await db.execute(
                    select(PlayWalletEntry.idempotency_key, PlayWalletEntry.memo).where(
                        PlayWalletEntry.tenant_id == tid, PlayWalletEntry.idempotency_key.in_(keys)
                    )
                )
            ).all()
        }
    return Page(
        items=[
            UplineReturnRow(
                id=row.id,
                created_at=row.created_at,
                reference=row.reference,
                account_id=row.sender_id,
                account_username=account_name,
                admin_id=row.player_id,
                admin_username=admin_name,
                amount=row.amount,
                account_balance_after=row.sender_balance_after,
                admin_balance_after=row.player_balance_after,
                remarks=memos.get(f"recall:{row.id}:out"),
            )
            for row, account_name, admin_name in rows
        ],
        total=int(total),
        page=params.page,
        page_size=params.page_size,
    )


async def earnings_to_wallet(
    db: AsyncSession, current: CurrentUser, data: EarningsToWalletIn, meta: RequestMeta
) -> EarningsToWalletOut:
    """Move available commission into the selected account’s wallet; the actor’s wallet is unchanged."""
    authorize_wallet_operator(current)
    await _write_gate(db, current, wallet_operator=True)
    tid = current.tenant.id
    fingerprint = _fingerprint(current, "earnings_to_wallet", data)
    await _lock(db, tid, "key:" + data.idempotency_key)
    from app.modules.commissions.models import EarningsMove
    from app.modules.commissions.moves import _available_earnings

    existing = (
        await db.execute(
            select(EarningsMove).where(EarningsMove.tenant_id == tid, EarningsMove.idempotency_key == data.idempotency_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != fingerprint or existing.user_id != data.target_user_id:
            raise Conflict("This idempotency key was already used for a different request", code="idempotency_conflict")
        username = await db.scalar(
            select(TenantUser.username).where(TenantUser.tenant_id == tid, TenantUser.id == existing.user_id)
        )
        return EarningsToWalletOut(
            id=existing.id,
            reference=existing.reference,
            target_user_id=existing.user_id,
            target_username=username or "",
            amount=existing.amount,
            earnings_before=existing.earnings_before,
            earnings_after=existing.earnings_after,
            wallet_after=existing.wallet_after,
            replayed=True,
        )

    target = await _upline(db, tid, data.target_user_id)
    await _lock(db, tid, f"earnings:{target.id}")
    available = await _available_earnings(db, tid, target.id, target.account_type)
    if data.amount > available:
        raise Conflict(
            "That amount is more than the available commission earnings",
            code="earnings_exceeded",
            details={"available": str(available)},
        )
    wallet = await wallet_for(db, tid, target.id, lock=True)
    if wallet.balance + data.amount > MAX_WALLET_BALANCE:
        raise Conflict("The transfer would exceed the maximum balance", code="balance_limit")
    move_id = uuid.uuid4()
    if not await _post(
        db,
        wallet,
        WalletEntryKind.EARNINGS,
        data.amount,
        f"earnings:{move_id}",
        memo=data.remarks[:200],
    ):
        raise Conflict("This transfer was already posted", code="duplicate_grant")
    row = EarningsMove(
        id=move_id,
        tenant_id=tid,
        reference=_reference(),
        user_id=target.id,
        amount=data.amount,
        earnings_before=available,
        earnings_after=available - data.amount,
        wallet_after=wallet.balance,
        idempotency_key=data.idempotency_key,
        request_hash=fingerprint,
    )
    db.add(row)
    await db.flush()
    await audit.record(
        db,
        actor=current.actor,
        tenant_id=tid,
        action="balance.earnings_to_wallet",
        resource_type="earnings_move",
        resource_id=row.id,
        changes={
            "admin_id": str(current.id),
            "recipient_id": str(target.id),
            "amount": str(data.amount),
            "remarks": data.remarks,
            "earnings": [str(available), str(available - data.amount)],
            "wallet": [None, str(wallet.balance)],
        },
        meta=meta,
    )
    publish_to_users_after_commit(
        db,
        tid,
        "balance.updated",
        {"purpose": "convert", "status": "merged", "amount": str(data.amount)},
        {current.id, target.id},
    )
    await notify_staff(db, tid, P.POINTS_VIEW_ALL, "betting.ledger_updated", {"kind": "earnings"})
    return EarningsToWalletOut(
        id=row.id,
        reference=row.reference,
        target_user_id=target.id,
        target_username=target.username,
        amount=row.amount,
        earnings_before=row.earnings_before,
        earnings_after=row.earnings_after,
        wallet_after=row.wallet_after,
        replayed=False,
    )
