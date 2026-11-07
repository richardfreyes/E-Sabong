"""Live streams through MediaMTX: keys, playback tokens, source rules, status and reconciliation.

Media never passes through FastAPI. The API only (1) stores configuration, (2) answers one
MediaMTX auth callback per publisher/viewer session, (3) mints short-lived playback tokens and
(4) reconciles MediaMTX state every few seconds (status, viewer counts, path configuration,
removal of viewers whose access ended).

Paths: ``live/<path_id>`` carries the stream as published (OBS/WHIP) or pulled (external/test).
``live/<path_id>/rtc`` is a relay with the video copied and the audio converted to Opus, because
browsers cannot play AAC (OBS's default) over WebRTC. HLS always reads ``live/<path_id>``.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import structlog
from cryptography.fernet import InvalidToken
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import platform_session
from app.core.deps import revoked_family_key
from app.core.redis import get_redis
from app.modules.realtime.publisher import publish
from app.modules.video.mediamtx import MediaMTX, MediaMTXUnavailable
from app.modules.video.models import VideoStream
from app.modules.video.service import cipher

log = structlog.get_logger()

PATH_ID_RE = re.compile(r"^live/([A-Za-z0-9_-]{24})(/rtc)?$")
TOKEN_TTL_SECONDS = 300
RECONNECT_GRACE_SECONDS = 30
SOURCE_CONNECT_SECONDS = 45
STATE_CACHE_SECONDS = 2
PUBLISH_FAILURE_LIMIT = 20
PUBLISH_FAILURE_WINDOW = 600
WEBRTC_VIDEO = {"H264", "VP8", "VP9", "AV1"}
WEBRTC_AUDIO = {"Opus", "G711", "G722"}
LOOPBACK = {"127.0.0.1", "::1"}
VIEWER_READERS = {"webrtcSession", "hlsSession"}

# Must match infrastructure/mediamtx/mediamtx.yml (a unit test keeps them identical).
RELAY_COMMAND = (
    "ffmpeg -hide_banner -loglevel error -rtsp_transport tcp -i rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH "
    "-map 0:v? -map 0:a? -c:v copy -c:a libopus -b:a 96k -ar 48000 "
    "-f rtsp -rtsp_transport tcp rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH/rtc"
)
# Development test pattern: moving 720p30 picture and a tone, H.264 + Opus, 1 s keyframes.
TEST_COMMAND = (
    "ffmpeg -hide_banner -loglevel error -re -f lavfi -i testsrc2=size=1280x720:rate=30 "
    "-f lavfi -i sine=frequency=440:sample_rate=48000 "
    "-c:v libx264 -preset ultrafast -tune zerolatency -profile:v baseline -pix_fmt yuv420p "
    "-g 30 -keyint_min 30 -b:v 1500k -maxrate 1500k -bufsize 750k -c:a libopus -b:a 64k "
    "-f rtsp -rtsp_transport tcp rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH"
)
# MediaMTX scheme -> stored protocol label. Only encrypted transports, except RTMP pulls.
SOURCE_SCHEMES = {"https": "hls", "rtmp": "rtmp", "rtmps": "rtmps", "wheps": "whep"}


class StreamConfigError(ValueError):
    pass


# --------------------------------------------------------------------------- identifiers & keys


def new_path_id() -> str:
    return secrets.token_urlsafe(18)  # 24 URL-safe characters, 144 bits


def new_publish_key() -> str:
    return secrets.token_urlsafe(24)  # 32 URL-safe characters, 192 bits


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def stream_path(stream: VideoStream) -> str:
    return f"live/{stream.path_id}"


def parse_path(path: str | None) -> tuple[str, bool] | None:
    """``live/<id>`` -> (id, False); ``live/<id>/rtc`` -> (id, True); anything else -> None."""
    match = PATH_ID_RE.match(path or "")
    return (match.group(1), bool(match.group(2))) if match else None


# --------------------------------------------------------------------------- playback tokens


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _token_key(purpose: bytes = b"esabong/video-playback/v1") -> bytes:
    secret = get_settings().jwt_secret.get_secret_value().encode()
    return hmac.new(secret, purpose, hashlib.sha256).digest()


# Separate keys: a playback token can never be replayed as a publish or recording token.
PUBLISH_PURPOSE = b"esabong/video-publish/v1"
RECORDING_PURPOSE = b"esabong/video-recording/v1"


def sign_token(claims: dict[str, Any], purpose: bytes, ttl: int) -> tuple[str, datetime]:
    expires = int(time.time()) + ttl
    body = _b64(json.dumps({"v": 1, **claims, "e": expires}, separators=(",", ":")).encode())
    signature = _b64(hmac.new(_token_key(purpose), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}", datetime.fromtimestamp(expires, UTC)


def verify_token(token: str | None, purpose: bytes, *, allow_expired: bool = False) -> dict[str, Any] | None:
    """Claims of a valid token, or None. For media sessions expiry only gates the start."""
    if not token or len(token) > 1024 or token.count(".") != 1:
        return None
    body, signature = token.split(".")
    expected = _b64(hmac.new(_token_key(purpose), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        claims = json.loads(_unb64(body))
    except ValueError:
        return None
    if not isinstance(claims, dict) or claims.get("v") != 1 or not isinstance(claims.get("e"), int):
        return None
    if not allow_expired and claims["e"] <= int(time.time()):
        return None
    return claims


def sign_playback(
    *,
    tenant_id: uuid.UUID,
    stream_id: uuid.UUID,
    path_id: str,
    user_id: uuid.UUID,
    family: uuid.UUID,
    audience: str,
    ttl: int = TOKEN_TTL_SECONDS,
) -> tuple[str, datetime]:
    claims = {"t": str(tenant_id), "s": str(stream_id), "p": path_id, "u": str(user_id), "f": str(family), "a": audience}
    return sign_token(claims, b"esabong/video-playback/v1", ttl)


def verify_playback(token: str | None, *, allow_expired: bool = False) -> dict[str, Any] | None:
    return verify_token(token, b"esabong/video-playback/v1", allow_expired=allow_expired)


def sign_publish(
    *,
    tenant_id: uuid.UUID,
    stream_id: uuid.UUID,
    path_id: str,
    user_id: uuid.UUID,
    family: uuid.UUID,
    broadcast_id: uuid.UUID,
    ttl: int = TOKEN_TTL_SECONDS,
) -> tuple[str, datetime]:
    """Browser (WHIP) publishing for one broadcast session. Unlike the OBS publish key it names
    the operator and the session, so stopping the broadcast or suspending the account ends it."""
    claims = {"t": str(tenant_id), "s": str(stream_id), "p": path_id, "u": str(user_id), "f": str(family), "b": str(broadcast_id)}
    return sign_token(claims, PUBLISH_PURPOSE, ttl)


def verify_publish(token: str | None, *, allow_expired: bool = False) -> dict[str, Any] | None:
    return verify_token(token, PUBLISH_PURPOSE, allow_expired=allow_expired)


def token_from_query(query: str | None) -> str | None:
    values = parse_qs(query or "").get("token")
    return values[0] if values else None


def ice_servers(user_id: uuid.UUID) -> list[dict[str, Any]]:
    """STUN as configured; TURN with time-limited credentials (TURN REST API / coturn)."""
    s = get_settings()
    servers: list[dict[str, Any]] = [{"urls": url} for url in s.stream_stun_urls]
    if s.stream_turn_urls and s.stream_turn_secret is not None:
        username = f"{int(time.time()) + 3600}:{user_id}"
        digest = hmac.new(s.stream_turn_secret.get_secret_value().encode(), username.encode(), hashlib.sha1).digest()
        servers.append({"urls": s.stream_turn_urls, "username": username, "credential": base64.b64encode(digest).decode()})
    return servers


# --------------------------------------------------------------------------- external sources


def validate_source_url(url: str) -> tuple[str, str]:
    """(protocol, host) for an operator-approved external source; raises StreamConfigError."""
    url = url.strip()
    if not url or len(url) > 2000 or any(c.isspace() for c in url) or "\\" in url:
        raise StreamConfigError("Enter a single source URL without spaces")
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        raise StreamConfigError("The source URL is not valid") from None
    protocol = SOURCE_SCHEMES.get(parts.scheme.lower())
    if protocol is None:
        raise StreamConfigError("Supported sources: https://…/…m3u8 (HLS), rtmp://, rtmps:// and wheps:// (WebRTC)")
    host = (parts.hostname or "").lower()
    if not host:
        raise StreamConfigError("The source URL needs a host name")
    if protocol == "hls" and not parts.path.lower().endswith(".m3u8"):
        raise StreamConfigError("HLS sources must point to an .m3u8 playlist")
    if host not in [h.lower() for h in get_settings().video_source_hosts]:
        raise StreamConfigError(f"{host} is not an approved source host (VIDEO_SOURCE_HOSTS)")
    return protocol, host


def encrypt_source(url: str) -> str:
    return cipher().encrypt(url.strip().encode()).decode()


def decrypt_source(stream: VideoStream) -> str | None:
    if not stream.source_url:
        return None
    try:
        return cipher().decrypt(stream.source_url.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def record_config(recording: Any) -> dict[str, Any]:
    """MediaMTX recording settings for one recording: its own directory under the tenant's, so
    files of different recordings (and tenants) never mix. Retention is applied by the API."""
    root = get_settings().mediamtx_recordings_root.rstrip("/")
    return {
        "record": True,
        "recordPath": f"{root}/{recording.tenant_id}/{recording.id}/%path/%Y-%m-%d_%H-%M-%S-%f",
        "recordFormat": "fmp4",
        "recordPartDuration": "1s",
        "recordSegmentDuration": "1h",
        "recordDeleteAfter": "0s",
    }


def desired_config(stream: VideoStream, recording: Any = None) -> dict[str, Any] | None:
    """Dynamic MediaMTX path configuration. OBS/WHIP paths use the static regex entry instead,
    except while recording: an exact-name entry then repeats the regex entry plus the recording
    settings (MediaMTX applies it without disconnecting the publisher).

    Disabled streams still exist so administrators can preview them, but pull on demand.
    """
    record = record_config(recording) if recording is not None else {}
    if stream.source_kind == "external":
        url = decrypt_source(stream)
        if url is None:
            return None
        return {
            "source": url,
            "sourceOnDemand": not stream.enabled and not record,
            "runOnReady": RELAY_COMMAND,
            "runOnReadyRestart": True,
            **record,
        }
    if stream.source_kind == "test" and get_settings().test_source_available:
        if stream.enabled or record:
            return {"source": "publisher", "runOnInit": TEST_COMMAND, "runOnInitRestart": True, **record}
        return {"source": "publisher", "runOnDemand": TEST_COMMAND, "runOnDemandRestart": True}
    if stream.source_kind == "obs" and record:
        return {"source": "publisher", "runOnReady": RELAY_COMMAND, "runOnReadyRestart": True, **record}
    return None


_CONFIG_KEYS = (
    "source",
    "sourceOnDemand",
    "runOnInit",
    "runOnInitRestart",
    "runOnDemand",
    "runOnDemandRestart",
    "runOnReady",
    "record",
    "recordPath",
)


def config_differs(desired: dict[str, Any], current: dict[str, Any] | None) -> bool:
    if current is None:
        return True
    defaults = {"sourceOnDemand": False, "runOnInitRestart": False, "runOnDemandRestart": False, "record": False}
    # MediaMTX reports its default recordPath for every path; it only matters while recording.
    keys = _CONFIG_KEYS if desired.get("record") else tuple(k for k in _CONFIG_KEYS if k != "recordPath")
    return any(desired.get(k, defaults.get(k, "")) != current.get(k, defaults.get(k, "")) for k in keys)


# --------------------------------------------------------------------------- status


@dataclass
class PathState:
    ready: bool = False
    tracks: list[str] = field(default_factory=list)
    rtc_ready: bool = False
    viewers: int = 0
    publisher: dict[str, Any] | None = None
    bytes_received: int = 0


def path_state(paths: dict[str, dict[str, Any]], path_id: str) -> PathState:
    main = paths.get(f"live/{path_id}")
    rtc = paths.get(f"live/{path_id}/rtc")
    readers = [r for info in (main, rtc) if info for r in info.get("readers") or [] if r.get("type") in VIEWER_READERS]
    return PathState(
        ready=bool(main and main.get("ready")),
        tracks=list((main or {}).get("tracks") or []),
        rtc_ready=bool(rtc and rtc.get("ready")),
        viewers=len(readers),
        publisher=(main or {}).get("source"),
        bytes_received=int((main or {}).get("bytesReceived") or 0),
    )


def webrtc_compatible(tracks: list[str]) -> bool:
    video = [t for t in tracks if t in WEBRTC_VIDEO]
    return bool(video) and all(t in WEBRTC_VIDEO or t in WEBRTC_AUDIO for t in tracks)


def compute_status(stream: VideoStream, state: PathState | None, now: datetime, reachable: bool) -> tuple[str, str | None]:
    """The five admin-visible states from MediaMTX's view of the path."""
    if not reachable:
        return "error", "Streaming server unreachable"
    if state is not None and state.ready:
        if not webrtc_compatible(state.tracks) and not state.rtc_ready:
            return "live", "Live over HLS; preparing the WebRTC relay"
        return "live", None
    recently_live = stream.last_live_at is not None and now - stream.last_live_at < timedelta(seconds=RECONNECT_GRACE_SECONDS)
    requested = stream.reconnect_requested_at is not None and now - stream.reconnect_requested_at < timedelta(
        seconds=SOURCE_CONNECT_SECONDS
    )
    if recently_live:
        return "reconnecting", "Source dropped; waiting for it to resume"
    if stream.source_kind == "obs":
        return "offline", "Waiting for the broadcaster"
    if stream.source_kind == "test" and not get_settings().test_source_available:
        return "error", "Test streams are disabled in this environment"
    if not stream.enabled:
        return "offline", "Starts on demand while disabled (preview to test)"
    if requested:
        return "connecting", "Connecting to the source"
    if stream.source_kind == "external":
        return "error", "No media from the external source; check the URL, access and codecs"
    return "error", "Test pattern generator is not running"


