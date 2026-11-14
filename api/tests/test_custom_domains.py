"""Custom hosts bind identity before authentication, including rental mode."""

import pytest
from sqlalchemy import delete

from app.core.database import platform_session
from app.modules.tenants.rental import RentalSettings
from tests.conftest import PASSWORD, client_for, unique


@pytest.fixture
async def dns(monkeypatch):
    from app.modules.tenants import service

    tokens = []
    monkeypatch.setattr(service, "_lookup_txt", lambda _: tokens)
    monkeypatch.setattr(service, "_routing_addresses", lambda _: {"8.8.8.8"})
    monkeypatch.setattr(service, "_probe_tls", lambda *_: True)
    return tokens


async def add(owner, tenant, dns):
    host = f"{unique('custom')}.example.com"
    r = await owner.client.post(f"/api/v1/platform/tenants/{tenant.id}/domains", json={"domain": host})
    assert r.status_code == 201, r.text
    row = r.json()
    dns.append(row["instructions"]["txt_value"])
    return host, row


async def verify(owner, row):
    r = await owner.client.post(f"/api/v1/platform/domains/{row['id']}/verify")
    assert r.status_code == 200, r.text
    return r.json()


async def test_lifecycle(app, owner, tenant, other_tenant, dns):
    host, row = await add(owner, tenant, dns)
    path = f"/api/v1/platform/domains/{row['id']}"
    async with client_for(app, host) as c:
        assert (await c.get("/api/v1/public/tenant")).status_code == 404
    assert (await verify(owner, row))["ssl_status"] == "pending"
    assert (await verify(owner, row))["connection_status"] == "active"
    async with client_for(app, host) as c:
        assert (await c.get("/api/v1/public/tenant")).json()["id"] == str(tenant.id)
        r = await c.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        assert r.status_code == 200
        assert all("Domain=" not in h and "HttpOnly" in h for h in r.headers.get_list("set-cookie"))
    r = await owner.client.post(f"/api/v1/platform/tenants/{other_tenant.id}/domains", json={"domain": host.upper()})
    assert r.status_code == 409
    assert (await owner.client.patch(path, json={"enabled": False})).json()["connection_status"] == "disabled"
    async with client_for(app, host, token=tenant.admin.headers["Authorization"].split()[1]) as c:
        assert (await c.get("/api/v1/auth/me")).status_code == 404
    async with client_for(app, "owner.test.local") as c:
        assert (await c.get("/api/v1/internal/tls-ask", params={"domain": host})).status_code == 404
    assert (await owner.client.patch(path, json={"enabled": True})).status_code == 200
    renamed = f"{unique('renamed')}.example.com"
    updated = (await owner.client.patch(path, json={"domain": renamed})).json()
    assert updated["verification_status"] == "pending" and updated["verified_at"] is None
    assert updated["instructions"]["txt_value"] != row["instructions"]["txt_value"]
    for h in (host, renamed):
        async with client_for(app, h) as c:
            assert (await c.get("/api/v1/public/tenant")).status_code == 404
    assert (await owner.client.delete(path)).status_code == 200
    assert (await tenant.admin.get("/api/v1/auth/me")).status_code == 200


async def test_shared_and_custom_isolation(app, owner, tenant, other_tenant, dns):
    host, row = await add(owner, tenant, dns)
    await verify(owner, row)
    async with platform_session() as db:
        db.add(
            RentalSettings(
                id=1, shared_domain_enabled=True, default_tenant_id=other_tenant.id, broadcast_tenant_id=other_tenant.id
            )
        )
    try:
        async with client_for(app, host) as c:
            assert (await c.get("/api/v1/public/tenant")).json()["id"] == str(tenant.id)
            for path in ("/api/v1/auth/me", "/api/v1/admin/users"):
                r = await c.get(path, headers={"Authorization": other_tenant.admin.headers["Authorization"]})
                assert r.status_code == 401, r.text
            r = await c.post("/api/v1/auth/login", json={"username": f"admin@{other_tenant.slug}", "password": PASSWORD})
            assert r.status_code == 401
            r = await c.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
            assert r.status_code == 200 and r.json()["user"]["tenant"]["id"] == str(tenant.id)
            assert r.json()["user"]["tenant"]["player_url"] is None
            c.headers["Authorization"] = "Bearer " + r.json()["access_token"]
            assert (await c.get(f"/api/v1/admin/users/{other_tenant.admin_id}")).status_code == 404
            assert (await c.post("/api/v1/admin/streams", json={})).status_code == 403
        async with client_for(app, "test.local") as c:
            assert (await c.get("/api/v1/public/tenant")).json()["id"] == str(other_tenant.id)
            r = await c.post("/api/v1/auth/login", json={"username": f"admin@{tenant.slug}", "password": PASSWORD})
            assert r.status_code == 200 and r.json()["user"]["tenant"]["id"] == str(tenant.id)
    finally:
        async with platform_session() as db:
            await db.execute(delete(RentalSettings))


