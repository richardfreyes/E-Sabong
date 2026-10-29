import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, Index, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, SoftDelete, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum


class EventStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    LIVE = "live"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# Events visible in the end-user portal.
PUBLISHED_EVENT_STATUSES = (EventStatus.SCHEDULED, EventStatus.LIVE, EventStatus.COMPLETED)

EVENT_TRANSITIONS: dict[EventStatus, frozenset[EventStatus]] = {
    EventStatus.DRAFT: frozenset({EventStatus.SCHEDULED, EventStatus.CANCELLED}),
    EventStatus.SCHEDULED: frozenset({EventStatus.LIVE, EventStatus.CANCELLED, EventStatus.DRAFT}),
    EventStatus.LIVE: frozenset({EventStatus.COMPLETED, EventStatus.CANCELLED}),
    EventStatus.COMPLETED: frozenset(),
    EventStatus.CANCELLED: frozenset(),
}


class Event(Base, UUIDPrimaryKey, Timestamps, SoftDelete, TenantScoped):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_events_tenant_id_id"),
        CheckConstraint("ends_at IS NULL OR ends_at >= starts_at", name="ends_after_starts"),
        Index("ix_events_tenant_status_starts", "tenant_id", "status", "starts_at"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    venue: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(nullable=False)
    ends_at: Mapped[datetime | None]
    status: Mapped[EventStatus] = mapped_column(str_enum(EventStatus), nullable=False, default=EventStatus.DRAFT)
    # Sticky internal mode: assignments may be revoked, verification cannot be
    # disabled through a legacy result endpoint. Deferred so public projections
    # never need to load this operational control.
    result_verification_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), deferred=True)
    created_by_id: Mapped[uuid.UUID | None]
