import base64
import hashlib
import hmac
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from app.core.config import get_settings
from app.modules.video import streams
from app.modules.video.models import VideoStream

ROOT = Path(__file__).resolve().parents[2]


def _token(**kw):  # type: ignore[no-untyped-def]
    args = dict(tenant_id=uuid.uuid4(), stream_id=uuid.uuid4(), path_id="A" * 24, user_id=uuid.uuid4(), family=uuid.uuid4())
    return streams.sign_playback(**{**args, "audience": "player", **kw})


def test_playback_token_round_trip_tamper_and_expiry() -> None:
    token, expires = _token()
    claims = streams.verify_playback(token)
    assert claims and claims["p"] == "A" * 24 and claims["a"] == "player"
    assert expires > datetime.now(UTC)
    body, sig = token.split(".")
    assert streams.verify_playback(f"{body}.{sig[:-1]}{'A' if sig[-1] != 'A' else 'B'}") is None
    assert streams.verify_playback(body[:-2] + "xx." + sig) is None
    assert streams.verify_playback(None) is None and streams.verify_playback("x" * 2000) is None
    old, _ = _token(ttl=-5)
    assert streams.verify_playback(old) is None
    # A session that started with a valid token is not ended by the token's expiry.
    assert streams.verify_playback(old, allow_expired=True) is not None


def test_tokens_are_bound_to_the_server_secret(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    token, _ = _token()
    monkeypatch.setenv("JWT_SECRET", "another-secret-0123456789abcdef0123456789")
    get_settings.cache_clear()
    try:
        assert streams.verify_playback(token) is None
    finally:
        get_settings.cache_clear()


def test_path_parsing_and_keys() -> None:
    pid = streams.new_path_id()
    assert len(pid) == 24 and streams.parse_path(f"live/{pid}") == (pid, False)
    assert streams.parse_path(f"live/{pid}/rtc") == (pid, True)
    for bad in (None, "", f"live/{pid}/x", "live/short", f"other/{pid}", f"live/{pid}/rtc/extra"):
        assert streams.parse_path(bad) is None
    key = streams.new_publish_key()
    assert len(key) >= 32 and streams.key_hash(key) == hashlib.sha256(key.encode()).hexdigest()


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://media.ok.example/live/index.m3u8", True),
        ("rtmp://media.ok.example/app/key", True),
        ("rtmps://media.ok.example:443/app/key", True),
        ("wheps://media.ok.example/live/whep", True),
        ("http://media.ok.example/a.m3u8", False),
        ("https://media.ok.example/a.mp4", False),
        ("https://other.example/a.m3u8", False),
        ("rtsp://media.ok.example/cam", False),
        ("file:///etc/passwd", False),
        ("https://media.ok.example/a.m3u8 extra", False),
        ("https://media.ok.example:99999/a.m3u8", False),
    ],
)
def test_source_urls(monkeypatch, url: str, ok: bool) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("VIDEO_SOURCE_HOSTS", '["media.ok.example"]')
    get_settings.cache_clear()
    try:
        if ok:
            assert streams.validate_source_url(url)[1] == "media.ok.example"
        else:
            with pytest.raises(streams.StreamConfigError):
                streams.validate_source_url(url)
    finally:
        get_settings.cache_clear()


def _stream(**kw) -> VideoStream:  # type: ignore[no-untyped-def]
    base = dict(source_kind="obs", enabled=True, last_live_at=None, reconnect_requested_at=None, path_id="A" * 24)
    return VideoStream(**{**base, **kw})


