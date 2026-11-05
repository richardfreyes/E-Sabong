"""Tenant-scoped declaration queue. Pending proposals are never official results."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Integer, Numeric, String, UniqueConstraint, text
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


class GhostAutoStatus(StrEnum):
    ACTIVE = "active"
    STOPPED = "stopped"


class GhostAutoBet(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    """A Declarator's automatic ghost (dummy) betting on one fight.

    Every ``interval_seconds`` the API's ghost runner adds ``meron_amount`` / ``wala_amount`` to the
    fight's dummy pools (never a balance). One active job per event; it stops on its own when the
    fight's betting closes, the event leaves LIVE, or the Declarator loses the assignment."""

    __tablename__ = "ghost_auto_bets"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "event_id"], ["events.tenant_id", "events.id"], name="fk_ghost_auto_bets_event"),
        ForeignKeyConstraint(["tenant_id", "match_id"], ["matches.tenant_id", "matches.id"], name="fk_ghost_auto_bets_match"),
        ForeignKeyConstraint(
            ["tenant_id", "started_by_id"], ["tenant_users.tenant_id", "tenant_users.id"], name="fk_ghost_auto_bets_started_by"
        ),
        CheckConstraint("meron_amount >= 0 AND wala_amount >= 0 AND (meron_amount > 0 OR wala_amount > 0)", name="amounts"),
        CheckConstraint("interval_seconds BETWEEN 1 AND 3600", name="interval"),
        CheckConstraint("status IN ('active', 'stopped')", name="status"),
        Index(
            "uq_ghost_auto_bets_active_event",
            "tenant_id",
            "event_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_ghost_auto_bets_due", "next_run_at", postgresql_where=text("status = 'active'")),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    match_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    started_by_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    meron_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    wala_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=GhostAutoStatus.ACTIVE)
    runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_meron: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=Decimal("0.00"))
    added_wala: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=Decimal("0.00"))
    next_run_at: Mapped[datetime] = mapped_column(nullable=False)
    stopped_at: Mapped[datetime | None]
    stop_reason: Mapped[str | None] = mapped_column(String(200))