async def test_owner_blocked(app, owner, tenant, dns):
    host, row = await add(owner, tenant, dns)
    await verify(owner, row)
    async with client_for(app, host) as c:
        r = await c.post("/api/v1/platform/auth/login", json={"email": owner.email, "password": PASSWORD})
        assert r.status_code == 404 and not r.headers.get_list("set-cookie")
        assert (
            await c.get("/api/v1/platform/tenants", headers={"Authorization": owner.client.headers["Authorization"]})
        ).status_code == 404
        assert (await c.get("/api/v1/public/tenant", headers={"X-Forwarded-Host": "owner.test.local"})).json()["id"] == str(
            tenant.id
        )


async def test_bad_dns_and_ssl(app, owner, tenant, dns, monkeypatch):
    from app.modules.tenants import service

    host, row = await add(owner, tenant, dns)
    dns.clear()
    assert (await verify(owner, row))["connection_status"] == "failed"
    async with client_for(app, host) as c:
        assert (await c.get("/api/v1/public/tenant")).status_code == 404
    dns.append(row["instructions"]["txt_value"])
    monkeypatch.setattr(service, "_routing_addresses", lambda _: set())
    assert (await verify(owner, row))["verification_status"] == "failed"
    monkeypatch.setattr(service, "_routing_addresses", lambda _: {"8.8.8.8"})
    await verify(owner, row)
    monkeypatch.setattr(service, "_probe_tls", lambda *_: False)
    result = await verify(owner, row)
    assert result["ssl_status"] == "failed" and result["ssl_checked_at"]
    assert (await tenant.admin.get("/api/v1/auth/me")).status_code == 200


@pytest.mark.parametrize(
    "value",
    ["evil.com:443", "https://user@evil.com", "x.-bad.com", "x.bad-.com", "*.evil.com", "https://evil.com/path", "evil.com?x=1"],
)
async def test_invalid_hosts(owner, tenant, value):
    assert (await owner.client.post(f"/api/v1/platform/tenants/{tenant.id}/domains", json={"domain": value})).status_code == 422


@pytest.mark.parametrize(
    "actual,expected,allowed",
    [
        ({"8.8.8.8"}, {"8.8.8.8"}, True),
        ({"8.8.8.8", "1.1.1.1"}, {"8.8.8.8"}, False),
        ({"127.0.0.1"}, {"127.0.0.1"}, False),
        ({"169.254.169.254"}, {"169.254.169.254"}, False),
        (set(), {"8.8.8.8"}, False),
    ],
)
def test_routing_checks_every_address(monkeypatch, actual, expected, allowed):
    from app.core.config import get_settings
    from app.modules.tenants import service

    monkeypatch.setattr(get_settings(), "custom_domain_gateway_ips", "")
    monkeypatch.setattr(service, "_addresses", lambda host: actual if host == "custom.example" else expected)
    assert bool(service._routing_addresses("custom.example")) is allowed


async def test_duplicate_registration_race(owner, tenant, other_tenant):
    import asyncio

    host = f"{unique('race')}.example.com"
    results = await asyncio.gather(
        *[owner.client.post(f"/api/v1/platform/tenants/{t.id}/domains", json={"domain": host}) for t in (tenant, other_tenant)]
    )
    assert sorted(r.status_code for r in results) == [201, 409]


async def test_verification_cannot_approve_a_concurrent_edit(owner, tenant, dns, monkeypatch):
    import asyncio
    import threading

    from app.modules.tenants import service

    host, row = await add(owner, tenant, dns)
    started, release = threading.Event(), threading.Event()

    def lookup(_):
        started.set()
        assert release.wait(5)
        return dns

    monkeypatch.setattr(service, "_lookup_txt", lookup)
    checking = asyncio.create_task(verify(owner, row))
    assert await asyncio.to_thread(started.wait, 5)
    editing = asyncio.create_task(
        owner.client.patch(f"/api/v1/platform/domains/{row['id']}", json={"domain": f"{unique('edited')}.example.com"})
    )
    release.set()
    await checking
    result = await editing
    assert result.status_code == 200, result.text
    assert result.json()["verification_status"] == "pending"
    assert result.json()["verified_at"] is None


