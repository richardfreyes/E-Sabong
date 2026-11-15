"""Original Admin controls one source; existing/new tenants receive only eligible playback."""

import json
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.database import platform_session
from app.core.redis import get_redis
from app.modules.realtime.publisher import publish
from app.modules.tenants.models import DomainKind, DomainVerification, Tenant, TenantDomain
from app.modules.tenants.rental import RentalSettings
from app.modules.video import streams
from app.modules.video.global_stream import configuration
from tests.conftest import PASSWORD, TenantHandle, client_for, make_tenant, unique
from tests.streaming_fixtures import streaming_tenant
from tests.test_betting_staff import configure
from tests.test_events import _event
from tests.test_video_streams import mtx  # noqa: F401 - shared MediaMTX transport fixture

tenant = streaming_tenant


URL = "/api/v1/admin/streams"


async def test_original_admin_global_broadcast(app, owner, tenant, other_tenant, plans, mtx):  # noqa: F811
    # Reuse the permanently designated workspace when other tests ran before this one.
    async with platform_session() as db:
        original = await db.scalar(select(Tenant).where(Tenant.is_global_stream_provider))
    if original.id != tenant.id:
        admin = client_for(app, f"{original.slug}.test.local")
        tenant.clients.append(admin)
        login = await admin.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        assert login.status_code == 200, login.text
        admin.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        tenant = TenantHandle(
            app, original.id, original.slug, f"{original.slug}.test.local", admin, uuid.UUID(login.json()["user"]["id"])
        )
    for t, expected in ((tenant, True), (other_tenant, False)):
        assert (await t.admin.get("/api/v1/public/tenant")).json()["is_global_stream_provider"] is expected
    stream = await tenant.admin.post(URL, json={"name": unique("global"), "source_kind": "obs", "enabled": True})
    assert stream.status_code == 201, stream.text
    source = stream.json()
    source["path_id"] = source["obs_stream_key"].split("?", 1)[0]
    assert source["is_default"] is True
    sid = source["id"]
    mtx.live(source["path_id"])
    await get_redis().delete("video:paths")

    # Every management URL rejects consumers, even with their normal administrator permissions.
    for method, path, body in (
        ("get", URL, None),
        ("get", URL + "/health", None),
        ("post", URL, {"name": "unauthorized", "source_kind": "obs"}),
        ("patch", URL + "/" + sid, {"enabled": False}),
        ("post", URL + "/" + sid + "/rotate-key", {}),
        ("post", URL + "/" + sid + "/preview", {}),
        ("get", "/api/v1/admin/operators", None),
        ("get", "/api/v1/operator/overview", None),
        ("get", "/api/v1/admin/settings/video-provider", None),
    ):
        result = await other_tenant.admin.request(method, path, json=body)
        assert result.status_code == 403, (method, path, result.text)
        assert result.json()["error"]["code"] == "global_stream_provider_required"
    result = await other_tenant.admin.post(
        "/api/v1/admin/users",
        json={
            "username": unique("forbidden"),
            "full_name": "No operator",
            "account_type": "stream_operator",
            "password": PASSWORD,
        },
    )
    assert result.status_code == 403
    for path in ("/streams", "/streams/" + sid + "/rotate-key"):
        result = await owner.client.post("/api/v1/platform/rental" + path, json={})
        assert result.status_code == 404
    result = await owner.client.put("/api/v1/platform/rental/settings", json={"global_stream_id": sid})
    assert result.status_code == 422
    assert (await tenant.admin.get("/api/v1/admin/operators")).status_code == 200
    assert (await tenant.admin.get("/api/v1/operator/overview")).status_code == 200

    # A tenant created while already broadcasting receives exactly the same path without setup.
    future = await make_tenant(app, owner, plans["test-pro"])
    pubsub = get_redis().pubsub()
    await pubsub.subscribe("rt:global:stream")
    try:
        for t in (other_tenant, future):
            await configure(t, starting_balance="1000.00")
            user = await t.create_user(unique("viewer"), "player")
            player = await t.login(user["username"])
            assert (await player.get("/api/v1/portal/betting")).status_code == 200
            # Video is already available before this new tenant publishes any event.
            inherited = await player.post("/api/v1/portal/video/global/playback")
            assert inherited.status_code == 200 and inherited.json()["stream"]["id"] == sid
            event = await _event(t)
            assert (await t.admin.post(f"/api/v1/admin/events/{event['id']}/publish")).status_code == 200
            response = await player.post(f"/api/v1/video/events/{event['id']}/playback")
            assert response.status_code == 200, response.text
            video = response.json()
            assert video["stream"]["id"] == sid
            token = parse_qs(urlsplit(video["playback"]["url"]).query)["token"][0]
            claims = streams.verify_playback(token)
            assert claims["t"] == str(t.id) and claims["s"] == sid
            allowed, _ = await streams.authorize(
                {"action": "read", "protocol": "webrtc", "path": f"live/{source['path_id']}", "token": token}
            )
            assert allowed
            from tests.test_video_balance import set_balance

            await set_balance(t, user["id"], "99.99")
            assert (await player.post(f"/api/v1/video/events/{event['id']}/playback")).status_code == 403
            assert not (
                await streams.authorize(
                    {"action": "read", "protocol": "hls", "path": f"live/{source['path_id']}", "token": token}
                )
            )[0]
            await set_balance(t, user["id"], "1000.00")
            # Consumer identity is retained on a custom domain as well.
            custom = unique("viewer") + ".example.test"
            async with platform_session() as db:
                db.add(
                    TenantDomain(
                        tenant_id=t.id,
                        domain=custom,
                        kind=DomainKind.CUSTOM,
                        is_primary=False,
                        verification_status=DomainVerification.VERIFIED,
                        verification_token="verified",
                        enabled=True,
                    )
                )
            async with client_for(app, custom, player.headers["Authorization"].split()[1]) as client:
                assert (await client.post(f"/api/v1/video/events/{event['id']}/playback")).json()["stream"]["id"] == sid
            if t is other_tenant:
                # Shared-domain routing changes identity resolution, never the global video source.
                async with platform_session() as db:
                    db.add(RentalSettings(id=1, shared_domain_enabled=True, default_tenant_id=t.id))
                async with client_for(app, "test.local", player.headers["Authorization"].split()[1]) as client:
                    assert (await client.post(f"/api/v1/video/events/{event['id']}/playback")).json()["stream"]["id"] == sid
                # Provider management remains available on shared-domain access.
                async with client_for(app, "test.local", tenant.admin.headers["Authorization"].split()[1]) as client:
                    assert (await client.get(URL)).status_code == 200
                async with platform_session() as db:
                    row = await db.get(RentalSettings, 1)
                    row.shared_domain_enabled = False

        # Global source changes notify all tenants; financial frames never enter this channel.
        while await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1):
            pass
        await publish(tenant.id, "wallet.updated", {"balance": "secret"})
        assert await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1) is None
        await publish(tenant.id, "video.source_changed", {"stream_id": sid, "secret": "private"})
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
        assert json.loads(message["data"]) == {"type": "video.source_changed", "data": {"stream_id": sid}}
        assert (await tenant.admin.patch(URL + "/" + sid, json={"enabled": False})).status_code == 200
        response = await player.post(f"/api/v1/video/events/{event['id']}/playback")
        assert response.json()["status"] == "disabled"
        assert (await configuration()).global_stream_id == uuid.UUID(sid)
        assert not (
            await streams.authorize({"action": "read", "protocol": "webrtc", "path": f"live/{source['path_id']}", "token": token})
        )[0]
        replacement = await tenant.admin.post(
            URL, json={"name": unique("replacement"), "source_kind": "obs", "enabled": True, "is_default": True}
        )
        assert replacement.status_code == 201
        next_stream = replacement.json()
        mtx.live(next_stream["obs_stream_key"].split("?", 1)[0])
        await get_redis().delete("video:paths")
        switched = await player.post(f"/api/v1/video/events/{event['id']}/playback")
        assert switched.json()["stream"]["id"] == next_stream["id"]
        assert not (await streams._authorize_read({"protocol": "hls", "token": token}, source["path_id"]))[0]
    finally:
        await pubsub.aclose()
        await future.admin.aclose()
        for client in future.clients:
            await client.aclose()
        async with platform_session() as db:
            row = await db.get(RentalSettings, 1)
            if row:
                await db.delete(row)

    # Even privileged application SQL cannot transfer the system designation.
    with pytest.raises(IntegrityError, match="designation is permanent"):
        async with platform_session() as db:
            await db.execute(text("UPDATE tenants SET is_global_stream_provider = false WHERE id = :id"), {"id": tenant.id})
    assert (await owner.client.delete(f"/api/v1/platform/tenants/{tenant.id}")).status_code == 409
