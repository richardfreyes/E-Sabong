"""Record of one Loader allocation. The points themselves stay on the play-balance wallets."""

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedAt, TenantScoped, UUIDPrimaryKey

MONEY = Numeric(14, 2)


class LoaderGrant(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    """Append-only. A Loader cannot update or delete a row after it is written."""

    __tablename__ = "loader_grants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_loader_grants_tenant_id_id"),
        UniqueConstraint("tenant_id", "reference", name="uq_loader_grants_reference"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_loader_grants_key"),
        CheckConstraint("amount > 0", name="amount_positive"),
        ForeignKeyConstraint(
            ["tenant_id", "loader_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_loader_grants_loader",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recipient_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_loader_grants_recipient",
        ),
    )

    reference: Mapped[str] = mapped_column(String(32), nullable=False)
    loader_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    recipient_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    loader_balance_after: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    recipient_balance_after: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
