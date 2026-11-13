"""Live streams (MediaMTX): configuration, secrets, tenant/role isolation, playback, the MediaMTX
auth callback and reconciliation. MediaMTX is replaced by an in-memory fake of its v3 API."""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.database import platform_session
from app.core.redis import get_redis
from app.modules.audit.models import AuditLog
from app.modules.video import mediamtx, stream_router, streams
from app.modules.video.models import VideoStream
from tests.test_declarator import setup_desk
from tests.test_events import _event

URL = "/api/v1/admin/streams"
HOOK = "/api/v1/internal/mediamtx/auth"
API_PASSWORD = "test-mediamtx-api-password-0123456789"


class FakeMediaMTX(httpx.AsyncBaseTransport):
    """Enough of the MediaMTX v3 API for status, configuration, sessions and kicks."""

    def __init__(self) -> None:
        self.paths: dict[str, dict] = {}
        self.config: dict[str, dict] = {"~^live/([A-Za-z0-9_-]{24})$": {}, "~^live/[A-Za-z0-9_-]{24}/rtc$": {}}
        self.sessions: dict[str, list[dict]] = {"webrtcsessions": [], "hlssessions": [], "rtmpconns": []}
        self.kicked: list[tuple[str, str]] = []
        self.calls: list[str] = []
        self.up = True

    def live(self, path_id: str, tracks=("H264", "Opus"), rtc=False, source=("rtmpConn", "pub-1")) -> None:
        self.paths[f"live/{path_id}"] = {
            "name": f"live/{path_id}",
            "ready": True,
            "tracks": list(tracks),
            "readers": [],
            "source": {"type": source[0], "id": source[1]},
            "outboundBytes": 0,
        }
        if rtc:
            self.paths[f"live/{path_id}/rtc"] = {
                "name": f"live/{path_id}/rtc",
                "ready": True,
                "tracks": ["H264", "Opus"],
                "readers": [],
            }

    def viewer(self, kind: str, path: str, token: str, session_id: str | None = None) -> str:
        sid = session_id or str(uuid.uuid4())
        self.sessions[kind].append({"id": sid, "path": path, "query": f"token={token}", "state": "read"})
        self.paths[path]["readers"].append({"type": "webrtcSession" if kind == "webrtcsessions" else "hlsSession", "id": sid})
        return sid

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("down")
        assert request.headers.get("authorization", "").startswith("Basic ")
        path = request.url.path
        self.calls.append(f"{request.method} {path}")

        def page(items: list) -> httpx.Response:
            return httpx.Response(200, json={"itemCount": len(items), "pageCount": 1, "items": items})

        if path == "/v3/paths/list":
            return page(list(self.paths.values()))
        if path.startswith("/v3/paths/get/"):
            info = self.paths.get(path.removeprefix("/v3/paths/get/"))
            return httpx.Response(200, json=info) if info else httpx.Response(404, json={"error": "not found"})
        if path == "/v3/config/paths/list":
            return page([{"name": k, **v} for k, v in self.config.items()])
        for op in ("add", "replace", "delete"):
            prefix = f"/v3/config/paths/{op}/"
            if path.startswith(prefix):
                name = path.removeprefix(prefix)
                if op == "delete":
                    return httpx.Response(200 if self.config.pop(name, None) is not None else 404)
                if op == "replace" and name not in self.config:
                    return httpx.Response(404)
                self.config[name] = json.loads(request.content)
                return httpx.Response(200)
        m = re.match(r"^/v3/(\w+)/(list|kick/(.+))$", path)
        if m and m.group(1) in self.sessions:
            if m.group(2) == "list":
                return page(self.sessions[m.group(1)])
            self.kicked.append((m.group(1), m.group(3)))
            self.sessions[m.group(1)] = [s for s in self.sessions[m.group(1)] if s["id"] != m.group(3)]
            return httpx.Response(200)
        return httpx.Response(404)


