"""Move existing wallet points from a master agent or agent to an account in their hierarchy.

An agent can fund a player assigned directly to them. A master agent can fund its own agents and
the players it invited itself, never the players of its agents. The sender's play wallet is debited and the recipient's play
wallet is credited by the same amount, in one transaction, with both wallets locked in id order.
Commission earnings are never read or posted. A repeated idempotency key returns the original
transfer and posts nothing again.
"""

import hashlib
import json
import secrets
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import ConfigDict, Field
from sqlalchemy import Numeric, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, aliased, mapped_column

from app.core.deps import CurrentUser
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.permissions import P
from app.core.security import utcnow
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.auth.models import AccountStatus
from app.modules.balances.models import MAX_WALLET_BALANCE
from app.modules.betting.models import PlayWallet, WalletEntryKind
from app.modules.betting.service import _post, settings_for, starting_grant
from app.modules.realtime.publisher import notify_staff, publish_after_commit
from app.modules.users.models import AccountType, TenantUser
from app.shared.models import Base, CreatedAt, TenantScoped, UUIDPrimaryKey
from app.shared.schemas import Page, PageParams, Schema

ZERO = Decimal("0.00")
MONEY = Numeric(14, 2)


class BalanceTransfer(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    __tablename__ = "balance_transfers"

    reference: Mapped[str] = mapped_column(nullable=False)
    sender_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    sender_balance_before: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    sender_balance_after: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    player_balance_before: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    player_balance_after: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default="applied")
    idempotency_key: Mapped[str] = mapped_column(nullable=False)
    request_hash: Mapped[str] = mapped_column(nullable=False)


class TransferIn(Schema):
    """Amount to move from the caller's wallet. The sender is the signed-in account, never a field."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    amount: Annotated[Decimal, Field(gt=0, le=Decimal("1000000.00"), max_digits=9, decimal_places=2)]
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]


class WalletBalance(Schema):
    balance: Decimal
    version: int


class TransferPreview(Schema):
    player_id: uuid.UUID
    player_username: str
    player_balance: Decimal
    sender_balance: Decimal


class BalanceTransferOut(Schema):
    id: uuid.UUID
    reference: str
    sender_id: uuid.UUID
    sender_username: str
    sender_role: str
    # The player fields are None when a master agent reads a transfer one of its agents sent: the
    # recipient is that agent's player, whom the master agent does not see.
    player_id: uuid.UUID | None
    player_username: str | None
    amount: Decimal
    sender_balance_before: Decimal
    sender_balance_after: Decimal
    player_balance_before: Decimal | None
    player_balance_after: Decimal | None
    status: str
    created_at: datetime
    replayed: bool = False


def _require_sender(actor: CurrentUser) -> None:
    if actor.user.account_type not in (AccountType.MASTER_AGENT, AccountType.AGENT):
        raise Forbidden("Only a master agent or agent can add balance", code="agents_only")
    if actor.user.status != AccountStatus.ACTIVE or actor.user.deleted_at is not None:
        raise Forbidden("This account cannot add balance", code="agents_only")


def _fingerprint(actor: CurrentUser, player_id: uuid.UUID, amount: Decimal) -> str:
    raw = json.dumps(
        {"actor": str(actor.id), "player_id": str(player_id), "amount": f"{amount:.2f}"},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


async def _eligible_player(db: AsyncSession, actor: CurrentUser, player_id: uuid.UUID) -> TenantUser:
    """An account this caller may fund. Anyone else is not found, including other tenants.

    An agent funds only a player whose parent is that agent. A master agent funds its own agents and
    the players it invited itself; its agents' players belong to those agents. Master agents, staff,
    and the caller themselves are not recipients.
    """
    user = (
        await db.execute(
            select(TenantUser).where(
                TenantUser.tenant_id == actor.tenant.id,
                TenantUser.id == player_id,
                TenantUser.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if user is None:
        raise NotFound("Account not found")
    if actor.user.account_type == AccountType.AGENT:
        allowed = user.account_type == AccountType.PLAYER and user.parent_id == actor.id
    elif actor.user.account_type == AccountType.MASTER_AGENT:
        allowed = user.account_type in (AccountType.PLAYER, AccountType.AGENT) and user.parent_id == actor.id
    else:
        allowed = False
    if not allowed:
        raise NotFound("Account not found")
    return user


async def _ensure_wallet(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    await db.execute(
        insert(PlayWallet)
        .values(tenant_id=tenant_id, user_id=user_id, balance=ZERO, version=0)
        .on_conflict_do_nothing(constraint="uq_play_wallets_tenant_user")
    )


async def _lock_wallets(
    db: AsyncSession, tenant_id: uuid.UUID, sender_id: uuid.UUID, player_id: uuid.UUID
) -> tuple[PlayWallet, PlayWallet]:
    """Create missing rows, then lock both in id order so two transfers cannot deadlock."""
    await _ensure_wallet(db, tenant_id, sender_id)
    await _ensure_wallet(db, tenant_id, player_id)
    rows = list(
        (
            await db.execute(
                select(PlayWallet)
                .where(PlayWallet.tenant_id == tenant_id, PlayWallet.user_id.in_((sender_id, player_id)))
                .order_by(PlayWallet.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    by_user = {row.user_id: row for row in rows}
    return by_user[sender_id], by_user[player_id]


def _out(
    row: BalanceTransfer, sender: TenantUser, player: TenantUser, *, replayed: bool = False, hide_player: bool = False
) -> BalanceTransferOut:
    return BalanceTransferOut(
        id=row.id,
        reference=row.reference,
        sender_id=row.sender_id,
        sender_username=sender.username,
        sender_role=sender.account_type.value,
        player_id=None if hide_player else row.player_id,
        player_username=None if hide_player else player.username,
        amount=row.amount,
        sender_balance_before=row.sender_balance_before,
        sender_balance_after=row.sender_balance_after,
        player_balance_before=None if hide_player else row.player_balance_before,
        player_balance_after=None if hide_player else row.player_balance_after,
        status=row.status,
        created_at=row.created_at,
        replayed=replayed,
    )


async def sender_wallet(db: AsyncSession, actor: CurrentUser) -> WalletBalance:
    """The caller's own wallet. Reading it does not grant a starting balance."""
    _require_sender(actor)
    wallet = (
        await db.execute(select(PlayWallet).where(PlayWallet.tenant_id == actor.tenant.id, PlayWallet.user_id == actor.id))
    ).scalar_one_or_none()
    if wallet is None:
        return WalletBalance(balance=ZERO, version=0)
    return WalletBalance(balance=wallet.balance, version=wallet.version)


