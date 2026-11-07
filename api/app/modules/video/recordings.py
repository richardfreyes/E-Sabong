"""Operator recordings: MediaMTX records a stream's path into a directory of its own while a
recording runs; this module starts/stops that, keeps metadata in PostgreSQL in step with the
files, applies the configured limits and retention, and signs short-lived file URLs.

Layout (shared volume): ``<RECORDINGS_ROOT>/<tenant_id>/<recording_id>/live/<path_id>/<start>.mp4``
— MediaMTX sees the same directory at ``MEDIAMTX_RECORDINGS_ROOT``. A recording may hold
several fMP4 segments: one per hour, and a new one whenever the publisher reconnects.
"""

import asyncio
import os
import re
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import platform_session
from app.modules.video import broadcasts, streams
from app.modules.video.models import VideoRecording

log = structlog.get_logger()

SEGMENT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(\d{6})\.mp4$")
PATH_DIR_RE = re.compile(r"^[A-Za-z0-9_-]{24}$")
# A stopped recording is finalized once MediaMTX has closed its files (it does so within ~1 s).
FINALIZE_AFTER_SECONDS = 6
FILE_TOKEN_TTL = 900
GB = 1024**3


def root() -> Path:
    return Path(get_settings().recordings_root)


def recording_dir(rec: VideoRecording) -> Path:
    return root() / str(rec.tenant_id) / str(rec.id)


# --------------------------------------------------------------------------- files


def scan(rec: VideoRecording) -> list[dict[str, Any]]:
    """Segments on disk, oldest first: file (relative to the recording directory), size,
    start time (from the name, UTC) and duration (last write minus start)."""
    base = recording_dir(rec) / "live"
    segments: list[dict[str, Any]] = []
    try:
        path_dirs = [d for d in base.iterdir() if d.is_dir() and PATH_DIR_RE.match(d.name)]
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return segments
    for d in path_dirs:
        for f in d.iterdir():
            match = SEGMENT_RE.match(f.name)
            if not match or not f.is_file():
                continue
            st = f.stat()
            started = datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S").replace(microsecond=int(match.group(2)), tzinfo=UTC)
            ended = datetime.fromtimestamp(st.st_mtime, UTC)
            segments.append(
                {
                    "file": f"live/{d.name}/{f.name}",
                    "size": st.st_size,
                    "started_at": started.isoformat(),
                    "duration": max(0, round((ended - started).total_seconds())),
                }
            )
    segments.sort(key=lambda s: s["started_at"])
    return segments


def segment_path(rec: VideoRecording, index: int) -> Path | None:
    """The file of segment ``index``, only if it is still inside this recording's directory."""
    segments = rec.segments or []
    if not 0 <= index < len(segments):
        return None
    base = recording_dir(rec).resolve()
    path = (base / str(segments[index]["file"])).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        return None
    return path


def delete_files(rec: VideoRecording) -> None:
    target = recording_dir(rec)
    if target.resolve().is_relative_to(root().resolve()) and target.exists():
        shutil.rmtree(target, ignore_errors=True)


def storage_writable() -> bool:
    r = root()
    return r.is_dir() and os.access(r, os.W_OK | os.X_OK)


def free_bytes() -> int | None:
    try:
        return shutil.disk_usage(root()).free
    except OSError:
        return None


