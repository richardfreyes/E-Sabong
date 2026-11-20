"""EdgeGuardMiddleware: per-address and per-account request limits, temporary blocks, request-size
checks, optional concurrency caps and WebSocket abuse limits. Pure ASGI, around the routers.

Rules that keep legitimate traffic untouched:
  * Mode ``off`` passes every request through untouched; ``monitor`` (production default) only
    counts and logs; ``enforce`` refuses with the API's usual 429 envelope.
  * Authenticated requests to critical routes (betting, declarations, balances, wallet moves,
    commissions) are never refused, only recorded.
  * Health checks and container-internal callbacks are exempt; HLS/WebRTC never reach the API.
  * Fail open: if Redis does not answer within SECURITY_REDIS_TIMEOUT_MS the request is served.
  * The client address is the one the trusted gateway wrote (uvicorn --proxy-headers); a client
    cannot pick it with its own X-Forwarded-For (infrastructure/gateway/Caddyfile).
"""

import asyncio
import contextlib
import ipaddress
import json
import time
import uuid
from typing import Any
from urllib.parse import parse_qs

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.security import events, store
from app.security.classify import classify
from app.security.config import SecuritySettings, get_security_settings, resolved_mode

WS_CLOSE_POLICY = 1008  # client sent frames faster than any portal does
WS_REFRESH_SECONDS = 60


def _client_ip(scope: Scope) -> str:
    client = scope.get("client")
    return client[0] if client else "unknown"


def _principal(token: str | None, path: str) -> str | None:
    """``<audience>:<subject>`` for a valid access token, else None. Signature-checked, so a
    client cannot spread its requests over invented accounts."""
    if not token:
        return None
    audience = "platform" if path.startswith("/api/v1/platform/") else "tenant"
    try:
        claims = decode_access_token(token, audience)  # type: ignore[arg-type]
    except AppError:
        return None
    return f"{audience}:{claims.subject_id}"


def _bearer(headers: Headers) -> str | None:
    value = headers.get("authorization", "")
    return value[7:].strip() or None if value[:7].lower() == "bearer " else None


