import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedAt, UUIDPrimaryKey
from app.shared.types import str_enum


class ActorType(StrEnum):
    PLATFORM_USER = "platform_user"
    TENANT_USER = "tenant_user"
    SYSTEM = "system"


class AuditLog(Base, UUIDPrimaryKey, CreatedAt):
    """Append-only. Database grants allow only SELECT and INSERT."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_actor", "actor_type", "actor_id"),
    )

    # NULL tenant = platform-level action; such rows are invisible through RLS.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    actor_type: Mapped[ActorType] = mapped_column(str_enum(ActorType), nullable=False)
    actor_id: Mapped[uuid.UUID | None]
    actor_label: Mapped[str | None] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64))
    changes: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    request_id: Mapped[str | None] = mapped_column(String(64))