async def cached_paths() -> tuple[dict[str, dict[str, Any]], bool]:
    """MediaMTX path list shared by every API worker for STATE_CACHE_SECONDS."""
    redis = get_redis()
    cached = await redis.get("video:paths")
    if cached:
        data = json.loads(cached)
        return data["paths"], data["reachable"]
    try:
        async with MediaMTX() as mtx:
            paths, reachable = await mtx.paths(), True
    except MediaMTXUnavailable:
        paths, reachable = {}, False
    await redis.set("video:paths", json.dumps({"paths": paths, "reachable": reachable}), ex=STATE_CACHE_SECONDS)
    return paths, reachable


async def live_status(stream: VideoStream) -> tuple[str, str | None, PathState]:
    paths, reachable = await cached_paths()
    state = path_state(paths, stream.path_id)
    status, detail = compute_status(stream, state, datetime.now(UTC), reachable)
    return status, detail, state


# --------------------------------------------------------------------------- MediaMTX auth hook


async def stream_by_path(path_id: str) -> VideoStream | None:
    async with platform_session() as db:
        return await db.scalar(select(VideoStream).where(VideoStream.path_id == path_id))


async def authorize(request: dict[str, Any]) -> tuple[bool, str]:
    """Decide one MediaMTX authentication request. Returns (allowed, reason for logs)."""
    s = get_settings()
    action, protocol = request.get("action"), request.get("protocol")
    if action in ("api", "metrics"):
        limiter = f"video:api-fail:{request.get('ip') or 'unknown'}"
        redis = get_redis()
        if int(await redis.get(limiter) or 0) >= PUBLISH_FAILURE_LIMIT:
            return False, "too many failed API logins"
        password = s.mediamtx_api_password.get_secret_value() if s.mediamtx_api_password else ""
        ok = hmac.compare_digest(str(request.get("user") or ""), s.mediamtx_api_user) and hmac.compare_digest(
            str(request.get("password") or ""), password
        )
        if not (ok and password):
            if await redis.incr(limiter) == 1:
                await redis.expire(limiter, PUBLISH_FAILURE_WINDOW)
            return False, "invalid API credentials"
        return True, "api"
    if action not in ("publish", "read"):
        return False, "action not permitted"
    parsed = parse_path(request.get("path"))
    if parsed is None:
        return False, "unknown path"
    path_id, is_rtc = parsed
    if protocol == "rtsp":
        # RTSP listens on loopback inside the MediaMTX container only: the audio relay and
        # the development test pattern. Nothing outside the container can reach it.
        return request.get("ip") in LOOPBACK, "internal relay"
    if action == "publish":
        return await _authorize_publish(request, path_id, is_rtc)
    return await _authorize_read(request, path_id)