@pytest.fixture
async def mtx(monkeypatch, tenant):  # type: ignore[no-untyped-def]
    from tests.test_betting_staff import configure

    await configure(tenant, starting_balance="1000.00")
    fake = FakeMediaMTX()
    monkeypatch.setenv("MEDIAMTX_API_PASSWORD", API_PASSWORD)
    monkeypatch.setenv("VIDEO_CREDENTIALS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("VIDEO_SOURCE_HOSTS", '["media.authorized.example"]')
    get_settings.cache_clear()
    monkeypatch.setattr(mediamtx, "test_transport", fake)

    async def public(host: str) -> str:
        return "203.0.113.10"

    monkeypatch.setattr(stream_router, "public_ip", public)
    redis = get_redis()
    for key in await redis.keys("video:*"):
        await redis.delete(key)
    yield fake
    get_settings.cache_clear()


async def _hook(app_client, body: dict, headers: dict | None = None) -> int:  # type: ignore[no-untyped-def]
    r = await app_client.post(HOOK, json=body, headers=headers or {})
    return r.status_code


def _token(url: str) -> str:
    return parse_qs(urlsplit(url).query)["token"][0]


async def _db_stream(stream_id: str) -> VideoStream:
    async with platform_session() as db:
        return await db.scalar(select(VideoStream).where(VideoStream.id == uuid.UUID(stream_id)))


# --------------------------------------------------------------------------- configuration


async def test_create_returns_the_publish_key_once_and_stores_only_secrets_safely(tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    r = await tenant.admin.post(URL, json={"name": "Main arena", "source_kind": "obs"})
    assert r.status_code == 201, r.text
    created = r.json()
    key = created["publish_key"]
    assert key and created["obs_stream_key"].endswith(f"?key={key}") and created["publish_key_hint"] == key[-4:]
    assert created["ingest"]["whip_path"].endswith("/whip")
    listed = (await tenant.admin.get(URL)).text
    assert key not in listed and '"publish_key":' not in listed
    row = await _db_stream(created["id"])
    assert row.publish_key_hash == streams.key_hash(key) and key not in json.dumps(row.publish_key_hash)

    secret = "https://user:s3cret-pass@media.authorized.example/live/index.m3u8"
    r = await tenant.admin.post(URL, json={"name": "Partner feed", "source_kind": "external", "source_url": secret})
    assert r.status_code == 201, r.text
    ext = r.json()
    assert "s3cret" not in r.text and ext["source_host"] == "media.authorized.example" and ext["source_protocol"] == "hls"
    assert "publish_key" not in ext or ext["publish_key"] is None
    row = await _db_stream(ext["id"])
    assert "s3cret" not in row.source_url and streams.decrypt_source(row) == secret
    # MediaMTX received the decrypted URL (server-side only) with on-demand pulling while disabled.
    assert mtx.config[f"live/{row.path_id}"]["source"] == secret and mtx.config[f"live/{row.path_id}"]["sourceOnDemand"] is True
    async with platform_session() as db:
        audit = (await db.scalars(select(AuditLog).where(AuditLog.resource_type == "video_stream"))).all()
    text = json.dumps([a.changes for a in audit], default=str)
    assert "s3cret" not in text and key not in text and {"video.stream_created"} <= {a.action for a in audit}


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://media.authorized.example/a.m3u8", "Supported sources"),
        ("https://evil.example/a.m3u8", "not an approved source host"),
        ("https://media.authorized.example/page.html", ".m3u8"),
        ("rtsp://media.authorized.example/cam", "Supported sources"),
    ],
)
async def test_external_sources_are_restricted(tenant, mtx, url, message) -> None:  # type: ignore[no-untyped-def]
    r = await tenant.admin.post(URL, json={"name": "Feed", "source_kind": "external", "source_url": url})
    assert r.status_code == 422 and message in r.text


