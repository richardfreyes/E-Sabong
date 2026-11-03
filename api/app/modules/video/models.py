import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, Timestamps, UUIDPrimaryKey


class VideoProvider(Base):
    __tablename__ = "video_providers"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    credentials: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), default="disabled")
    checked_at: Mapped[datetime | None]


STREAM_SOURCES = ("obs", "external", "test")
STREAM_STATES = ("offline", "connecting", "live", "reconnecting", "error")


class VideoStream(Base, UUIDPrimaryKey, Timestamps):
    """One live stream served by MediaMTX at ``live/<path_id>``.

    ``path_id`` is random and unguessable, but it is not a credential: publishing needs the
    publish key (stored only as a SHA-256 hash) and reading needs a short-lived playback token.
    """

    __tablename__ = "video_streams"
    __table_args__ = (
        CheckConstraint("source_kind IN ('obs', 'external', 'test')", name="source_kind"),
        CheckConstraint("status IN ('offline', 'connecting', 'live', 'reconnecting', 'error')", name="status"),
        CheckConstraint("length(trim(name)) > 0", name="name_nonempty"),
        CheckConstraint("source_kind <> 'external' OR source_url IS NOT NULL", name="external_has_source"),
        CheckConstraint("viewers >= 0", name="viewers_non_negative"),
        ForeignKeyConstraint(
            ["tenant_id", "event_id"], ["events.tenant_id", "events.id"], name="fk_video_streams_event", ondelete="SET NULL"
        ),
        Index("uq_video_streams_name", "tenant_id", "name", unique=True),
        Index("uq_video_streams_event", "tenant_id", "event_id", unique=True, postgresql_where=text("event_id IS NOT NULL")),
        Index("uq_video_streams_default", "tenant_id", unique=True, postgresql_where=text("is_default")),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # Fernet-encrypted external URL (it may embed provider credentials). Never returned.
    source_url: Mapped[str | None] = mapped_column(Text)
    source_protocol: Mapped[str | None] = mapped_column(String(8))
    source_host: Mapped[str | None] = mapped_column(String(253))
    path_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    publish_key_hash: Mapped[str | None] = mapped_column(String(64))
    publish_key_hint: Mapped[str | None] = mapped_column(String(8))
    key_rotated_at: Mapped[datetime | None]
    event_id: Mapped[uuid.UUID | None]
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="offline", server_default=text("'offline'"))
    status_detail: Mapped[str | None] = mapped_column(String(240))
    status_changed_at: Mapped[datetime | None]
    last_live_at: Mapped[datetime | None]
    checked_at: Mapped[datetime | None]
    viewers: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    tracks: Mapped[list[Any] | None] = mapped_column(JSONB)
    reconnect_requested_at: Mapped[datetime | None]