async def _authorize_publish(request: dict[str, Any], path_id: str, is_rtc: bool) -> tuple[bool, str]:
    if is_rtc or request.get("protocol") not in ("rtmp", "webrtc"):
        return False, "publishing not permitted on this path or protocol"
    redis = get_redis()
    limiter = f"video:publish-fail:{request.get('ip') or 'unknown'}"
    if int(await redis.get(limiter) or 0) >= PUBLISH_FAILURE_LIMIT:
        return False, "too many failed publish attempts"
    query = parse_qs(request.get("query") or "")
    stream = await stream_by_path(path_id)
    ok, reason = False, "invalid publish key"
    # Browser broadcasts (WHIP) carry a short-lived publish token in the query; OBS uses the key.
    token = (query.get("token") or [None])[0]
    if token is not None and request.get("protocol") == "webrtc":
        ok, reason = await _publish_token_allowed(verify_publish(token), stream, path_id)
    else:
        key = (query.get("key") or [None])[0] or request.get("password") or request.get("token") or ""
        ok = bool(
            key
            and stream is not None
            and stream.source_kind == "obs"
            and stream.publish_key_hash
            and hmac.compare_digest(key_hash(str(key)), stream.publish_key_hash)
        )
        reason = "publish key" if ok else "invalid publish key"
    if not ok:
        failures = await redis.incr(limiter)
        if failures == 1:
            await redis.expire(limiter, PUBLISH_FAILURE_WINDOW)
    return ok, reason


