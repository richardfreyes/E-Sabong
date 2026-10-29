"""WebSocket endpoint for live match status, results and personal notifications.

Clients connect to ``/api/ws?token=<access token>`` on their tenant's host. The
socket receives tenant-wide broadcasts plus messages addressed to the user, and
is closed when the access token expires (the client reconnects with a fresh one).
"""

import asyncio
import contextlib
import json
from typing import Annotated

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.database import tenant_session
from app.core.deps import revoked_family_key
from app.core.errors import AppError
from app.core.redis import get_redis
from app.core.security import decode_access_token, utcnow
from app.core.tenancy import resolve_tenant
from app.modules.auth.models import AccountStatus
from app.modules.realtime.publisher import tenant_channel, user_channel
from app.modules.users.models import TenantUser

router = APIRouter()
log = structlog.get_logger("ws")

CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_TOKEN_EXPIRED = 4408
REVOCATION_CHECK_SECONDS = 30


@router.websocket("/api/ws")
async def websocket_endpoint(
    websocket: WebSocket, token: str = "", dev_slug: Annotated[str | None, Query(alias="tenant")] = None
) -> None:
    await websocket.accept()
    tenant = await resolve_tenant(websocket, dev_slug)  # type: ignore[arg-type]
    if tenant is None or not tenant.is_operational:
        await websocket.close(code=CLOSE_FORBIDDEN, reason="workspace unavailable")
        return
    try:
        claims = decode_access_token(token, "tenant")
    except AppError:
        await websocket.close(code=CLOSE_UNAUTHORIZED, reason="invalid token")
        return
    if claims.tenant_id != tenant.id:
        await websocket.close(code=CLOSE_UNAUTHORIZED, reason="invalid token")
        return
    redis = get_redis()
    if await redis.exists(revoked_family_key(claims.session_family)):
        await websocket.close(code=CLOSE_UNAUTHORIZED, reason="session revoked")
        return
    async with tenant_session(tenant.id) as db:
        user = (
            await db.execute(select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.id == claims.subject_id))
        ).scalar_one_or_none()
        if user is None or user.deleted_at is not None or user.status != AccountStatus.ACTIVE:
            await websocket.close(code=CLOSE_UNAUTHORIZED, reason="account inactive")
            return

    pubsub = redis.pubsub()
    await pubsub.subscribe(tenant_channel(tenant.id), user_channel(tenant.id, claims.subject_id))
    await websocket.send_json({"type": "connected", "data": {"tenant": tenant.slug}})

    async def forward() -> None:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            await websocket.send_text(message["data"])

    async def receive() -> None:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    async def guard() -> None:
        while True:
            remaining = (claims.expires_at - utcnow()).total_seconds()
            if remaining <= 0:
                await websocket.close(code=CLOSE_TOKEN_EXPIRED, reason="token expired")
                return
            await asyncio.sleep(min(remaining, REVOCATION_CHECK_SECONDS))
            if await redis.exists(revoked_family_key(claims.session_family)):
                await websocket.close(code=CLOSE_UNAUTHORIZED, reason="session revoked")
                return

    tasks = [asyncio.create_task(t()) for t in (forward, receive, guard)]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, WebSocketDisconnect | RuntimeError):
                log.warning("ws_task_error", error=repr(exc))
    finally:
        for t in tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe()
            await pubsub.aclose()
