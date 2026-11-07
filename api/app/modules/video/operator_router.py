"""Stream Operator dashboard API (``/operator/*``), Operator administration (``/admin/operators``)
and signed recording downloads.

Access: ``streams.operate``. Stream Operators reach only the streams an administrator assigned
to them; administrators (``settings.manage``) reach every stream of their workspace. Every
query is scoped to the caller's tenant (and RLS enforces it again).
"""

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.database import on_commit, platform_session
from app.core.deps import (
    CurrentUser,
    Meta,
    TenantDb,
    current_tenant_optional,
    require_module,
    require_permissions,
    revoked_family_key,
)
from app.core.errors import AppError, Conflict, Forbidden, NotFound, Unprocessable
from app.core.permissions import P
from app.core.redis import get_redis
from app.core.tenancy import TenantContext
from app.modules.audit import service as audit
from app.modules.events.models import Event
from app.modules.users.models import AccountType, TenantUser
from app.modules.video import broadcasts, recordings, streams
from app.modules.video.models import VideoBroadcast, VideoRecording, VideoStream, VideoStreamOperator
from app.modules.video.operator_schemas import (
    AssignStreams,
    BroadcastOut,
    BroadcastStart,
    ObsCredentials,
    OperatorAccount,
    OperatorIngest,
    OperatorOverview,
    OperatorServer,
    OperatorsOverview,
    OperatorStream,
    PublishGrant,
    RecordingAccess,
    RecordingFile,
    RecordingOut,
)
from app.modules.video.schemas import VideoResult
from app.modules.video.stream_router import _ingest, _require_streaming, _throttle_reconnect, stream_playback
from app.shared.schemas import Page, PageDep, paginate

log = structlog.get_logger()
router = APIRouter(tags=["video-operator"], dependencies=[Depends(require_module("events"))])
# No router-level tenant dependency: <video> and download links cannot send the dev tenant header,
# so the tenant comes from the signed link (and must match the host's tenant when there is one).
files_router = APIRouter(tags=["video-operator"])

Operator = Annotated[CurrentUser, Depends(require_permissions(P.STREAMS_OPERATE))]
OperatorAdmin = Annotated[CurrentUser, Depends(require_permissions(P.USERS_MANAGE, P.SETTINGS_MANAGE))]
# Encoder bitrate ceilings for browser broadcasts (H.264). They bound egress per viewer too.
BITRATE_KBPS = {"720p": 2500, "1080p": 4500}


# --------------------------------------------------------------------------- access helpers


def sees_all(current: CurrentUser) -> bool:
    """Administrators operate every stream; everyone else only assigned ones."""
    return current.has(P.SETTINGS_MANAGE) and current.user.account_type != AccountType.STREAM_OPERATOR


async def operable_ids(db, current: CurrentUser) -> set[uuid.UUID] | None:  # type: ignore[no-untyped-def]
    """Stream ids the caller may operate; None means all of the tenant's streams."""
    if sees_all(current):
        return None
    rows = await db.scalars(
        select(VideoStreamOperator.stream_id).where(
            VideoStreamOperator.tenant_id == current.tenant.id, VideoStreamOperator.user_id == current.id
        )
    )
    return set(rows.all())


async def operable_stream(db, current: CurrentUser, stream_id: uuid.UUID, *, lock: bool = False) -> VideoStream:  # type: ignore[no-untyped-def]
    allowed = await operable_ids(db, current)
    if allowed is not None and stream_id not in allowed:
        raise NotFound("Stream not found")
    stmt = select(VideoStream).where(VideoStream.tenant_id == current.tenant.id, VideoStream.id == stream_id)
    stream = await db.scalar(stmt.with_for_update() if lock else stmt)
    if stream is None:
        raise NotFound("Stream not found")
    return stream


async def visible_recording(db, current: CurrentUser, recording_id: uuid.UUID, *, lock: bool = False) -> VideoRecording:  # type: ignore[no-untyped-def]
    stmt = select(VideoRecording).where(VideoRecording.tenant_id == current.tenant.id, VideoRecording.id == recording_id)
    rec = await db.scalar(stmt.with_for_update() if lock else stmt)
    allowed = await operable_ids(db, current)
    # Recordings of a deleted stream stay visible to administrators only.
    if rec is None or (allowed is not None and rec.stream_id not in allowed):
        raise NotFound("Recording not found")
    return rec