async def _publish_token_allowed(claims: dict[str, Any] | None, stream: VideoStream | None, path_id: str) -> tuple[bool, str]:
    from app.modules.video.models import VideoBroadcast

    if claims is None or claims.get("p") != path_id:
        return False, "missing, expired or foreign publish token"
    if stream is None or stream.source_kind != "obs" or str(stream.id) != claims["s"] or str(stream.tenant_id) != claims["t"]:
        return False, "stream not found"
    if await get_redis().exists(revoked_family_key(uuid.UUID(claims["f"]))):
        return False, "session revoked"
    async with platform_session() as db:
        broadcast = await db.get(VideoBroadcast, uuid.UUID(claims["b"]))
    if broadcast is None or broadcast.status == "ended" or broadcast.stream_id != stream.id:
        return False, "broadcast session ended"
    return True, "publish token"


async def _authorize_read(request: dict[str, Any], path_id: str) -> tuple[bool, str]:
    if request.get("protocol") not in ("webrtc", "hls"):
        return False, "reading permitted over WebRTC and HLS only"
    claims = verify_playback(request.get("token") or token_from_query(request.get("query")))
    if claims is None or claims["p"] != path_id:
        return False, "missing, expired or foreign playback token"
    if await get_redis().exists(revoked_family_key(uuid.UUID(claims["f"]))):
        return False, "session revoked"
    stream = await stream_by_path(path_id)
    if stream is None or str(stream.id) != claims["s"] or str(stream.tenant_id) != claims["t"]:
        return False, "stream not found"
    if claims["a"] == "player" and not stream.enabled:
        return False, "stream disabled for players"
    if claims["a"] == "player" and await at_capacity(stream):
        return False, "viewer limit reached"
    return True, "playback token"


