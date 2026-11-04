"""Authenticated native WebSocket rooms for neutral simulations (shared Redis pub/sub).

Authorization is enforced continuously but kept off the delivery path:

* Every socket re-checks its session, workspace, account and each joined arena against the
  database once per ``AUTH_REFRESH_SECONDS`` (spread across sockets), and closes on failure.
* A frame or countdown tick is only forwarded while the room's last successful check is
  younger than ``AUTH_MAX_AGE_SECONDS``; otherwise the socket checks synchronously first
  (fail closed if the background refresh lags).
* Session revocation is pushed over Redis and closes the socket immediately.
* Countdown ticks are read once per room per worker (``_RoomTicker``), not per socket.

Room frames are forwarded as the exact text published to Redis: no per-socket JSON work.
"""

import asyncio
import contextlib
import json
import math
import random
import time
import uuid
from collections.abc import Callable
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.database import tenant_session
from app.core.deps import CurrentUser, revoked_family_channel, revoked_family_key
from app.core.errors import AppError, Forbidden
from app.core.redis import get_pubsub_redis, get_redis
from app.core.security import decode_access_token, utcnow
from app.core.tenancy import resolve_tenant
from app.modules.simulations import service
from app.modules.simulations.models import SimulationMatch, SimulationState
from app.modules.tenants.service import effective_modules
from app.modules.users.models import TenantUser

router = APIRouter()
log = structlog.get_logger("simulation_ws")

AUTH_REFRESH_SECONDS = 1.0
AUTH_MAX_AGE_SECONDS = 2.0
TICK_SECONDS = 0.5

TickListener = Callable[[dict[str, Any]], None]


class _RoomTicker:
    """One FINAL_CALL countdown reader per room per worker, shared by all local sockets."""

    def __init__(self, tenant_id: uuid.UUID, arena_id: uuid.UUID, channel: str) -> None:
        self.tenant_id, self.arena_id, self.channel = tenant_id, arena_id, channel
        self.listeners: dict[int, TickListener] = {}
        self.identity: tuple[uuid.UUID, int, int] | None = None
        self.last: dict[str, Any] | None = None
        self.task = asyncio.get_running_loop().create_task(self._run())

    async def _read(self) -> dict[str, Any] | None:
        async with tenant_session(self.tenant_id) as db:
            match = await db.scalar(
                select(SimulationMatch).where(
                    SimulationMatch.tenant_id == self.tenant_id,
                    SimulationMatch.arena_id == self.arena_id,
                    SimulationMatch.state == SimulationState.FINAL_CALL,
                )
            )
        if match is None or match.final_call_ends_at is None:
            self.identity = self.last = None
            return None
        now = utcnow()
        remaining = max(0, math.ceil((match.final_call_ends_at - now).total_seconds()))
        identity = match.id, match.version, remaining
        if identity == self.identity:
            return None
        self.identity = identity
        return {
            "type": "COUNTDOWN_TICK",
            "room": self.channel,
            "data": {
                "matchId": str(match.id),
                "version": match.version,
                "remainingSeconds": remaining,
                "endsAt": match.final_call_ends_at.isoformat(),
                "timestamp": now.isoformat(),
            },
        }

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(TICK_SECONDS)
                if not self.listeners:
                    return
                try:
                    tick = await self._read()
                except Exception:  # noqa: BLE001 - a failed read skips one tick; sockets stay up
                    log.warning("countdown_read_failed", room=self.channel, exc_info=True)
                    continue
                if tick is not None:
                    self.last = tick
                    for listener in list(self.listeners.values()):
                        listener(tick)
        finally:
            # No await between the empty check and removal, so a concurrent join cannot be lost.
            if _tickers.get(self.channel) is self:
                del _tickers[self.channel]


_tickers: dict[str, _RoomTicker] = {}