class EdgeGuardMiddleware:
    def __init__(self, app: ASGIApp, security: SecuritySettings | None = None) -> None:
        self.app = app
        self.cfg = security or get_security_settings()
        self.mode = resolved_mode(self.cfg, production=get_settings().is_production)
        self.env = self.cfg.env_name or get_settings().environment
        self.allow = self.cfg.allowlist_networks
        self.critical = self.cfg.critical
        self.caps = {prefix: asyncio.Semaphore(n) for prefix, n in self.cfg.concurrency.items()}
        self.timeout = self.cfg.redis_timeout_ms / 1000
        self._tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------ helpers

    def _allowlisted(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in self.allow)

    def _event(self, event: str, scope: Scope, **fields: Any) -> None:
        events.emit(
            event,
            env=self.env,
            mode=self.mode,
            client_ip=_client_ip(scope),
            method=scope.get("method", "WS"),
            path=scope.get("path", ""),
            **fields,
        )

    def _background(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _audit_block(self, ip: str, reason: dict[str, Any]) -> None:
        """Durable record next to the platform audit log (best effort, off the request path)."""
        with contextlib.suppress(Exception):
            from app.core.database import platform_session
            from app.modules.audit.models import ActorType
            from app.modules.audit.service import Actor, RequestMeta, record

            async with platform_session() as db:
                await record(
                    db,
                    actor=Actor(ActorType.SYSTEM, None, "edge-guard"),
                    action="security.ip_blocked",
                    resource_type="client_address",
                    resource_id=ip,
                    changes=reason,
                    meta=RequestMeta(ip=ip),
                )

    @staticmethod
    async def _refuse(scope: Scope, send: Send, status: int, code: str, message: str, retry_after: int | None) -> None:
        body = json.dumps(
            {
                "error": {
                    "code": code,
                    "message": message,
                    "details": {},
                    "request_id": (scope.get("state") or {}).get("request_id"),
                }
            },
            separators=(",", ":"),
        ).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ]
        if retry_after is not None:
            headers.append((b"retry-after", str(retry_after).encode()))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    # ------------------------------------------------------------------ ASGI entry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "http":
            await self._http(scope, receive, send)
        elif kind == "websocket":
            await self._websocket(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    # ------------------------------------------------------------------ HTTP

    async def _http(self, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope["path"]
        cls = classify(path, self.critical)
        state = scope.setdefault("state", {})
        state["security_class"] = cls
        if self.mode == "off" or cls == "exempt" or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        ip = _client_ip(scope)
        if self._allowlisted(ip):
            await self.app(scope, receive, send)
            return
        enforce = self.mode == "enforce"
        headers = Headers(scope=scope)

        # -- request size (Caddy also caps bodies, including chunked ones) --------------------
        limit = self.cfg.max_upload_bytes if cls == "upload" else self.cfg.max_body_bytes
        length = headers.get("content-length", "")
        if length.isdigit() and int(length) > limit:
            self._event(
                "request_too_large",
                scope,
                rule="max_body_bytes",
                action="refused" if enforce else "would_refuse",
                security_class=cls,
                status=413,
                bytes=int(length),
                limit=limit,
            )
            if enforce:
                await self._refuse(scope, send, 413, "request_too_large", "The request body is too large", None)
                return

        # -- rate limits ----------------------------------------------------------------------
        principal = _principal(_bearer(headers), path)
        protected = cls == "critical" and principal is not None
        m = store.minute()
        if cls == "auth":
            group, group_key, group_limit = "auth", ip, self.cfg.auth_per_minute
        elif principal:
            group, group_key, group_limit = "user", principal, self.cfg.user_per_minute
        else:
            group, group_key, group_limit = "anon", ip, self.cfg.anon_per_minute
        try:
            async with asyncio.timeout(self.timeout):
                block_ttl, ip_count, group_count = await store.count(
                    store.BLOCK_PREFIX + ip, [f"sec:rl:ip:{ip}:{m}", f"sec:rl:{group}:{group_key}:{m}"]
                )
        except Exception as exc:  # noqa: BLE001 - fail open, never break a request
            events.store_unavailable(self.env, exc)
            await self._capped(scope, receive, send, path)
            return

        blocked = block_ttl != -2
        over_ip = ip_count - self.cfg.ip_per_minute
        over_group = group_count - group_limit
        if over_ip == 1:
            # First request over the flood ceiling this minute: one strike.
            self._background(self._strike(scope, ip, ip_count))
        refuse = enforce and not protected and (blocked or over_ip > 0 or over_group > 0)
        # refused | would_refuse (monitor mode) | observed_critical (signed-in critical route: never refused)
        action = "refused" if refuse else ("observed_critical" if protected and enforce else "would_refuse")
        if blocked and events.sampled(ip_count):
            self._event(
                "blocked_address_request",
                scope,
                rule="temporary_block",
                action=action,
                security_class=cls,
                status=429 if refuse else None,
                seconds_left=block_ttl,
            )
        if over_ip > 0 and events.sampled(over_ip):
            self._event(
                "rate_limit_exceeded",
                scope,
                rule="ip_per_minute",
                action=action,
                security_class=cls,
                status=429 if refuse else None,
                count=ip_count,
                limit=self.cfg.ip_per_minute,
            )
        if over_group > 0 and events.sampled(over_group):
            self._event(
                "rate_limit_exceeded",
                scope,
                rule=f"{group}_per_minute",
                action=action,
                security_class=cls,
                status=429 if refuse else None,
                count=group_count,
                limit=group_limit,
                principal=principal,
            )
        if refuse:
            retry = block_ttl if blocked and block_ttl > 0 else store.seconds_left_in_minute()
            await self._refuse(scope, send, 429, "rate_limited", "Too many requests. Try again shortly.", retry)
            return
        await self._capped(scope, receive, send, path)

    async def _strike(self, scope: Scope, ip: str, count: int) -> None:
        reason = {
            "rule": "ip_per_minute",
            "count": count,
            "limit": self.cfg.ip_per_minute,
            "strikes": self.cfg.block_after_strikes,
            "env": self.env,
            "at": int(time.time()),
        }
        try:
            async with asyncio.timeout(self.timeout * 5):
                strikes, outcome = await store.strike(
                    ip,
                    window=self.cfg.strike_window_seconds,
                    threshold=self.cfg.block_after_strikes,
                    block_seconds=self.cfg.block_seconds,
                    reason=reason,
                    enforce=self.mode == "enforce",
                )
        except Exception as exc:  # noqa: BLE001
            events.store_unavailable(self.env, exc)
            return
        if outcome == 1:
            self._event(
                "temporary_block",
                scope,
                rule="ip_per_minute",
                action="blocked",
                strikes=strikes,
                block_seconds=self.cfg.block_seconds,
            )
            await self._audit_block(ip, {**reason, "block_seconds": self.cfg.block_seconds})
        elif outcome == 2:
            self._event(
                "temporary_block",
                scope,
                rule="ip_per_minute",
                action="would_block",
                strikes=strikes,
                block_seconds=self.cfg.block_seconds,
            )

    async def _capped(self, scope: Scope, receive: Receive, send: Send, path: str) -> None:
        cap = next((sem for prefix, sem in self.caps.items() if path.startswith(prefix)), None) if self.caps else None
        if cap is None:
            await self.app(scope, receive, send)
            return
        if cap.locked() and self.mode != "enforce":
            self._event("concurrency_saturated", scope, rule="concurrency_limits", action="observed")
            await self.app(scope, receive, send)
            return
        try:
            async with asyncio.timeout(self.cfg.concurrency_wait_seconds):
                await cap.acquire()
        except TimeoutError:
            self._event("concurrency_saturated", scope, rule="concurrency_limits", action="refused", status=503)
            await self._refuse(scope, send, 503, "busy", "This report is busy. Try again shortly.", 5)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            cap.release()

    # ------------------------------------------------------------------ WebSocket

    async def _websocket(self, scope: Scope, receive: Receive, send: Send) -> None:
        path: str = scope["path"]
        ip = _client_ip(scope)
        if self.mode == "off" or not path.startswith("/api/ws") or self._allowlisted(ip):
            await self.app(scope, receive, send)
            return
        enforce = self.mode == "enforce"
        headers = Headers(scope=scope)
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        token = _bearer(headers) or (query.get("token") or [None])[0]
        user = _principal(token, path)
        connection = uuid.uuid4().hex
        try:
            async with asyncio.timeout(self.timeout):
                block_ttl, hs_ip, hs_user, open_ip, open_user = await store.ws_open(ip, user, connection)
        except Exception as exc:  # noqa: BLE001 - fail open
            events.store_unavailable(self.env, exc)
            await self.app(scope, receive, send)
            return

        reasons = []
        if block_ttl != -2 and user is None:
            reasons.append(("temporary_block", 0, 0))
        if hs_ip > self.cfg.ws_handshakes_per_minute_ip:
            reasons.append(("ws_handshakes_per_minute_ip", hs_ip, self.cfg.ws_handshakes_per_minute_ip))
        if user and hs_user > self.cfg.ws_handshakes_per_minute_user:
            reasons.append(("ws_handshakes_per_minute_user", hs_user, self.cfg.ws_handshakes_per_minute_user))
        if open_ip > self.cfg.ws_max_per_ip:
            reasons.append(("ws_max_per_ip", open_ip, self.cfg.ws_max_per_ip))
        if user and open_user > self.cfg.ws_max_per_user:
            reasons.append(("ws_max_per_user", open_user, self.cfg.ws_max_per_user))
        for rule, value, limit in reasons:
            if rule == "temporary_block" or events.sampled(value - limit):
                self._event(
                    "websocket_limit_exceeded",
                    scope,
                    rule=rule,
                    action="refused" if enforce else "would_refuse",
                    security_class="websocket",
                    count=value,
                    limit=limit,
                    principal=user,
                )
        if reasons and enforce:
            with contextlib.suppress(Exception):
                await store.ws_close(ip, user, connection)
            # Refused before accept: the client sees a failed handshake and backs off (1 s .. 30 s).
            message = await receive()
            if message["type"] == "websocket.connect":
                await send({"type": "websocket.close", "code": 1013, "reason": ""})
            return

        window_start = time.monotonic()
        frames = 0
        flooded = False

        async def guarded_receive() -> Message:
            nonlocal window_start, frames, flooded
            message = await receive()
            if message["type"] != "websocket.receive":
                return message
            now = time.monotonic()
            if now - window_start >= 10:
                window_start, frames = now, 0
            frames += 1
            over = frames - self.cfg.ws_messages_per_10s
            if over > 0 and events.sampled(over):
                self._event(
                    "websocket_message_flood",
                    scope,
                    rule="ws_messages_per_10s",
                    action="refused" if enforce else "would_refuse",
                    security_class="websocket",
                    count=frames,
                    limit=self.cfg.ws_messages_per_10s,
                    principal=user,
                )
            if over > 0 and enforce:
                flooded = True
                # The endpoint sees an ordinary disconnect and cleans up; the close goes out below.
                return {"type": "websocket.disconnect", "code": WS_CLOSE_POLICY}
            return message

        async def refresh() -> None:
            while True:
                await asyncio.sleep(WS_REFRESH_SECONDS)
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(self.timeout * 5):
                        await store.ws_refresh(ip, user, connection)

        refresher = asyncio.create_task(refresh())
        try:
            await self.app(scope, guarded_receive, send)
        finally:
            refresher.cancel()
            with contextlib.suppress(BaseException):
                await refresher
            with contextlib.suppress(Exception):
                async with asyncio.timeout(self.timeout * 5):
                    await store.ws_close(ip, user, connection)
            if flooded:
                with contextlib.suppress(Exception):
                    await send({"type": "websocket.close", "code": WS_CLOSE_POLICY, "reason": "message rate"})