async def at_capacity(stream: VideoStream) -> bool:
    """True when the stream already serves STREAM_MAX_VIEWERS sessions (staff are not limited)."""
    cap = get_settings().stream_max_viewers
    if not cap:
        return False
    paths, _ = await cached_paths()
    return path_state(paths, stream.path_id).viewers >= cap


# --------------------------------------------------------------------------- configuration push


async def active_recordings(stream_ids: list[uuid.UUID] | None = None) -> dict[uuid.UUID, Any]:
    """Running recordings by stream id (platform session: used outside tenant requests too)."""
    from app.modules.video.models import VideoRecording

    stmt = select(VideoRecording).where(VideoRecording.status == "recording", VideoRecording.stream_id.is_not(None))
    if stream_ids is not None:
        stmt = stmt.where(VideoRecording.stream_id.in_(stream_ids))
    async with platform_session() as db:
        return {r.stream_id: r for r in (await db.scalars(stmt)).all()}


_LOOKUP = object()


async def apply_stream(stream: VideoStream, mtx: MediaMTX | None = None, recording: Any = _LOOKUP) -> bool:
    """Push one stream's dynamic configuration now. False when MediaMTX is unreachable;
    the reconciliation task retries."""
    if not get_settings().streaming_configured:
        return False
    if recording is _LOOKUP:
        recording = (await active_recordings([stream.id])).get(stream.id)
    try:
        if mtx is None:
            async with MediaMTX() as client:
                return await apply_stream(stream, client, recording)
        desired = desired_config(stream, recording)
        if desired is None:
            await mtx.delete_path(stream_path(stream))
        else:
            await mtx.set_path(stream_path(stream), desired)
        await get_redis().delete("video:paths")
        return True
    except MediaMTXUnavailable as exc:
        log.warning("video_stream_apply_failed", stream_id=str(stream.id), error=str(exc))
        return False


