import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

SourceKind = Literal["obs", "external", "test"]
StreamState = Literal["offline", "connecting", "live", "reconnecting", "error"]


class _Name(BaseModel):
    @field_validator("name", check_fields=False)
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = " ".join(value.split())
        if not value:
            raise ValueError("Enter a stream name")
        return value


class StreamCreate(_Name):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    source_kind: SourceKind
    # Write-only: may embed provider credentials, so it is encrypted and never returned.
    source_url: SecretStr | None = None
    event_id: uuid.UUID | None = None
    is_default: bool = False
    enabled: bool = False


class StreamUpdate(_Name):
    """Only the fields sent are changed. Send ``event_id: null`` to unassign."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    source_url: SecretStr | None = None
    event_id: uuid.UUID | None = None
    is_default: bool | None = None
    enabled: bool | None = None


class Ingest(BaseModel):
    """How a broadcaster reaches this stream. The secret key itself is never repeated here."""

    rtmp_server: str | None
    rtmps_server: str | None
    stream_key_format: str
    whip_path: str


class StreamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    source_kind: SourceKind
    source_protocol: str | None
    source_host: str | None
    event_id: uuid.UUID | None
    event_name: str | None = None
    is_default: bool
    enabled: bool
    status: StreamState
    status_detail: str | None
    status_changed_at: datetime | None
    last_live_at: datetime | None
    checked_at: datetime | None
    viewers: int
    tracks: list[str] | None
    webrtc_ready: bool = False
    publish_key_hint: str | None
    key_rotated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    ingest: Ingest | None = None


class StreamWithKey(StreamOut):
    """Returned once after creation or key rotation; store the key in OBS, it cannot be shown again."""

    publish_key: str | None = None
    obs_stream_key: str | None = None


class StreamHealth(BaseModel):
    configured: bool
    reachable: bool
    detail: str | None = None
    streams: int = 0
    live: int = 0
    viewers: int = 0
    webrtc_sessions: int = 0
    hls_sessions: int = 0
    publishers: int = 0
    outbound_kbps: float | None = None
    test_source_available: bool
    rtmp_server: str | None = None
    rtmps_server: str | None = None
    checked_at: datetime


class StreamPlayback(BaseModel):
    """Same-origin URLs through Caddy. The token authorizes starting one session on one stream."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["webrtc"] = "webrtc"
    url: str
    fallback_url: str
    expires_at: datetime
    key: str
    ice_servers: list[dict[str, Any]] = Field(default_factory=list)


class StreamBrief(BaseModel):
    id: uuid.UUID
    name: str
    status: StreamState
    detail: str | None = None
    viewers: int | None = None
