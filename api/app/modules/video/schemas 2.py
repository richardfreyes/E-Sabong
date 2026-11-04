import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    adapter: Literal["demo", "json-bearer-v1"] = "demo"
    event_id: uuid.UUID | None = None
    website_url: str = Field(default="", max_length=2000)
    api_base_url: str = Field(default="", max_length=2000)
    login_endpoint: str = Field(default="/api/auth/login", max_length=500)
    broadcasting_auth_endpoint: str = Field(default="/broadcasting/auth", max_length=500)
    playback_endpoint: str = Field(default="/api/video/get-playback-url", max_length=500)
    live_event_endpoint: str = Field(default="/api/games/live/fetch", max_length=500)
    websocket_url: str = Field(default="", max_length=2000)
    game_id: str = Field(default="", max_length=200)
    video_id: str = Field(default="", max_length=200)

    @field_validator("login_endpoint", "broadcasting_auth_endpoint", "playback_endpoint", "live_event_endpoint")
    @classmethod
    def relative_endpoint(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            not value.startswith("/")
            or value.startswith("//")
            or parts.netloc
            or parts.scheme
            or parts.query
            or parts.fragment
            or "\\" in value
        ):
            raise ValueError("Use an absolute API path without query parameters")
        return value


class ProviderUpdate(ProviderConfig):
    username: SecretStr | None = None
    password: SecretStr | None = None
    clear_credentials: bool = False


class Playback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["mp4", "hls", "dash"]
    url: str = Field(max_length=4096)
    expires_at: datetime | None = None


class VideoResult(BaseModel):
    status: str
    message: str
    # Playback validates external provider responses (mp4/hls/dash only); StreamPlayback is
    # built by this API for MediaMTX streams and is never accepted from a provider.
    playback: "Playback | StreamPlayback | None" = None
    metadata: dict[str, str | int | None] | None = None
    stream: "StreamBrief | None" = None


from app.modules.video.stream_schemas import StreamBrief, StreamPlayback  # noqa: E402

VideoResult.model_rebuild()
