"""Balance requests and moving one's own commission into one's own play wallet.

A request does not post anything. Every Loader and administrator in the tenant can decide it:

* a Loader approval spends that Loader's allocated play balance (the existing grant);
* an administrator approval adds the amount with the existing account grant.

An earnings move debits available commission and credits the same account's play wallet by the
same amount, in one transaction. It does not touch any other account.
"""

import hashlib
import json
import secrets
import uuid
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.deps import CurrentUser
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.security import utcnow
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.balances.models import MAX_WALLET_BALANCE
from app.modules.balances.schemas import AccountGrantIn
from app.modules.balances.service import account_grant
from app.modules.agents.service import is_direct
from app.modules.betting.models import PlayWallet, PlayWalletEntry, WalletEntryKind
from app.modules.betting.service import _post, wallet_for
from app.modules.commissions.models import BalanceRequest, BalanceRequestStatus, EarningsMove
from app.modules.commissions.schemas import (
    ApproverOut,
    BalanceRequestIn,
    BalanceRequestOut,
    DecisionIn,
    EarningsMoveIn,
    EarningsMoveOut,
    LoadIn,
    LoadOut,
)
from app.modules.commissions.service import _require_agent
from app.modules.loader.schemas import GrantIn
from app.modules.loader.service import grant as loader_grant
from app.modules.notifications.service import notify
from app.modules.realtime.publisher import publish_to_users_after_commit
from app.modules.users.models import AccountStatus, AccountType, TenantUser

APPROVERS = frozenset({AccountType.LOADER, AccountType.ADMIN})
REQUESTERS = frozenset({AccountType.MASTER_AGENT, AccountType.AGENT})


def _reference(prefix: str) -> str:
    return f"{prefix}-{utcnow():%Y%m%d}-{secrets.token_hex(4).upper()}"


def _fingerprint(actor: CurrentUser, action: str, body: dict) -> str:
    raw = json.dumps({"actor": str(actor.id), "action": action, "request": body}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


async def _lock(db: AsyncSession, key: str) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})


def _out(row: BalanceRequest, requester: str, *, replayed: bool = False) -> BalanceRequestOut:
    return BalanceRequestOut(
        id=row.id,
        created_at=row.created_at,
        reference=row.reference,
        requester_id=row.requester_id,
        requester_username=requester,
        amount=row.amount,
        purpose=row.purpose,  # type: ignore[arg-type]
        status=row.status.value,  # type: ignore[arg-type]
        decided_at=row.decided_at,
        replayed=replayed,
    )


async def approvers(db: AsyncSession, actor: CurrentUser) -> list[ApproverOut]:
    _require_agent(actor)
    rows = (
        await db.execute(
            select(TenantUser)
            .where(
                TenantUser.tenant_id == actor.tenant.id,
                TenantUser.deleted_at.is_(None),
                TenantUser.status == AccountStatus.ACTIVE,
                TenantUser.account_type.in_(APPROVERS),
            )
            .order_by(TenantUser.username)
        )
    ).scalars()
    return [
        ApproverOut(id=user.id, username=user.username, full_name=user.full_name, account_type=user.account_type.value)
        for user in rows
    ]


async def list_requests(db: AsyncSession, actor: CurrentUser) -> list[BalanceRequestOut]:
    _require_agent(actor)
    requester = aliased(TenantUser)
    rows = (
        await db.execute(
            select(BalanceRequest, requester.username)
            .join(requester, (requester.tenant_id == BalanceRequest.tenant_id) & (requester.id == BalanceRequest.requester_id))
            .where(BalanceRequest.tenant_id == actor.tenant.id, BalanceRequest.requester_id == actor.id)
            .order_by(BalanceRequest.created_at.desc())
            .limit(30)
        )
    ).all()
    return [_out(row, requester_name) for row, requester_name in rows]