async def names(db, tenant_id: uuid.UUID, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:  # type: ignore[no-untyped-def]
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = await db.execute(
        select(TenantUser.id, TenantUser.full_name).where(TenantUser.tenant_id == tenant_id, TenantUser.id.in_(wanted))
    )
    return dict(rows.all())  # type: ignore[arg-type]


def broadcast_out(b: VideoBroadcast | None, who: dict[uuid.UUID, str], me: uuid.UUID | None = None) -> BroadcastOut | None:
    if b is None:
        return None
    out = BroadcastOut.model_validate(b)
    out.operator_name = who.get(b.operator_id) if b.operator_id else None
    out.mine = me is not None and b.operator_id == me
    return out


def recording_out(rec: VideoRecording | None, who: dict[uuid.UUID, str]) -> RecordingOut | None:
    if rec is None:
        return None
    out = RecordingOut.model_validate(rec)
    out.started_by_name = who.get(rec.started_by_id) if rec.started_by_id else None
    out.segment_count = len(rec.segments or [])
    return out


def notify_after_commit(db, tenant_id: uuid.UUID, event: str, data: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    async def send() -> None:
        async with platform_session() as pdb:
            await broadcasts.notify(pdb, tenant_id, event, data)

    on_commit(db, send)


def publish_grant(stream: VideoStream, b: VideoBroadcast, current: CurrentUser, who: dict[uuid.UUID, str]) -> PublishGrant:
    token, expires = streams.sign_publish(
        tenant_id=stream.tenant_id,
        stream_id=stream.id,
        path_id=stream.path_id,
        user_id=current.id,
        family=current.session_family,
        broadcast_id=b.id,
    )
    return PublishGrant(
        broadcast=broadcast_out(b, who, current.id),  # type: ignore[arg-type]
        whip_url=f"/stream/webrtc/live/{stream.path_id}/whip?token={token}",
        expires_at=expires,
        ice_servers=streams.ice_servers(current.id),
        max_bitrate_kbps=BITRATE_KBPS.get(b.resolution or "720p", 2500),
    )


async def inbound_kbps(stream: VideoStream, state: streams.PathState) -> float | None:
    """Ingest bitrate from two samples of MediaMTX's received-byte counter."""
    if not state.ready:
        return None
    redis = get_redis()
    key = f"video:inbound:{stream.id}"
    now = datetime.now(UTC).timestamp()
    previous = await redis.get(key)
    await redis.set(key, json.dumps({"bytes": state.bytes_received, "at": now}), ex=120)
    if not previous:
        return None
    sample = json.loads(previous)
    elapsed = now - sample["at"]
    if elapsed < 1 or state.bytes_received < sample["bytes"]:
        return None
    return round((state.bytes_received - sample["bytes"]) * 8 / 1000 / elapsed, 1)


# --------------------------------------------------------------------------- dashboard


@router.get("/operator/overview", response_model=OperatorOverview)
async def overview(current: Operator, db: TenantDb) -> OperatorOverview:
    s = get_settings()
    tenant_id = current.tenant.id
    allowed = await operable_ids(db, current)
    stmt = select(VideoStream).where(VideoStream.tenant_id == tenant_id).order_by(VideoStream.created_at)
    if allowed is not None:
        stmt = stmt.where(VideoStream.id.in_(allowed or {uuid.uuid4()}))
    rows = list((await db.scalars(stmt)).all())
    ids = [r.id for r in rows]
    open_b = {
        b.stream_id: b
        for b in (
            await db.scalars(select(VideoBroadcast).where(VideoBroadcast.stream_id.in_(ids), VideoBroadcast.status != "ended"))
        ).all()
    }
    active_r = {
        r.stream_id: r
        for r in (
            await db.scalars(
                select(VideoRecording).where(VideoRecording.stream_id.in_(ids), VideoRecording.status == "recording")
            )
        ).all()
    }
    who = await names(db, tenant_id, {b.operator_id for b in open_b.values()} | {r.started_by_id for r in active_r.values()})
    event_names = dict(
        (
            await db.execute(
                select(Event.id, Event.name).where(
                    Event.tenant_id == tenant_id, Event.id.in_([r.event_id for r in rows if r.event_id])
                )
            )
        ).all()
    )
    paths, reachable = await streams.cached_paths() if s.streaming_configured else ({}, False)
    out: list[OperatorStream] = []
    now = datetime.now(UTC)
    for stream in rows:
        state = streams.path_state(paths, stream.path_id)
        status, detail = (
            streams.compute_status(stream, state, now, reachable)
            if s.streaming_configured
            else ("error", "Streaming is not configured")
        )
        ingest = None
        base = _ingest(stream)
        if base is not None:
            ingest = OperatorIngest(**base.model_dump(), publish_key_hint=stream.publish_key_hint)
        out.append(
            OperatorStream(
                id=stream.id,
                name=stream.name,
                source_kind=stream.source_kind,  # type: ignore[arg-type]
                visible_to_players=stream.enabled,
                event_name=event_names.get(stream.event_id) if stream.event_id else None,
                is_default=stream.is_default,
                status=status,  # type: ignore[arg-type]
                status_detail=detail,
                viewers=state.viewers,
                tracks=state.tracks or None,
                webrtc_ready=state.ready and (streams.webrtc_compatible(state.tracks) or state.rtc_ready),
                inbound_kbps=await inbound_kbps(stream, state),
                browser_publishing=stream.source_kind == "obs",
                ingest=ingest,
                broadcast=broadcast_out(open_b.get(stream.id), who, current.id),
                recording=recording_out(active_r.get(stream.id), who),
            )
        )
    problem = await recordings.storage_problem(db, tenant_id)
    server = OperatorServer(
        configured=s.streaming_configured,
        reachable=reachable,
        recording_available=problem is None,
        recording_problem=problem,
        rtmp_server=f"{s.stream_rtmp_url.rstrip('/')}/live" if s.stream_rtmp_url else None,
        rtmps_server=f"{s.stream_rtmps_url.rstrip('/')}/live" if s.stream_rtmps_url else None,
        max_viewers=s.stream_max_viewers,
        retention_days=s.recording_retention_days,
        max_recording_minutes=s.recording_max_minutes,
        quota_bytes=int(s.recording_tenant_quota_gb * recordings.GB),
        used_bytes=await recordings.tenant_usage(db, tenant_id),
        checked_at=now,
    )
    return OperatorOverview(server=server, streams=out)


@router.get("/operator/broadcasts", response_model=Page[BroadcastOut])
async def list_broadcasts(current: Operator, db: TenantDb, params: PageDep) -> Page[BroadcastOut]:
    """Streaming session history: sessions on the caller's streams and the caller's own sessions
    (kept after an unassignment); administrators see every session of the workspace."""
    stmt = select(VideoBroadcast).where(VideoBroadcast.tenant_id == current.tenant.id)
    allowed = await operable_ids(db, current)
    if allowed is not None:
        mine = VideoBroadcast.operator_id == current.id
        stmt = stmt.where(VideoBroadcast.stream_id.in_(allowed) | mine if allowed else mine)
    rows, total = await paginate(db, stmt.order_by(VideoBroadcast.started_at.desc(), VideoBroadcast.id), params)
    who = await names(db, current.tenant.id, {b.operator_id for b in rows})
    items = [broadcast_out(b, who, current.id) for b in rows]
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)  # type: ignore[arg-type]


@router.post("/operator/streams/{stream_id}/broadcast", response_model=PublishGrant, status_code=201)
async def start_broadcast(
    stream_id: uuid.UUID, body: BroadcastStart, current: Operator, db: TenantDb, meta: Meta
) -> PublishGrant:
    """Open a browser broadcast and hand out its WHIP publish URL (token valid 5 minutes to
    connect; the session then runs until stopped)."""
    _require_streaming()
    stream = await operable_stream(db, current, stream_id, lock=True)
    if stream.source_kind != "obs":
        raise Unprocessable("Browser broadcasting is available on OBS/WHIP streams only", code="browser_unsupported")
    _, _, state = await streams.live_status(stream)
    if state.ready and not body.takeover and await broadcasts.open_for(db, stream.id) is None:
        # Live from OBS before the reconciliation task recorded the session.
        raise Conflict(
            "An encoder is already live on this stream. Take over to replace it.", code="stream_busy", details={"method": "obs"}
        )
    b, replaced = await broadcasts.start_browser(
        db, stream, current.id, body.resolution, takeover_by=current.user.username if body.takeover else None
    )
    await audit.record(
        db,
        actor=current.actor,
        action="video.broadcast_started",
        resource_type="video_stream",
        resource_id=stream.id,
        tenant_id=current.tenant.id,
        changes={
            "broadcast_id": str(b.id),
            "resolution": b.resolution,
            "takeover": body.takeover,
            "replaced": str(replaced.id) if replaced else None,
        },
        meta=meta,
    )
    notify_after_commit(db, current.tenant.id, "video.broadcast", broadcasts.event_data(b))
    return publish_grant(stream, b, current, await names(db, current.tenant.id, {current.id}))


@router.post("/operator/streams/{stream_id}/broadcast/token", response_model=PublishGrant)
async def broadcast_token(stream_id: uuid.UUID, current: Operator, db: TenantDb) -> PublishGrant:
    """A fresh publish URL for the caller's own open broadcast (reconnect after a drop)."""
    _require_streaming()
    stream = await operable_stream(db, current, stream_id)
    b = await broadcasts.open_for(db, stream.id)
    if b is None or b.method != "browser" or b.operator_id != current.id:
        raise Conflict("This broadcast has ended; start a new one", code="broadcast_ended")
    return publish_grant(stream, b, current, await names(db, current.tenant.id, {current.id}))


@router.post("/operator/streams/{stream_id}/broadcast/stop", response_model=BroadcastOut | None)
async def stop_broadcast(stream_id: uuid.UUID, current: Operator, db: TenantDb, meta: Meta) -> BroadcastOut | None:
    stream = await operable_stream(db, current, stream_id, lock=True)
    b = await broadcasts.open_for(db, stream.id, lock=True)
    if b is None:
        return None
    if b.method != "browser":
        raise Unprocessable(
            "This stream is live from OBS or another encoder; stop it in that software", code="external_publisher"
        )
    broadcasts.end(b, f"Stopped by {current.user.username}")
    await db.flush()
    await audit.record(
        db,
        actor=current.actor,
        action="video.broadcast_stopped",
        resource_type="video_stream",
        resource_id=stream.id,
        tenant_id=current.tenant.id,
        changes={"broadcast_id": str(b.id)},
        meta=meta,
    )

    async def kick() -> None:
        await streams.kick_broadcast(stream, b.id)

    on_commit(db, kick)
    notify_after_commit(db, current.tenant.id, "video.broadcast", broadcasts.event_data(b))
    return broadcast_out(b, await names(db, current.tenant.id, {b.operator_id}), current.id)


@router.post("/operator/streams/{stream_id}/obs-key", response_model=ObsCredentials)
async def obs_key(stream_id: uuid.UUID, current: Operator, db: TenantDb, meta: Meta) -> ObsCredentials:
    """Issue a new OBS publish key (the previous one stops working). Shown once."""
    stream = await operable_stream(db, current, stream_id, lock=True)
    if stream.source_kind != "obs":
        raise Unprocessable("Only OBS/WHIP streams have a publish key", code="no_publish_key")
    key = streams.new_publish_key()
    stream.publish_key_hash, stream.publish_key_hint, stream.key_rotated_at = streams.key_hash(key), key[-4:], datetime.now(UTC)
    browser_live = (b := await broadcasts.open_for(db, stream.id)) is not None and b.method == "browser"
    await audit.record(
        db,
        actor=current.actor,
        action="video.stream_key_rotated",
        resource_type="video_stream",
        resource_id=stream.id,
        tenant_id=current.tenant.id,
        changes={"via": "operator_dashboard"},
        meta=meta,
    )
    if not browser_live:
        # The encoder using the old key is disconnected and cannot publish again.
        async def drop_publisher() -> None:
            await streams.disconnect(stream, publishers=True, viewers=False)

        on_commit(db, drop_publisher)
    s = get_settings()
    return ObsCredentials(
        rtmp_server=f"{s.stream_rtmp_url.rstrip('/')}/live" if s.stream_rtmp_url else None,
        rtmps_server=f"{s.stream_rtmps_url.rstrip('/')}/live" if s.stream_rtmps_url else None,
        stream_key=f"{stream.path_id}?key={key}",
        whip_url=f"/stream/webrtc/live/{stream.path_id}/whip",
        whip_bearer_token=key,
    )


@router.post("/operator/streams/{stream_id}/reconnect", status_code=202)
async def reconnect(stream_id: uuid.UUID, current: Operator, db: TenantDb, meta: Meta) -> Response:
    """Re-pull an external/test source, or drop the OBS connection so the encoder reconnects.
    Browser broadcasts reconnect from the dashboard itself."""
    _require_streaming()
    stream = await operable_stream(db, current, stream_id)
    b = await broadcasts.open_for(db, stream.id)
    if b is not None and b.method == "browser":
        raise Unprocessable("Browser broadcasts reconnect from the dashboard", code="browser_reconnect")
    await _throttle_reconnect(stream)
    stream.reconnect_requested_at = datetime.now(UTC)
    await audit.record(
        db,
        actor=current.actor,
        action="video.stream_reconnect",
        resource_type="video_stream",
        resource_id=stream.id,
        tenant_id=current.tenant.id,
        changes={"via": "operator_dashboard"},
        meta=meta,
    )

    async def restart() -> None:
        await streams.restart(stream)

    on_commit(db, restart)
    return Response(status_code=202)


@router.post("/operator/streams/{stream_id}/preview", response_model=VideoResult)
async def preview(stream_id: uuid.UUID, current: Operator, db: TenantDb) -> VideoResult:
    """What players see, for the operator's monitor (works before the stream is visible)."""
    _require_streaming()
    stream = await operable_stream(db, current, stream_id)
    return await stream_playback(stream, current, "operator")


# --------------------------------------------------------------------------- recordings


@router.post("/operator/streams/{stream_id}/recordings", response_model=RecordingOut, status_code=201)
async def start_recording(stream_id: uuid.UUID, current: Operator, db: TenantDb, meta: Meta) -> RecordingOut:
    _require_streaming()
    stream = await operable_stream(db, current, stream_id, lock=True)
    problem = await recordings.storage_problem(db, current.tenant.id)
    if problem:
        raise Unprocessable(problem, code="recording_unavailable")
    _, _, state = await streams.live_status(stream)
    if not state.ready:
        raise Conflict("Start streaming first: recording captures a live stream", code="stream_not_live")
    running = await db.scalar(
        select(VideoRecording.id).where(VideoRecording.stream_id == stream.id, VideoRecording.status == "recording")
    )
    if running is not None:
        raise Conflict("This stream is already being recorded", code="recording_active")
    rec = recordings.new_recording(stream, current.id)
    db.add(rec)
    try:
        await db.flush()
    except IntegrityError:
        raise Conflict("This stream is already being recorded", code="recording_active") from None
    # Fail the request (and roll back) when MediaMTX does not take the recording settings.
    if not await streams.apply_stream(stream, recording=rec):
        raise AppError("The streaming server did not start the recording; try again", code="recording_failed", status_code=503)
    await audit.record(
        db,
        actor=current.actor,
        action="video.recording_started",
        resource_type="video_recording",
        resource_id=rec.id,
        tenant_id=current.tenant.id,
        changes={"stream_id": str(stream.id), "stream": stream.name},
        meta=meta,
    )

    async def settle() -> None:
        # Closes the race with a reconciliation pass that read the recordings before this commit.
        await streams.apply_stream(stream)

    on_commit(db, settle)
    notify_after_commit(db, current.tenant.id, "video.recording", recordings.event_data(rec))
    await db.refresh(rec)
    return recording_out(rec, await names(db, current.tenant.id, {current.id}))  # type: ignore[return-value]


@router.post("/operator/recordings/{recording_id}/stop", response_model=RecordingOut)
async def stop_recording(recording_id: uuid.UUID, current: Operator, db: TenantDb, meta: Meta) -> RecordingOut:
    rec = await visible_recording(db, current, recording_id, lock=True)
    if rec.status == "recording":
        recordings.mark_stopped(rec, by=current.id)
        await db.flush()
        await audit.record(
            db,
            actor=current.actor,
            action="video.recording_stopped",
            resource_type="video_recording",
            resource_id=rec.id,
            tenant_id=current.tenant.id,
            changes={"stream_id": str(rec.stream_id) if rec.stream_id else None},
            meta=meta,
        )
        stream = await db.scalar(
            select(VideoStream).where(VideoStream.tenant_id == current.tenant.id, VideoStream.id == rec.stream_id)
        )
        if stream is not None:
            # Best effort now (the reconciliation task removes the setting within seconds otherwise).
            await streams.apply_stream(stream, recording=None)

            async def settle() -> None:
                await streams.apply_stream(stream)

            on_commit(db, settle)
        notify_after_commit(db, current.tenant.id, "video.recording", recordings.event_data(rec))
    await db.refresh(rec)
    return recording_out(rec, await names(db, current.tenant.id, {rec.started_by_id}))  # type: ignore[return-value]


@router.get("/operator/recordings", response_model=Page[RecordingOut])
async def list_recordings(
    current: Operator,
    db: TenantDb,
    params: PageDep,
    stream_id: uuid.UUID | None = None,
    status: Annotated[str | None, Query(pattern="^(recording|finalizing|completed|failed)$")] = None,
) -> Page[RecordingOut]:
    stmt = select(VideoRecording).where(VideoRecording.tenant_id == current.tenant.id)
    allowed = await operable_ids(db, current)
    if allowed is not None:
        stmt = stmt.where(VideoRecording.stream_id.in_(allowed or {uuid.uuid4()}))
    if stream_id is not None:
        stmt = stmt.where(VideoRecording.stream_id == stream_id)
    if status is not None:
        stmt = stmt.where(VideoRecording.status == status)
    rows, total = await paginate(db, stmt.order_by(VideoRecording.started_at.desc(), VideoRecording.id), params)
    who = await names(db, current.tenant.id, {r.started_by_id for r in rows})
    return Page(items=[recording_out(r, who) for r in rows], total=total, page=params.page, page_size=params.page_size)  # type: ignore[misc]


@router.post("/operator/recordings/{recording_id}/access", response_model=RecordingAccess)
async def recording_access(recording_id: uuid.UUID, current: Operator, db: TenantDb, meta: Meta) -> RecordingAccess:
    """Short-lived play and download links for each file of a finished recording."""
    rec = await visible_recording(db, current, recording_id)
    if rec.status != "completed":
        raise Conflict("The recording is not ready to play yet", code="recording_not_ready")
    files: list[RecordingFile] = []
    expires = datetime.now(UTC)
    base = f"/api/v1/video/recordings/{rec.id}/files"
    for index, seg in enumerate(rec.segments or []):
        play, expires = recordings.sign_file_token(
            rec, user_id=current.id, family=current.session_family, index=index, download=False
        )
        down, _ = recordings.sign_file_token(rec, user_id=current.id, family=current.session_family, index=index, download=True)
        files.append(
            RecordingFile(
                index=index,
                started_at=seg["started_at"],
                duration=seg["duration"],
                size=seg["size"],
                play_url=f"{base}/{index}?token={play}",
                download_url=f"{base}/{index}?token={down}",
            )
        )
    await audit.record(
        db,
        actor=current.actor,
        action="video.recording_accessed",
        resource_type="video_recording",
        resource_id=rec.id,
        tenant_id=current.tenant.id,
        meta=meta,
    )
    who = await names(db, current.tenant.id, {rec.started_by_id})
    return RecordingAccess(recording=recording_out(rec, who), files=files, expires_at=expires)  # type: ignore[arg-type]


@router.delete("/operator/recordings/{recording_id}", status_code=204)
async def delete_recording(recording_id: uuid.UUID, current: Operator, db: TenantDb, meta: Meta) -> Response:
    if not sees_all(current):
        raise Forbidden("Only an administrator can delete recordings", code="permission_denied")
    rec = await visible_recording(db, current, recording_id, lock=True)
    if rec.status in ("recording", "finalizing"):
        raise Conflict("Stop the recording before deleting it", code="recording_active")
    await db.delete(rec)
    await audit.record(
        db,
        actor=current.actor,
        action="video.recording_deleted",
        resource_type="video_recording",
        resource_id=rec.id,
        tenant_id=current.tenant.id,
        changes={"stream": rec.stream_name, "size_bytes": rec.size_bytes},
        meta=meta,
    )

    async def remove_files() -> None:
        import asyncio

        await asyncio.to_thread(recordings.delete_files, rec)

    on_commit(db, remove_files)
    return Response(status_code=204)


_FILENAME = re.compile(r"[^A-Za-z0-9_-]+")


@files_router.get("/video/recordings/{recording_id}/files/{index}", include_in_schema=False)
async def recording_file(
    recording_id: uuid.UUID,
    index: int,
    host_tenant: Annotated[TenantContext | None, Depends(current_tenant_optional)],
    token: Annotated[str, Query(max_length=1024)] = "",
) -> FileResponse:
    """Serve one recording file to the holder of a link from ``/access`` (``<video>`` elements and
    downloads cannot send the bearer token). Supports HTTP range requests for seeking.

    The link is HMAC-signed for one tenant, recording and file; on a tenant's own host (always
    in production) the host's tenant must be that tenant too."""
    from app.core.database import tenant_session
    from app.modules.tenants.service import effective_modules

    claims = recordings.verify_file_token(token)
    missing = NotFound("Recording not found or the link expired")
    if claims is None or claims["r"] != str(recording_id) or claims["i"] != index:
        raise missing
    tenant_id = uuid.UUID(claims["t"])
    if host_tenant is not None and host_tenant.id != tenant_id:
        raise missing
    if await get_redis().exists(revoked_family_key(uuid.UUID(claims["f"]))):
        raise missing
    if "events" not in await effective_modules(tenant_id):
        raise missing
    async with tenant_session(tenant_id) as db:
        rec = await db.scalar(
            select(VideoRecording).where(VideoRecording.tenant_id == tenant_id, VideoRecording.id == recording_id)
        )
    if rec is None or rec.status != "completed":
        raise missing
    path = recordings.segment_path(rec, index)
    if path is None:
        raise missing
    stamp = rec.started_at.strftime("%Y%m%d-%H%M%S")
    name = f"{_FILENAME.sub('-', rec.stream_name).strip('-') or 'recording'}-{stamp}"
    if len(rec.segments) > 1:
        name += f"-part{index + 1}"
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"{name}.mp4",
        content_disposition_type="attachment" if claims.get("d") else "inline",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


# --------------------------------------------------------------------------- administration


@router.get("/admin/operators", response_model=OperatorsOverview)
async def operators_overview(current: OperatorAdmin, db: TenantDb) -> OperatorsOverview:
    tenant_id = current.tenant.id
    users = list(
        (
            await db.scalars(
                select(TenantUser)
                .where(
                    TenantUser.tenant_id == tenant_id,
                    TenantUser.account_type == AccountType.STREAM_OPERATOR,
                    TenantUser.deleted_at.is_(None),
                )
                .order_by(TenantUser.username)
            )
        ).all()
    )
    assignments: dict[uuid.UUID, list[uuid.UUID]] = {}
    for row in (await db.scalars(select(VideoStreamOperator).where(VideoStreamOperator.tenant_id == tenant_id))).all():
        assignments.setdefault(row.user_id, []).append(row.stream_id)
    stream_rows = list(
        (await db.scalars(select(VideoStream).where(VideoStream.tenant_id == tenant_id).order_by(VideoStream.created_at))).all()
    )
    active = list(
        (
            await db.scalars(
                select(VideoBroadcast)
                .where(VideoBroadcast.tenant_id == tenant_id, VideoBroadcast.status != "ended")
                .order_by(VideoBroadcast.started_at.desc())
            )
        ).all()
    )
    recent = list(
        (
            await db.scalars(
                select(VideoBroadcast)
                .where(VideoBroadcast.tenant_id == tenant_id, VideoBroadcast.status == "ended")
                .order_by(VideoBroadcast.ended_at.desc())
                .limit(25)
            )
        ).all()
    )
    running = list(
        (
            await db.scalars(
                select(VideoRecording)
                .where(VideoRecording.tenant_id == tenant_id, VideoRecording.status.in_(("recording", "finalizing")))
                .order_by(VideoRecording.started_at.desc())
            )
        ).all()
    )
    who = await names(db, tenant_id, {b.operator_id for b in active + recent} | {r.started_by_id for r in running})
    by_operator = {b.operator_id: b for b in active if b.operator_id}
    return OperatorsOverview(
        operators=[
            OperatorAccount(
                id=u.id,
                username=u.username,
                full_name=u.full_name,
                status=str(u.status),
                last_login_at=u.last_login_at,
                created_at=u.created_at,
                must_change_password=u.must_change_password,
                stream_ids=assignments.get(u.id, []),
                broadcast=broadcast_out(by_operator.get(u.id), who),
            )
            for u in users
        ],
        streams=[
            {"id": str(s.id), "name": s.name, "source_kind": s.source_kind, "status": s.status, "enabled": s.enabled}
            for s in stream_rows
        ],
        active_sessions=[broadcast_out(b, who) for b in active],  # type: ignore[misc]
        recent_sessions=[broadcast_out(b, who) for b in recent],  # type: ignore[misc]
        active_recordings=[recording_out(r, who) for r in running],  # type: ignore[misc]
    )


@router.put("/admin/operators/{user_id}/streams", response_model=OperatorAccount)
async def assign_streams(
    user_id: uuid.UUID, body: AssignStreams, current: OperatorAdmin, db: TenantDb, meta: Meta
) -> OperatorAccount:
    """Replace the streams a Stream Operator may broadcast and record."""
    tenant_id = current.tenant.id
    user = await db.scalar(
        select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.id == user_id, TenantUser.deleted_at.is_(None))
    )
    if user is None:
        raise NotFound("Account not found")
    if user.account_type != AccountType.STREAM_OPERATOR:
        raise Unprocessable("Streams can only be assigned to Stream Operator accounts", code="not_stream_operator")
    wanted = set(body.stream_ids)
    found = set(
        (await db.scalars(select(VideoStream.id).where(VideoStream.tenant_id == tenant_id, VideoStream.id.in_(wanted)))).all()
    )
    if found != wanted:
        raise NotFound("Stream not found")
    rows = list(
        (
            await db.scalars(
                select(VideoStreamOperator).where(
                    VideoStreamOperator.tenant_id == tenant_id, VideoStreamOperator.user_id == user_id
                )
            )
        ).all()
    )
    have = {r.stream_id for r in rows}
    removed, added = have - wanted, wanted - have
    for r in rows:
        if r.stream_id in removed:
            await db.delete(r)
    for stream_id in sorted(added):
        db.add(
            VideoStreamOperator(
                id=uuid.uuid4(), tenant_id=tenant_id, stream_id=stream_id, user_id=user_id, assigned_by_id=current.id
            )
        )
    ended: list[VideoBroadcast] = []
    if removed:
        # Their browser broadcasts on streams they lose end now (the publisher is disconnected).
        for b in (
            await db.scalars(
                select(VideoBroadcast).where(
                    VideoBroadcast.stream_id.in_(removed),
                    VideoBroadcast.operator_id == user_id,
                    VideoBroadcast.status != "ended",
                )
            )
        ).all():
            broadcasts.end(b, "Stream unassigned by an administrator")
            ended.append(b)
    await db.flush()
    if added or removed:
        await audit.record(
            db,
            actor=current.actor,
            action="video.operator_streams_assigned",
            resource_type="tenant_user",
            resource_id=user.id,
            tenant_id=tenant_id,
            changes={"added": sorted(str(i) for i in added), "removed": sorted(str(i) for i in removed)},
            meta=meta,
        )
        notify_after_commit(db, tenant_id, "video.assignments", {"user_id": str(user.id)})
    for b in ended:
        notify_after_commit(db, tenant_id, "video.broadcast", broadcasts.event_data(b))
    return OperatorAccount(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        status=str(user.status),
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        must_change_password=user.must_change_password,
        stream_ids=sorted(wanted),
    )