async def disconnect(stream: VideoStream, *, publishers: bool, viewers: bool, remove_path: bool = False) -> None:
    """Kick sessions on a stream's paths (key rotation, disable, delete). Best effort."""
    if not get_settings().streaming_configured:
        return
    names = {stream_path(stream), stream_path(stream) + "/rtc"}
    try:
        async with MediaMTX() as mtx:
            if remove_path:
                await mtx.delete_path(stream_path(stream))
            if publishers:
                info = await mtx.path(stream_path(stream))
                source = (info or {}).get("source") or {}
                kind = {"rtmpConn": "rtmpconns", "webRTCSession": "webrtcsessions"}.get(source.get("type", ""))
                if kind:
                    await mtx.kick(kind, source["id"])
            if viewers:
                for kind in ("webrtcsessions", "hlssessions"):
                    for session in await mtx.sessions(kind):
                        if session.get("path") in names and session.get("state", "read") != "publish":
                            await mtx.kick(kind, session["id"])
        await get_redis().delete("video:paths")
    except MediaMTXUnavailable as exc:
        log.warning("video_stream_disconnect_failed", stream_id=str(stream.id), error=str(exc))


async def restart(stream: VideoStream) -> None:
    """Explicit reconnect: re-pull an external/test source, or drop the OBS publisher (OBS
    reconnects on its own). Viewers recover through the player's automatic reconnect."""
    if not get_settings().streaming_configured:
        return
    try:
        async with MediaMTX() as mtx:
            if stream.source_kind == "obs":
                info = await mtx.path(stream_path(stream))
                source = (info or {}).get("source") or {}
                kind = {"rtmpConn": "rtmpconns", "webRTCSession": "webrtcsessions"}.get(source.get("type", ""))
                if kind:
                    await mtx.kick(kind, source["id"])
            else:
                await mtx.delete_path(stream_path(stream))
                desired = desired_config(stream, (await active_recordings([stream.id])).get(stream.id))
                if desired is not None:
                    await mtx.set_path(stream_path(stream), desired)
        await get_redis().delete("video:paths")
    except MediaMTXUnavailable as exc:
        log.warning("video_stream_restart_failed", stream_id=str(stream.id), error=str(exc))


