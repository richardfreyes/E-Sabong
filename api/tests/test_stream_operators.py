"""Stream Operator role, the Operator dashboard API, browser (WHIP) broadcasts and recordings.

MediaMTX is the in-memory fake from test_video_streams; recordings are files the test writes
where MediaMTX would (the real server is exercised by the end-to-end check, see the README).
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.database import platform_session
from app.core.redis import get_redis
from app.modules.audit.models import AuditLog
from app.modules.video import recordings, streams
from app.modules.video.models import VideoBroadcast, VideoRecording, VideoStream
from tests import test_video_streams
from tests.conftest import PASSWORD, client_for
from tests.test_events import _event
from tests.test_video_streams import _db_stream, _hook

mtx = test_video_streams.mtx  # the in-memory MediaMTX fixture
STREAMS = "/api/v1/admin/streams"
OPS = "/api/v1/operator"


@pytest.fixture
def rec_root(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    root = tmp_path / "recordings"
    root.mkdir()
    monkeypatch.setenv("RECORDINGS_ROOT", str(root))
    monkeypatch.setenv("RECORDING_MIN_FREE_GB", "0")
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


async def _operator(tenant, username: str = "camop"):  # type: ignore[no-untyped-def]
    user = await tenant.create_user(username, "stream_operator")
    return user, await tenant.login(username)


async def _stream(tenant, name: str = "Arena cam", **extra) -> dict:  # type: ignore[no-untyped-def]
    r = await tenant.admin.post(STREAMS, json={"name": name, "source_kind": "obs", **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def _assign(tenant, user_id: str, *stream_ids: str) -> None:  # type: ignore[no-untyped-def]
    r = await tenant.admin.put(f"/api/v1/admin/operators/{user_id}/streams", json={"stream_ids": list(stream_ids)})
    assert r.status_code == 200, r.text


def _q(url: str, name: str = "token") -> str:
    return parse_qs(urlsplit(url).query)[name][0]


def _segment(
    root: Path, tenant_id, rec_id: str, path_id: str, name: str = "2026-11-07_08-10-00-309644.mp4", size: int = 4096
) -> Path:  # type: ignore[no-untyped-def]
    d = root / str(tenant_id) / rec_id / "live" / path_id
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x01" * (size - 12))
    return f


async def _reconcile() -> None:
    await get_redis().delete("video:paths")
    await streams.reconcile()
    await get_redis().delete("video:paths")


async def _set(model, row_id, **values) -> None:  # type: ignore[no-untyped-def]
    async with platform_session() as db:
        await db.execute(update(model).where(model.id == uuid.UUID(str(row_id))).values(**values))


# --------------------------------------------------------------------------- role and scope


async def test_stream_operator_account_is_limited_to_the_operator_dashboard(tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    user, op = await _operator(tenant)
    assert user["account_type"] == "stream_operator"
    assert [r["name"] for r in user["roles"]] == ["stream_operator"]
    me = (await op.get("/api/v1/auth/me")).json()
    assert me["permissions"] == ["streams.operate"]
    assert (await op.get(f"{OPS}/overview")).json()["streams"] == []

    event = await _event(tenant)
    # Every other surface refuses the account: admin, declarator, agent, player and video playback.
    for path in (
        "/api/v1/admin/users",
        STREAMS,
        "/api/v1/admin/operators",
        "/api/v1/declarator/events",
        "/api/v1/agent/users",
        "/api/v1/portal/dashboard",
        "/api/v1/portal/events",
        "/api/v1/admin/balance/summary",
    ):
        r = await op.get(path)
        assert r.status_code == 403, (path, r.status_code, r.text)
    r = await op.post(f"/api/v1/video/events/{event['id']}/playback")
    assert r.status_code == 403 and r.json()["error"]["code"] == "operator_scope"
    # Even a role granted later cannot widen it: the account type is scoped on the server.
    roles = {r["name"]: r["id"] for r in (await tenant.admin.get("/api/v1/admin/roles")).json()}
    await tenant.admin.patch(f"/api/v1/admin/users/{user['id']}", json={"role_ids": [roles["tenant_admin"]]})
    assert (await op.get("/api/v1/admin/users")).status_code == 403

    # Sign-in: the administration portal (which hosts the Operator dashboard) only.
    portal = client_for(tenant.app, tenant.host)
    refused = await portal.post(
        "/api/v1/auth/login", json={"username": "camop", "password": PASSWORD}, headers={"X-Portal": "player"}
    )
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "portal_not_permitted"
    assert (
        await portal.post("/api/v1/auth/login", json={"username": "camop", "password": PASSWORD}, headers={"X-Portal": "admin"})
    ).status_code == 200
    await portal.aclose()


async def test_admin_manages_operator_accounts_and_operators_cannot(tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    user, op = await _operator(tenant)
    listed = (await tenant.admin.get("/api/v1/admin/users", params={"account_type": "stream_operator"})).json()
    assert [u["username"] for u in listed["items"]] == ["camop"]
    reset = await tenant.admin.post(f"/api/v1/admin/users/{user['id']}/reset-password")
    assert reset.status_code == 200 and reset.json()["temporary_password"]
    assert (await op.get(f"{OPS}/overview")).status_code == 401  # reset ends existing sessions
    assert (await tenant.admin.post(f"/api/v1/admin/users/{user['id']}/suspend")).status_code == 200
    assert (
        await client_for(tenant.app, tenant.host).post(
            "/api/v1/auth/login", json={"username": "camop", "password": reset.json()["temporary_password"]}
        )
    ).status_code == 403
    assert (await tenant.admin.post(f"/api/v1/admin/users/{user['id']}/activate")).status_code == 200
    overview = (await tenant.admin.get("/api/v1/admin/operators")).json()
    assert [o["username"] for o in overview["operators"]] == ["camop"]
    # Streams can only be assigned to Stream Operator accounts.
    await tenant.create_user("plain", "player")
    other = (await tenant.admin.get("/api/v1/admin/users", params={"account_type": "player"})).json()["items"][0]
    s = await _stream(tenant)
    r = await tenant.admin.put(f"/api/v1/admin/operators/{other['id']}/streams", json={"stream_ids": [s["id"]]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_stream_operator"


async def test_operators_see_only_assigned_streams_of_their_own_tenant(tenant, other_tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    user, op = await _operator(tenant)
    mine, theirs = await _stream(tenant, "Cam A"), await _stream(tenant, "Cam B")
    foreign = await _stream(other_tenant, "Foreign")
    await _assign(tenant, user["id"], mine["id"])
    seen = (await op.get(f"{OPS}/overview")).json()
    assert [s["name"] for s in seen["streams"]] == ["Cam A"]
    stream = seen["streams"][0]
    assert stream["ingest"]["publish_key_hint"] == mine["publish_key"][-4:] and mine["publish_key"] not in str(seen)
    for sid in (theirs["id"], foreign["id"]):
        for suffix in ("/broadcast", "/recordings", "/preview", "/obs-key"):
            r = await op.post(f"{OPS}/streams/{sid}{suffix}", json={} if suffix == "/broadcast" else None)
            assert r.status_code == 404, (suffix, r.text)
    # Another tenant's administrator can neither assign this tenant's stream nor see the operator.
    r = await other_tenant.admin.put(f"/api/v1/admin/operators/{user['id']}/streams", json={"stream_ids": [foreign["id"]]})
    assert r.status_code == 404
    r = await tenant.admin.put(f"/api/v1/admin/operators/{user['id']}/streams", json={"stream_ids": [foreign["id"]]})
    assert r.status_code == 404
    assert (await other_tenant.admin.get("/api/v1/admin/operators")).json()["operators"] == []
    # Administrators operate every stream of their workspace.
    assert {s["name"] for s in (await tenant.admin.get(f"{OPS}/overview")).json()["streams"]} == {"Cam A", "Cam B"}
    # Unassigning removes access.
    await _assign(tenant, user["id"])
    assert (await op.get(f"{OPS}/overview")).json()["streams"] == []


# --------------------------------------------------------------------------- browser broadcasts


async def test_browser_broadcast_publish_token_is_bound_to_the_session(tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    user, op = await _operator(tenant)
    s = await _stream(tenant, enabled=True)
    row = await _db_stream(s["id"])
    await _assign(tenant, user["id"], s["id"])
    grant = await op.post(f"{OPS}/streams/{s['id']}/broadcast", json={"resolution": "1080p"})
    assert grant.status_code == 201, grant.text
    g = grant.json()
    assert g["whip_url"].startswith(f"/stream/webrtc/live/{row.path_id}/whip?token=") and g["max_bitrate_kbps"] == 4500
    assert g["broadcast"]["status"] == "connecting" and g["broadcast"]["mine"] and g["broadcast"]["resolution"] == "1080p"
    token = _q(g["whip_url"])
    path = f"live/{row.path_id}"
    publish = {"action": "publish", "protocol": "webrtc", "path": path, "ip": "198.51.100.9", "query": f"token={token}"}
    c = tenant.admin
    assert await _hook(c, publish) == 200
    assert await _hook(c, {**publish, "protocol": "rtmp"}) == 401  # tokens are for WHIP only
    assert await _hook(c, {**publish, "path": path + "/rtc"}) == 401
    other = await _stream(tenant, "Other cam")
    assert await _hook(c, {**publish, "path": f"live/{(await _db_stream(other['id'])).path_id}"}) == 401
    # A playback token is never a publish token (and vice versa).
    play, _ = streams.sign_playback(
        tenant_id=tenant.id,
        stream_id=row.id,
        path_id=row.path_id,
        user_id=uuid.UUID(user["id"]),
        family=uuid.uuid4(),
        audience="admin",
    )
    assert await _hook(c, {**publish, "query": f"token={play}"}) == 401
    read = {"action": "read", "protocol": "webrtc", "path": path, "ip": "198.51.100.9", "query": f"token={token}"}
    assert await _hook(c, read) == 401

    # Media arrives: the session goes live; the publisher is a WHIP session carrying the token.
    mtx.live(row.path_id, source=("webRTCSession", "pub-browser"))
    mtx.sessions["webrtcsessions"].append({"id": "pub-browser", "path": path, "query": f"token={token}", "state": "publish"})
    await _reconcile()
    overview = (await op.get(f"{OPS}/overview")).json()["streams"][0]
    assert overview["broadcast"]["status"] == "live" and overview["status"] == "live"
    admin_view = (await tenant.admin.get("/api/v1/admin/operators")).json()
    assert admin_view["active_sessions"][0]["operator_name"] == "Camop"
    assert admin_view["operators"][0]["broadcast"]["method"] == "browser"

    # Reconnect: a fresh token for the same session.
    again = (await op.post(f"{OPS}/streams/{s['id']}/broadcast/token")).json()
    assert again["broadcast"]["id"] == g["broadcast"]["id"]
    assert streams.verify_publish(_q(again["whip_url"]))["b"] == g["broadcast"]["id"]

    # Stop ends the session, kicks the publisher and the old token stops working.
    stopped = await op.post(f"{OPS}/streams/{s['id']}/broadcast/stop")
    assert stopped.status_code == 200 and stopped.json()["status"] == "ended"
    assert ("webrtcsessions", "pub-browser") in mtx.kicked
    assert await _hook(c, publish) == 401
    assert (await op.post(f"{OPS}/streams/{s['id']}/broadcast/token")).json()["error"]["code"] == "broadcast_ended"
    actions = await _audit(tenant.id, "video.broadcast_started", "video.broadcast_stopped")
    assert actions == ["video.broadcast_started", "video.broadcast_stopped"]


async def test_takeover_suspension_and_disconnect_end_browser_broadcasts(tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    a_user, a = await _operator(tenant, "opa")
    b_user, b = await _operator(tenant, "opb")
    s = await _stream(tenant)
    row = await _db_stream(s["id"])
    await _assign(tenant, a_user["id"], s["id"])
    await _assign(tenant, b_user["id"], s["id"])
    first = (await a.post(f"{OPS}/streams/{s['id']}/broadcast", json={})).json()
    busy = await b.post(f"{OPS}/streams/{s['id']}/broadcast", json={})
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "stream_busy"
    second = (await b.post(f"{OPS}/streams/{s['id']}/broadcast", json={"takeover": True})).json()
    async with platform_session() as db:
        old = await db.get(VideoBroadcast, uuid.UUID(first["broadcast"]["id"]))
    assert old.status == "ended" and old.end_reason == "Taken over by opb"
    path = f"live/{row.path_id}"
    old_token, new_token = _q(first["whip_url"]), _q(second["whip_url"])
    assert (
        await _hook(tenant.admin, {"action": "publish", "protocol": "webrtc", "path": path, "query": f"token={old_token}"}) == 401
    )

    # The replaced publisher (if still connected) is disconnected by reconciliation.
    mtx.live(row.path_id, source=("webRTCSession", "pub-b"))
    mtx.sessions["webrtcsessions"] += [
        {"id": "pub-a", "path": path, "query": f"token={old_token}", "state": "publish"},
        {"id": "pub-b", "path": path, "query": f"token={new_token}", "state": "publish"},
    ]
    await _reconcile()
    assert ("webrtcsessions", "pub-a") in mtx.kicked and ("webrtcsessions", "pub-b") not in mtx.kicked

    # Suspending the operator revokes their login: their live publisher is disconnected.
    assert (await tenant.admin.post(f"/api/v1/admin/users/{b_user['id']}/suspend")).status_code == 200
    await _reconcile()
    assert ("webrtcsessions", "pub-b") in mtx.kicked

    # No media for longer than the grace period ends the session.
    mtx.paths.clear()
    await _set(VideoBroadcast, second["broadcast"]["id"], last_seen_at=datetime.now(UTC) - timedelta(minutes=5))
    await _reconcile()
    async with platform_session() as db:
        ended = await db.get(VideoBroadcast, uuid.UUID(second["broadcast"]["id"]))
    assert ended.status == "ended" and ended.end_reason == "Source disconnected"


async def test_obs_sessions_are_observed_and_browser_publishing_refuses_busy_streams(tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    user, op = await _operator(tenant)
    s = await _stream(tenant)
    row = await _db_stream(s["id"])
    await _assign(tenant, user["id"], s["id"])
    mtx.live(row.path_id, source=("rtmpConn", "obs-1"))
    await get_redis().delete("video:paths")
    # Live from OBS before reconciliation noticed: still refused without takeover.
    r = await op.post(f"{OPS}/streams/{s['id']}/broadcast", json={})
    assert r.status_code == 409 and r.json()["error"]["details"]["method"] == "obs"
    await _reconcile()
    active = (await tenant.admin.get("/api/v1/admin/operators")).json()["active_sessions"]
    assert [(x["method"], x["status"], x["operator_id"]) for x in active] == [("obs", "live", None)]
    stop = await op.post(f"{OPS}/streams/{s['id']}/broadcast/stop")
    assert stop.status_code == 422 and stop.json()["error"]["code"] == "external_publisher"
    # OBS key: shown once to the operator, old key disconnected.
    creds = (await op.post(f"{OPS}/streams/{s['id']}/obs-key")).json()
    assert creds["stream_key"].startswith(f"{row.path_id}?key=") and creds["whip_bearer_token"] in creds["stream_key"]
    publish = {
        "action": "publish",
        "protocol": "rtmp",
        "path": f"live/{row.path_id}",
        "query": f"key={creds['whip_bearer_token']}",
    }
    assert await _hook(tenant.admin, publish) == 200
    assert ("rtmpconns", "obs-1") in mtx.kicked


# --------------------------------------------------------------------------- recordings


async def test_recording_lifecycle_playback_and_download(tenant, other_tenant, mtx, rec_root) -> None:  # type: ignore[no-untyped-def]
    user, op = await _operator(tenant)
    s = await _stream(tenant)
    row = await _db_stream(s["id"])
    await _assign(tenant, user["id"], s["id"])
    r = await op.post(f"{OPS}/streams/{s['id']}/recordings")
    assert r.status_code == 409 and r.json()["error"]["code"] == "stream_not_live"

    mtx.live(row.path_id)
    await get_redis().delete("video:paths")
    started = await op.post(f"{OPS}/streams/{s['id']}/recordings")
    assert started.status_code == 201, started.text
    rec = started.json()
    assert rec["status"] == "recording" and rec["started_by_name"] == "Camop"
    # MediaMTX now records the path into this recording's own directory.
    conf = mtx.config[f"live/{row.path_id}"]
    assert conf["record"] is True and conf["recordFormat"] == "fmp4"
    assert conf["recordPath"] == f"/recordings/{tenant.id}/{rec['id']}/%path/%Y-%m-%d_%H-%M-%S-%f"
    assert conf["runOnReady"] == streams.RELAY_COMMAND
    again = await op.post(f"{OPS}/streams/{s['id']}/recordings")
    assert again.status_code == 409 and again.json()["error"]["code"] == "recording_active"
    await _reconcile()
    assert mtx.config[f"live/{row.path_id}"]["record"] is True  # reconciliation keeps it

    _segment(rec_root, tenant.id, rec["id"], row.path_id, size=10_000)
    _segment(rec_root, tenant.id, rec["id"], row.path_id, name="2026-11-07_08-20-00-000001.mp4", size=6_000)
    await _reconcile()
    listed = (await op.get(f"{OPS}/recordings")).json()
    assert listed["total"] == 1 and listed["items"][0]["size_bytes"] == 16_000 and listed["items"][0]["segment_count"] == 2

    stopped = (await op.post(f"{OPS}/recordings/{rec['id']}/stop")).json()
    assert stopped["status"] == "finalizing"
    assert f"live/{row.path_id}" not in mtx.config  # back to the regex entry: recording off
    await _set(VideoRecording, rec["id"], stopped_at=datetime.now(UTC) - timedelta(seconds=30))
    await _reconcile()
    access = await op.post(f"{OPS}/recordings/{rec['id']}/access")
    assert access.status_code == 200, access.text
    files = access.json()["files"]
    assert [f["size"] for f in files] == [10_000, 6_000] and access.json()["recording"]["status"] == "completed"

    viewer = client_for(tenant.app, tenant.host)  # no bearer: <video> elements cannot send one
    play = await viewer.get(files[0]["play_url"], headers={"Range": "bytes=0-99"})
    assert play.status_code == 206 and len(play.content) == 100 and play.headers["content-type"] == "video/mp4"
    assert play.headers["content-disposition"].startswith("inline") and "no-store" in play.headers["cache-control"]
    down = await viewer.get(files[1]["download_url"])
    assert down.status_code == 200 and down.headers["content-disposition"].startswith("attachment") and len(down.content) == 6000
    # Links are bound to tenant, recording and file, and cannot be forged.
    foreign = client_for(other_tenant.app, other_tenant.host)
    assert (await foreign.get(files[0]["play_url"])).status_code == 404
    assert (await viewer.get(files[0]["play_url"].replace("/files/0", "/files/1"))).status_code == 404
    assert (await viewer.get(files[0]["play_url"][:-3] + "abc")).status_code == 404
    assert (await viewer.get(f"/api/v1/video/recordings/{rec['id']}/files/0")).status_code == 404
    # Other tenants cannot list or open it.
    assert (await other_tenant.admin.get(f"{OPS}/recordings")).json()["total"] == 0
    assert (await other_tenant.admin.post(f"{OPS}/recordings/{rec['id']}/access")).status_code == 404

    # Operators cannot delete; administrators can (files are removed too).
    assert (await op.delete(f"{OPS}/recordings/{rec['id']}")).status_code == 403
    assert (await tenant.admin.delete(f"{OPS}/recordings/{rec['id']}")).status_code == 204
    assert not (rec_root / str(tenant.id) / rec["id"]).exists()
    # Links stop working with the recording, and when the holder's sessions are revoked.
    assert (await viewer.get(files[0]["play_url"])).status_code == 404
    await viewer.aclose()
    await foreign.aclose()
    actions = await _audit(
        tenant.id, "video.recording_started", "video.recording_stopped", "video.recording_accessed", "video.recording_deleted"
    )
    assert actions == [
        "video.recording_started",
        "video.recording_stopped",
        "video.recording_accessed",
        "video.recording_deleted",
    ]


async def test_failed_recordings_limits_and_retention(tenant, mtx, rec_root, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    user, op = await _operator(tenant)
    s = await _stream(tenant)
    row = await _db_stream(s["id"])
    await _assign(tenant, user["id"], s["id"])
    mtx.live(row.path_id)
    await get_redis().delete("video:paths")

    # The source drops before any file is written: the recording fails safely.
    rec = (await op.post(f"{OPS}/streams/{s['id']}/recordings")).json()
    await op.post(f"{OPS}/recordings/{rec['id']}/stop")
    await _set(VideoRecording, rec["id"], stopped_at=datetime.now(UTC) - timedelta(seconds=30))
    await _reconcile()
    failed = (await op.get(f"{OPS}/recordings", params={"status": "failed"})).json()["items"]
    assert [f["id"] for f in failed] == [rec["id"]] and "No video was recorded" in failed[0]["detail"]
    assert (await op.post(f"{OPS}/recordings/{rec['id']}/access")).status_code == 409

    # Duration limit: stopped automatically by reconciliation.
    long = (await op.post(f"{OPS}/streams/{s['id']}/recordings")).json()
    _segment(rec_root, tenant.id, long["id"], row.path_id)
    await _set(VideoRecording, long["id"], started_at=datetime.now(UTC) - timedelta(hours=5))
    await _reconcile()
    async with platform_session() as db:
        auto = await db.get(VideoRecording, uuid.UUID(long["id"]))
    assert auto.status == "finalizing" and "minute limit" in auto.detail and f"live/{row.path_id}" not in mtx.config

    # Quota reached: new recordings are refused with the reason.
    monkeypatch.setenv("RECORDING_TENANT_QUOTA_GB", "0.000001")
    get_settings.cache_clear()
    r = await op.post(f"{OPS}/streams/{s['id']}/recordings")
    assert r.status_code == 422 and "storage limit" in r.json()["error"]["message"]
    assert (await op.get(f"{OPS}/overview")).json()["server"]["recording_available"] is False
    monkeypatch.setenv("RECORDING_TENANT_QUOTA_GB", "20")
    monkeypatch.setenv("RECORDINGS_ROOT", str(rec_root / "missing"))
    get_settings.cache_clear()
    r = await op.post(f"{OPS}/streams/{s['id']}/recordings")
    assert r.status_code == 422 and r.json()["error"]["message"] == "Recording storage is not available on this server"
    monkeypatch.setenv("RECORDINGS_ROOT", str(rec_root))
    get_settings.cache_clear()

    # Retention: expired recordings lose their files and their row.
    await _set(VideoRecording, long["id"], stopped_at=datetime.now(UTC) - timedelta(minutes=1))
    await _reconcile()
    await _set(VideoRecording, long["id"], expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert (await recordings.purge())["removed"] >= 1
    assert not (rec_root / str(tenant.id) / long["id"]).exists()
    async with platform_session() as db:
        assert await db.get(VideoRecording, uuid.UUID(long["id"])) is None


async def test_deleting_a_stream_ends_its_sessions_and_recordings(tenant, mtx, rec_root) -> None:  # type: ignore[no-untyped-def]
    user, op = await _operator(tenant)
    s = await _stream(tenant)
    row = await _db_stream(s["id"])
    await _assign(tenant, user["id"], s["id"])
    mtx.live(row.path_id)
    await get_redis().delete("video:paths")
    rec = (await op.post(f"{OPS}/streams/{s['id']}/recordings")).json()
    _segment(rec_root, tenant.id, rec["id"], row.path_id)
    await _reconcile()
    assert (await tenant.admin.delete(f"{STREAMS}/{s['id']}")).status_code == 204
    mtx.paths.clear()
    await _reconcile()
    async with platform_session() as db:
        r = await db.get(VideoRecording, uuid.UUID(rec["id"]))
        open_sessions = (
            await db.scalars(
                select(VideoBroadcast).where(VideoBroadcast.tenant_id == tenant.id, VideoBroadcast.status != "ended")
            )
        ).all()
    assert r.stream_id is None and r.status == "finalizing" and "deleted" in r.detail and open_sessions == []
    # The recording survives for administrators (operators lose it with the stream).
    assert (await tenant.admin.get(f"{OPS}/recordings")).json()["total"] == 1
    assert (await op.get(f"{OPS}/recordings")).json()["total"] == 0


# --------------------------------------------------------------------------- players


async def test_player_viewer_capacity_is_enforced(tenant, mtx, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("STREAM_MAX_VIEWERS", "1")
    get_settings.cache_clear()
    event = await _event(tenant)
    assert (await tenant.admin.post(f"/api/v1/admin/events/{event['id']}/publish")).status_code == 200
    s = await _stream(tenant, is_default=True, enabled=True)
    row = await _db_stream(s["id"])
    mtx.live(row.path_id)
    await get_redis().delete("video:paths")
    await tenant.create_user("fan1", "player")
    player = await tenant.login("fan1")
    play = f"/api/v1/video/events/{event['id']}/playback"
    first = (await player.post(play)).json()
    assert first["status"] == "live"
    mtx.viewer("webrtcsessions", f"live/{row.path_id}", _q(first["playback"]["url"]))
    await get_redis().delete("video:paths")
    full = (await player.post(play)).json()
    assert full["status"] == "error" and "capacity" in full["message"] and full["playback"] is None
    read = {
        "action": "read",
        "protocol": "webrtc",
        "path": f"live/{row.path_id}",
        "query": f"token={_q(first['playback']['url'])}",
    }
    assert await _hook(tenant.admin, read) == 401
    # Staff monitoring is not limited.
    admin_play = (await tenant.admin.post(f"{STREAMS}/{s['id']}/preview")).json()
    assert admin_play["status"] == "live"
    assert await _hook(tenant.admin, {**read, "query": f"token={_q(admin_play['playback']['url'])}"}) == 200


async def _audit(tenant_id, *actions: str) -> list[str]:  # type: ignore[no-untyped-def]
    async with platform_session() as db:
        rows = await db.scalars(
            select(AuditLog.action)
            .where(AuditLog.tenant_id == tenant_id, AuditLog.action.in_(actions))
            .order_by(AuditLog.created_at, AuditLog.id)
        )
        return list(rows.all())


def test_recording_config_matches_the_static_obs_entry() -> None:
    """While recording, the exact-name entry must equal the regex entry plus recording settings,
    or MediaMTX would recreate the path and disconnect the publisher."""
    stream = VideoStream(id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="x", source_kind="obs", path_id="a" * 24, enabled=True)
    assert streams.desired_config(stream) is None
    rec = VideoRecording(id=uuid.uuid4(), tenant_id=stream.tenant_id)
    conf = streams.desired_config(stream, rec)
    assert conf is not None
    assert {k: conf[k] for k in ("source", "runOnReady", "runOnReadyRestart")} == {
        "source": "publisher",
        "runOnReady": streams.RELAY_COMMAND,
        "runOnReadyRestart": True,
    }
    assert streams.config_differs(conf, {**conf, "record": False})
    assert not streams.config_differs(conf, dict(conf))
    assert streams.config_differs(conf, {**conf, "recordPath": "/recordings/other/%path"})


def test_reconcile_does_not_rewrite_unchanged_paths_that_are_not_recording(monkeypatch) -> None:
    """MediaMTX lists its default record settings for every path; a stream that is not recording
    must not look changed on every pass (each rewrite reloads MediaMTX's configuration)."""
    monkeypatch.setattr(streams, "decrypt_source", lambda _s: "rtmps://media.authorized.example/live/a")
    for kind in ("external", "test"):
        stream = VideoStream(id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="x", source_kind=kind, path_id="b" * 24, enabled=True)
        desired = streams.desired_config(stream)
        assert desired is not None
        reported = {
            "sourceOnDemand": False,
            "runOnInitRestart": False,
            "runOnDemandRestart": False,
            "runOnReadyRestart": True,
            "record": False,
            "recordPath": "./recordings/%path/%Y-%m-%d_%H-%M-%S-%f_%z",
            "recordFormat": "fmp4",
            **desired,
        }
        assert not streams.config_differs(desired, reported), kind


async def test_broadcast_history_is_scoped_to_the_operator(tenant, other_tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    a_user, a = await _operator(tenant, "hista")
    b_user, b = await _operator(tenant, "histb")
    mine, other = await _stream(tenant, "Hist A"), await _stream(tenant, "Hist B")
    await _assign(tenant, a_user["id"], mine["id"])
    await _assign(tenant, b_user["id"], other["id"])
    assert (await a.get(f"{OPS}/broadcasts")).json()["total"] == 0
    await a.post(f"{OPS}/streams/{mine['id']}/broadcast", json={})
    await b.post(f"{OPS}/streams/{other['id']}/broadcast", json={})
    seen = (await a.get(f"{OPS}/broadcasts")).json()
    assert [(x["stream_name"], x["mine"]) for x in seen["items"]] == [("Hist A", True)]
    await a.post(f"{OPS}/streams/{mine['id']}/broadcast/stop")
    # Unassigned: the operator still sees their own past sessions, nothing else.
    await _assign(tenant, a_user["id"])
    assert [x["stream_name"] for x in (await a.get(f"{OPS}/broadcasts")).json()["items"]] == ["Hist A"]
    assert (await tenant.admin.get(f"{OPS}/broadcasts")).json()["total"] == 2
    assert (await other_tenant.admin.get(f"{OPS}/broadcasts")).json()["total"] == 0


async def test_players_fall_back_to_the_default_stream_when_the_event_stream_is_hidden(tenant, mtx) -> None:  # type: ignore[no-untyped-def]
    event = await _event(tenant)
    assert (await tenant.admin.post(f"/api/v1/admin/events/{event['id']}/publish")).status_code == 200
    house = await _stream(tenant, "House cam", is_default=True, enabled=True)
    await _stream(tenant, "Event cam", event_id=event["id"], enabled=False)
    mtx.live((await _db_stream(house["id"])).path_id)
    await get_redis().delete("video:paths")
    await tenant.create_user("fan2", "player")
    player = await tenant.login("fan2")
    seen = (await player.post(f"/api/v1/video/events/{event['id']}/playback")).json()
    assert seen["status"] == "live" and seen["stream"]["name"] == "House cam"
    # Staff still monitor the event's own (hidden) stream.
    staff = (await tenant.admin.post(f"/api/v1/video/events/{event['id']}/playback?audience=declarator")).json()
    assert staff["stream"]["name"] == "Event cam"


async def test_upgrade_backfills_the_role_for_existing_workspaces(tenant) -> None:  # type: ignore[no-untyped-def]
    """Workspaces created before 0027 get the stream_operator role and admins streams.operate."""
    from sqlalchemy import delete, text

    from app.modules.users.models import Role, RolePermission
    from tests.conftest import run_alembic

    result = run_alembic("downgrade", "0026")
    assert result.returncode == 0, result.stderr
    async with platform_session() as db:  # as before 0027: no stream_operator role
        await db.execute(delete(Role).where(Role.tenant_id == tenant.id, Role.name == "stream_operator"))
    result = run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    async with platform_session() as db:
        role = await db.scalar(select(Role).where(Role.tenant_id == tenant.id, Role.name == "stream_operator"))
        perms = (
            await db.execute(
                select(Role.name)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .where(Role.tenant_id == tenant.id, RolePermission.permission == "streams.operate")
            )
        ).scalars()
        forced = await db.scalar(text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'tenants'"))
    assert role is not None and role.is_system
    assert set(perms) == {"stream_operator", "tenant_admin"}
    assert forced is True