async def test_websocket_rejects_foreign_tenant(app, owner, tenant, other_tenant, dns):
    from httpx_ws import WebSocketDisconnect, aconnect_ws

    from tests.test_websocket import _receive, ws_client

    host, row = await add(owner, tenant, dns)
    await verify(owner, row)
    async with ws_client(app, host) as c:
        token = other_tenant.admin.headers["Authorization"].split()[1]
        async with aconnect_ws(f"/api/ws?token={token}", c) as ws:
            with pytest.raises(WebSocketDisconnect) as exc:
                await _receive(ws)
            assert exc.value.code == 4401


async def test_global_video_plays_on_custom_host_with_own_balance(app, owner, tenant, other_tenant, dns, monkeypatch):
    import secrets
    from types import SimpleNamespace

    from app.core.security import utcnow
    from app.modules.events.models import Event, EventStatus
    from app.modules.video import streams
    from app.modules.video.models import VideoStream
    from tests.test_betting import enable
    from tests.test_video_balance import set_balance

    host, row = await add(owner, other_tenant, dns)
    await verify(owner, row)
    await enable(other_tenant, starting_balance="100")
    user = await other_tenant.create_user(unique("viewer"), "player")
    async with platform_session() as db:
        stream = VideoStream(
            tenant_id=tenant.id, name="Owner stream", path_id=secrets.token_urlsafe(18), source_kind="obs", enabled=True
        )
        event = Event(tenant_id=other_tenant.id, name="Official arena", starts_at=utcnow(), status=EventStatus.LIVE)
        db.add_all([stream, event])
        await db.flush()
        db.add(RentalSettings(id=1, shared_domain_enabled=True, broadcast_tenant_id=tenant.id, global_stream_id=stream.id))

    async def live(_):
        return "live", None, SimpleNamespace(viewers=0, tracks=["H264", "Opus"], rtc_ready=True)

    monkeypatch.setattr(streams, "live_status", live)

    async def capacity(_):
        return False

    monkeypatch.setattr(streams, "at_capacity", capacity)
    try:
        async with client_for(app, host) as c:
            r = await c.post("/api/v1/auth/login", json={"username": user["username"], "password": PASSWORD})
            assert r.status_code == 200, r.text
            c.headers["Authorization"] = "Bearer " + r.json()["access_token"]
            await c.get("/api/v1/portal/betting")
            r = await c.post(f"/api/v1/video/events/{event.id}/playback")
            assert r.status_code == 200, r.text
            result = r.json()
            assert result["status"] == "live" and result["stream"]["id"] == str(stream.id)
            assert result["playback"]["url"].startswith("/stream/webrtc/")
            assert result["playback"]["fallback_url"].startswith("/stream/hls/")
            from urllib.parse import parse_qs, urlsplit

            token = parse_qs(urlsplit(result["playback"]["url"]).query)["token"][0]
            assert streams.verify_playback(token)["t"] == str(other_tenant.id)
            assert (await streams._authorize_read({"protocol": "hls", "token": token}, stream.path_id))[0]
            await set_balance(other_tenant, user["id"], "99.99")
            assert (await c.post(f"/api/v1/video/events/{event.id}/playback")).status_code == 403
            assert not (await streams._authorize_read({"protocol": "hls", "token": token}, stream.path_id))[0]
            assert (await c.patch(f"/api/v1/admin/streams/{stream.id}", json={"enabled": False})).status_code == 403
    finally:
        async with platform_session() as db:
            await db.execute(delete(RentalSettings))


async def test_background_checks_verify_dns_and_observe_certificates(owner, tenant, dns, monkeypatch):
    from app.workers.tasks import domains as worker

    _, row = await add(owner, tenant, dns)
    monkeypatch.setattr(worker, "run_async", lambda runner: runner())
    assert await worker.recheck_pending() >= 1
    rows = (await owner.client.get(f"/api/v1/platform/tenants/{tenant.id}/domains")).json()
    checked = next(d for d in rows if d["id"] == row["id"])
    assert checked["verification_status"] == "verified" and checked["ssl_status"] == "pending"
    await worker.recheck_pending()
    rows = (await owner.client.get(f"/api/v1/platform/tenants/{tenant.id}/domains")).json()
    assert next(d for d in rows if d["id"] == row["id"])["ssl_status"] == "active"
