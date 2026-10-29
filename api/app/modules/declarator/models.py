"""Tenant-scoped declaration queue. Pending proposals are never official results."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum


class DeclarationStatus(StrEnum):
    PENDING = "pending"
    DECLARED = "declared"
    PENDING_APPROVAL = "pending_approval"
    UNDER_REVIEW = "under_review"
    FINALIZED = "finalized"
    CANCELLED = "cancelled"


class DeclarationOutcome(StrEnum):
    MERON = "meron"
    WALA = "wala"
    DRAW = "draw"
    NO_CONTEST = "no_contest"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = (DeclarationStatus.PENDING_APPROVAL, DeclarationStatus.UNDER_REVIEW)


class EventDeclarator(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    __tablename__ = "event_declarators"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_event_declarators_assignment"),
        ForeignKeyConstraint(
            ["tenant_id", "assigned_by_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_event_declarators_assigner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["tenant_id", "event_id"], ["events.tenant_id", "events.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["tenant_id", "user_id"], ["tenant_users.tenant_id", "tenant_users.id"], ondelete="RESTRICT"),
    )
    event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    assigned_by_id: Mapped[uuid.UUID] = mapped_column(nullable=False)


class ResultDeclaration(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "result_declarations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_result_declarations_tenant_id_id"),
        UniqueConstraint("tenant_id", "request_id", name="uq_result_declarations_request"),
        Index("ix_result_declarations_match_created", "tenant_id", "match_id", "created_at", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "match_id"], ["matches.tenant_id", "matches.id"], name="fk_declaration_match", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "declared_by_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_declaration_author",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "approved_by_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_declaration_approver",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_result_declarations_active",
            "match_id",
            unique=True,
            postgresql_where=text("status IN ('pending_approval', 'under_review')"),
        ),
    )
    match_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    outcome: Mapped[DeclarationOutcome] = mapped_column(str_enum(DeclarationOutcome), nullable=False)
    status: Mapped[DeclarationStatus] = mapped_column(str_enum(DeclarationStatus), nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[str | None] = mapped_column(String(500))
    declared_by_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    approved_by_id: Mapped[uuid.UUID | None]
    finalized_at: Mapped[datetime | None]


class DeclarationAudit(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    __tablename__ = "declaration_audit"
    __table_args__ = (
        UniqueConstraint("declaration_id", "sequence", name="uq_declaration_audit_sequence"),
        ForeignKeyConstraint(
            ["tenant_id", "declaration_id"], ["result_declarations.tenant_id", "result_declarations.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(["tenant_id", "actor_id"], ["tenant_users.tenant_id", "tenant_users.id"], ondelete="RESTRICT"),
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    declaration_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
