import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
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
        UniqueConstraint("tenant_id", "id", name="uq_video_streams_tenant_id_id"),
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


class VideoStreamOperator(Base, UUIDPrimaryKey):
    """A Stream Operator account authorized to broadcast and record one stream."""

    __tablename__ = "video_stream_operators"
    __table_args__ = (
        UniqueConstraint("stream_id", "user_id", name="uq_video_stream_operators_assignment"),
        ForeignKeyConstraint(
            ["tenant_id", "stream_id"],
            ["video_streams.tenant_id", "video_streams.id"],
            name="fk_video_stream_operators_stream",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["tenant_users.tenant_id", "tenant_users.id"],
            name="fk_video_stream_operators_user",
            ondelete="CASCADE",
        ),
        Index("ix_video_stream_operators_tenant_user", "tenant_id", "user_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    stream_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    assigned_by_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


BROADCAST_METHODS = ("browser", "obs", "external", "test")
BROADCAST_STATES = ("connecting", "live", "ended")


class VideoBroadcast(Base, UUIDPrimaryKey, Timestamps):
    """One streaming session on a stream: a browser broadcast started by an operator, or an
    OBS/external/test session the reconciliation task observed going live."""

    __tablename__ = "video_broadcasts"
    __table_args__ = (
        CheckConstraint("method IN ('browser', 'obs', 'external', 'test')", name="method"),
        CheckConstraint("resolution IS NULL OR resolution IN ('720p', '1080p')", name="resolution"),
        CheckConstraint("status IN ('connecting', 'live', 'ended')", name="status"),
        CheckConstraint("(status = 'ended') = (ended_at IS NOT NULL)", name="ended"),
        ForeignKeyConstraint(
            ["tenant_id", "stream_id"],
            ["video_streams.tenant_id", "video_streams.id"],
            name="fk_video_broadcasts_stream",
            ondelete="SET NULL",
        ),
        Index("ix_video_broadcasts_tenant_started", "tenant_id", text("started_at DESC")),
        Index("uq_video_broadcasts_open", "stream_id", unique=True, postgresql_where=text("status <> 'ended'")),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    stream_id: Mapped[uuid.UUID | None]
    stream_name: Mapped[str] = mapped_column(String(120), nullable=False)
    operator_id: Mapped[uuid.UUID | None]
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="connecting", server_default=text("'connecting'"))
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    live_at: Mapped[datetime | None]
    last_seen_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    end_reason: Mapped[str | None] = mapped_column(String(160))


RECORDING_STATES = ("recording", "finalizing", "completed", "failed")


class VideoRecording(Base, UUIDPrimaryKey, Timestamps):
    """A recording of one stream between Start and Stop. MediaMTX writes its fMP4 segments to
    ``<recordings root>/<tenant_id>/<recording id>/live/<path id>/``; ``segments`` lists them."""

    __tablename__ = "video_recordings"
    __table_args__ = (
        CheckConstraint("status IN ('recording', 'finalizing', 'completed', 'failed')", name="status"),
        CheckConstraint("duration_seconds >= 0", name="duration_non_negative"),
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        CheckConstraint("status = 'recording' OR stopped_at IS NOT NULL", name="stopped"),
        ForeignKeyConstraint(
            ["tenant_id", "stream_id"],
            ["video_streams.tenant_id", "video_streams.id"],
            name="fk_video_recordings_stream",
            ondelete="SET NULL",
        ),
        Index("ix_video_recordings_tenant_started", "tenant_id", text("started_at DESC")),
        Index("ix_video_recordings_expires", "expires_at"),
        Index("uq_video_recordings_active", "stream_id", unique=True, postgresql_where=text("status = 'recording'")),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    stream_id: Mapped[uuid.UUID | None]
    stream_name: Mapped[str] = mapped_column(String(120), nullable=False)
    started_by_id: Mapped[uuid.UUID | None]
    stopped_by_id: Mapped[uuid.UUID | None]
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="recording", server_default=text("'recording'"))
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    stopped_at: Mapped[datetime | None]
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    segments: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    detail: Mapped[str | None] = mapped_column(String(240))
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