async def preview(db: AsyncSession, actor: CurrentUser, player_id: uuid.UUID) -> TransferPreview:
    _require_sender(actor)
    player = await _eligible_player(db, actor, player_id)
    mine = await sender_wallet(db, actor)
    player_balance = (
        await db.execute(
            select(PlayWallet.balance).where(PlayWallet.tenant_id == actor.tenant.id, PlayWallet.user_id == player.id)
        )
    ).scalar_one_or_none()
    return TransferPreview(
        player_id=player.id,
        player_username=player.username,
        player_balance=player_balance if player_balance is not None else ZERO,
        sender_balance=mine.balance,
    )


async def transfer(
    db: AsyncSession, actor: CurrentUser, player_id: uuid.UUID, amount: Decimal, idempotency_key: str, meta: RequestMeta
) -> BalanceTransferOut:
    _require_sender(actor)
    if amount <= 0:
        raise Unprocessable("Enter an amount above zero", code="invalid_amount")
    player = await _eligible_player(db, actor, player_id)
    if player.status != AccountStatus.ACTIVE:
        raise Conflict("Only an active account can receive a balance", code="not_active")
    tenant_id = actor.tenant.id
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"balance-transfer:{tenant_id}:{idempotency_key}"},
    )
    fingerprint = _fingerprint(actor, player.id, amount)
    existing = (
        await db.execute(
            select(BalanceTransfer).where(
                BalanceTransfer.tenant_id == tenant_id, BalanceTransfer.idempotency_key == idempotency_key
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise Conflict("This idempotency key was already used for a different transfer", code="idempotency_conflict")
        sender = (
            await db.execute(select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.id == existing.sender_id))
        ).scalar_one()
        return _out(existing, sender, player, replayed=True)

    sender_wallet_row, player_wallet = await _lock_wallets(db, tenant_id, actor.id, player.id)
    # A player's first use still receives the configured starting balance. An agent recipient and
    # the sender do not: a transfer must not create points.
    if (
        player.account_type == AccountType.PLAYER
        and player_wallet.version == 0
        and (await settings_for(db, tenant_id)).starting_balance > 0
    ):
        await starting_grant(db, player_wallet)
    if sender_wallet_row.balance < amount:
        raise Unprocessable(
            "Not enough wallet points. Earnings cannot be transferred.",
            code="insufficient_balance",
            details={"balance": str(sender_wallet_row.balance), "requested": str(amount)},
        )
    if player_wallet.balance + amount > MAX_WALLET_BALANCE:
        raise Conflict("The transfer would exceed the maximum balance", code="balance_limit")
    sender_before = sender_wallet_row.balance
    player_before = player_wallet.balance
    row = BalanceTransfer(
        tenant_id=tenant_id,
        reference=f"XFR-{utcnow():%Y%m%d}-{secrets.token_hex(4).upper()}",
        sender_id=actor.id,
        player_id=player.id,
        amount=amount,
        sender_balance_before=sender_before,
        sender_balance_after=sender_before - amount,
        player_balance_before=player_before,
        player_balance_after=player_before + amount,
        status="applied",
        idempotency_key=idempotency_key,
        request_hash=fingerprint,
    )
    db.add(row)
    await db.flush()
    memo = f"Wallet transfer {row.reference} to {player.username}"[:200]
    await _post(db, sender_wallet_row, WalletEntryKind.TRANSFER, -amount, f"transfer:{row.id}:out", memo=memo)
    await _post(db, player_wallet, WalletEntryKind.TRANSFER, amount, f"transfer:{row.id}:in", memo=memo)
    await audit.record(
        db,
        actor=actor.actor,
        action="balance.transferred",
        resource_type="balance_transfer",
        resource_id=row.id,
        tenant_id=tenant_id,
        changes={
            "player_id": str(player.id),
            "amount": str(amount),
            "sender_balance_after": str(row.sender_balance_after),
            "player_balance_after": str(row.player_balance_after),
        },
        meta=meta,
    )
    payload = {
        "id": str(row.id),
        "reference": row.reference,
        "player_id": str(player.id),
        "amount": str(amount),
        "sender_balance": str(row.sender_balance_after),
        "player_balance": str(row.player_balance_after),
    }
    publish_after_commit(db, tenant_id, "balance.transferred", payload, user_id=actor.id)
    await notify_staff(db, tenant_id, P.POINTS_VIEW_ALL, "betting.ledger_updated", {"kind": "transfer"})
    return _out(row, actor.user, player)


async def history(db: AsyncSession, actor: CurrentUser, params: PageParams) -> Page[BalanceTransferOut]:
    """Transfers this caller sent. A master agent also sees transfers their agents sent, without the
    recipient: an agent's players are not visible to the master agent."""
    _require_sender(actor)
    tenant_id = actor.tenant.id
    scope = BalanceTransfer.sender_id == actor.id
    if actor.user.account_type == AccountType.MASTER_AGENT:
        from app.modules.agents.service import downline_ids_select

        scope = or_(
            BalanceTransfer.sender_id == actor.id,
            BalanceTransfer.sender_id.in_(downline_ids_select(tenant_id, actor.id, "transfer_senders")),
        )
    sender = aliased(TenantUser)
    player = aliased(TenantUser)
    stmt = (
        select(BalanceTransfer, sender, player)
        .join(sender, (sender.id == BalanceTransfer.sender_id) & (sender.tenant_id == tenant_id))
        .join(player, (player.id == BalanceTransfer.player_id) & (player.tenant_id == tenant_id))
        .where(BalanceTransfer.tenant_id == tenant_id, scope)
    )
    from sqlalchemy import func

    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(BalanceTransfer.created_at.desc(), BalanceTransfer.id).offset(params.offset).limit(params.page_size)
        )
    ).all()
    return Page(
        items=[_out(transfer, who, played, hide_player=who.id != actor.id) for transfer, who, played in rows],
        total=int(total),
        page=params.page,
        page_size=params.page_size,
    )
