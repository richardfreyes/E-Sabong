"""Independent non-monetary operational resource accounting. No gameplay dependencies."""

import uuid
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum

MAX_BALANCE = 9_007_199_254_740_991
MAX_AMOUNT = 1_000_000_000


class ResourceKind(StrEnum):
    COMPUTE = "compute"
    ADMIN_UTILITIES = "admin_utilities"
    SEATS = "seats"


class ResourceAction(StrEnum):
    # No user-to-user movement exists: units only enter by direct admin issuance to an account,
    # leave by the holder's own usage, and return to that same holder by a usage refund.
    ISSUE = "issue"
    CONSUME = "consume"
    REFUND = "refund"


class ResourceSettings(Base, Timestamps, TenantScoped):
    __tablename__ = "resource_settings"
    __table_args__ = (PrimaryKeyConstraint("tenant_id"),)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))


class ResourceWallet(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "resource_wallets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "resource_kind", name="uq_resource_wallet_owner_kind"),
        UniqueConstraint("tenant_id", "id", "resource_kind", name="uq_resource_wallet_identity"),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["tenant_users.tenant_id", "tenant_users.id"]),
        CheckConstraint(f"balance BETWEEN 0 AND {MAX_BALANCE}", name="resource_balance_range"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    resource_kind: Mapped[ResourceKind] = mapped_column(str_enum(ResourceKind), nullable=False)
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))


class ResourceTransaction(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    __tablename__ = "resource_transactions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_resource_transaction_key"),
        UniqueConstraint("tenant_id", "id", "resource_kind", name="uq_resource_transaction_identity"),
        UniqueConstraint("tenant_id", "refund_of_id", name="uq_resource_usage_refund"),
        ForeignKeyConstraint(
            ["tenant_id", "source_wallet_id", "resource_kind"],
            ["resource_wallets.tenant_id", "resource_wallets.id", "resource_wallets.resource_kind"],
            name="fk_resource_transaction_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "target_wallet_id", "resource_kind"],
            ["resource_wallets.tenant_id", "resource_wallets.id", "resource_wallets.resource_kind"],
            name="fk_resource_transaction_target",
        ),
        ForeignKeyConstraint(["tenant_id", "created_by_id"], ["tenant_users.tenant_id", "tenant_users.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "refund_of_id", "resource_kind"],
            ["resource_transactions.tenant_id", "resource_transactions.id", "resource_transactions.resource_kind"],
        ),
        CheckConstraint(f"amount BETWEEN 1 AND {MAX_AMOUNT}", name="resource_amount_range"),
        CheckConstraint(
            "(kind = 'issue' AND source_wallet_id IS NULL AND target_wallet_id IS NOT NULL AND refund_of_id IS NULL) OR "
            "(kind = 'consume' AND source_wallet_id IS NOT NULL AND target_wallet_id IS NULL AND refund_of_id IS NULL) OR "
            "(kind = 'refund' AND source_wallet_id IS NULL AND target_wallet_id IS NOT NULL AND refund_of_id IS NOT NULL)",
            name="resource_action_parties",
        ),
        CheckConstraint("source_wallet_id IS NULL OR target_wallet_id IS NULL", name="no_transfer"),
        Index("ix_resource_transaction_history", "tenant_id", "created_at", "id"),
    )
    kind: Mapped[ResourceAction] = mapped_column(str_enum(ResourceAction), nullable=False)
    resource_kind: Mapped[ResourceKind] = mapped_column(str_enum(ResourceKind), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_wallet_id: Mapped[uuid.UUID | None]
    target_wallet_id: Mapped[uuid.UUID | None]
    created_by_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    refund_of_id: Mapped[uuid.UUID | None]
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)


class ResourceLedgerEntry(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    __tablename__ = "resource_ledger_entries"
    __table_args__ = (
        UniqueConstraint("transaction_id", "wallet_id", name="uq_resource_entry_posting"),
        ForeignKeyConstraint(
            ["tenant_id", "transaction_id", "resource_kind"],
            ["resource_transactions.tenant_id", "resource_transactions.id", "resource_transactions.resource_kind"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "wallet_id", "resource_kind"],
            ["resource_wallets.tenant_id", "resource_wallets.id", "resource_wallets.resource_kind"],
        ),
        CheckConstraint("delta <> 0", name="resource_delta_nonzero"),
        CheckConstraint(f"balance_after BETWEEN 0 AND {MAX_BALANCE}", name="resource_entry_balance_range"),
        Index("ix_resource_entry_wallet", "tenant_id", "wallet_id", "created_at"),
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    wallet_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    resource_kind: Mapped[ResourceKind] = mapped_column(str_enum(ResourceKind), nullable=False)
    delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
