"""Closed-loop demonstration points ledger.

Points are non-monetary and non-redeemable. By design the ledger has no
relationship to matches or results: nothing can be staked on an outcome and
nothing is settled when a result is declared.
"""

import uuid
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum


class AccountKind(StrEnum):
    ISSUANCE = "issuance"  # tenant-wide source of points; balance may go negative
    USER = "user"


class TransactionKind(StrEnum):
    ALLOCATION = "allocation"  # issuance -> user, by an administrator
    TRANSFER = "transfer"  # user -> downline user
    ADJUSTMENT = "adjustment"  # administrative correction, credit or debit


class PointAccount(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "point_accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_point_accounts_tenant_id_id"),
        UniqueConstraint("tenant_id", "user_id", name="uq_point_accounts_tenant_user"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_point_accounts_user_same_tenant",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_point_accounts_issuance",
            "tenant_id",
            unique=True,
            postgresql_where=text("kind = 'issuance'"),
        ),
        CheckConstraint("kind = 'issuance' OR balance >= 0", name="non_negative_user_balance"),
        CheckConstraint(
            "(kind = 'issuance' AND user_id IS NULL) OR (kind = 'user' AND user_id IS NOT NULL)",
            name="owner_matches_kind",
        ),
    )

    kind: Mapped[AccountKind] = mapped_column(str_enum(AccountKind), nullable=False)
    user_id: Mapped[uuid.UUID | None]
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class PointTransaction(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    __tablename__ = "point_transactions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_point_transactions_tenant_id_id"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_point_transactions_idempotency"),
        ForeignKeyConstraint(
            ["tenant_id", "from_account_id"],
            ["point_accounts.tenant_id", "point_accounts.id"],
            name="fk_point_transactions_from_same_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "to_account_id"],
            ["point_accounts.tenant_id", "point_accounts.id"],
            name="fk_point_transactions_to_same_tenant",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("from_account_id <> to_account_id", name="distinct_accounts"),
        Index("ix_point_transactions_tenant_created", "tenant_id", "created_at"),
        Index("ix_point_transactions_from", "from_account_id", "created_at"),
        Index("ix_point_transactions_to", "to_account_id", "created_at"),
    )

    kind: Mapped[TransactionKind] = mapped_column(str_enum(TransactionKind), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    from_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    to_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    memo: Mapped[str | None] = mapped_column(String(300))
    created_by_id: Mapped[uuid.UUID] = mapped_column(nullable=False)


class PointLedgerEntry(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    __tablename__ = "point_ledger_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "transaction_id"],
            ["point_transactions.tenant_id", "point_transactions.id"],
            name="fk_point_ledger_entries_tx_same_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["point_accounts.tenant_id", "point_accounts.id"],
            name="fk_point_ledger_entries_account_same_tenant",
        ),
        CheckConstraint("delta <> 0", name="delta_non_zero"),
        Index("ix_point_ledger_entries_account_created", "account_id", "created_at"),
        Index("ix_point_ledger_entries_tx", "transaction_id"),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
