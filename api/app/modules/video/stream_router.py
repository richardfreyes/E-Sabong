"""Live stream administration, player-safe playback and the MediaMTX auth callback."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.database import on_commit
from app.core.deps import CurrentUser, Meta, TenantDb, require_module
from app.core.errors import AppError, Conflict, NotFound, TooManyRequests, Unprocessable
from app.core.redis import get_redis
from app.modules.audit import service as audit
from app.modules.events.models import Event
from app.modules.realtime.publisher import publish_after_commit
from app.modules.tenants.admin_router import SettingsManager
from app.modules.video import streams
from app.modules.video.models import VideoStream
from app.modules.video.schemas import VideoResult
from app.modules.video.service import ProviderFailure, public_ip
from app.modules.video.stream_schemas import (
    Ingest,
    StreamBrief,
    StreamCreate,
    StreamHealth,
    StreamOut,
    StreamPlayback,
    StreamUpdate,
    StreamWithKey,
)

log = structlog.get_logger()
router = APIRouter(tags=["video"], dependencies=[Depends(require_module("events"))])
internal_router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)

STATUS_MESSAGES = {
    "live": "Live",
    "offline": "Stream offline — waiting for the broadcast",
    "connecting": "Connecting to the live stream",
    "reconnecting": "Live stream interrupted — reconnecting",
    "error": "Live stream unavailable",
}
FORWARDING_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded", "x-real-ip")


def _require_streaming() -> None:
    if not get_settings().streaming_configured:
        raise AppError(
            "Live streaming is not configured on this server (MEDIAMTX_API_PASSWORD)",
            code="streaming_unconfigured",
            status_code=503,
        )


def _ingest(stream: VideoStream) -> Ingest | None:
    if stream.source_kind != "obs":
        return None
    s = get_settings()
    return Ingest(
        rtmp_server=f"{s.stream_rtmp_url.rstrip('/')}/live" if s.stream_rtmp_url else None,
        rtmps_server=f"{s.stream_rtmps_url.rstrip('/')}/live" if s.stream_rtmps_url else None,
        stream_key_format=f"{stream.path_id}?key=<publish key>",
        whip_path=f"/stream/webrtc/live/{stream.path_id}/whip",
    )


async def _out(db, stream: VideoStream, *, live: bool = True) -> StreamOut:  # type: ignore[no-untyped-def]
    out = StreamOut.model_validate(stream)
    out.ingest = _ingest(stream)
    if stream.event_id:
        out.event_name = await db.scalar(
            select(Event.name).where(Event.tenant_id == stream.tenant_id, Event.id == stream.event_id)
        )
    if live and get_settings().streaming_configured:
        status, detail, state = await streams.live_status(stream)
        out.status, out.status_detail, out.viewers = status, detail, state.viewers  # type: ignore[assignment]
        out.webrtc_ready = state.ready and (streams.webrtc_compatible(state.tracks) or state.rtc_ready)
        if state.ready:
            out.tracks = state.tracks
    return out


async def _get(db, tenant_id: uuid.UUID, stream_id: uuid.UUID, *, lock: bool = False) -> VideoStream:  # type: ignore[no-untyped-def]
    stmt = select(VideoStream).where(VideoStream.tenant_id == tenant_id, VideoStream.id == stream_id)
    stream = await db.scalar(stmt.with_for_update() if lock else stmt)
    if stream is None:
        raise NotFound("Stream not found")
    return stream


async def _check_event(db, tenant_id: uuid.UUID, event_id: uuid.UUID) -> None:  # type: ignore[no-untyped-def]
    found = await db.scalar(
        select(Event.id).where(Event.tenant_id == tenant_id, Event.id == event_id, Event.deleted_at.is_(None))
    )
    if found is None:
        raise NotFound("Event not found")


async def _apply_source(stream: VideoStream, url: str) -> None:
    if len(url) > 2000:
        raise Unprocessable("The source URL is too long")
    try:
        protocol, host = streams.validate_source_url(url)
    except streams.StreamConfigError as exc:
        raise Unprocessable(str(exc), code="invalid_source") from None
    try:
        # MediaMTX pulls from inside the server's network: refuse names that resolve to private,
        # loopback or reserved addresses (also enforce egress rules at the network layer).
        await public_ip(host)
    except ProviderFailure as exc:
        raise Unprocessable(exc.message, code="invalid_source") from None
    stream.source_url = streams.encrypt_source(url)
    stream.source_protocol, stream.source_host = protocol, host


async def _clear_default(db, tenant_id: uuid.UUID, keep: uuid.UUID | None) -> None:  # type: ignore[no-untyped-def]
    stmt = update(VideoStream).where(VideoStream.tenant_id == tenant_id, VideoStream.is_default)
    if keep is not None:
        stmt = stmt.where(VideoStream.id != keep)
    await db.execute(stmt.values(is_default=False))


async def _flush_unique(db) -> None:  # type: ignore[no-untyped-def]
    try:
        await db.flush()
    except IntegrityError as exc:
        text = str(exc.orig)
        if "uq_video_streams_event" in text:
            raise Conflict("Another stream is already assigned to this event", code="event_has_stream") from None
        if "uq_video_streams_name" in text:
            raise Conflict("A stream with this name already exists", code="duplicate_name") from None
        raise


def _after_commit_apply(db, stream: VideoStream, *, changed: bool = True) -> None:  # type: ignore[no-untyped-def]
    async def push() -> None:
        await streams.apply_stream(stream)

    on_commit(db, push)
    if changed:
        publish_after_commit(db, stream.tenant_id, "video.source_changed", {"stream_id": str(stream.id)})


# --------------------------------------------------------------------------- administration


@router.get("/admin/streams/health", response_model=StreamHealth)
async def health(current: SettingsManager, db: TenantDb) -> StreamHealth:
    s = get_settings()
    now = datetime.now(UTC)
    rows = list((await db.scalars(select(VideoStream).where(VideoStream.tenant_id == current.tenant.id))).all())
    base = {
        "configured": s.streaming_configured,
        "test_source_available": s.test_source_available,
        "rtmp_server": f"{s.stream_rtmp_url.rstrip('/')}/live" if s.stream_rtmp_url else None,
        "rtmps_server": f"{s.stream_rtmps_url.rstrip('/')}/live" if s.stream_rtmps_url else None,
        "streams": len(rows),
        "checked_at": now,
    }
    if not s.streaming_configured:
        return StreamHealth(**base, reachable=False, detail="Set MEDIAMTX_API_PASSWORD to enable live streaming")
    paths, reachable = await streams.cached_paths()
    if not reachable:
        return StreamHealth(**base, reachable=False, detail="Streaming server unreachable")
    names = {f"live/{r.path_id}" for r in rows} | {f"live/{r.path_id}/rtc" for r in rows}
    mine = [info for name, info in paths.items() if name in names]
    readers = [r for info in mine for r in info.get("readers") or []]
    outbound = sum(int(info.get("outboundBytes") or info.get("bytesSent") or 0) for info in mine)
    redis = get_redis()
    key = f"video:health:{current.tenant.id}"
    previous = await redis.get(key)
    await redis.set(key, json.dumps({"bytes": outbound, "at": now.timestamp()}), ex=600)
    kbps = None
    if previous:
        sample = json.loads(previous)
        elapsed = now.timestamp() - sample["at"]
        if elapsed >= 1 and outbound >= sample["bytes"]:
            kbps = round((outbound - sample["bytes"]) * 8 / 1000 / elapsed, 1)
    return StreamHealth(
        **base,
        reachable=True,
        live=sum(1 for r in rows if streams.path_state(paths, r.path_id).ready),
        viewers=sum(1 for r in readers if r.get("type") in streams.VIEWER_READERS),
        webrtc_sessions=sum(1 for r in readers if r.get("type") == "webrtcSession"),
        hls_sessions=sum(1 for r in readers if r.get("type") == "hlsSession"),
        publishers=sum(1 for info in mine if (info.get("source") or {}).get("type") in ("rtmpConn", "webRTCSession")),
        outbound_kbps=kbps,
    )


@router.get("/admin/streams", response_model=list[StreamOut])
async def list_streams(current: SettingsManager, db: TenantDb) -> list[StreamOut]:
    rows = (
        await db.scalars(select(VideoStream).where(VideoStream.tenant_id == current.tenant.id).order_by(VideoStream.created_at))
    ).all()
    return [await _out(db, r) for r in rows]


@router.post("/admin/streams", response_model=StreamWithKey, status_code=201)
async def create_stream(body: StreamCreate, current: SettingsManager, db: TenantDb, meta: Meta) -> StreamWithKey:
    _require_streaming()
    s = get_settings()
    count = await db.scalar(select(func.count()).select_from(VideoStream).where(VideoStream.tenant_id == current.tenant.id))
    if (count or 0) >= s.stream_max_per_tenant:
        raise Unprocessable(f"This workspace already has {s.stream_max_per_tenant} streams", code="stream_limit")
    if body.source_kind == "test" and not s.test_source_available:
        raise Unprocessable("Test streams are only available in development", code="test_source_disabled")
    if body.event_id:
        await _check_event(db, current.tenant.id, body.event_id)
    now = datetime.now(UTC)
    stream = VideoStream(
        id=uuid.uuid4(),
        tenant_id=current.tenant.id,
        name=body.name,
        source_kind=body.source_kind,
        path_id=streams.new_path_id(),
        event_id=body.event_id,
        is_default=body.is_default,
        enabled=body.enabled,
        status="offline",
        status_changed_at=now,
        reconnect_requested_at=now,
        viewers=0,
    )
    key = None
    if body.source_kind == "external":
        if body.source_url is None:
            raise Unprocessable("Enter the authorized source URL", code="invalid_source")
        await _apply_source(stream, body.source_url.get_secret_value())
    elif body.source_url is not None:
        raise Unprocessable("Only external streams take a source URL", code="invalid_source")
    if body.source_kind == "obs":
        key = streams.new_publish_key()
        stream.publish_key_hash, stream.publish_key_hint, stream.key_rotated_at = streams.key_hash(key), key[-4:], now
    if body.is_default:
        await _clear_default(db, current.tenant.id, None)
    db.add(stream)
    await _flush_unique(db)
    await db.refresh(stream)  # load database-generated timestamps for the response
    await audit.record(
        db,
        actor=current.actor,
        action="video.stream_created",
        resource_type="video_stream",
        resource_id=stream.id,
        tenant_id=current.tenant.id,
        changes={
            "name": stream.name,
            "source_kind": stream.source_kind,
            "source_host": stream.source_host,
            "event_id": str(stream.event_id) if stream.event_id else None,
            "is_default": stream.is_default,
            "enabled": stream.enabled,
        },
        meta=meta,
    )
    _after_commit_apply(db, stream)
    out = StreamWithKey(**(await _out(db, stream, live=False)).model_dump())
    if key:
        out.publish_key, out.obs_stream_key = key, f"{stream.path_id}?key={key}"
    return out


@router.patch("/admin/streams/{stream_id}", response_model=StreamOut)
async def update_stream(
    stream_id: uuid.UUID, body: StreamUpdate, current: SettingsManager, db: TenantDb, meta: Meta
) -> StreamOut:
    stream = await _get(db, current.tenant.id, stream_id, lock=True)
    sent = body.model_fields_set
    changes: dict[str, Any] = {}
    now = datetime.now(UTC)
    was_enabled = stream.enabled
    if "name" in sent and body.name is not None and body.name != stream.name:
        changes["name"] = [stream.name, body.name]
        stream.name = body.name
    if "source_url" in sent and body.source_url is not None:
        if stream.source_kind != "external":
            raise Unprocessable("Only external streams take a source URL", code="invalid_source")
        _require_streaming()
        await _apply_source(stream, body.source_url.get_secret_value())
        changes["source"] = "replaced"  # never the URL: it may embed provider credentials
        stream.reconnect_requested_at = now
    if "event_id" in sent and body.event_id != stream.event_id:
        if body.event_id is not None:
            await _check_event(db, current.tenant.id, body.event_id)
        changes["event_id"] = [str(stream.event_id) if stream.event_id else None, str(body.event_id) if body.event_id else None]
        stream.event_id = body.event_id
    if "is_default" in sent and body.is_default is not None and body.is_default != stream.is_default:
        if body.is_default:
            await _clear_default(db, current.tenant.id, stream.id)
        changes["is_default"] = [stream.is_default, body.is_default]
        stream.is_default = body.is_default
    if "enabled" in sent and body.enabled is not None and body.enabled != stream.enabled:
        changes["enabled"] = [stream.enabled, body.enabled]
        stream.enabled = body.enabled
        if body.enabled:
            stream.reconnect_requested_at = now
    if not changes:
        return await _out(db, stream)
    await _flush_unique(db)
    await db.refresh(stream)  # updated_at is set by the database
    await audit.record(
        db,
        actor=current.actor,
        action="video.stream_updated",
        resource_type="video_stream",
        resource_id=stream.id,
        tenant_id=current.tenant.id,
        changes=changes,
        meta=meta,
    )
    _after_commit_apply(db, stream)
    if was_enabled and not stream.enabled:
        # Players watching a stream that was just disabled are disconnected now; the
        # reconciliation task would otherwise do it within a few seconds.
        async def drop_players() -> None:
            await streams.reconcile_one_disable(stream)

        on_commit(db, drop_players)
    return await _out(db, stream, live=False)


@router.delete("/admin/streams/{stream_id}", status_code=204)
async def delete_stream(stream_id: uuid.UUID, current: SettingsManager, db: TenantDb, meta: Meta) -> Response:
    stream = await _get(db, current.tenant.id, stream_id, lock=True)
    await db.delete(stream)
    await audit.record(
        db,
        actor=current.actor,
        action="video.stream_deleted",
        resource_type="video_stream",
        resource_id=stream.id,
        tenant_id=current.tenant.id,
        changes={"name": stream.name},
        meta=meta,
    )

    async def remove() -> None:
        await streams.disconnect(stream, publishers=True, viewers=True, remove_path=True)

    on_commit(db, remove)
    publish_after_commit(db, current.tenant.id, "video.source_changed", {"stream_id": str(stream.id)})
    return Response(status_code=204)


@router.post("/admin/streams/{stream_id}/rotate-key", response_model=StreamWithKey)
async def rotate_key(stream_id: uuid.UUID, current: SettingsManager, db: TenantDb, meta: Meta) -> StreamWithKey:
    stream = await _get(db, current.tenant.id, stream_id, lock=True)
    if stream.source_kind != "obs":
        raise Unprocessable("Only OBS/WHIP streams have a publish key", code="no_publish_key")
    key = streams.new_publish_key()
    stream.publish_key_hash, stream.publish_key_hint, stream.key_rotated_at = streams.key_hash(key), key[-4:], datetime.now(UTC)
    await audit.record(
        db,
        actor=current.actor,
        action="video.stream_key_rotated",
        resource_type="video_stream",
        resource_id=stream.id,
        tenant_id=current.tenant.id,
        meta=meta,
    )

    async def drop_publisher() -> None:
        # The broadcaster using the old key is disconnected and cannot publish again.
        await streams.disconnect(stream, publishers=True, viewers=False)

    on_commit(db, drop_publisher)
    out = StreamWithKey(**(await _out(db, stream, live=False)).model_dump())
    out.publish_key, out.obs_stream_key = key, f"{stream.path_id}?key={key}"
    return out


@router.post("/admin/streams/{stream_id}/reconnect", response_model=StreamOut)
async def reconnect(stream_id: uuid.UUID, current: SettingsManager, db: TenantDb, meta: Meta) -> StreamOut:
    _require_streaming()
    stream = await _get(db, current.tenant.id, stream_id, lock=True)
    await _throttle_reconnect(stream)
    stream.reconnect_requested_at = datetime.now(UTC)
    await audit.record(
        db,
        actor=current.actor,
        action="video.stream_reconnect",
        resource_type="video_stream",
        resource_id=stream.id,
        tenant_id=current.tenant.id,
        meta=meta,
    )

    async def restart() -> None:
        await streams.restart(stream)

    on_commit(db, restart)
    return await _out(db, stream, live=False)


@router.post("/admin/streams/{stream_id}/preview", response_model=VideoResult)
async def preview(stream_id: uuid.UUID, current: SettingsManager, db: TenantDb) -> VideoResult:
    """Administrator playback that works before the stream is enabled for players."""
    _require_streaming()
    stream = await _get(db, current.tenant.id, stream_id)
    return await stream_playback(stream, current, "admin")


async def _throttle_reconnect(stream: VideoStream) -> None:
    if not await get_redis().set(f"video:reconnect:{stream.id}", "1", nx=True, ex=10):
        raise TooManyRequests("A reconnect was just requested; try again in a few seconds", code="reconnect_throttled")


# --------------------------------------------------------------------------- playback


async def stream_playback(stream: VideoStream, current: CurrentUser, audience: str) -> VideoResult:
    status, detail, state = await streams.live_status(stream)
    brief = StreamBrief(id=stream.id, name=stream.name, status=status, detail=detail)  # type: ignore[arg-type]
    if audience != "player":
        brief.viewers = state.viewers
    # Disabled external/test sources start on demand (no CPU or bandwidth while nobody
    # watches). An administrator preview or declarator monitor is that demand: hand out playback
    # and MediaMTX starts the source when the session asks for it. Players never get here
    # (disabled streams are not offered to them).
    on_demand = status == "offline" and audience != "player" and not stream.enabled and stream.source_kind in ("external", "test")
    if status != "live" and not on_demand:
        return VideoResult(status=status, message=STATUS_MESSAGES[status], stream=brief)
    if audience == "player" and await streams.at_capacity(stream):
        # STREAM_MAX_VIEWERS protects the server's bandwidth; the auth callback enforces it too.
        return VideoResult(status="error", message="The live stream is at capacity; trying again shortly", stream=brief)
    token, expires = streams.sign_playback(
        tenant_id=current.tenant.id,
        stream_id=stream.id,
        path_id=stream.path_id,
        user_id=current.id,
        family=current.session_family,
        audience=audience,
    )
    path = streams.stream_path(stream)
    # AAC (OBS's default) cannot play over WebRTC; read the Opus relay when it is needed and up.
    webrtc_path = path if streams.webrtc_compatible(state.tracks) or not state.rtc_ready else f"{path}/rtc"
    playback = StreamPlayback(
        url=f"/stream/webrtc/{webrtc_path}/whep?token={token}",
        fallback_url=f"/stream/hls/{path}/index.m3u8?token={token}",
        expires_at=expires,
        key=str(stream.id),
        ice_servers=streams.ice_servers(current.id),
    )
    if on_demand:
        return VideoResult(status="connecting", message="Starting the source for this preview", playback=playback, stream=brief)
    return VideoResult(status="live", message=STATUS_MESSAGES["live"], playback=playback, stream=brief)


async def event_stream_result(db, current: CurrentUser, event_id: uuid.UUID, action: str, audience: str) -> VideoResult | None:  # type: ignore[no-untyped-def]
    """Playback for the stream assigned to an event (or the default arena stream).

    None when no MediaMTX stream applies, so the caller falls back to the external provider.
    Players only see enabled streams; declarators monitor their assigned event's stream
    before it is opened to players.
    """
    from app.core.database import platform_session
    from app.modules.tenants.rental import settings as rental_settings

    rental = await rental_settings()
    if rental and rental.shared_domain_enabled:
        async with platform_session() as global_db:
            stream = await global_db.get(VideoStream, rental.global_stream_id) if rental.global_stream_id else None
        if stream is None or not stream.enabled:
            return VideoResult(status="disabled", message="The global stream is offline")
        return await stream_playback(stream, current, audience)
    if not get_settings().streaming_configured:
        return None
    stream = await streams.stream_for_event(db, current.tenant.id, event_id, audience=audience)
    if stream is None:
        return None
    if action == "event":
        status, detail, state = await streams.live_status(stream)
        return VideoResult(
            status=status,
            message=STATUS_MESSAGES[status],
            metadata={"name": stream.name, "status": status, "viewers": state.viewers},
            stream=StreamBrief(id=stream.id, name=stream.name, status=status, detail=detail, viewers=state.viewers),  # type: ignore[arg-type]
        )
    if action == "reconnect" and audience == "declarator" and stream.source_kind != "obs":
        # A declarator may ask the server to re-pull its assigned external/test source when it
        # is not live. OBS publishers reconnect by themselves; nothing to restart server-side.
        status, _, _ = await streams.live_status(stream)
        if status != "live" and await get_redis().set(f"video:reconnect:{stream.id}", "1", nx=True, ex=10):
            await db.execute(
                update(VideoStream).where(VideoStream.id == stream.id).values(reconnect_requested_at=datetime.now(UTC))
            )
            await streams.restart(stream)
    return await stream_playback(stream, current, audience)


# --------------------------------------------------------------------------- MediaMTX callback


@internal_router.post("/mediamtx/auth")
async def mediamtx_auth(request: Request) -> Response:
    """MediaMTX asks here before every publisher/viewer session and control-API call.

    No secret travels in the URL. The route is unreachable from outside (Caddy answers 404 for
    /api/v1/internal/*), refuses anything that came through a proxy, and only answers
    allow (200) or deny (401); granting still needs a valid publish key or playback token.
    """
    if not get_settings().streaming_configured or any(h in request.headers for h in FORWARDING_HEADERS):
        return Response(status_code=404)
    body = await request.body()
    if len(body) > 16384:
        return Response(status_code=413)
    try:
        payload = json.loads(body)
    except ValueError:
        return Response(status_code=400)
    if not isinstance(payload, dict):
        return Response(status_code=400)
    allowed, reason = await streams.authorize(payload)
    log.info(
        "mediamtx_auth",
        allowed=allowed,
        reason=reason,
        action=payload.get("action"),
        protocol=payload.get("protocol"),
        path=str(payload.get("path") or "")[:80],
        ip=payload.get("ip"),
    )
    return Response(status_code=200 if allowed else 401)