async def kick_broadcast(stream: VideoStream, broadcast_id: uuid.UUID) -> int:
    """Disconnect the browser publisher of one broadcast session now (best effort; the
    reconciliation task disconnects it within seconds otherwise)."""
    if not get_settings().streaming_configured:
        return 0
    kicked = 0
    try:
        async with MediaMTX() as mtx:
            for session in await mtx.sessions("webrtcsessions"):
                if session.get("state") != "publish" or session.get("path") != stream_path(stream):
                    continue
                claims = verify_publish(token_from_query(session.get("query")), allow_expired=True)
                if claims is not None and claims.get("b") == str(broadcast_id):
                    await mtx.kick("webrtcsessions", session["id"])
                    kicked += 1
        await get_redis().delete("video:paths")
    except MediaMTXUnavailable as exc:
        log.warning("video_broadcast_kick_failed", stream_id=str(stream.id), error=str(exc))
    return kicked


async def reconcile_one_disable(stream: VideoStream) -> int:
    """Disconnect the player sessions of a stream that was just disabled (admin previews and
    declarator monitors keep playing)."""
    if not get_settings().streaming_configured:
        return 0
    names = {stream_path(stream), stream_path(stream) + "/rtc"}
    kicked = 0
    try:
        async with MediaMTX() as mtx:
            for kind in ("webrtcsessions", "hlssessions"):
                for session in await mtx.sessions(kind):
                    if session.get("path") not in names or session.get("state") == "publish":
                        continue
                    claims = verify_playback(token_from_query(session.get("query")), allow_expired=True)
                    if claims is None or claims.get("a") == "player":
                        await mtx.kick(kind, session["id"])
                        kicked += 1
    except MediaMTXUnavailable as exc:
        log.warning("video_stream_disable_kick_failed", stream_id=str(stream.id), error=str(exc))
    return kicked


# --------------------------------------------------------------------------- reconciliation


async def _enforce_access(mtx: MediaMTX, by_path: dict[str, VideoStream], open_broadcasts: set[str] | None = None) -> int:
    """End sessions whose access ended after they started: viewers of a stream disabled for
    players or deleted, browser broadcasts whose session was stopped, and anyone whose login
    session was revoked (suspended, password reset, signed out everywhere)."""
    sessions = [(kind, s) for kind in ("webrtcsessions", "hlssessions") for s in await mtx.sessions(kind)]
    claims_by_id: dict[str, dict[str, Any] | None] = {}
    publish_claims: dict[str, dict[str, Any] | None] = {}
    families: set[str] = set()
    for kind, session in sessions:
        token = token_from_query(session.get("query"))
        if session.get("state") == "publish":
            # Only browser broadcasts carry a publish token; OBS (WHIP bearer key / RTMP) does not.
            if kind == "webrtcsessions" and token is not None:
                claims = verify_publish(token, allow_expired=True)
                publish_claims[session["id"]] = claims
                if claims:
                    families.add(claims["f"])
            continue
        claims = verify_playback(token, allow_expired=True)
        claims_by_id[session["id"]] = claims
        if claims:
            families.add(claims["f"])
    revoked: set[str] = set()
    if families:
        redis = get_redis()
        ordered = sorted(families)
        flags = [await redis.exists(revoked_family_key(uuid.UUID(f))) for f in ordered]
        revoked = {f for f, flag in zip(ordered, flags, strict=True) if flag}
    kicked = 0
    for kind, session in sessions:
        parsed = parse_path(session.get("path"))
        reason = None
        if session["id"] in publish_claims:
            claims = publish_claims[session["id"]]
            if claims is None or parsed is None or claims["p"] != parsed[0]:
                reason = "no valid publish token"
            elif claims["f"] in revoked:
                reason = "session revoked"
            elif open_broadcasts is not None and claims["b"] not in open_broadcasts:
                reason = "broadcast stopped"
        elif parsed is not None and session["id"] in claims_by_id:
            claims = claims_by_id[session["id"]]
            stream = by_path.get(parsed[0])
            if stream is None:
                reason = "stream removed"
            elif claims is None or claims["p"] != parsed[0]:
                reason = "no valid playback token"
            elif claims["a"] == "player" and not stream.enabled:
                reason = "stream disabled"
            elif claims["f"] in revoked:
                reason = "session revoked"
        if reason:
            await mtx.kick(kind, session["id"])
            kicked += 1
            log.info("video_session_disconnected", path=session.get("path"), state=session.get("state"), reason=reason)
    return kicked


