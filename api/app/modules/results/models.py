import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedAt, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum


class MatchOutcome(StrEnum):
    MERON = "meron"
    WALA = "wala"
    DRAW = "draw"
    NO_CONTEST = "no_contest"


class MatchResult(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    """The current official result of a match (one row per match)."""

    __tablename__ = "match_results"
    __table_args__ = (
        UniqueConstraint("match_id", name="uq_match_results_match"),
        ForeignKeyConstraint(
            ["tenant_id", "match_id"],
            ["matches.tenant_id", "matches.id"],
            name="fk_match_results_match_same_tenant",
            ondelete="RESTRICT",
        ),
    )

    match_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    outcome: Mapped[MatchOutcome] = mapped_column(str_enum(MatchOutcome), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500))
    declared_by_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    declared_at: Mapped[datetime] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class MatchResultHistory(Base, UUIDPrimaryKey, CreatedAt, TenantScoped):
    """Append-only record of every declaration and correction."""

    __tablename__ = "match_result_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "match_id"],
            ["matches.tenant_id", "matches.id"],
            name="fk_match_result_history_match_same_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_match_result_history_match", "match_id", "revision"),
    )

    match_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[MatchOutcome] = mapped_column(str_enum(MatchOutcome), nullable=False)
    previous_outcome: Mapped[MatchOutcome | None] = mapped_column(str_enum(MatchOutcome, name="previous_outcome"))
    reason: Mapped[str | None] = mapped_column(String(500))
    changed_by_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