async def create_request(
    db: AsyncSession, actor: CurrentUser, data: BalanceRequestIn, meta: RequestMeta
) -> BalanceRequestOut:
    _require_agent(actor)
    if actor.user.status != AccountStatus.ACTIVE:
        raise Forbidden("This account cannot request a balance", code="not_active")
    tid = actor.tenant.id
    fingerprint = _fingerprint(actor, "balance_request", data.model_dump(mode="json", exclude={"idempotency_key"}))
    await _lock(db, f"balance-request:{tid}:{data.idempotency_key}")
    existing = (
        await db.execute(
            select(BalanceRequest).where(BalanceRequest.tenant_id == tid, BalanceRequest.idempotency_key == data.idempotency_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != fingerprint or existing.requester_id != actor.id:
            raise Conflict("This idempotency key was already used for a different request", code="idempotency_conflict")
        return _out(existing, actor.user.username, replayed=True)

    row = BalanceRequest(
        tenant_id=tid,
        reference=_reference("BRQ"),
        requester_id=actor.id,
        amount=data.amount,
        purpose=data.purpose,
        status=BalanceRequestStatus.PENDING,
        idempotency_key=data.idempotency_key,
        request_hash=fingerprint,
    )
    if data.purpose == "cashout":
        held = await _wallet_balance(db, tid, actor.id)
        if data.amount > held:
            raise Conflict("That amount is more than your main wallet", code="insufficient_balance", details={"balance": str(held)})
    elif data.purpose == "earnings":
        available = await _available_earnings(db, tid, actor.id, actor.user.account_type)
        if data.amount > available:
            raise Conflict(
                "That amount is more than the available commission earnings",
                code="earnings_exceeded",
                details={"available": str(available)},
            )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    if data.purpose == "cashout":
        await _debit_main_wallet(db, tid, actor.id, data.amount, f"balance-request:{row.id}:hold", memo="Main wallet cashout requested")
    await audit.record(
        db,
        actor=actor.actor,
        action="balance_request.created",
        resource_type="balance_request",
        resource_id=row.id,
        tenant_id=tid,
        changes={"amount": [None, str(data.amount)], "purpose": [None, data.purpose]},
        meta=meta,
    )
    await _notify_approvers(db, actor, row, data.purpose)
    await _announce(db, tid, actor.id, purpose=data.purpose, status="pending", amount=row.amount, request_id=row.id)
    return _out(row, actor.user.username)


async def _notify_approvers(db: AsyncSession, actor: CurrentUser, row: BalanceRequest, purpose: str) -> None:
    """Every active Loader and Super Admin (tenant administrator) is told to approve."""
    approver_ids = (
        await db.execute(
            select(TenantUser.id).where(
                TenantUser.tenant_id == actor.tenant.id,
                TenantUser.deleted_at.is_(None),
                TenantUser.status == AccountStatus.ACTIVE,
                TenantUser.account_type.in_(APPROVERS),
            )
        )
    ).scalars()
    if purpose == "cashout":
        title = f"Cashout from {actor.user.username} needs approval"
        body = f"{actor.user.username} asked to cash out {row.amount} from the main wallet."
    elif purpose == "earnings":
        title = f"Earnings withdrawal from {actor.user.username} needs approval"
        body = f"{actor.user.username} asked to withdraw {row.amount} of earnings."
    else:
        title = f"{actor.user.username} requested {row.amount}"
        body = f"{actor.user.username} asked for {row.amount} play balance."
    for user_id in approver_ids:
        await notify(
            db,
            tenant_id=actor.tenant.id,
            user_id=user_id,
            type="balance.approval_required",
            title=title,
            body=body,
            data={"request_id": str(row.id), "purpose": purpose},
        )


async def inbox(db: AsyncSession, actor: CurrentUser) -> list[BalanceRequestOut]:
    if actor.user.account_type not in APPROVERS:
        raise Forbidden("Only a Loader or an administrator reviews balance requests", code="balance_request_approver_only")
    requester = aliased(TenantUser)
    rows = (
        await db.execute(
            select(BalanceRequest, requester.username)
            .join(requester, (requester.tenant_id == BalanceRequest.tenant_id) & (requester.id == BalanceRequest.requester_id))
            .where(BalanceRequest.tenant_id == actor.tenant.id)
            .order_by(BalanceRequest.created_at.desc())
            .limit(50)
        )
    ).all()
    return [_out(row, requester_name) for row, requester_name in rows]


async def _wallet_balance(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Decimal:
    wallet = (
        await db.execute(
            select(PlayWallet).where(PlayWallet.tenant_id == tenant_id, PlayWallet.user_id == user_id).with_for_update()
        )
    ).scalar_one_or_none()
    return wallet.balance if wallet is not None else Decimal("0")


async def _debit_main_wallet(
    db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, amount: Decimal, key: str, *, memo: str = "Main wallet cashout approved"
) -> None:
    wallet = (
        await db.execute(
            select(PlayWallet).where(PlayWallet.tenant_id == tenant_id, PlayWallet.user_id == user_id).with_for_update()
        )
    ).scalar_one_or_none()
    held = wallet.balance if wallet is not None else Decimal("0")
    if wallet is None or held < amount:
        raise Conflict("That amount is more than the main wallet", code="insufficient_balance", details={"balance": str(held)})
    await _post(db, wallet, WalletEntryKind.TRANSFER, -amount, key, memo=memo)


async def _release_cashout(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, amount: Decimal, request_id: uuid.UUID) -> None:
    """Put a cashout back when it is declined. Approval leaves the hold in place."""
    hold = (
        await db.execute(
            select(PlayWalletEntry.id).where(
                PlayWalletEntry.tenant_id == tenant_id, PlayWalletEntry.idempotency_key == f"balance-request:{request_id}:hold"
            )
        )
    ).first()
    if hold is None:
        return
    wallet = (
        await db.execute(select(PlayWallet).where(PlayWallet.tenant_id == tenant_id, PlayWallet.user_id == user_id).with_for_update())
    ).scalar_one_or_none()
    if wallet is None:
        return
    await _post(
        db, wallet, WalletEntryKind.TRANSFER, amount, f"balance-request:{request_id}:release", memo="Cashout declined",
    )


async def _announce(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    requester_id: uuid.UUID,
    *,
    purpose: str,
    status: str,
    amount: Decimal,
    request_id: uuid.UUID | None = None,
) -> None:
    """Tell the account, and every approver, so open screens refresh the balance and the log."""
    audience = {requester_id}
    audience.update(
        (
            await db.execute(
                select(TenantUser.id).where(
                    TenantUser.tenant_id == tenant_id,
                    TenantUser.deleted_at.is_(None),
                    TenantUser.status == AccountStatus.ACTIVE,
                    TenantUser.account_type.in_(APPROVERS),
                )
            )
        ).scalars()
    )
    data: dict[str, str] = {"purpose": purpose, "status": status, "amount": str(amount)}
    if request_id is not None:
        data["request_id"] = str(request_id)
    publish_to_users_after_commit(db, tenant_id, "balance.updated", data, audience)


async def _available_earnings(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    account_type: AccountType,
    *,
    ignore_request: uuid.UUID | None = None,
) -> Decimal:
    from app.modules.commissions.models import CommissionEntry

    if account_type == AccountType.MASTER_AGENT:
        where, mine = CommissionEntry.master_agent_id == user_id, CommissionEntry.master_points
    else:
        where, mine = CommissionEntry.agent_id == user_id, CommissionEntry.agent_points
    earned = Decimal(
        await db.scalar(select(func.coalesce(func.sum(mine), 0)).where(CommissionEntry.tenant_id == tenant_id, where)) or 0
    )
    moved = Decimal(
        await db.scalar(
            select(func.coalesce(func.sum(EarningsMove.amount), 0)).where(
                EarningsMove.tenant_id == tenant_id, EarningsMove.user_id == user_id
            )
        )
        or 0
    )
    pending_stmt = select(func.coalesce(func.sum(BalanceRequest.amount), 0)).where(
        BalanceRequest.tenant_id == tenant_id,
        BalanceRequest.requester_id == user_id,
        BalanceRequest.purpose == "earnings",
        BalanceRequest.status == BalanceRequestStatus.PENDING,
    )
    if ignore_request is not None:
        pending_stmt = pending_stmt.where(BalanceRequest.id != ignore_request)
    pending = Decimal(await db.scalar(pending_stmt) or 0)
    return earned - moved - pending


async def _take_earnings(
    db: AsyncSession, tenant_id: uuid.UUID, user: TenantUser, amount: Decimal, key: str, request_id: uuid.UUID
) -> None:
    """Remove commission without adding it to the main wallet. Convert does that separately."""
    await _lock(db, f"earnings:{tenant_id}:{user.id}")
    already = (
        await db.execute(select(EarningsMove.id).where(EarningsMove.tenant_id == tenant_id, EarningsMove.idempotency_key == key))
    ).first()
    if already is not None:
        return
    available = await _available_earnings(db, tenant_id, user.id, user.account_type, ignore_request=request_id)
    if amount > available:
        raise Conflict(
            "That amount is more than the available commission earnings",
            code="earnings_exceeded",
            details={"available": str(available)},
        )
    db.add(
        EarningsMove(
            tenant_id=tenant_id,
            reference=_reference("ERN"),
            user_id=user.id,
            amount=amount,
            earnings_before=available,
            earnings_after=available - amount,
            wallet_after=await _wallet_balance(db, tenant_id, user.id),
            idempotency_key=key,
            request_hash=hashlib.sha256(f"earnings-withdraw:{key}".encode()).hexdigest(),
        )
    )
    await db.flush()


async def decide(
    db: AsyncSession, actor: CurrentUser, request_id: uuid.UUID, data: DecisionIn, meta: RequestMeta
) -> BalanceRequestOut:
    if actor.user.account_type not in APPROVERS:
        raise Forbidden("Only a Loader or an administrator reviews balance requests", code="balance_request_approver_only")
    tid = actor.tenant.id
    await _lock(db, f"balance-request-decision:{tid}:{request_id}")
    row = (
        await db.execute(
            select(BalanceRequest)
            .where(BalanceRequest.tenant_id == tid, BalanceRequest.id == request_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Balance request not found")
    requester_user = (
        await db.execute(select(TenantUser).where(TenantUser.tenant_id == tid, TenantUser.id == row.requester_id))
    ).scalar_one()
    requester = requester_user.username
    if row.status != BalanceRequestStatus.PENDING:
        if (data.decision == "approve" and row.status == BalanceRequestStatus.APPROVED) or (
            data.decision == "reject" and row.status == BalanceRequestStatus.REJECTED
        ):
            return _out(row, requester, replayed=True)
        raise Conflict("This request was already decided", code="already_decided")

    if data.decision == "approve":
        note = f"Balance request {row.reference}"
        key = f"balance-request:{row.id}"
        if row.purpose == "cashout":
            held = (
                await db.execute(
                    select(PlayWalletEntry.id).where(
                        PlayWalletEntry.tenant_id == tid, PlayWalletEntry.idempotency_key == f"{key}:hold"
                    )
                )
            ).first()
            if held is None:
                await _debit_main_wallet(db, tid, row.requester_id, row.amount, f"{key}:out")
        elif row.purpose == "earnings":
            await _take_earnings(db, tid, requester_user, row.amount, key, row.id)
        elif actor.user.account_type == AccountType.LOADER:
            await loader_grant(
                db,
                actor,
                GrantIn(target_user_id=row.requester_id, amount=row.amount, reason=note, idempotency_key=key),
                meta,
            )
        else:
            await account_grant(
                db,
                actor,
                AccountGrantIn(target_user_id=row.requester_id, amount=row.amount, remarks=note, idempotency_key=key),
                meta,
            )
        row.status = BalanceRequestStatus.APPROVED
        action = "balance_request.approved"
    else:
        if row.purpose == "cashout":
            await _release_cashout(db, tid, row.requester_id, row.amount, row.id)
        row.status = BalanceRequestStatus.REJECTED
        action = "balance_request.rejected"
    row.approver_id = actor.id
    row.decided_at = utcnow()
    row.decided_by_id = actor.id
    await audit.record(
        db,
        actor=actor.actor,
        action=action,
        resource_type="balance_request",
        resource_id=row.id,
        tenant_id=tid,
        changes={"status": ["pending", row.status.value], "amount": [None, str(row.amount)]},
        meta=meta,
    )
    await _announce(db, tid, row.requester_id, purpose=row.purpose, status=row.status.value, amount=row.amount, request_id=row.id)
    return _out(row, requester)


async def list_moves(db: AsyncSession, actor: CurrentUser) -> list[EarningsMoveOut]:
    _require_agent(actor)
    rows = (
        await db.execute(
            select(EarningsMove)
            .where(EarningsMove.tenant_id == actor.tenant.id, EarningsMove.user_id == actor.id)
            .order_by(EarningsMove.created_at.desc())
            .limit(30)
        )
    ).scalars()
    return [_move_out(row) for row in rows]


def _move_out(row: EarningsMove, *, replayed: bool = False) -> EarningsMoveOut:
    return EarningsMoveOut(
        id=row.id,
        created_at=row.created_at,
        reference=row.reference,
        amount=row.amount,
        earnings_before=row.earnings_before,
        earnings_after=row.earnings_after,
        wallet_after=row.wallet_after,
        kind="withdrawal" if row.idempotency_key.startswith("balance-request:") else "merge",
        replayed=replayed,
    )


async def move_earnings(db: AsyncSession, actor: CurrentUser, data: EarningsMoveIn, meta: RequestMeta) -> EarningsMoveOut:
    _require_agent(actor)
    if actor.user.account_type not in REQUESTERS or actor.user.status != AccountStatus.ACTIVE:
        raise Forbidden("This account cannot move earnings", code="not_active")
    tid = actor.tenant.id
    fingerprint = _fingerprint(actor, "earnings_move", {"amount": str(data.amount)})
    await _lock(db, f"earnings-move:{tid}:{data.idempotency_key}")
    existing = (
        await db.execute(
            select(EarningsMove).where(EarningsMove.tenant_id == tid, EarningsMove.idempotency_key == data.idempotency_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != fingerprint or existing.user_id != actor.id:
            raise Conflict("This idempotency key was already used for a different transfer", code="idempotency_conflict")
        return _move_out(existing, replayed=True)

    await _lock(db, f"earnings:{tid}:{actor.id}")
    available = await _available_earnings(db, tid, actor.id, actor.user.account_type)
    if data.amount > available:
        raise Conflict(
            "That amount is more than the available commission earnings",
            code="earnings_exceeded",
            details={"available": str(available)},
        )
    wallet = await wallet_for(db, tid, actor.id, lock=True)
    if wallet.balance + data.amount > MAX_WALLET_BALANCE:
        raise Conflict("The transfer would exceed the maximum balance", code="balance_limit")
    move_id = uuid.uuid4()
    posted = await _post(
        db,
        wallet,
        WalletEntryKind.EARNINGS,
        data.amount,
        f"earnings:{move_id}",
        memo="Commission earnings moved to this account's play balance",
    )
    if not posted:
        raise Conflict("This transfer was already posted", code="duplicate_grant")
    row = EarningsMove(
        id=move_id,
        tenant_id=tid,
        reference=_reference("ERN"),
        user_id=actor.id,
        amount=data.amount,
        earnings_before=available,
        earnings_after=available - data.amount,
        wallet_after=wallet.balance,
        idempotency_key=data.idempotency_key,
        request_hash=fingerprint,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    await audit.record(
        db,
        actor=actor.actor,
        action="earnings.moved_to_wallet",
        resource_type="earnings_move",
        resource_id=row.id,
        tenant_id=tid,
        changes={"amount": [str(available), str(available - data.amount)], "wallet": [None, str(wallet.balance)]},
        meta=meta,
    )
    await _announce(db, tid, actor.id, purpose="convert", status="merged", amount=row.amount, request_id=row.id)
    return _move_out(row)


async def load_funds(db: AsyncSession, actor: CurrentUser, data: LoadIn, meta: RequestMeta) -> LoadOut:
    """Move play balance with a direct downline account, or commission into that account's wallet."""
    _require_agent(actor)
    if actor.user.status != AccountStatus.ACTIVE:
        raise Forbidden("This account cannot load funds", code="not_active")
    tid = actor.tenant.id
    if not await is_direct(db, tid, actor.id, data.recipient_id):
        raise NotFound("Account not found")
    recipient = (
        await db.execute(
            select(TenantUser).where(
                TenantUser.tenant_id == tid, TenantUser.id == data.recipient_id, TenantUser.deleted_at.is_(None)
            )
        )
    ).scalar_one()
    if recipient.status != AccountStatus.ACTIVE:
        raise Conflict("Only an active account can be used", code="not_active")
    key = f"load:{data.idempotency_key}"
    await _lock(db, f"{key}:{tid}")
    posted = (
        await db.execute(
            select(PlayWalletEntry).where(PlayWalletEntry.tenant_id == tid, PlayWalletEntry.idempotency_key == f"{key}:in")
        )
    ).scalar_one_or_none()
    if posted is not None:
        wallet = await wallet_for(db, tid, actor.id)
        return LoadOut(
            method=data.method,
            recipient_id=recipient.id,
            recipient_username=recipient.username,
            amount=data.amount,
            balance_after=wallet.balance,
            replayed=True,
        )
    memo = (data.details or data.method)[:200]
    if data.method == "commission":
        await _lock(db, f"earnings:{tid}:{actor.id}")
        available = await _earned(db, actor) - Decimal(
            await db.scalar(
                select(func.coalesce(func.sum(EarningsMove.amount), 0)).where(
                    EarningsMove.tenant_id == tid, EarningsMove.user_id == actor.id
                )
            )
            or 0
        )
        if data.amount > available:
            raise Conflict("That amount is more than the available commission earnings", code="earnings_exceeded", details={"available": str(available)})
        dest = await wallet_for(db, tid, recipient.id, lock=True)
        if dest.balance + data.amount > MAX_WALLET_BALANCE:
            raise Conflict("The transfer would exceed the maximum balance", code="balance_limit")
        move_id = uuid.uuid4()
        if not await _post(db, dest, WalletEntryKind.EARNINGS, data.amount, f"{key}:in", memo=f"Commission from {actor.user.username}: {memo}"[:200]):
            raise Conflict("This transfer was already posted", code="duplicate_grant")
        db.add(EarningsMove(
            id=move_id, tenant_id=tid, reference=_reference("ERN"), user_id=actor.id, amount=data.amount,
            earnings_before=available, earnings_after=available - data.amount, wallet_after=dest.balance,
            idempotency_key=data.idempotency_key, request_hash=_fingerprint(actor, "load", {"method": data.method, "recipient_id": str(data.recipient_id), "amount": str(data.amount)}),
        ))
        balance_after = dest.balance
    else:
        caller = await wallet_for(db, tid, actor.id)
        other = await wallet_for(db, tid, recipient.id)
        first, second = sorted((caller, other), key=lambda w: w.id.int)
        locked = {first.user_id: await wallet_for(db, tid, first.user_id, lock=True)}
        locked[second.user_id] = await wallet_for(db, tid, second.user_id, lock=True)
        source = locked[recipient.id if data.method == "withdraw" else actor.id]
        dest = locked[actor.id if data.method == "withdraw" else recipient.id]
        if source.balance < data.amount:
            raise Conflict("Not enough play balance", code="insufficient_balance", details={"available": str(source.balance)})
        if dest.balance + data.amount > MAX_WALLET_BALANCE:
            raise Conflict("The transfer would exceed the maximum balance", code="balance_limit")
        if not await _post(db, source, WalletEntryKind.TRANSFER, -data.amount, f"{key}:out", memo=memo):
            raise Conflict("This transfer was already posted", code="duplicate_grant")
        if not await _post(db, dest, WalletEntryKind.TRANSFER, data.amount, f"{key}:in", memo=memo):
            raise Conflict("This transfer was already posted", code="duplicate_grant")
        balance_after = locked[actor.id].balance
    await audit.record(
        db, actor=actor.actor, action=f"wallet.{data.method}", resource_type="tenant_user", resource_id=recipient.id,
        tenant_id=tid, changes={"amount": [None, str(data.amount)], "method": [None, data.method]}, meta=meta,
    )
    return LoadOut(method=data.method, recipient_id=recipient.id, recipient_username=recipient.username, amount=data.amount, balance_after=balance_after)