async def tenant_usage(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    used = await db.scalar(
        select(func.coalesce(func.sum(VideoRecording.size_bytes), 0)).where(VideoRecording.tenant_id == tenant_id)
    )
    return int(used or 0)


async def storage_problem(db: AsyncSession, tenant_id: uuid.UUID) -> str | None:
    """Why a new recording cannot start now, or None."""
    s = get_settings()
    if not s.recording_configured:
        return "Recording is not enabled on this server"
    if not await asyncio.to_thread(storage_writable):
        return "Recording storage is not available on this server"
    free = await asyncio.to_thread(free_bytes)
    if free is not None and free < s.recording_min_free_gb * GB:
        return "The server is low on disk space; recording is paused until space is freed"
    if await tenant_usage(db, tenant_id) >= s.recording_tenant_quota_gb * GB:
        return f"Recording storage limit reached ({s.recording_tenant_quota_gb:g} GB); delete old recordings first"
    return None


# --------------------------------------------------------------------------- lifecycle


def new_recording(stream: Any, user_id: uuid.UUID) -> VideoRecording:
    now = datetime.now(UTC)
    return VideoRecording(
        id=uuid.uuid4(),
        tenant_id=stream.tenant_id,
        stream_id=stream.id,
        stream_name=stream.name,
        started_by_id=user_id,
        status="recording",
        started_at=now,
        expires_at=now + timedelta(days=get_settings().recording_retention_days),
        segments=[],
    )


def mark_stopped(rec: VideoRecording, *, by: uuid.UUID | None, detail: str | None = None, now: datetime | None = None) -> None:
    rec.status, rec.stopped_at, rec.stopped_by_id = "finalizing", now or datetime.now(UTC), by
    if detail:
        rec.detail = detail[:240]


def event_data(rec: VideoRecording) -> dict[str, Any]:
    return {"recording_id": str(rec.id), "stream_id": str(rec.stream_id) if rec.stream_id else None, "status": rec.status}


async def enforce_limits(now: datetime) -> dict[uuid.UUID, VideoRecording]:
    """Stop running recordings that hit the duration limit, the tenant quota or low disk; the
    rest are returned by stream id for the MediaMTX configuration."""
    s = get_settings()
    async with platform_session() as db:
        rows = list(
            (
                await db.scalars(
                    select(VideoRecording).where(VideoRecording.status == "recording").with_for_update(skip_locked=True)
                )
            ).all()
        )
        if not rows:
            return {}
        free = await asyncio.to_thread(free_bytes)
        low_disk = free is not None and free < s.recording_min_free_gb * GB
        usage: dict[uuid.UUID, int] = {}
        active: dict[uuid.UUID, VideoRecording] = {}
        stopped: list[VideoRecording] = []
        for rec in rows:
            if rec.tenant_id not in usage:
                usage[rec.tenant_id] = await tenant_usage(db, rec.tenant_id)
            reason = None
            if rec.stream_id is None:
                reason = "The stream was deleted while recording"
            elif now - rec.started_at >= timedelta(minutes=s.recording_max_minutes):
                reason = f"Stopped automatically at the {s.recording_max_minutes}-minute limit"
            elif low_disk:
                reason = "Stopped automatically: the server is low on disk space"
            elif usage[rec.tenant_id] >= s.recording_tenant_quota_gb * GB:
                reason = "Stopped automatically: the recording storage limit was reached"
            if reason:
                mark_stopped(rec, by=None, detail=reason, now=now)
                stopped.append(rec)
            else:
                active[rec.stream_id] = rec  # type: ignore[index]
        await db.flush()
        for rec in stopped:
            log.info("video_recording_auto_stopped", recording_id=str(rec.id), reason=rec.detail)
            await broadcasts.notify(db, rec.tenant_id, "video.recording", event_data(rec))
        return active


async def refresh(now: datetime) -> int:
    """Bring size, duration and segments of unfinished recordings up to date; finalize stopped
    ones (completed with files, failed without). Returns how many changed status."""
    finalized = 0
    async with platform_session() as db:
        rows = list(
            (
                await db.scalars(
                    select(VideoRecording)
                    .where(VideoRecording.status.in_(("recording", "finalizing")))
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for rec in rows:
            segments = await asyncio.to_thread(scan, rec)
            rec.segments = segments
            rec.size_bytes = sum(int(x["size"]) for x in segments)
            rec.duration_seconds = sum(int(x["duration"]) for x in segments)
            if rec.status != "finalizing" or now - (rec.stopped_at or now) < timedelta(seconds=FINALIZE_AFTER_SECONDS):
                continue
            if segments:
                rec.status = "completed"
            else:
                rec.status = "failed"
                rec.detail = rec.detail or (
                    "No video was recorded: the stream was not live, or the streaming server could not write to recording storage"
                )
            finalized += 1
            await broadcasts.notify(db, rec.tenant_id, "video.recording", event_data(rec))
    return finalized


async def purge(now: datetime | None = None) -> dict[str, int]:
    """Delete recordings past their retention date: files first, then the row."""
    now = now or datetime.now(UTC)
    removed = 0
    async with platform_session() as db:
        rows = list(
            (
                await db.scalars(
                    select(VideoRecording)
                    .where(VideoRecording.expires_at <= now, VideoRecording.status.in_(("completed", "failed")))
                    .limit(500)
                )
            ).all()
        )
        for rec in rows:
            await asyncio.to_thread(delete_files, rec)
            await db.execute(delete(VideoRecording).where(VideoRecording.id == rec.id))
            removed += 1
            log.info("video_recording_expired", recording_id=str(rec.id), tenant_id=str(rec.tenant_id))
    return {"removed": removed}


# --------------------------------------------------------------------------- file access


def sign_file_token(
    rec: VideoRecording, *, user_id: uuid.UUID, family: uuid.UUID, index: int, download: bool
) -> tuple[str, datetime]:
    claims = {"t": str(rec.tenant_id), "r": str(rec.id), "i": index, "u": str(user_id), "f": str(family), "d": download}
    return streams.sign_token(claims, streams.RECORDING_PURPOSE, FILE_TOKEN_TTL)


def verify_file_token(token: str | None) -> dict[str, Any] | None:
    return streams.verify_token(token, streams.RECORDING_PURPOSE)
