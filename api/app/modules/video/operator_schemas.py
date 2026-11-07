import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.video.stream_schemas import Ingest

Resolution = Literal["720p", "1080p"]


class BroadcastOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    stream_id: uuid.UUID | None
    stream_name: str
    operator_id: uuid.UUID | None
    operator_name: str | None = None
    method: Literal["browser", "obs", "external", "test"]
    resolution: Resolution | None
    status: Literal["connecting", "live", "ended"]
    started_at: datetime
    live_at: datetime | None
    ended_at: datetime | None
    end_reason: str | None
    mine: bool = False


class SegmentOut(BaseModel):
    index: int
    started_at: datetime
    duration: int
    size: int


class RecordingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    stream_id: uuid.UUID | None
    stream_name: str
    started_by_id: uuid.UUID | None
    started_by_name: str | None = None
    stopped_by_id: uuid.UUID | None
    status: Literal["recording", "finalizing", "completed", "failed"]
    started_at: datetime
    stopped_at: datetime | None
    duration_seconds: int
    size_bytes: int
    segment_count: int = 0
    detail: str | None
    expires_at: datetime


class OperatorIngest(Ingest):
    publish_key_hint: str | None = None


class OperatorStream(BaseModel):
    id: uuid.UUID
    name: str
    source_kind: Literal["obs", "external", "test"]
    visible_to_players: bool
    event_name: str | None = None
    is_default: bool
    status: Literal["offline", "connecting", "live", "reconnecting", "error"]
    status_detail: str | None
    viewers: int
    tracks: list[str] | None
    webrtc_ready: bool
    inbound_kbps: float | None = None
    browser_publishing: bool
    ingest: OperatorIngest | None
    broadcast: BroadcastOut | None
    recording: RecordingOut | None


class OperatorServer(BaseModel):
    configured: bool
    reachable: bool
    recording_available: bool
    recording_problem: str | None
    rtmp_server: str | None
    rtmps_server: str | None
    max_viewers: int
    retention_days: int
    max_recording_minutes: int
    quota_bytes: int
    used_bytes: int
    checked_at: datetime


class OperatorOverview(BaseModel):
    server: OperatorServer
    streams: list[OperatorStream]


class BroadcastStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution: Resolution = "720p"
    takeover: bool = False


class PublishGrant(BaseModel):
    """Everything a browser needs to publish one session over WHIP. The token is short-lived,
    bound to this session and never usable for viewing or for another stream."""

    broadcast: BroadcastOut
    whip_url: str
    expires_at: datetime
    ice_servers: list[dict[str, Any]]
    max_bitrate_kbps: int


class ObsCredentials(BaseModel):
    rtmp_server: str | None
    rtmps_server: str | None
    stream_key: str
    whip_url: str
    whip_bearer_token: str


class RecordingFile(BaseModel):
    index: int
    started_at: datetime
    duration: int
    size: int
    play_url: str
    download_url: str


class RecordingAccess(BaseModel):
    recording: RecordingOut
    files: list[RecordingFile]
    expires_at: datetime


class OperatorAccount(BaseModel):
    id: uuid.UUID
    username: str
    full_name: str
    status: str
    last_login_at: datetime | None
    created_at: datetime
    must_change_password: bool
    stream_ids: list[uuid.UUID]
    broadcast: BroadcastOut | None = None


class OperatorsOverview(BaseModel):
    operators: list[OperatorAccount]
    streams: list[dict[str, Any]]
    active_sessions: list[BroadcastOut]
    recent_sessions: list[BroadcastOut]
    active_recordings: list[RecordingOut]


class AssignStreams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stream_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
