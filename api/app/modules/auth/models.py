import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedAt, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum


class PlatformRole(StrEnum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    BILLING = "billing"
    SUPPORT = "support"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class SubjectType(StrEnum):
    PLATFORM = "platform"
    TENANT = "tenant"


class PlatformUser(Base, UUIDPrimaryKey, Timestamps):
    __tablename__ = "platform_users"
    __table_args__ = (Index("uq_platform_users_email", func.lower(text("email")), unique=True),)

    email: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[PlatformRole] = mapped_column(str_enum(PlatformRole), nullable=False)
    status: Mapped[AccountStatus] = mapped_column(str_enum(AccountStatus), nullable=False, default=AccountStatus.ACTIVE)
    last_login_at: Mapped[datetime | None]


class AuthSession(Base, UUIDPrimaryKey, CreatedAt):
    """One row per issued refresh token. Rotation links rows within a family."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_family", "family_id"),
        Index("ix_auth_sessions_subject", "subject_type", "subject_id"),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    subject_type: Mapped[SubjectType] = mapped_column(str_enum(SubjectType), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None]
    revoked_reason: Mapped[str | None] = mapped_column(String(64))
    replaced_by_id: Mapped[uuid.UUID | None]
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
