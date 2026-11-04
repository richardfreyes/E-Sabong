"""Administrator balance management on the existing play-balance ledger (``play_wallets``).

Every operation is recorded here and posts its ledger entries in the same transaction. Nothing
moves from one account to another:

* an account grant credits one active player;
* a uniform grant credits the same amount to every eligible participant of the tenant;
* a reset sets every eligible participant to the configured starting balance;
* a correction reverses (part of) an earlier administrator posting, or applies a uniform grant
  that an eligible participant missed. It is proposed by one administrator and must be approved
  by a different one before it posts.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum

MAX_OPERATION_AMOUNT = Decimal("1000000.00")
# play_wallets.balance is NUMERIC(14, 2).
MAX_WALLET_BALANCE = Decimal("999999999999.99")


class BalanceOperationKind(StrEnum):
    ACCOUNT_GRANT = "account_grant"
    UNIFORM_GRANT = "uniform_grant"
    RESET = "reset"
    CORRECTION = "correction"


class BalanceOperationStatus(StrEnum):
    PENDING = "pending"  # corrections only, awaiting a second administrator
    APPLIED = "applied"
    REJECTED = "rejected"


class CorrectionDirection(StrEnum):
    CREDIT = "credit"
    DEBIT = "debit"


class BalanceOperation(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "balance_operations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_balance_operations_tenant_id_id"),
        UniqueConstraint("tenant_id", "reference", name="uq_balance_operations_reference"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_balance_operations_key"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_id"], ["tenant_users.tenant_id", "tenant_users.id"], name="fk_balance_operations_created_by"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reviewed_by_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_balance_operations_reviewed_by",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "target_user_id"], ["tenant_users.tenant_id", "tenant_users.id"], name="fk_balance_operations_target"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "corrects_operation_id"],
            ["balance_operations.tenant_id", "balance_operations.id"],
            name="fk_balance_operations_corrects_operation",
        ),
        CheckConstraint("amount >= 0 AND amount <= 1000000", name="amount_range"),
        Index("ix_balance_operations_history", "tenant_id", "created_at", "id"),
        Index("ix_balance_operations_corrects_entry", "corrects_entry_id"),
        Index("ix_balance_operations_corrects_operation", "corrects_operation_id", "target_user_id"),
    )

    reference: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[BalanceOperationKind] = mapped_column(str_enum(BalanceOperationKind), nullable=False)
    status: Mapped[BalanceOperationStatus] = mapped_column(str_enum(BalanceOperationStatus), nullable=False)
    # Uniform grant: credited to each account. Reset: the target balance. Correction: the amount moved.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    direction: Mapped[CorrectionDirection | None] = mapped_column(str_enum(CorrectionDirection))
    target_user_id: Mapped[uuid.UUID | None]
    corrects_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("play_wallet_entries.id", ondelete="RESTRICT"))
    corrects_operation_id: Mapped[uuid.UUID | None]
    remarks: Mapped[str | None] = mapped_column(String(300))
    created_by_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reviewed_by_id: Mapped[uuid.UUID | None]
    reviewed_at: Mapped[datetime | None]
    review_remarks: Mapped[str | None] = mapped_column(String(300))
    accounts_affected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_delta: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=Decimal("0.00"))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
