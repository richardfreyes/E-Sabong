"""Edge guard (app/security): limits refuse only what they should, never critical signed-in work.

The guard wraps the real application here, in enforce mode with tiny limits, so every check runs
against real routes, real tokens and the real Redis counters. Test transports report a loopback
client (always allowlisted), so each test pins its own TEST-NET client address.
"""

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest
from httpx_ws import WebSocketDisconnect, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport

from app.security import events, store
from app.security.classify import classify
from app.security.config import DEFAULT_CRITICAL_PREFIXES, SecuritySettings, resolved_mode
from app.security.middleware import EdgeGuardMiddleware
from tests.conftest import client_for


def new_ip() -> str:
    # 198.18.0.0/15 is reserved for benchmarking: never a real client, never allowlisted.
    n = uuid.uuid4().int
    return f"198.{18 + n % 2}.{(n >> 8) % 256}.{(n >> 16) % 254 + 1}"


def at(app: Any, ip: str) -> Any:
    """Present every request as coming from ``ip`` (what uvicorn's proxy headers produce)."""

    async def shim(scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": (ip, 40000)}
        await app(scope, receive, send)

    return shim


def guarded(app: Any, **overrides: Any) -> EdgeGuardMiddleware:
    base: dict[str, Any] = {
        "guard_mode": "enforce",
        "env_name": "test",
        "ip_per_minute": 1000,
        "user_per_minute": 1000,
        "anon_per_minute": 1000,
        "auth_per_minute": 1000,
    }
    return EdgeGuardMiddleware(app, SecuritySettings(**{**base, **overrides}))


@pytest.fixture
def security_log(monkeypatch) -> list[tuple[str, dict]]:  # type: ignore[no-untyped-def]
    lines: list[tuple[str, dict]] = []

    class Recorder:
        def warning(self, event: str, **fields: Any) -> None:
            lines.append((event, fields))

    monkeypatch.setattr(events, "log", Recorder())
    return lines


def token_of(client: httpx.AsyncClient) -> str:
    return client.headers["Authorization"].split()[1]


# ------------------------------------------------------------------ configuration


def test_mode_defaults_off_locally_and_monitor_in_production() -> None:
    assert resolved_mode(SecuritySettings(guard_mode=None), production=False) == "off"
    assert resolved_mode(SecuritySettings(guard_mode=None), production=True) == "monitor"
    assert resolved_mode(SecuritySettings(guard_mode="enforce"), production=False) == "enforce"
    assert resolved_mode(SecuritySettings(guard_mode="off"), production=True) == "off"


def test_classification() -> None:
    c = DEFAULT_CRITICAL_PREFIXES
    assert classify("/api/health", c) == "exempt"
    assert classify("/api/v1/internal/mediamtx/auth", c) == "exempt"
    assert classify("/api/v1/auth/login", c) == "auth"
    assert classify("/api/v1/platform/auth/refresh", c) == "auth"
    assert classify("/api/v1/auth/me", c) == "general"
    assert classify("/api/v1/portal/matches/abc/bets", c) == "critical"
    assert classify("/api/v1/declarator/matches/abc/dummy-bets", c) == "critical"
    assert classify("/api/v1/resources/issuances", c) == "critical"
    assert classify("/api/v1/admin/branding/assets/logo", c) == "upload"
    assert classify("/api/v1/public/tenant", c) == "public"
    assert classify("/api/ws", c) == "websocket"


def test_allowlist_parsing() -> None:
    s = SecuritySettings(allowlist="203.0.113.7, 198.51.100.0/24")
    nets = {str(n) for n in s.allowlist_networks}
    assert {"127.0.0.0/8", "::1/128", "203.0.113.7/32", "198.51.100.0/24"} <= nets
    assert SecuritySettings(concurrency_limits="/api/v1/admin/reports/=4,bad,/x=0").concurrency == {"/api/v1/admin/reports/": 4}


# ------------------------------------------------------------------ HTTP limits


async def test_off_mode_never_counts_or_refuses(app, tenant) -> None:  # type: ignore[no-untyped-def]
    ip = new_ip()
    guard = guarded(app, guard_mode="off", ip_per_minute=1, user_per_minute=1)
    async with client_for(at(guard, ip), tenant.host, token_of(tenant.admin)) as c:
        for _ in range(4):
            assert (await c.get("/api/v1/auth/me")).status_code == 200
    assert await store.get_redis().get(f"sec:rl:ip:{ip}:{store.minute()}") is None


async def test_monitor_mode_logs_but_serves(app, tenant, security_log) -> None:  # type: ignore[no-untyped-def]
    ip = new_ip()
    guard = guarded(app, guard_mode="monitor", user_per_minute=2)
    async with client_for(at(guard, ip), tenant.host, token_of(tenant.admin)) as c:
        statuses = [(await c.get("/api/v1/auth/me")).status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 200]
    hits = [f for e, f in security_log if e == "rate_limit_exceeded"]
    assert hits and hits[0]["rule"] == "user_per_minute" and hits[0]["action"] == "would_refuse"
    assert hits[0]["client_ip"] == ip and hits[0]["path"] == "/api/v1/auth/me" and hits[0]["env"] == "test"


async def test_enforce_refuses_general_route_with_api_envelope(app, tenant, security_log) -> None:  # type: ignore[no-untyped-def]
    ip = new_ip()
    guard = guarded(app, user_per_minute=2)
    async with client_for(at(guard, ip), tenant.host, token_of(tenant.admin)) as c:
        ok = [(await c.get("/api/v1/auth/me")).status_code for _ in range(2)]
        refused = await c.get("/api/v1/auth/me")
    assert ok == [200, 200]
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(refused.headers["retry-after"]) <= 60
    assert refused.headers["cache-control"] == "no-store"
    assert any(f["action"] == "refused" and f["status"] == 429 for e, f in security_log if e == "rate_limit_exceeded")


async def test_real_middleware_stack_order_and_refusal(app, tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """In create_app() the guard sits inside the request id and security headers, so a refusal
    carries both, exactly like every other API error."""
    import app.security.middleware as guard_module
    from app.main import create_app

    names = [m.cls.__name__ for m in app.user_middleware]
    assert (
        names.index("RequestContextMiddleware")
        < names.index("SecurityHeadersMiddleware")
        < names.index("EdgeGuardMiddleware")
        < names.index("UsageMeterMiddleware")
    )
    settings = SecuritySettings(guard_mode="enforce", env_name="test", user_per_minute=1)
    monkeypatch.setattr(guard_module, "get_security_settings", lambda: settings)
    stack = create_app()
    async with client_for(at(stack, new_ip()), tenant.host, token_of(tenant.admin)) as c:
        assert (await c.get("/api/v1/auth/me")).status_code == 200
        refused = await c.get("/api/v1/auth/me")
    assert refused.status_code == 429
    assert refused.json()["error"]["request_id"] == refused.headers["x-request-id"]
    assert refused.headers["x-content-type-options"] == "nosniff"
    assert refused.headers["x-frame-options"] == "DENY"


async def test_limits_are_per_account_not_shared(app, tenant) -> None:  # type: ignore[no-untyped-def]
    """Two accounts behind one carrier NAT address keep separate budgets."""
    ip = new_ip()
    await tenant.create_user("natplayer", "player")
    player = await tenant.login("natplayer")
    guard = guarded(app, user_per_minute=2)
    async with (
        client_for(at(guard, ip), tenant.host, token_of(tenant.admin)) as a,
        client_for(at(guard, ip), tenant.host, token_of(player)) as b,
    ):
        assert [(await a.get("/api/v1/auth/me")).status_code for _ in range(3)] == [200, 200, 429]
        assert [(await b.get("/api/v1/auth/me")).status_code for _ in range(2)] == [200, 200]


async def test_critical_routes_never_refuse_signed_in_users(app, tenant, security_log) -> None:  # type: ignore[no-untyped-def]
    ip = new_ip()
    guard = guarded(app, ip_per_minute=1, user_per_minute=1)
    await store.set_block(ip, 120, {"rule": "test"})
    try:
        async with client_for(at(guard, ip), tenant.host, token_of(tenant.admin)) as c:
            statuses = [(await c.get("/api/v1/admin/balance/summary")).status_code for _ in range(4)]
            general = await c.get("/api/v1/auth/me")
    finally:
        await store.clear_block(ip)
    assert 429 not in statuses and statuses[0] == statuses[-1]
    assert general.status_code == 429  # the same address is still limited elsewhere
    assert any(f["action"] == "observed_critical" for _, f in security_log)


async def test_critical_routes_still_limit_anonymous_floods(app, tenant) -> None:  # type: ignore[no-untyped-def]
    ip = new_ip()
    guard = guarded(app, anon_per_minute=2)
    async with client_for(at(guard, ip), tenant.host) as c:
        statuses = [(await c.get("/api/v1/admin/balance/summary")).status_code for _ in range(3)]
    assert statuses == [401, 401, 429]


async def test_forged_or_foreign_tokens_count_as_anonymous(app, tenant, other_tenant) -> None:  # type: ignore[no-untyped-def]
    ip = new_ip()
    guard = guarded(app, anon_per_minute=2)
    async with client_for(at(guard, ip), tenant.host, "not-a-jwt") as c:
        statuses = [(await c.get("/api/v1/admin/balance/summary")).status_code for _ in range(3)]
    assert statuses[-1] == 429


async def test_exempt_and_allowlisted_traffic_is_never_refused(app, tenant) -> None:  # type: ignore[no-untyped-def]
    ip = new_ip()
    guard = guarded(app, ip_per_minute=1, anon_per_minute=1)
    async with client_for(at(guard, ip), tenant.host) as c:
        assert {(await c.get("/api/health")).status_code for _ in range(5)} == {200}
    other = new_ip()
    allowed = guarded(app, ip_per_minute=1, anon_per_minute=1, allowlist=f"{other}/32")
    async with client_for(at(allowed, other), tenant.host) as c:
        assert {(await c.get("/api/v1/public/tenant")).status_code for _ in range(4)} == {200}


async def test_auth_endpoints_have_their_own_address_budget(app, tenant) -> None:  # type: ignore[no-untyped-def]
    ip = new_ip()
    guard = guarded(app, auth_per_minute=2)
    async with client_for(at(guard, ip), tenant.host) as c:
        statuses = [
            (await c.post("/api/v1/auth/login", json={"username": "nobody", "password": "x" * 12})).status_code for _ in range(3)
        ]
    assert statuses[:2] == [401, 401] and statuses[2] == 429


async def test_flood_strikes_lead_to_temporary_block_and_unblock(app, tenant, security_log) -> None:  # type: ignore[no-untyped-def]
    ip = new_ip()
    guard = guarded(app, ip_per_minute=2, block_after_strikes=1, block_seconds=120)
    try:
        async with client_for(at(guard, ip), tenant.host) as c:
            for _ in range(3):
                await c.get("/api/v1/public/tenant")
            for _ in range(50):  # the strike is recorded off the request path
                if await store.get_redis().exists(store.BLOCK_PREFIX + ip):
                    break
                await asyncio.sleep(0.02)
            blocks = {row["ip"]: row for row in await store.list_blocks()}
            assert ip in blocks and 0 < blocks[ip]["seconds_left"] <= 120 and blocks[ip]["rule"] == "ip_per_minute"
            refused = await c.get("/api/v1/public/tenant")
            assert refused.status_code == 429 and int(refused.headers["retry-after"]) > 60
            assert await store.clear_block(ip)
            await store.get_redis().delete(f"sec:rl:ip:{ip}:{store.minute()}", f"sec:rl:anon:{ip}:{store.minute()}")
            assert (await c.get("/api/v1/public/tenant")).status_code == 200
    finally:
        await store.clear_block(ip)
    assert any(e == "temporary_block" and f["action"] == "blocked" for e, f in security_log)


async def test_monitor_mode_never_creates_blocks(app, tenant, security_log) -> None:  # type: ignore[no-untyped-def]
    ip = new_ip()
    guard = guarded(app, guard_mode="monitor", ip_per_minute=1, block_after_strikes=1)
    async with client_for(at(guard, ip), tenant.host) as c:
        assert [(await c.get("/api/v1/public/tenant")).status_code for _ in range(3)] == [200, 200, 200]
    await asyncio.sleep(0.2)
    assert not await store.get_redis().exists(store.BLOCK_PREFIX + ip)
    assert any(e == "temporary_block" and f["action"] == "would_block" for e, f in security_log)


async def test_oversized_bodies(app, tenant) -> None:  # type: ignore[no-untyped-def]
    ip = new_ip()
    guard = guarded(app, max_body_bytes=2048)
    async with client_for(at(guard, ip), tenant.host, token_of(tenant.admin)) as c:
        big = await c.post(
            "/api/v1/admin/events", content=b"{" + b" " * 4096 + b"}", headers={"content-type": "application/json"}
        )
        assert big.status_code == 413 and big.json()["error"]["code"] == "request_too_large"
        small = await c.post("/api/v1/admin/events", json={"name": "Size check", "starts_at": "2030-01-01T10:00:00+00:00"})
        assert small.status_code == 201
    monitor = guarded(app, guard_mode="monitor", max_body_bytes=2048)
    async with client_for(at(monitor, ip), tenant.host, token_of(tenant.admin)) as c:
        r = await c.post("/api/v1/admin/events", content=b"{" + b" " * 4096 + b"}", headers={"content-type": "application/json"})
        assert r.status_code == 422  # the handler's own answer: monitor mode only records


async def test_redis_outage_fails_open(app, tenant, monkeypatch, security_log) -> None:  # type: ignore[no-untyped-def]
    async def broken(*_: Any, **__: Any) -> list[int]:
        raise ConnectionError("redis down")

    monkeypatch.setattr(store, "count", broken)
    monkeypatch.setattr(events, "_last_store_warning", 0.0)
    guard = guarded(app, ip_per_minute=1, user_per_minute=1)
    async with client_for(at(guard, new_ip()), tenant.host, token_of(tenant.admin)) as c:
        assert [(await c.get("/api/v1/auth/me")).status_code for _ in range(3)] == [200, 200, 200]
    assert sum(1 for e, _ in security_log if e == "security_store_unavailable") == 1


async def test_slow_redis_does_not_delay_requests(app, tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def slow(*_: Any, **__: Any) -> list[int]:
        await asyncio.sleep(5)
        return [-2, 1, 1]

    monkeypatch.setattr(store, "count", slow)
    guard = guarded(app, redis_timeout_ms=50)
    async with client_for(at(guard, new_ip()), tenant.host, token_of(tenant.admin)) as c:
        started = asyncio.get_running_loop().time()
        assert (await c.get("/api/v1/auth/me")).status_code == 200
        assert asyncio.get_running_loop().time() - started < 2


async def test_concurrency_caps(app, tenant) -> None:  # type: ignore[no-untyped-def]
    gate = asyncio.Event()

    async def slow_app(scope, receive, send):  # type: ignore[no-untyped-def]
        await gate.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    guard = guarded(slow_app, concurrency_limits="/api/v1/admin/reports/=1", concurrency_wait_seconds=0.1)
    transport = httpx.ASGITransport(app=at(guard, new_ip()))
    async with httpx.AsyncClient(transport=transport, base_url="http://x") as c:
        first = asyncio.create_task(c.get("/api/v1/admin/reports/a"))
        await asyncio.sleep(0.05)
        second = await c.get("/api/v1/admin/reports/b")
        other = asyncio.create_task(c.get("/api/v1/admin/users"))  # uncapped prefix is not queued
        gate.set()
        assert (await first).status_code == 200 and second.status_code == 503 and (await other).status_code == 200


# ------------------------------------------------------------------ WebSockets


def ws_client(app: Any, host: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url=f"http://{host}")


async def _receive(ws, timeout: float = 5.0) -> dict:  # type: ignore[no-untyped-def]
    return json.loads(await asyncio.wait_for(ws.receive_text(), timeout))


async def test_websocket_normal_use_is_untouched(app, tenant) -> None:  # type: ignore[no-untyped-def]
    ip = new_ip()
    guard = guarded(app, ws_messages_per_10s=5)
    async with ws_client(at(guard, ip), tenant.host) as c:
        async with aconnect_ws(f"/api/ws?token={token_of(tenant.admin)}", c) as ws:
            assert (await _receive(ws))["type"] == "connected"
            for _ in range(3):
                await ws.send_text('{"type":"ping"}')
                assert (await _receive(ws))["type"] == "pong"
            assert await store.get_redis().zcard(f"sec:ws:ip:{ip}") == 1
    for _ in range(50):
        if await store.get_redis().zcard(f"sec:ws:ip:{ip}") == 0:
            break
        await asyncio.sleep(0.02)
    assert await store.get_redis().zcard(f"sec:ws:ip:{ip}") == 0  # released on close


async def test_websocket_message_flood_closes_with_policy_code(app, tenant, security_log) -> None:  # type: ignore[no-untyped-def]
    guard = guarded(app, ws_messages_per_10s=3)
    async with ws_client(at(guard, new_ip()), tenant.host) as c:
        async with aconnect_ws(f"/api/ws?token={token_of(tenant.admin)}", c) as ws:
            await _receive(ws)
            with pytest.raises(WebSocketDisconnect) as closed:
                for _ in range(10):
                    await ws.send_text('{"type":"ping"}')
                    await _receive(ws)
    assert closed.value.code == 1008
    assert any(e == "websocket_message_flood" for e, _ in security_log)


async def test_websocket_handshake_and_concurrency_limits(app, tenant) -> None:  # type: ignore[no-untyped-def]
    ip = new_ip()
    guard = guarded(app, ws_max_per_user=1)
    token = token_of(tenant.admin)
    async with ws_client(at(guard, ip), tenant.host) as c:
        async with aconnect_ws(f"/api/ws?token={token}", c) as ws:
            await _receive(ws)
            with pytest.raises(Exception):  # noqa: B017 - refused before accept (HTTP 403 upgrade failure)
                async with aconnect_ws(f"/api/ws?token={token}", c) as second:
                    await _receive(second, 2)
    other = new_ip()
    limited = guarded(app, ws_handshakes_per_minute_ip=1)
    async with ws_client(at(limited, other), tenant.host) as c:
        async with aconnect_ws(f"/api/ws?token={token}", c) as ws:
            await _receive(ws)
        with pytest.raises(Exception):  # noqa: B017
            async with aconnect_ws(f"/api/ws?token={token}", c) as again:
                await _receive(again, 2)


async def test_websocket_guard_off_is_passthrough(app, tenant) -> None:  # type: ignore[no-untyped-def]
    guard = guarded(app, guard_mode="off", ws_messages_per_10s=1, ws_max_per_user=1)
    async with ws_client(at(guard, new_ip()), tenant.host) as c:
        async with aconnect_ws(f"/api/ws?token={token_of(tenant.admin)}", c) as ws:
            await _receive(ws)
            for _ in range(4):
                await ws.send_text('{"type":"ping"}')
                assert (await _receive(ws))["type"] == "pong"


# ------------------------------------------------------------------ operator CLI


async def test_cli_block_list_unblock_is_audited(app, capsys) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.core.database import platform_session
    from app.modules.audit.models import AuditLog
    from app.security import admin

    ip = new_ip()
    await admin.block(ip, 5, "abuse report #1")
    await admin.show_blocks()
    assert ip in capsys.readouterr().out
    with pytest.raises(SystemExit):
        await admin.block(ip, 0, "never permanent")
    with pytest.raises(SystemExit):
        await admin.block(ip, 100000, "never permanent")
    await admin.unblock(ip)
    assert not await store.get_redis().exists(store.BLOCK_PREFIX + ip)
    async with platform_session() as db:
        actions = (
            await db.scalars(select(AuditLog.action).where(AuditLog.resource_id == ip).order_by(AuditLog.created_at))
        ).all()
    assert actions == ["security.ip_blocked", "security.ip_unblocked"]
