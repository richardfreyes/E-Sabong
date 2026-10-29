import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedAt, TenantScoped, UUIDPrimaryKey


class Notification(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    __tablename__ = "notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_notifications_user_same_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_notifications_user_unread", "tenant_id", "user_id", "read_at", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str | None] = mapped_column(String(1000))
    data: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    read_at: Mapped[datetime | None]
