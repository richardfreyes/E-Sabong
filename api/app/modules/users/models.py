import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.auth.models import AccountStatus
from app.shared.models import Base, CreatedAt, SoftDelete, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum


class AccountType(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    DECLARATOR = "declarator"
    MASTER_AGENT = "master_agent"
    AGENT = "agent"
    PLAYER = "player"


STAFF_TYPES = frozenset({AccountType.ADMIN, AccountType.OPERATOR, AccountType.DECLARATOR})
AGENT_TYPES = frozenset({AccountType.MASTER_AGENT, AccountType.AGENT})

# Allowed parent account types for each account type. ``None`` = top level.
HIERARCHY_PARENTS: dict[AccountType, frozenset[AccountType | None]] = {
    AccountType.ADMIN: frozenset({None}),
    AccountType.OPERATOR: frozenset({None}),
    AccountType.DECLARATOR: frozenset({None}),
    AccountType.MASTER_AGENT: frozenset({None}),
    AccountType.AGENT: frozenset({AccountType.MASTER_AGENT}),
    AccountType.PLAYER: frozenset({None, AccountType.MASTER_AGENT, AccountType.AGENT}),
}


class TenantUser(Base, UUIDPrimaryKey, Timestamps, SoftDelete, TenantScoped):
    __tablename__ = "tenant_users"
    __table_args__ = (
        # (tenant_id, id) is the target of composite FKs so that rows can only
        # reference users of the same tenant, independent of application checks.
        UniqueConstraint("tenant_id", "id", name="uq_tenant_users_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_tenant_users_parent_same_tenant",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_tenant_users_username",
            "tenant_id",
            func.lower(text("username")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_tenant_users_email",
            "tenant_id",
            func.lower(text("email")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND email IS NOT NULL"),
        ),
        Index("ix_tenant_users_parent", "tenant_id", "parent_id"),
        Index("ix_tenant_users_type", "tenant_id", "account_type"),
    )

    username: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))
    account_type: Mapped[AccountType] = mapped_column(str_enum(AccountType), nullable=False)
    parent_id: Mapped[uuid.UUID | None]
    status: Mapped[AccountStatus] = mapped_column(str_enum(AccountStatus), nullable=False, default=AccountStatus.ACTIVE)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None]
    created_by_id: Mapped[uuid.UUID | None]


class Role(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_roles_tenant_id_id"),
        UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300))
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    permissions: Mapped[list["RolePermission"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", passive_deletes=True
    )


class RolePermission(Base, CreatedAt):
    __tablename__ = "role_permissions"
    __table_args__ = (
        PrimaryKeyConstraint("role_id", "permission"),
        ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.id"],
            name="fk_role_permissions_role_same_tenant",
            ondelete="CASCADE",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    permission: Mapped[str] = mapped_column(String(64), nullable=False)


class UserRole(Base, CreatedAt):
    __tablename__ = "user_roles"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "role_id"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_user_roles_user_same_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.id"],
            name="fk_user_roles_role_same_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_user_roles_role", "role_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
