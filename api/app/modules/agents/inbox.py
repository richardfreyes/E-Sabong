"""My Inbox: a player asks their upline to take play points back, and the upline approves or denies it.

Approving debits the player's wallet and credits the direct upline by the same amount. Earnings are
never read. The request is not a cash payment.
"""

import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import ConfigDict, Field
from sqlalchemy import Numeric, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.deps import CurrentUser
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.security import utcnow
from app.modules.agents.service import is_in_downline
from app.modules.agents.transfers import _lock_wallets
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.auth.models import AccountStatus
from app.modules.betting.models import PlayWallet, WalletEntryKind
from app.modules.betting.service import _post
from app.modules.realtime.publisher import publish_after_commit
from app.modules.users.models import AccountType, TenantUser
from app.shared.models import Base, CreatedAt, TenantScoped, UUIDPrimaryKey
from app.shared.schemas import Schema

ZERO = Decimal("0.00")
MONEY = Numeric(14, 2)
Kind = Literal["cashout"]
Status = Literal["pending", "approved", "denied"]


class InboxRequest(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    __tablename__ = "inbox_requests"

    player_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    upline_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default="pending")
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    decided_at: Mapped[datetime | None] = mapped_column(default=None)
    idempotency_key: Mapped[str] = mapped_column(nullable=False)
    request_hash: Mapped[str] = mapped_column(nullable=False)


class InboxIn(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    amount: Annotated[Decimal, Field(gt=0, le=Decimal("1000000.00"), max_digits=9, decimal_places=2)]
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]


