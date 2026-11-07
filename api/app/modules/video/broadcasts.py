"""Streaming sessions (``video_broadcasts``): who is broadcasting what, and since when.

A browser broadcast is opened by an operator and owns a short-lived WHIP publish token; the
session survives brief drops (the dashboard reconnects with a fresh token) and ends on Stop, on
takeover, or when no media arrives within the grace period. OBS, external and test sessions are
opened and closed by the reconciliation task from what MediaMTX reports, so the admin view of
active sessions covers every way a stream can be live.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import platform_session
from app.core.errors import Conflict
from app.core.permissions import P
from app.modules.realtime.publisher import publish, users_with_permission
from app.modules.video.models import VideoBroadcast, VideoStream
from app.modules.video.streams import RECONNECT_GRACE_SECONDS, PathState, path_state

log = structlog.get_logger()

# A browser session that never delivers media is closed after this long.
CONNECT_TIMEOUT_SECONDS = 60


async def notify(db: AsyncSession, tenant_id: uuid.UUID, event: str, data: dict[str, Any]) -> None:
    """Operator-dashboard events go to the accounts that may operate streams (operators and
    administrators) on their personal channels, never tenant-wide to players."""
    for user_id in sorted(await users_with_permission(db, tenant_id, P.STREAMS_OPERATE)):
        await publish(tenant_id, event, data, user_id=user_id)


def event_data(b: VideoBroadcast) -> dict[str, Any]:
    return {
        "stream_id": str(b.stream_id) if b.stream_id else None,
        "broadcast_id": str(b.id),
        "status": b.status,
        "method": b.method,
    }


async def open_for(db: AsyncSession, stream_id: uuid.UUID, *, lock: bool = False) -> VideoBroadcast | None:
    stmt = select(VideoBroadcast).where(VideoBroadcast.stream_id == stream_id, VideoBroadcast.status != "ended")
    return await db.scalar(stmt.with_for_update() if lock else stmt)


async def open_ids() -> set[str]:
    """Open browser broadcasts: a WHIP publisher whose session is not in here is disconnected."""
    async with platform_session() as db:
        rows = await db.scalars(
            select(VideoBroadcast.id).where(VideoBroadcast.status != "ended", VideoBroadcast.method == "browser")
        )
        return {str(i) for i in rows.all()}


def end(b: VideoBroadcast, reason: str, now: datetime | None = None) -> None:
    b.status, b.ended_at, b.end_reason = "ended", now or datetime.now(UTC), reason[:160]


async def start_browser(
    db: AsyncSession, stream: VideoStream, operator_id: uuid.UUID, resolution: str, *, takeover_by: str | None
) -> tuple[VideoBroadcast, VideoBroadcast | None]:
    """Open a browser session. Returns (new session, session it replaced or None).

    Another live session on the stream is only replaced when the caller asked to take over
    (``takeover_by`` names them for the replaced session's end reason)."""
    current = await open_for(db, stream.id, lock=True)
    replaced = None
    if current is not None:
        mine = current.method == "browser" and current.operator_id == operator_id
        if not mine and takeover_by is None:
            raise Conflict(
                "Someone is already broadcasting on this stream. Take over to replace their broadcast.",
                code="stream_busy",
                details={"method": current.method, "status": current.status},
            )
        end(current, "Restarted by the same operator" if mine else f"Taken over by {takeover_by}")
        replaced = current
        await db.flush()
    broadcast = VideoBroadcast(
        id=uuid.uuid4(),
        tenant_id=stream.tenant_id,
        stream_id=stream.id,
        stream_name=stream.name,
        operator_id=operator_id,
        method="browser",
        resolution=resolution,
        status="connecting",
        started_at=datetime.now(UTC),
    )
    db.add(broadcast)
    try:
        await db.flush()
    except IntegrityError:
        raise Conflict("Another broadcast started on this stream at the same moment; try again", code="stream_busy") from None
    return broadcast, replaced


async def track(streams: list[VideoStream], paths: dict[str, dict[str, Any]], now: datetime) -> None:
    """Open, promote and close sessions from MediaMTX's view of each stream (one pass)."""
    by_id = {s.id: s for s in streams}
    async with platform_session() as db:
        rows = list((await db.scalars(select(VideoBroadcast).where(VideoBroadcast.status != "ended"))).all())
        open_by_stream = {b.stream_id: b for b in rows if b.stream_id is not None}
        changed: list[VideoBroadcast] = []
        for b in rows:
            if b.stream_id is None or b.stream_id not in by_id:
                end(b, "Stream deleted", now)
                changed.append(b)
        for stream in streams:
            state = path_state(paths, stream.path_id)
            b = open_by_stream.get(stream.id)
            if b is not None and b.status == "ended":
                continue
            outcome = _advance(stream, b, state, now)
            if outcome is None:
                continue
            if b is None:
                db.add(outcome)
            changed.append(outcome)
        if not changed:
            return
        try:
            await db.flush()
        except IntegrityError:
            # An operator opened a session for the same stream during this pass; next pass settles it.
            await db.rollback()
            return
        for b in changed:
            await notify(db, b.tenant_id, "video.broadcast", event_data(b))


def _advance(stream: VideoStream, b: VideoBroadcast | None, state: PathState, now: datetime) -> VideoBroadcast | None:
    """The session to save for this stream after this observation, or None when unchanged."""
    if state.ready:
        if b is None:
            method = stream.source_kind if stream.source_kind in ("external", "test") else "obs"
            return VideoBroadcast(
                id=uuid.uuid4(),
                tenant_id=stream.tenant_id,
                stream_id=stream.id,
                stream_name=stream.name,
                method=method,
                status="live",
                started_at=now,
                live_at=now,
                last_seen_at=now,
            )
        b.last_seen_at = now
        if b.status == "connecting":
            b.status, b.live_at = "live", now
            return b
        return None
    if b is None:
        return None
    if b.status == "connecting" and now - b.started_at > timedelta(seconds=CONNECT_TIMEOUT_SECONDS):
        end(b, "No video arrived from the browser", now)
        return b
    if b.status == "live" and now - (b.last_seen_at or b.started_at) > timedelta(seconds=RECONNECT_GRACE_SECONDS):
        end(b, "Source disconnected", now)
        return b
    return None
