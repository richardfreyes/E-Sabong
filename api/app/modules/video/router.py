import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.config import get_settings
from app.core.deps import CurrentUserDep, Meta, TenantDb, require_module
from app.core.errors import Forbidden, NotFound, Unprocessable
from app.core.permissions import P
from app.core.redis import get_redis
from app.modules.audit import service as audit
from app.modules.declarator.service import access_event
from app.modules.events.models import PUBLISHED_EVENT_STATUSES, Event
from app.modules.realtime.publisher import publish_after_commit
from app.modules.tenants.admin_router import SettingsManager
from app.modules.video.models import VideoProvider
from app.modules.video.schemas import ProviderConfig, ProviderUpdate, VideoResult
from app.modules.video.service import ProviderFailure, encrypt_credentials, retrieve, validate_url

router = APIRouter(tags=["video"], dependencies=[Depends(require_module("events"))])
Action = Literal["playback", "refresh", "reconnect", "test", "event"]


async def row_for(db, tenant_id):
    return (await db.execute(select(VideoProvider).where(VideoProvider.tenant_id == tenant_id))).scalar_one_or_none()


def public_config(row):
    config = row.config if row else ProviderConfig().model_dump(mode="json")
    return {
        **config,
        "credentials_saved": bool(row and row.credentials),
        "status": row.status if row else "disabled",
        "checked_at": row.checked_at if row else None,
        # The admin form offers the demonstration adapter only where it can play.
        "demo_available": not get_settings().is_production,
    }


@router.get("/admin/settings/video-provider")
async def settings(current: SettingsManager, db: TenantDb):
    return public_config(await row_for(db, current.tenant.id))


@router.put("/admin/settings/video-provider")
async def save(body: ProviderUpdate, current: SettingsManager, db: TenantDb, meta: Meta):
    config = ProviderConfig.model_validate(body.model_dump(exclude={"username", "password", "clear_credentials"}))
    if config.adapter == "demo" and config.enabled and get_settings().is_production:
        raise Unprocessable("The demonstration video is not available in production; configure an authorized provider")
    if config.adapter != "demo":
        try:
            validate_url(config.api_base_url, get_settings().video_api_hosts)
        except ProviderFailure as exc:
            raise Unprocessable(exc.message) from None
        if not config.event_id:
            raise Unprocessable("Select the internal event assigned to this video source")
    if config.event_id:
        event = await db.scalar(
            select(Event).where(Event.tenant_id == current.tenant.id, Event.id == config.event_id, Event.deleted_at.is_(None))
        )
        if not event:
            raise NotFound("Event not found")
    row = await row_for(db, current.tenant.id)
    if row is None:
        row = VideoProvider(tenant_id=current.tenant.id)
        db.add(row)
    if body.clear_credentials:
        row.credentials = None
    elif body.username is not None or body.password is not None:
        if (
            body.username is None
            or body.password is None
            or not body.username.get_secret_value()
            or not body.password.get_secret_value()
        ):
            raise Unprocessable("Supply both username and password to replace credentials")
        row.credentials = encrypt_credentials(body.username.get_secret_value(), body.password.get_secret_value())
    row.config = config.model_dump(mode="json")
    row.status = "configured" if config.enabled else "disabled"
    row.checked_at = None
    await get_redis().delete(f"video:cache:{current.tenant.id}")
    await audit.record(
        db,
        actor=current.actor,
        action="video.configuration_updated",
        resource_type="video_provider",
        resource_id=current.tenant.id,
        tenant_id=current.tenant.id,
        meta=meta,
    )
    await db.flush()
    publish_after_commit(db, current.tenant.id, "video.source_changed", {})
    return public_config(row)


async def perform(db, current, action):
    row = await row_for(db, current.tenant.id)
    if not row:
        return VideoResult(status="disabled", message="Video temporarily unavailable — provider not configured")
    config = ProviderConfig.model_validate(row.config)
    if not config.enabled or config.adapter == "demo":
        result = await retrieve(config, row.credentials, action)
        row.status, row.checked_at = result.status, datetime.now(UTC)
        if action in ("refresh", "reconnect") and current.has(P.SETTINGS_MANAGE):
            publish_after_commit(db, current.tenant.id, "video.source_changed", {})
        return result
    redis = get_redis()
    version = hashlib.sha256((json.dumps(row.config, sort_keys=True) + (row.credentials or "")).encode()).hexdigest()
    key = f"video:cache:{current.tenant.id}:{version}"
    if action == "playback":
        cached = await redis.get(key)
        if cached:
            return VideoResult.model_validate_json(cached)
    # One upstream attempt per tenant per five seconds, across replicas, including failures.
    if not await redis.set(f"video:attempt:{current.tenant.id}", "1", nx=True, ex=5):
        return VideoResult(status="busy", message="Video source is busy. Please retry in a few seconds.")
    result = await retrieve(config, row.credentials, action)
    row.status, row.checked_at = result.status, datetime.now(UTC)
    if result.playback:
        ttl = 20
        if result.playback.expires_at:
            ttl = min(ttl, int((result.playback.expires_at - datetime.now(UTC)).total_seconds()) - 1)
        if ttl > 0:
            await redis.set(key, result.model_dump_json(), ex=ttl)
    elif not result.playback and action != "event":
        await redis.delete(key)
    if action in ("refresh", "reconnect") and current.has(P.SETTINGS_MANAGE):
        publish_after_commit(db, current.tenant.id, "video.source_changed", {})
    return result


@router.post("/admin/settings/video-provider/{action}", response_model=VideoResult)
async def admin_action(action: Action, current: SettingsManager, db: TenantDb):
    return await perform(db, current, action)


@router.post("/video/events/{event_id}/{action}", response_model=VideoResult)
async def playback(
    event_id: uuid.UUID,
    action: Action,
    current: CurrentUserDep,
    db: TenantDb,
    audience: Literal["player", "declarator"] = "player",
):
    if audience == "declarator":
        if not current.has_any(P.RESULTS_SUBMIT, P.RESULTS_APPROVE):
            raise Forbidden("Declarator access required")
        await access_event(db, current, event_id)
    else:
        if action in ("event", "test"):
            raise Forbidden("Declarator access required")
        event = await db.scalar(
            select(Event).where(
                Event.tenant_id == current.tenant.id,
                Event.id == event_id,
                Event.deleted_at.is_(None),
                Event.status.in_(PUBLISHED_EVENT_STATUSES),
            )
        )
        if not event:
            raise NotFound("Published event not found")
    # A MediaMTX stream assigned to this event (or the tenant's default arena stream) takes
    # precedence; without one, the external provider behaves exactly as before.
    from app.modules.video.stream_router import event_stream_result

    streamed = await event_stream_result(db, current, event_id, action, audience)
    if streamed is not None:
        return streamed
    row = await row_for(db, current.tenant.id)
    if row and row.config.get("event_id") and row.config["event_id"] != str(event_id):
        return VideoResult(status="unavailable", message="No video source assigned to this event")
    return await perform(db, current, action)