async def test_private_resolution_and_conflicts_are_refused(tenant, mtx, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.modules.video.service import ProviderFailure

    async def private(host: str) -> str:
        raise ProviderFailure("invalid_destination", "Private and reserved destinations are prohibited")

    monkeypatch.setattr(stream_router, "public_ip", private)
    r = await tenant.admin.post(
        URL, json={"name": "Inside", "source_kind": "external", "source_url": "https://media.authorized.example/a.m3u8"}
    )
    assert r.status_code == 422 and "Private" in r.text
    event = await _event(tenant)
    a = (
        await tenant.admin.post(URL, json={"name": "A", "source_kind": "obs", "event_id": event["id"], "is_default": True})
    ).json()
    assert (await tenant.admin.post(URL, json={"name": "A", "source_kind": "obs"})).status_code == 409
    clash = await tenant.admin.post(URL, json={"name": "B", "source_kind": "obs", "event_id": event["id"]})
    assert clash.status_code == 409 and "already assigned" in clash.text
    b = (await tenant.admin.post(URL, json={"name": "B", "source_kind": "obs", "is_default": True})).json()
    assert b["is_default"] and not (await _db_stream(a["id"])).is_default  # one default per tenant


async def test_test_pattern_is_refused_in_production(tenant, mtx, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = get_settings()
    monkeypatch.setattr(type(settings), "is_production", property(lambda _: True))
    r = await tenant.admin.post(URL, json={"name": "Pattern", "source_kind": "test"})
    assert r.status_code == 422 and (await tenant.admin.get(URL + "/health")).json()["test_source_available"] is False


async def test_streaming_unconfigured_is_explicit(tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Empty (not just unset): an environment variable also overrides a developer's api/.env.
    monkeypatch.setenv("MEDIAMTX_API_PASSWORD", "")
    get_settings.cache_clear()
    assert (await tenant.admin.post(URL, json={"name": "X", "source_kind": "obs"})).status_code == 503
    health = (await tenant.admin.get(URL + "/health")).json()
    assert health["configured"] is False and "MEDIAMTX_API_PASSWORD" in health["detail"]
    get_settings.cache_clear()


# --------------------------------------------------------------------------- isolation and roles


async def test_streams_are_tenant_isolated_and_admin_only(tenant, other_tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    s = (await tenant.admin.post(URL, json={"name": "Mine", "source_kind": "obs"})).json()
    assert (await other_tenant.admin.get(URL)).json() == []
    for method, suffix in (("patch", ""), ("delete", ""), ("post", "/preview"), ("post", "/rotate-key"), ("post", "/reconnect")):
        kwargs = {"json": {"enabled": True}} if method == "patch" else {}
        r = await getattr(other_tenant.admin, method)(f"{URL}/{s['id']}{suffix}", **kwargs)
        assert r.status_code == 404, (method, suffix, r.text)
    await tenant.create_user("viewer9", "player")
    viewer = await tenant.login("viewer9")
    assert (await viewer.get("/api/v1/portal/betting")).status_code == 200
    for path in (URL, URL + "/health"):
        assert (await viewer.get(path)).status_code == 403
    assert (await viewer.post(URL, json={"name": "Nope", "source_kind": "obs"})).status_code == 403


# --------------------------------------------------------------------------- playback


async def test_player_sees_only_enabled_live_streams_and_declarator_monitors_assigned(tenant, other_tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    _, event, _, judge = await setup_desk(tenant)
    s = (await tenant.admin.post(URL, json={"name": "Arena cam", "source_kind": "obs", "event_id": event["id"]})).json()
    row = await _db_stream(s["id"])
    await tenant.create_user("watcher", "player")
    player = await tenant.login("watcher")
    assert (await player.get("/api/v1/portal/betting")).status_code == 200
    play = f"/api/v1/video/events/{event['id']}/playback"

    # Disabled: players fall back to the external provider (not configured -> unavailable).
    assert (await player.post(play)).json()["status"] in ("disabled", "unavailable")
    # Declarators monitor their assigned event's stream before it opens to players.
    offline = (await judge.post(play + "?audience=declarator")).json()
    assert offline["status"] == "offline" and offline["stream"]["name"] == "Arena cam" and offline["playback"] is None

    mtx.live(row.path_id, tracks=("H264", "MPEG-4 Audio"), rtc=True)
    await get_redis().delete("video:paths")
    monitor = (await judge.post(play + "?audience=declarator")).json()
    assert monitor["status"] == "live" and monitor["playback"]["type"] == "webrtc"
    # AAC cannot play over WebRTC: the Opus relay path is used; HLS reads the original.
    assert monitor["playback"]["url"].startswith(f"/stream/webrtc/live/{row.path_id}/rtc/whep?token=")
    assert monitor["playback"]["fallback_url"].startswith(f"/stream/hls/live/{row.path_id}/index.m3u8?token=")

    assert (await tenant.admin.patch(f"{URL}/{s['id']}", json={"enabled": True})).status_code == 200
    live = (await player.post(play)).json()
    assert live["status"] == "live" and live["playback"]["key"] == s["id"] and live["stream"]["viewers"] is None
    claims = streams.verify_playback(_token(live["playback"]["url"]))
    assert claims["a"] == "player" and claims["p"] == row.path_id and claims["t"] == str(tenant.id)
    # Another tenant's event, and an event the declarator is not assigned to, stay closed.
    foreign = await _event(other_tenant)
    assert (await judge.post(f"/api/v1/video/events/{foreign['id']}/playback?audience=declarator")).status_code == 404


async def test_default_arena_stream_applies_to_events_without_their_own(tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    event = await _event(tenant)
    s = (await tenant.admin.post(URL, json={"name": "House", "source_kind": "obs", "is_default": True, "enabled": True})).json()
    row = await _db_stream(s["id"])
    mtx.live(row.path_id)
    await get_redis().delete("video:paths")
    result = (await tenant.admin.post(f"/api/v1/video/events/{event['id']}/playback?audience=declarator")).json()
    assert result["stream"]["id"] == s["id"]
    # H.264 + Opus plays over WebRTC directly from the main path.
    assert result["playback"]["url"].startswith(f"/stream/webrtc/live/{row.path_id}/whep?")


# --------------------------------------------------------------------------- MediaMTX callback


async def test_hook_publish_requires_the_current_key_and_rate_limits_guessing(tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    s = (await tenant.admin.post(URL, json={"name": "Cam", "source_kind": "obs"})).json()
    row = await _db_stream(s["id"])
    path = f"live/{row.path_id}"
    good = {"action": "publish", "protocol": "rtmp", "path": path, "ip": "198.51.100.7", "query": f"key={s['publish_key']}"}
    c = tenant.admin
    assert await _hook(c, good) == 200
    assert await _hook(c, {**good, "protocol": "webrtc", "query": "", "token": s["publish_key"]}) == 200  # WHIP bearer
    assert await _hook(c, {**good, "path": path + "/rtc"}) == 401  # the relay path is internal
    assert await _hook(c, {**good, "protocol": "rtsp", "ip": "198.51.100.7"}) == 401  # RTSP only from inside the container
    assert await _hook(c, {**good, "protocol": "rtsp", "ip": "127.0.0.1"}) == 200
    # Through a proxy the callback does not exist.
    assert await _hook(c, good, {"X-Forwarded-For": "198.51.100.7"}) == 404

    rotated = (await c.post(f"{URL}/{s['id']}/rotate-key")).json()
    assert await _hook(c, good) == 401
    assert await _hook(c, {**good, "query": f"key={rotated['publish_key']}"}) == 200

    attacker = {**good, "ip": "192.0.2.66", "query": "key=guess"}
    for _ in range(streams.PUBLISH_FAILURE_LIMIT):
        assert await _hook(c, attacker) == 401
    assert await _hook(c, {**attacker, "query": f"key={rotated['publish_key']}"}) == 401  # locked out


async def test_hook_read_needs_a_valid_token_for_that_stream(tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    _, event, _, judge = await setup_desk(tenant)
    s = (
        await tenant.admin.post(URL, json={"name": "Cam", "source_kind": "obs", "event_id": event["id"], "enabled": True})
    ).json()
    other = (await tenant.admin.post(URL, json={"name": "Other", "source_kind": "obs"})).json()
    row, other_row = await _db_stream(s["id"]), await _db_stream(other["id"])
    mtx.live(row.path_id)
    await get_redis().delete("video:paths")
    await tenant.create_user("reader", "player")
    player = await tenant.login("reader")
    assert (await player.get("/api/v1/portal/betting")).status_code == 200
    playback = (await player.post(f"/api/v1/video/events/{event['id']}/playback")).json()["playback"]
    token = _token(playback["url"])
    read = {
        "action": "read",
        "protocol": "webrtc",
        "path": f"live/{row.path_id}",
        "ip": "198.51.100.8",
        "query": f"token={token}",
    }
    c = tenant.admin
    assert await _hook(c, read) == 200
    assert await _hook(c, {**read, "query": "", "token": token}) == 200  # Authorization: Bearer
    assert await _hook(c, {**read, "protocol": "hls"}) == 200
    assert await _hook(c, {**read, "path": f"live/{row.path_id}/rtc"}) == 200
    assert await _hook(c, {**read, "protocol": "rtmp"}) == 401  # public reads only over WebRTC/HLS
    assert await _hook(c, {**read, "path": f"live/{other_row.path_id}"}) == 401
    assert await _hook(c, {**read, "query": "token=" + token[:-2] + "xx"}) == 401
    assert await _hook(c, {**read, "query": ""}) == 401
    expired, _ = streams.sign_playback(
        tenant_id=tenant.id,
        stream_id=row.id,
        path_id=row.path_id,
        user_id=uuid.uuid4(),
        family=uuid.uuid4(),
        audience="player",
        ttl=-1,
    )
    assert await _hook(c, {**read, "query": f"token={expired}"}) == 401

    # Disabling hides the stream from players but not from the assigned declarator's monitor.
    monitor = (await judge.post(f"/api/v1/video/events/{event['id']}/playback?audience=declarator")).json()["playback"]
    await c.patch(f"{URL}/{s['id']}", json={"enabled": False})
    assert await _hook(c, read) == 401
    assert await _hook(c, {**read, "query": f"token={_token(monitor['url'])}"}) == 200

    # Session revocation (sign-out) also ends the right to start playback.
    await c.patch(f"{URL}/{s['id']}", json={"enabled": True})
    assert await _hook(c, read) == 200
    assert (await player.post("/api/v1/auth/logout")).status_code == 200
    assert await _hook(c, read) == 401


async def test_hook_control_api_credentials(tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    c = tenant.admin
    api = {"action": "api", "user": "esabong-api", "password": API_PASSWORD, "ip": "172.18.0.5", "path": ""}
    assert await _hook(c, api) == 200
    assert await _hook(c, {**api, "password": "wrong"}) == 401
    assert await _hook(c, {"action": "playback", "path": "live/x", "ip": "1.2.3.4"}) == 401


# --------------------------------------------------------------------------- reconciliation


async def test_reconcile_status_viewers_config_and_access_enforcement(tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    event = await _event(tenant)
    assert (await tenant.admin.post(f"/api/v1/admin/events/{event['id']}/publish")).status_code == 200
    s = (
        await tenant.admin.post(URL, json={"name": "Cam", "source_kind": "obs", "event_id": event["id"], "enabled": True})
    ).json()
    pattern = (await tenant.admin.post(URL, json={"name": "Pattern", "source_kind": "test", "enabled": True})).json()
    row, test_row = await _db_stream(s["id"]), await _db_stream(pattern["id"])
    mtx.config.pop(f"live/{test_row.path_id}", None)  # as after a MediaMTX restart
    mtx.config["live/" + "Z" * 24] = {"source": "publisher"}  # orphan of a deleted stream

    await streams.reconcile()
    assert mtx.config[f"live/{test_row.path_id}"]["runOnInit"] == streams.TEST_COMMAND
    assert "live/" + "Z" * 24 not in mtx.config
    assert (await _db_stream(s["id"])).status == "offline"

    mtx.live(row.path_id)
    await get_redis().delete("video:paths")  # the 2 s shared path cache
    await tenant.create_user("viewer1", "player")
    viewer = await tenant.login("viewer1")
    assert (await viewer.get("/api/v1/portal/betting")).status_code == 200
    token = _token((await viewer.post(f"/api/v1/video/events/{event['id']}/playback")).json()["playback"]["url"])
    player_session = mtx.viewer("webrtcsessions", f"live/{row.path_id}", token)
    preview = (await tenant.admin.post(f"{URL}/{s['id']}/preview")).json()["playback"]
    admin_session = mtx.viewer("hlssessions", f"live/{row.path_id}", _token(preview["fallback_url"]))
    result = await streams.reconcile()
    live = await _db_stream(s["id"])
    assert live.status == "live" and live.viewers == 2 and live.last_live_at is not None and result["kicked"] == 0

    # Disabled for players: the player's session ends, the administrator's preview continues.
    await tenant.admin.patch(f"{URL}/{s['id']}", json={"enabled": False})
    await streams.reconcile()
    assert ("webrtcsessions", player_session) in mtx.kicked and ("hlssessions", admin_session) not in mtx.kicked

    # The source drops: reconnecting within the grace period, then offline.
    mtx.paths.clear()
    await streams.reconcile()
    assert (await _db_stream(s["id"])).status == "reconnecting"
    async with platform_session() as db:
        await db.execute(
            update(VideoStream).where(VideoStream.id == row.id).values(last_live_at=datetime.now(UTC) - timedelta(minutes=5))
        )
    await streams.reconcile()
    assert (await _db_stream(s["id"])).status == "offline"

    mtx.up = False
    await streams.reconcile()
    down = await _db_stream(s["id"])
    assert down.status == "error" and down.status_detail == "Streaming server unreachable"


async def test_delete_and_key_rotation_disconnect_the_broadcaster(tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    s = (await tenant.admin.post(URL, json={"name": "Cam", "source_kind": "obs"})).json()
    row = await _db_stream(s["id"])
    mtx.live(row.path_id, source=("rtmpConn", "obs-conn-1"))
    assert (await tenant.admin.post(f"{URL}/{s['id']}/rotate-key")).status_code == 200
    assert ("rtmpconns", "obs-conn-1") in mtx.kicked
    assert (await tenant.admin.post(f"{URL}/{s['id']}/reconnect")).status_code == 200
    assert (await tenant.admin.post(f"{URL}/{s['id']}/reconnect")).status_code == 429  # throttled
    assert (await tenant.admin.delete(f"{URL}/{s['id']}")).status_code == 204
    assert await _db_stream(s["id"]) is None


async def test_preview_starts_an_on_demand_source_but_players_never_get_disabled_streams(tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    s = (await tenant.admin.post(URL, json={"name": "Pattern", "source_kind": "test", "is_default": True})).json()
    row = await _db_stream(s["id"])
    assert "runOnDemand" in mtx.config[f"live/{row.path_id}"]  # nothing runs until someone watches
    preview = (await tenant.admin.post(f"{URL}/{s['id']}/preview")).json()
    assert preview["status"] == "connecting" and preview["playback"]["url"].startswith(f"/stream/webrtc/live/{row.path_id}/whep")
    assert streams.verify_playback(_token(preview["playback"]["url"]))["a"] == "admin"
    event = await _event(tenant)
    assert (await tenant.admin.post(f"/api/v1/admin/events/{event['id']}/publish")).status_code == 200
    await tenant.create_user("viewer5", "player")
    viewer = await tenant.login("viewer5")
    assert (await viewer.get("/api/v1/portal/betting")).status_code == 200
    result = (await viewer.post(f"/api/v1/video/events/{event['id']}/playback")).json()
    assert result.get("stream") is None and result.get("playback") is None
