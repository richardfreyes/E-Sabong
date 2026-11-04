"""Simulated fight betting with play balances.

Play balances are deliberately separate from account points: a wallet is funded only by a
system starting grant, by administrator grants (one player, or the same amount to every
player), by resets, by maker-checker corrections of those administrator postings, and by its
own bet settlements. No account can transfer into another account's wallet, so a play balance
cannot be bought, sold or cashed out through the account hierarchy.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.betting.odds import Side
from app.shared.models import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum

MONEY = Numeric(14, 2)
POOL = Numeric(16, 2)
ODDS = Numeric(20, 10)

BetSide = Side


class BetStatus(StrEnum):
    ACCEPTED = "accepted"
    WON = "won"
    LOST = "lost"
    REFUNDED = "refunded"


class BetPricing(StrEnum):
    POOL = "pool"  # MERON/WALA: pari-mutuel, final multiplier locked when betting closes
    FIXED = "fixed"  # DRAW: multiplier locked when the bet is accepted


class BookState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    SETTLED = "settled"


class WalletEntryKind(StrEnum):
    GRANT = "grant"  # system starting balance
    STAKE = "stake"
    PAYOUT = "payout"
    REFUND = "refund"
    RESETTLEMENT = "resettlement"  # an official result was corrected after settlement
    # Administrator balance management (always linked to a ``balance_operations`` row).
    ACCOUNT_GRANT = "account_grant"  # an amount added to one player
    UNIFORM_GRANT = "uniform_grant"  # the same amount to every eligible participant
    RESET = "reset"  # every eligible participant back to the configured starting balance
    CORRECTION = "correction"  # reviewed reversal of an erroneous administrator posting


# Entry kinds produced by administrator balance management rather than by play.
ADMIN_ENTRY_KINDS = frozenset(
    {WalletEntryKind.ACCOUNT_GRANT, WalletEntryKind.UNIFORM_GRANT, WalletEntryKind.RESET, WalletEntryKind.CORRECTION}
)


class ResetPolicy(StrEnum):
    DISABLED = "disabled"
    STARTING_BALANCE = "starting_balance"  # an administrator may reset every participant to it


class BettingSettings(Base, Timestamps):
    __tablename__ = "betting_settings"
    __table_args__ = (
        CheckConstraint("commission_rate >= 0 AND commission_rate < 0.5", name="commission_range"),
        CheckConstraint("draw_multiplier >= 1", name="draw_multiplier_min"),
        CheckConstraint("min_bet > 0 AND max_bet >= min_bet", name="bet_limits"),
        CheckConstraint("max_fight_stake >= max_bet", name="fight_limit"),
        CheckConstraint("starting_balance >= 0", name="starting_balance_non_negative"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("0.1000"))
    draw_multiplier: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("8.00"))
    min_bet: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("10.00"))
    max_bet: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("10000.00"))
    max_fight_stake: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("50000.00"))
    # One-time grant per player wallet. Zero until an administrator configures it: no balance is
    # ever created by code defaults.
    starting_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0.00"))
    reset_policy: Mapped[ResetPolicy] = mapped_column(
        str_enum(ResetPolicy), nullable=False, default=ResetPolicy.DISABLED, server_default=ResetPolicy.DISABLED.value
    )


class PlayWallet(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "play_wallets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_play_wallets_tenant_id_id"),
        UniqueConstraint("tenant_id", "user_id", name="uq_play_wallets_tenant_user"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_play_wallets_user",
            ondelete="RESTRICT",
        ),
        CheckConstraint("balance >= 0", name="balance_non_negative"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0.00"))
    # Bumped with every entry so clients can drop stale realtime frames.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FightBook(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    """Betting state and running pool totals of one fight."""

    __tablename__ = "fight_books"
    __table_args__ = (
        UniqueConstraint("tenant_id", "match_id", name="uq_fight_books_match"),
        ForeignKeyConstraint(
            ["tenant_id", "match_id"], ["matches.tenant_id", "matches.id"], name="fk_fight_books_match", ondelete="RESTRICT"
        ),
        CheckConstraint("meron_pool >= 0 AND wala_pool >= 0 AND draw_pool >= 0", name="pools_non_negative"),
        CheckConstraint("dummy_meron >= 0 AND dummy_wala >= 0", name="dummy_pools_non_negative"),
    )

    match_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    state: Mapped[BookState] = mapped_column(str_enum(BookState), nullable=False, default=BookState.OPEN)
    meron_pool: Mapped[Decimal] = mapped_column(POOL, nullable=False, default=Decimal("0.00"))
    wala_pool: Mapped[Decimal] = mapped_column(POOL, nullable=False, default=Decimal("0.00"))
    draw_pool: Mapped[Decimal] = mapped_column(POOL, nullable=False, default=Decimal("0.00"))
    # Declarator pool amounts. They change the all-player totals and the odds, and never a balance.
    dummy_meron: Mapped[Decimal] = mapped_column(POOL, nullable=False, default=Decimal("0.00"), server_default="0")
    dummy_wala: Mapped[Decimal] = mapped_column(POOL, nullable=False, default=Decimal("0.00"), server_default="0")
    dummy_request_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    bet_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Snapshot taken when the book first opens; later setting changes apply to new books only.
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    opened_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    settled_at: Mapped[datetime | None]
    # Final multipliers, locked when betting closes.
    final_meron_odds: Mapped[Decimal | None] = mapped_column(ODDS)
    final_wala_odds: Mapped[Decimal | None] = mapped_column(ODDS)
    outcome: Mapped[str | None] = mapped_column(String(32))
    result_revision: Mapped[int | None] = mapped_column(Integer)
    commission_amount: Mapped[Decimal | None] = mapped_column(POOL)
    settle_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Bet(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "bets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "request_id", name="uq_bets_request"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["tenant_users.tenant_id", "tenant_users.id"], name="fk_bets_user", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "match_id"], ["matches.tenant_id", "matches.id"], name="fk_bets_match", ondelete="RESTRICT"
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("payout >= 0 AND credited >= 0", name="payout_non_negative"),
        CheckConstraint("(pricing = 'fixed') = (multiplier IS NOT NULL)", name="multiplier_matches_pricing"),
        CheckConstraint("(side = 'draw') = (pricing = 'fixed')", name="draw_is_fixed"),
        Index("ix_bets_match_user", "tenant_id", "match_id", "user_id"),
        Index("ix_bets_user_created", "tenant_id", "user_id", "created_at"),
    )

    match_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    side: Mapped[BetSide] = mapped_column(str_enum(BetSide, name="betside"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    pricing: Mapped[BetPricing] = mapped_column(str_enum(BetPricing), nullable=False)
    multiplier: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    status: Mapped[BetStatus] = mapped_column(str_enum(BetStatus), nullable=False, default=BetStatus.ACCEPTED)
    # Gross payout the bet is entitled to (stake included), and how much of it reached the wallet.
    payout: Mapped[Decimal] = mapped_column(POOL, nullable=False, default=Decimal("0.00"))
    credited: Mapped[Decimal] = mapped_column(POOL, nullable=False, default=Decimal("0.00"))
    net_profit: Mapped[Decimal | None] = mapped_column(POOL)
    settled_at: Mapped[datetime | None]


class PlayWalletEntry(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    """Append-only play balance ledger. The idempotency key makes every posting happen once."""

    __tablename__ = "play_wallet_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_play_wallet_entries_key"),
        ForeignKeyConstraint(
            ["tenant_id", "wallet_id"], ["play_wallets.tenant_id", "play_wallets.id"], name="fk_play_wallet_entries_wallet"
        ),
        CheckConstraint("delta <> 0", name="delta_non_zero"),
        CheckConstraint("balance_after >= 0", name="balance_after_non_negative"),
        CheckConstraint(
            "(kind IN ('account_grant', 'uniform_grant', 'reset', 'correction')) = (operation_id IS NOT NULL)",
            name="operation_kind",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "operation_id"],
            ["balance_operations.tenant_id", "balance_operations.id"],
            name="fk_play_wallet_entries_operation",
            ondelete="RESTRICT",
        ),
        Index("ix_play_wallet_entries_wallet_created", "wallet_id", "created_at"),
        Index("ix_play_wallet_entries_bet", "bet_id"),
        Index("ix_play_wallet_entries_operation", "operation_id"),
    )

    wallet_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[WalletEntryKind] = mapped_column(str_enum(WalletEntryKind), nullable=False)
    delta: Mapped[Decimal] = mapped_column(POOL, nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(POOL, nullable=False)
    bet_id: Mapped[uuid.UUID | None]
    match_id: Mapped[uuid.UUID | None]
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    memo: Mapped[str | None] = mapped_column(String(200))
    operation_id: Mapped[uuid.UUID | None]
