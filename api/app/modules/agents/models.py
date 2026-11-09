"""Tables created by migrations 0024 and 0025, kept mapped so the schema and models agree.

No route or service writes to them: agents do not move balances to or from players, and players
do not file requests with their upline (see unit_tests/test_non_monetary.py).
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedAt, TenantScoped, UUIDPrimaryKey

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