class DecisionIn(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    decision: Literal["approve", "deny"]


class InboxItem(Schema):
    id: uuid.UUID
    player_id: uuid.UUID
    player_username: str
    upline_id: uuid.UUID
    upline_username: str
    kind: Kind
    amount: Decimal
    status: Status
    created_at: datetime
    decided_at: datetime | None = None
    replayed: bool = False


class InboxCounts(Schema):
    cashout: int
    commission: int


def _require_player(actor: CurrentUser) -> None:
    inactive = actor.user.status != AccountStatus.ACTIVE or actor.user.deleted_at is not None
    if actor.user.account_type != AccountType.PLAYER or inactive:
        raise Forbidden("Only an active player can send a request", code="players_only")


def _require_agent(actor: CurrentUser) -> None:
    if actor.user.account_type not in (AccountType.MASTER_AGENT, AccountType.AGENT):
        raise Forbidden("Only a master agent or agent can review requests", code="agents_only")
    if actor.user.status != AccountStatus.ACTIVE or actor.user.deleted_at is not None:
        raise Forbidden("This account cannot review requests", code="agents_only")


def _fingerprint(actor_id: uuid.UUID, amount: Decimal) -> str:
    raw = json.dumps({"actor": str(actor_id), "amount": f"{amount:.2f}"}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _item(row: InboxRequest, player_name: str, upline_name: str, *, replayed: bool = False) -> InboxItem:
    return InboxItem(
        id=row.id,
        player_id=row.player_id,
        player_username=player_name,
        upline_id=row.upline_id,
        upline_username=upline_name,
        kind="cashout",
        amount=row.amount,
        status=row.status,  # type: ignore[arg-type]
        created_at=row.created_at,
        decided_at=row.decided_at,
        replayed=replayed,
    )


async def _names(db: AsyncSession, tenant_id: uuid.UUID, *ids: uuid.UUID) -> dict[uuid.UUID, str]:
    rows = (
        await db.execute(
            select(TenantUser.id, TenantUser.username).where(TenantUser.tenant_id == tenant_id, TenantUser.id.in_(ids))
        )
    ).all()
    return {row.id: row.username for row in rows}


async def _wallet_balance(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Decimal:
    balance = (
        await db.execute(select(PlayWallet.balance).where(PlayWallet.tenant_id == tenant_id, PlayWallet.user_id == user_id))
    ).scalar_one_or_none()
    return balance if balance is not None else ZERO


async def _pending_total(db: AsyncSession, tenant_id: uuid.UUID, player_id: uuid.UUID) -> Decimal:
    total = (
        await db.execute(
            select(func.coalesce(func.sum(InboxRequest.amount), 0)).where(
                InboxRequest.tenant_id == tenant_id,
                InboxRequest.player_id == player_id,
                InboxRequest.status == "pending",
            )
        )
    ).scalar_one()
    return Decimal(total)


def _notify(db: AsyncSession, tenant_id: uuid.UUID, user_ids: set[uuid.UUID], payload: dict[str, str]) -> None:
    for user_id in user_ids:
        publish_after_commit(db, tenant_id, "inbox.updated", payload, user_id=user_id)


async def submit(db: AsyncSession, actor: CurrentUser, amount: Decimal, idempotency_key: str, meta: RequestMeta) -> InboxItem:
    """Ask the direct upline to take ``amount`` play points back. One open request at a time."""
    _require_player(actor)
    tenant_id = actor.tenant.id
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"inbox:{tenant_id}:{actor.id}"},
    )
    fingerprint = _fingerprint(actor.id, amount)
    existing = (
        await db.execute(
            select(InboxRequest).where(InboxRequest.tenant_id == tenant_id, InboxRequest.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()
    parent = (
        (
            await db.execute(
                select(TenantUser).where(
                    TenantUser.tenant_id == tenant_id,
                    TenantUser.id == actor.user.parent_id,
                    TenantUser.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if actor.user.parent_id
        else None
    )
    if existing is not None:
        if existing.request_hash != fingerprint or existing.player_id != actor.id:
            raise Conflict("This idempotency key was already used for a different request", code="idempotency_conflict")
        names = await _names(db, tenant_id, existing.player_id, existing.upline_id)
        return _item(existing, names[existing.player_id], names[existing.upline_id], replayed=True)
    upline_ok = parent is not None and parent.account_type in (AccountType.AGENT, AccountType.MASTER_AGENT)
    if not upline_ok or parent.status != AccountStatus.ACTIVE:  # type: ignore[union-attr]
        raise Unprocessable("An active agent has to be your upline before you can send a request", code="no_upline")
    if await _pending_total(db, tenant_id, actor.id) > 0:
        raise Conflict("You already have a request waiting for your agent", code="request_open")
    available = await _wallet_balance(db, tenant_id, actor.id)
    if amount > available:
        raise Unprocessable(
            "Not enough wallet points for this request. Earnings cannot be requested.",
            code="insufficient_balance",
            details={"balance": str(available), "requested": str(amount)},
        )
    row = InboxRequest(
        tenant_id=tenant_id,
        player_id=actor.id,
        upline_id=parent.id,
        kind="cashout",
        amount=amount,
        status="pending",
        idempotency_key=idempotency_key,
        request_hash=fingerprint,
    )
    db.add(row)
    await db.flush()
    await audit.record(
        db,
        actor=actor.actor,
        action="inbox.requested",
        resource_type="inbox_request",
        resource_id=row.id,
        tenant_id=tenant_id,
        changes={"amount": str(amount), "upline_id": str(parent.id)},
        meta=meta,
    )
    notify = {parent.id}
    if parent.parent_id is not None:
        notify.add(parent.parent_id)
    _notify(
        db,
        tenant_id,
        notify,
        {
            "id": str(row.id),
            "status": "pending",
            "player_id": str(actor.id),
            "player_username": actor.user.username,
            "amount": str(amount),
        },
    )
    return _item(row, actor.user.username, parent.username)


async def mine(db: AsyncSession, actor: CurrentUser) -> list[InboxItem]:
    _require_player(actor)
    rows = (
        (
            await db.execute(
                select(InboxRequest)
                .where(InboxRequest.tenant_id == actor.tenant.id, InboxRequest.player_id == actor.id)
                .order_by(InboxRequest.created_at.desc())
                .limit(8)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return []
    names = await _names(db, actor.tenant.id, actor.id, *[row.upline_id for row in rows])
    return [_item(row, names[row.player_id], names[row.upline_id]) for row in rows]


async def _visible(db: AsyncSession, actor: CurrentUser, row: InboxRequest) -> bool:
    if row.upline_id == actor.id:
        return True
    if actor.user.account_type == AccountType.MASTER_AGENT:
        return await is_in_downline(db, actor.tenant.id, actor.id, row.player_id)
    return False


async def counts(db: AsyncSession, actor: CurrentUser) -> InboxCounts:
    _require_agent(actor)
    rows = await pending(db, actor)
    return InboxCounts(cashout=len(rows), commission=0)


async def pending(db: AsyncSession, actor: CurrentUser) -> list[InboxItem]:
    """Open requests this caller may decide. A master agent also sees requests under their agents."""
    _require_agent(actor)
    tenant_id = actor.tenant.id
    scope = InboxRequest.upline_id == actor.id
    if actor.user.account_type == AccountType.MASTER_AGENT:
        from app.modules.agents.service import downline_ids_select

        scope = or_(
            InboxRequest.upline_id == actor.id,
            InboxRequest.player_id.in_(downline_ids_select(tenant_id, actor.id, "inbox_players")),
        )
    rows = (
        (
            await db.execute(
                select(InboxRequest)
                .where(InboxRequest.tenant_id == tenant_id, InboxRequest.status == "pending", scope)
                .order_by(InboxRequest.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return []
    ids = [row.player_id for row in rows] + [row.upline_id for row in rows]
    names = await _names(db, tenant_id, *ids)
    return [_item(row, names[row.player_id], names[row.upline_id]) for row in rows]


async def decide(
    db: AsyncSession, actor: CurrentUser, request_id: uuid.UUID, decision: Literal["approve", "deny"], meta: RequestMeta
) -> InboxItem:
    _require_agent(actor)
    tenant_id = actor.tenant.id
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"inbox-decision:{tenant_id}:{request_id}"},
    )
    row = (
        await db.execute(
            select(InboxRequest).where(InboxRequest.tenant_id == tenant_id, InboxRequest.id == request_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or not await _visible(db, actor, row):
        raise NotFound("Request not found")
    if row.status != "pending":
        names = await _names(db, tenant_id, row.player_id, row.upline_id)
        return _item(row, names[row.player_id], names[row.upline_id], replayed=True)
    if decision == "approve":
        player_wallet, upline_wallet = await _lock_wallets(db, tenant_id, row.player_id, row.upline_id)
        if player_wallet.balance < row.amount:
            raise Unprocessable(
                "The player no longer has enough wallet points.",
                code="insufficient_balance",
                details={"balance": str(player_wallet.balance), "requested": str(row.amount)},
            )
        memo = f"Inbox request to {row.upline_id}"[:200]
        await _post(db, player_wallet, WalletEntryKind.TRANSFER, -row.amount, f"inbox:{row.id}:out", memo=memo)
        await _post(db, upline_wallet, WalletEntryKind.TRANSFER, row.amount, f"inbox:{row.id}:in", memo=memo)
        row.status = "approved"
    else:
        row.status = "denied"
    row.decided_by_id = actor.id
    row.decided_at = utcnow()
    await audit.record(
        db,
        actor=actor.actor,
        action="inbox.decided",
        resource_type="inbox_request",
        resource_id=row.id,
        tenant_id=tenant_id,
        changes={"decision": decision, "amount": str(row.amount), "player_id": str(row.player_id)},
        meta=meta,
    )
    names = await _names(db, tenant_id, row.player_id, row.upline_id)
    _notify(
        db,
        tenant_id,
        {row.player_id, row.upline_id, actor.id},
        {"id": str(row.id), "status": row.status, "player_id": str(row.player_id)},
    )
    return _item(row, names[row.player_id], names[row.upline_id])