def _join_ticker(tenant_id: uuid.UUID, arena_id: uuid.UUID, channel: str, listener: TickListener) -> Callable[[], None]:
    ticker = _tickers.get(channel)
    if ticker is None or ticker.task.done():
        ticker = _tickers[channel] = _RoomTicker(tenant_id, arena_id, channel)
    token = id(listener)
    ticker.listeners[token] = listener
    if ticker.last is not None:
        listener(ticker.last)

    def release() -> None:
        ticker.listeners.pop(token, None)

    return release


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
    authorized_at: dict[str, float] = {}  # monotonic time of the room's last successful check
    acknowledged: dict[str, asyncio.Event] = {}
    tick_leases: dict[str, Callable[[], None]] = {}
    pending_ticks: dict[str, dict[str, Any]] = {}
    tick_ready = asyncio.Event()
    send_lock = asyncio.Lock()
    pubsub = get_pubsub_redis().pubsub()
    # The private channel lets the listener acknowledge room joins before SUBSCRIBED is sent;
    # the revocation channel closes this socket as soon as its session family is revoked.
    private_channel = f"simulation:connection:{uuid.uuid4()}"
    revocation_channel = revoked_family_channel(claims.session_family)
    await pubsub.subscribe(private_channel, revocation_channel)

    async def send_text(text: str) -> None:
        async with send_lock:
            await asyncio.wait_for(websocket.send_text(text), 5)

    async def send(payload: dict) -> None:
        await send_text(json.dumps(payload, separators=(",", ":")))

    async def active_actor(db) -> CurrentUser:  # type: ignore[no-untyped-def]
        if claims.expires_at <= utcnow() or await redis.exists(revoked_family_key(claims.session_family)):
            raise Forbidden("Session expired or revoked")
        context = await resolve_tenant(websocket, dev_slug)  # type: ignore[arg-type]
        if context is None or not context.is_operational:
            raise Forbidden("Workspace unavailable")
        if not {"events", "matches"} <= set(await effective_modules(tenant.id)):
            raise Forbidden("Simulation modules are unavailable")
        return await service.actor_now(db, current)

    async def check_rooms() -> None:
        """Re-authorize the session and every joined room in one transaction."""
        checked = list(rooms.items())
        async with tenant_session(tenant.id) as db:
            actor = await active_actor(db)
            for _, arena_id in checked:
                await service.arena_access(db, actor, arena_id)
        now = time.monotonic()
        for channel, _ in checked:
            if channel in rooms:
                authorized_at[channel] = now

    async def ensure_authorized(channel: str) -> None:
        if claims.expires_at <= utcnow():
            raise Forbidden("Session expired")
        if time.monotonic() - authorized_at.get(channel, -math.inf) > AUTH_MAX_AGE_SECONDS:
            await check_rooms()

    async def close_revoked(reason: str, code: int = 4403) -> None:
        with contextlib.suppress(Exception):
            await websocket.close(code=code, reason=reason)

    def leave(channel: str) -> None:
        rooms.pop(channel, None)
        authorized_at.pop(channel, None)
        pending_ticks.pop(channel, None)
        if release := tick_leases.pop(channel, None):
            release()

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
                    leave(channel)
                    await pubsub.unsubscribe(channel)
                    await send({"type": "UNSUBSCRIBED", "room": channel})
                    continue
                if kind != "SUBSCRIBE":
                    raise ValueError("Unsupported message type")
                async with tenant_session(tenant.id) as db:
                    await service.arena_access(db, await active_actor(db), arena_id)
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
                    authorized_at[channel] = time.monotonic()
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
                if channel not in tick_leases:

                    def on_tick(tick: dict[str, Any], channel: str = channel) -> None:
                        pending_ticks[channel] = tick
                        tick_ready.set()

                    tick_leases[channel] = _join_ticker(tenant.id, arena_id, channel, on_tick)
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
            if message.get("type") != "message":
                continue
            if channel == revocation_channel:
                await close_revoked("Session revoked", 4401)
                return
            if channel not in rooms:
                continue
            try:
                await ensure_authorized(channel)
            except AppError:
                await close_revoked("Arena access revoked")
                return
            payload = message["data"]
            await send_text(payload.decode() if isinstance(payload, bytes) else payload)

    async def countdown() -> None:
        while True:
            await tick_ready.wait()
            tick_ready.clear()
            while pending_ticks:
                channel, tick = pending_ticks.popitem()
                if channel not in rooms:
                    continue
                try:
                    await ensure_authorized(channel)
                except AppError:
                    await close_revoked("Session or arena access revoked")
                    return
                await send(tick)

    async def refresh() -> None:
        # Spread sockets across the interval so checks do not arrive in synchronized waves.
        await asyncio.sleep(random.uniform(0, AUTH_REFRESH_SECONDS))  # noqa: S311 - jitter, not security
        while True:
            try:
                await check_rooms()
            except AppError:
                await close_revoked("Session or arena access revoked")
                return
            await asyncio.sleep(AUTH_REFRESH_SECONDS)

    await send({"type": "CONNECTED"})
    tasks = [asyncio.create_task(fn()) for fn in (receive, forward, countdown, refresh)]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect | RuntimeError):
                raise exc
    finally:
        for channel in list(rooms):
            leave(channel)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe()
            await pubsub.aclose()