def test_status_table() -> None:
    now = datetime.now(UTC)
    live = streams.PathState(ready=True, tracks=["H264", "Opus"])
    assert streams.compute_status(_stream(), live, now, True) == ("live", None)
    aac = streams.PathState(ready=True, tracks=["H264", "MPEG-4 Audio"])
    assert streams.compute_status(_stream(), aac, now, True)[0] == "live"
    assert "WebRTC relay" in streams.compute_status(_stream(), aac, now, True)[1]
    idle = streams.PathState()
    assert streams.compute_status(_stream(), idle, now, False) == ("error", "Streaming server unreachable")
    assert streams.compute_status(_stream(), idle, now, True)[0] == "offline"
    recent = _stream(last_live_at=now - timedelta(seconds=5))
    assert streams.compute_status(recent, idle, now, True)[0] == "reconnecting"
    stale = _stream(last_live_at=now - timedelta(minutes=5))
    assert streams.compute_status(stale, idle, now, True)[0] == "offline"
    ext = _stream(source_kind="external", reconnect_requested_at=now - timedelta(seconds=3))
    assert streams.compute_status(ext, idle, now, True)[0] == "connecting"
    ext_old = _stream(source_kind="external", reconnect_requested_at=now - timedelta(minutes=5))
    assert streams.compute_status(ext_old, idle, now, True)[0] == "error"
    assert streams.compute_status(_stream(source_kind="external", enabled=False), idle, now, True)[0] == "offline"


def test_webrtc_compatibility() -> None:
    assert streams.webrtc_compatible(["H264", "Opus"]) and streams.webrtc_compatible(["VP8"])
    assert not streams.webrtc_compatible(["H264", "MPEG-4 Audio"]) and not streams.webrtc_compatible(["Opus"])


def test_config_differs_ignores_unrelated_mediamtx_defaults() -> None:
    desired = {"source": "publisher", "runOnInit": "ffmpeg x", "runOnInitRestart": True}
    current = {**desired, "maxReaders": 0, "record": False, "sourceOnDemand": False, "runOnDemand": "", "runOnReady": ""}
    assert not streams.config_differs(desired, current)
    assert streams.config_differs(desired, {**current, "runOnInit": "ffmpeg y"})
    assert streams.config_differs(desired, None)


def test_turn_credentials_are_time_limited_hmacs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("STREAM_STUN_URLS", '["stun:stun.example.org:3478"]')
    monkeypatch.setenv("STREAM_TURN_URLS", '["turns:turn.example.org:5349"]')
    monkeypatch.setenv("STREAM_TURN_SECRET", "turn-shared-secret-0123456789abcdef0123")
    get_settings.cache_clear()
    try:
        user = uuid.uuid4()
        stun, turn = streams.ice_servers(user)
        assert stun == {"urls": "stun:stun.example.org:3478"}
        expiry, uid = turn["username"].split(":")
        assert uid == str(user) and 3500 < int(expiry) - time.time() <= 3600
        expected = hmac.new(b"turn-shared-secret-0123456789abcdef0123", turn["username"].encode(), hashlib.sha1).digest()
        assert turn["credential"] == base64.b64encode(expected).decode()
        assert "turn-shared-secret" not in str(turn)
    finally:
        get_settings.cache_clear()


def test_relay_command_matches_the_mediamtx_configuration() -> None:
    config = yaml.safe_load((ROOT / "infrastructure/mediamtx/mediamtx.yml").read_text())
    regex = config["paths"]["~^live/([A-Za-z0-9_-]{24})$"]
    assert regex["runOnReady"] == streams.RELAY_COMMAND and regex["runOnReadyRestart"] is True
    assert config["authMethod"] == "http" and "?" not in config["authHTTPAddress"]  # no secret in the callback URL
    assert config["rtspAddress"].startswith("127.0.0.1:")  # relay transport stays inside the container
    assert config["api"] and config["apiAddress"] == ":9997" and not config["pprof"] and not config["playback"]


def test_production_refuses_weak_streaming_secrets(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import Settings, production_problems

    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        mediamtx_api_password="short",  # noqa: S106 - deliberately weak test value
        stream_turn_secret="CHANGE_ME",  # noqa: S106 - deliberately weak test value
        stream_test_source=True,
    )
    problems = " ".join(production_problems(s))
    assert "MEDIAMTX_API_PASSWORD" in problems and "STREAM_TURN_SECRET" in problems and "STREAM_TEST_SOURCE" in problems
