"""Authenticated native WebSocket rooms for neutral simulations (shared Redis pub/sub)."""

import asyncio
import contextlib
import json
import math
import uuid
from typing import Annotated

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.database import tenant_session
from app.core.deps import CurrentUser, revoked_family_key
from app.core.errors import AppError, Forbidden
from app.core.redis import get_pubsub_redis, get_redis
from app.core.security import decode_access_token, utcnow
from app.core.tenancy import resolve_tenant
from app.modules.simulations import service
from app.modules.simulations.models import SimulationMatch, SimulationState
from app.modules.tenants.service import effective_modules
from app.modules.users.models import TenantUser

router = APIRouter()


@router.websocket("/api/ws/simulations")
async def simulation_socket(
    websocket: WebSocket, token: str = "", dev_slug: Annotated[str | None, Query(alias="tenant")] = None
) -> None:
    await websocket.accept()
    tenant = await resolve_tenant(websocket, dev_slug)  # type: ignore[arg-type]
    authorization = websocket.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization[7:]
    try:
        claims = decode_access_token(token, "tenant")
        if tenant is None or not tenant.is_operational or claims.tenant_id != tenant.id:
            raise Forbidden("Workspace unavailable")
        if not {"events", "matches"} <= set(await effective_modules(tenant.id)):
            raise Forbidden("Simulation modules are unavailable")
        redis = get_redis()
        if await redis.exists(revoked_family_key(claims.session_family)):
            raise Forbidden("Session revoked")
        async with tenant_session(tenant.id) as db:
            user = await db.get(TenantUser, claims.subject_id)
            if user is None:
                raise Forbidden("Account unavailable")
            current = CurrentUser(user, frozenset(), tenant, claims.session_family)
            await service.actor_now(db, current)
    except AppError:
        await websocket.close(code=4401, reason="Authentication or workspace unavailable")
        return

    rooms: dict[str, uuid.UUID] = {}
    acknowledged: dict[str, asyncio.Event] = {}
    ticks: dict[str, tuple[uuid.UUID, int, int]] = {}
    send_lock = asyncio.Lock()
    pubsub = get_pubsub_redis().pubsub()
    # Keep one private subscription alive so the listener can acknowledge room joins
    # before SUBSCRIBED is sent. It carries no application data.
    private_channel = f"simulation:connection:{uuid.uuid4()}"
    await pubsub.subscribe(private_channel)

    async def send(payload: dict) -> None:
        async with send_lock:
            await asyncio.wait_for(websocket.send_json(payload), 5)

    async def active_actor(db) -> CurrentUser:  # type: ignore[no-untyped-def]
        if claims.expires_at <= utcnow() or await redis.exists(revoked_family_key(claims.session_family)):
            raise Forbidden("Session expired or revoked")
        context = await resolve_tenant(websocket, dev_slug)  # type: ignore[arg-type]
        if context is None or not context.is_operational:
            raise Forbidden("Workspace unavailable")
        if not {"events", "matches"} <= set(await effective_modules(tenant.id)):
            raise Forbidden("Simulation modules are unavailable")
        return await service.actor_now(db, current)

    async def authorize(arena_id: uuid.UUID) -> None:
        async with tenant_session(tenant.id) as db:
            actor = await active_actor(db)
            await service.arena_access(db, actor, arena_id)

    async def receive() -> None:
        while True:
            raw = await websocket.receive_text()
            if len(raw) > 4096:
                await send({"type": "ERROR", "code": "message_too_large"})
                continue
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                kind = data.get("type")
                if kind in ("PING", "ping"):
                    await send({"type": "PONG"})
                    continue
                arena_id = uuid.UUID(data.get("arenaId", ""))
                channel = service.room(tenant.id, arena_id)
                if data.get("room", channel) != channel or str(data.get("tenantId", tenant.id)) != str(tenant.id):
                    raise Forbidden("Room is outside this workspace")
                if kind == "UNSUBSCRIBE":
                    rooms.pop(channel, None)
                    ticks.pop(channel, None)
                    await pubsub.unsubscribe(channel)
                    await send({"type": "UNSUBSCRIBED", "room": channel})
                    continue
                if kind != "SUBSCRIBE":
                    raise ValueError("Unsupported message type")
                await authorize(arena_id)
                if channel not in rooms:
                    if len(rooms) >= 8:
                        raise Forbidden("Room subscription limit reached")
                    event = acknowledged[channel] = asyncio.Event()
                    rooms[channel] = arena_id
                    await pubsub.subscribe(channel)
                    await asyncio.wait_for(event.wait(), 3)
                    acknowledged.pop(channel, None)
                async with tenant_session(tenant.id) as db:
                    actor = await active_actor(db)
                    await service.arena_access(db, actor, arena_id)
                    matches = list(
                        (
                            await db.scalars(
                                select(SimulationMatch)
                                .where(
                                    SimulationMatch.tenant_id == tenant.id,
                                    SimulationMatch.arena_id == arena_id,
                                    SimulationMatch.state != SimulationState.SETTLED,
                                )
                                .order_by(SimulationMatch.number)
                                .limit(100)
                            )
                        ).all()
                    )
                    snapshots = [(await service.view(db, row)).model_dump(mode="json") for row in matches]
                await send({"type": "SUBSCRIBED", "room": channel, "data": {"matches": snapshots}})
            except AppError as exc:
                await send({"type": "ERROR", "code": exc.code, "message": exc.message})
            except (ValueError, TypeError, AttributeError):
                await send({"type": "ERROR", "code": "invalid_message"})

    async def forward() -> None:
        async for message in pubsub.listen():
            channel = message.get("channel")
            if isinstance(channel, bytes):
                channel = channel.decode()
            if message.get("type") == "subscribe":
                if channel in acknowledged:
                    acknowledged[channel].set()
                continue
            if message.get("type") != "message" or channel not in rooms:
                continue
            try:
                await authorize(rooms[channel])
            except AppError:
                await websocket.close(code=4403, reason="Arena access revoked")
                return
            payload = message["data"]
            if isinstance(payload, bytes):
                payload = payload.decode()
            await send(json.loads(payload))

    async def pulse() -> None:
        while True:
            await asyncio.sleep(0.5)
            try:
                async with tenant_session(tenant.id) as db:
                    actor = await active_actor(db)
                    for channel, arena_id in list(rooms.items()):
                        await service.arena_access(db, actor, arena_id)
                        match = await db.scalar(
                            select(SimulationMatch).where(
                                SimulationMatch.tenant_id == tenant.id,
                                SimulationMatch.arena_id == arena_id,
                                SimulationMatch.state == SimulationState.FINAL_CALL,
                            )
                        )
                        if match is None or match.final_call_ends_at is None:
                            ticks.pop(channel, None)
                            continue
                        now = utcnow()
                        remaining = max(0, math.ceil((match.final_call_ends_at - now).total_seconds()))
                        tick = match.id, match.version, remaining
                        if ticks.get(channel) != tick:
                            ticks[channel] = tick
                            await send(
                                {
                                    "type": "COUNTDOWN_TICK",
                                    "room": channel,
                                    "data": {
                                        "matchId": str(match.id),
                                        "version": match.version,
                                        "remainingSeconds": remaining,
                                        "endsAt": match.final_call_ends_at.isoformat(),
                                        "timestamp": now.isoformat(),
                                    },
                                }
                            )
            except AppError:
                await websocket.close(code=4403, reason="Session or arena access revoked")
                return

    await send({"type": "CONNECTED"})
    tasks = [asyncio.create_task(fn()) for fn in (receive, forward, pulse)]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect | RuntimeError):
                raise exc
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe()
            await pubsub.aclose()
