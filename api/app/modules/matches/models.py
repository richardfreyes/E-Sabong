import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, SoftDelete, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum


class MatchStatus(StrEnum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


MATCH_TRANSITIONS: dict[MatchStatus, frozenset[MatchStatus]] = {
    MatchStatus.SCHEDULED: frozenset({MatchStatus.IN_PROGRESS, MatchStatus.CANCELLED}),
    MatchStatus.IN_PROGRESS: frozenset({MatchStatus.COMPLETED, MatchStatus.CANCELLED}),
    MatchStatus.COMPLETED: frozenset(),
    MatchStatus.CANCELLED: frozenset(),
}


class Match(Base, UUIDPrimaryKey, Timestamps, SoftDelete, TenantScoped):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_matches_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            ["events.tenant_id", "events.id"],
            name="fk_matches_event_same_tenant",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_matches_event_number",
            "event_id",
            "match_number",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_matches_tenant_status", "tenant_id", "status"),
        CheckConstraint("match_number > 0", name="match_number_positive"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    match_number: Mapped[int] = mapped_column(Integer, nullable=False)
    meron_name: Mapped[str] = mapped_column(String(120), nullable=False)
    meron_details: Mapped[str | None] = mapped_column(String(500))
    wala_name: Mapped[str] = mapped_column(String(120), nullable=False)
    wala_details: Mapped[str | None] = mapped_column(String(500))
    scheduled_at: Mapped[datetime | None]
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    status: Mapped[MatchStatus] = mapped_column(str_enum(MatchStatus), nullable=False, default=MatchStatus.SCHEDULED)
    cancel_reason: Mapped[str | None] = mapped_column(String(300))
