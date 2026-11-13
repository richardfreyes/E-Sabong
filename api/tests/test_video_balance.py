from decimal import Decimal

import pytest
from sqlalchemy import update

from app.core.database import tenant_session
from app.core.redis import get_redis
from app.modules.betting.models import PlayWallet
from app.modules.video import streams
from tests import test_video_streams
from tests.test_declarator import setup_desk
from tests.test_video_streams import URL, _db_stream, _hook, _token

mtx = test_video_streams.mtx


async def set_balance(tenant, user_id, value):
    async with tenant_session(tenant.id) as db:
        await db.execute(
            update(PlayWallet)
            .where(PlayWallet.tenant_id == tenant.id, PlayWallet.user_id == user_id)
            .values(balance=Decimal(value))
        )


@pytest.mark.parametrize(
    "balance,allowed", [("0", False), ("50", False), ("99.99", False), ("100", True), ("150", True), ("1000", True)]
)
async def test_player_balance_boundaries(tenant, mtx, balance, allowed):
    _, event, _, judge = await setup_desk(tenant)
    user = await tenant.create_user("balance_viewer", "player")
    viewer = await tenant.login("balance_viewer")
    await viewer.get("/api/v1/portal/betting")
    await set_balance(tenant, user["id"], balance)
    play = f"/api/v1/video/events/{event['id']}/playback"
    for action in ("playback", "refresh", "reconnect"):
        response = await viewer.post(play.removesuffix("playback") + action)
        assert response.status_code == (200 if allowed else 403), response.text
        if not allowed:
            assert response.json()["error"]["code"] == "stream_insufficient_balance"
    assert (await judge.post(play + "?audience=declarator")).status_code == 200
    assert (await tenant.admin.post(play)).status_code == 200


async def test_old_tokens_deny_low_balance_and_reconciliation_kicks_only_that_player(tenant, mtx):
    _, event, _, judge = await setup_desk(tenant)
    created = (
        await tenant.admin.post(URL, json={"name": "Balance cam", "source_kind": "obs", "enabled": True, "event_id": event["id"]})
    ).json()
    row = await _db_stream(created["id"])
    mtx.live(row.path_id)
    await get_redis().delete("video:paths")
    users, viewers, tokens = [], [], []
    play = f"/api/v1/video/events/{event['id']}/playback"
    for name in ("poor_viewer", "rich_viewer"):
        users.append(await tenant.create_user(name, "player"))
        viewer = await tenant.login(name)
        await viewer.get("/api/v1/portal/betting")
        viewers.append(viewer)
        tokens.append(_token((await viewer.post(play)).json()["playback"]["url"]))
    preview = _token((await judge.post(play + "?audience=declarator")).json()["playback"]["url"])
    path = f"live/{row.path_id}"
    poor_rtc = mtx.viewer("webrtcsessions", path, tokens[0])
    poor_hls = mtx.viewer("hlssessions", path, tokens[0])
    rich = mtx.viewer("webrtcsessions", path, tokens[1])
    staff = mtx.viewer("hlssessions", path, preview)
    await set_balance(tenant, users[0]["id"], "99.99")
    for protocol in ("webrtc", "hls"):
        body = {"action": "read", "protocol": protocol, "path": path, "query": f"token={tokens[0]}"}
        assert await _hook(tenant.admin, body) == 401
        assert await _hook(tenant.admin, {**body, "query": f"token={tokens[1]}"}) == 200
    await streams.reconcile()
    assert ("webrtcsessions", poor_rtc) in mtx.kicked
    assert ("hlssessions", poor_hls) in mtx.kicked
    assert ("webrtcsessions", rich) not in mtx.kicked
    assert ("hlssessions", staff) not in mtx.kicked
    await set_balance(tenant, users[0]["id"], "100.00")
    assert (await viewers[0].post(play)).json()["playback"]


async def test_committed_bet_stops_playback_only_when_available_balance_drops_below_100(tenant, mtx):
    import uuid

    _, event, fight, judge = await setup_desk(tenant)
    user = await tenant.create_user("betting_viewer", "player")
    viewer = await tenant.login("betting_viewer")
    await viewer.get("/api/v1/portal/betting")
    await set_balance(tenant, user["id"], "150.00")
    created = (
        await tenant.admin.post(URL, json={"name": "Bet cam", "source_kind": "obs", "enabled": True, "event_id": event["id"]})
    ).json()
    row = await _db_stream(created["id"])
    mtx.live(row.path_id)
    await get_redis().delete("video:paths")
    token = _token((await viewer.post(f"/api/v1/video/events/{event['id']}/playback")).json()["playback"]["url"])
    sid = mtx.viewer("webrtcsessions", f"live/{row.path_id}", token)
    await judge.post(f"/api/v1/declarator/matches/{fight['id']}/betting/open")

    async def bet(amount):
        return await viewer.post(
            f"/api/v1/portal/matches/{fight['id']}/bets",
            json={"request_id": str(uuid.uuid4()), "side": "meron", "amount": amount},
        )

    first = await bet("50.00")
    assert first.status_code == 201, first.text
    assert (await viewer.get("/api/v1/portal/betting")).json()["wallet"]["balance"] == "100.00"
    assert ("webrtcsessions", sid) not in mtx.kicked
    second = await bet("10.00")
    assert second.status_code == 201, second.text
    assert (await viewer.get("/api/v1/portal/betting")).json()["wallet"]["balance"] == "90.00"
    assert ("webrtcsessions", sid) in mtx.kicked


async def test_streaming_reads_existing_wallet_when_betting_is_disabled(tenant, mtx):
    _, event, _, _ = await setup_desk(tenant)
    user = await tenant.create_user("disabled_betting_viewer", "player")
    viewer = await tenant.login("disabled_betting_viewer")
    await viewer.get("/api/v1/portal/betting")
    await set_balance(tenant, user["id"], "100.00")
    assert (await tenant.admin.put("/api/v1/admin/betting/settings", json={"enabled": False})).status_code == 200
    assert (await viewer.get("/api/v1/video/access")).json()["balance"] == "100.00"
    assert (await viewer.post(f"/api/v1/video/events/{event['id']}/playback")).status_code == 200