async def reconcile() -> dict[str, int]:
    """One pass: apply recording limits, push missing configuration, refresh status/viewers,
    enforce access, then track broadcast sessions and recording files."""
    from app.modules.video import broadcasts, recordings

    if not get_settings().streaming_configured:
        return {"streams": 0}
    now = datetime.now(UTC)
    async with platform_session() as db:
        streams = list((await db.scalars(select(VideoStream))).all())
    by_path = {s.path_id: s for s in streams}
    active = await recordings.enforce_limits(now)
    open_broadcasts = await broadcasts.open_ids()
    kicked = 0
    try:
        async with MediaMTX() as mtx:
            configured = await mtx.configured_paths()
            known: set[str] = set()
            for stream in streams:
                desired = desired_config(stream, active.get(stream.id))
                if desired is None:
                    continue
                known.add(stream_path(stream))
                if config_differs(desired, configured.get(stream_path(stream))):
                    await mtx.set_path(stream_path(stream), desired)
            for name in configured:
                if name.startswith("live/") and name not in known:
                    await mtx.delete_path(name)
            paths = await mtx.paths()
            kicked = await _enforce_access(mtx, by_path, open_broadcasts)
        reachable = True
    except MediaMTXUnavailable as exc:
        log.warning("video_reconcile_unreachable", error=str(exc))
        paths, reachable = {}, False
    await get_redis().set("video:paths", json.dumps({"paths": paths, "reachable": reachable}), ex=STATE_CACHE_SECONDS)
    changed = 0
    async with platform_session() as db:
        for stream in streams:
            state = path_state(paths, stream.path_id)
            status, detail = compute_status(stream, state, now, reachable)
            values: dict[str, Any] = {"checked_at": now, "viewers": state.viewers if reachable else 0}
            if state.ready:
                values["last_live_at"] = now
                values["tracks"] = state.tracks
            if status != stream.status or detail != stream.status_detail:
                values.update(status=status, status_detail=detail, status_changed_at=now)
                changed += 1
                await publish_status(stream, status)
            await db.execute(update(VideoStream).where(VideoStream.id == stream.id).values(**values))
    if reachable:
        await broadcasts.track(streams, paths, now)
    await recordings.refresh(now)
    return {"streams": len(streams), "changed": changed, "kicked": kicked}


async def publish_status(stream: VideoStream, status: str) -> None:
    await publish(
        stream.tenant_id,
        "video.stream_status",
        {"stream_id": str(stream.id), "status": status, "event_id": str(stream.event_id) if stream.event_id else None},
    )


# --------------------------------------------------------------------------- lookups


async def stream_for_event(
    db: AsyncSession, tenant_id: uuid.UUID, event_id: uuid.UUID, *, audience: str | None = None
) -> VideoStream | None:
    """The event's own stream, else the tenant's default arena stream.

    Players only get streams visible to them: when the event's own stream is hidden they fall back
    to the default arena stream (if that one is visible) instead of losing the video."""
    rows = (
        await db.scalars(
            select(VideoStream).where(
                VideoStream.tenant_id == tenant_id,
                (VideoStream.event_id == event_id) | VideoStream.is_default,
            )
        )
    ).all()
    candidates = [r for r in rows if r.event_id == event_id] + [r for r in rows if r.is_default and r.event_id != event_id]
    if audience == "player":
        candidates = [r for r in candidates if r.enabled]
    return candidates[0] if candidates else None
