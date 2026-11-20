import uuid
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.core.database import tenant_session
from app.core.redis import get_redis
from app.modules.betting.models import PlayWallet
from app.modules.video import streams
from tests import test_video_streams
from tests.streaming_fixtures import streaming_tenant
from tests.test_declarator import setup_desk
from tests.test_video_streams import URL, _db_stream, _hook, _token

tenant = streaming_tenant


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


async def _bet_cam(tenant, event):
    created = (
        await tenant.admin.post(URL, json={"name": "Bet cam", "source_kind": "obs", "enabled": True, "event_id": event["id"]})
    ).json()
    row = await _db_stream(created["id"])
    mtx_path = f"live/{row.path_id}"
    return row, mtx_path


async def _bettor(tenant, name, funded):
    user = await tenant.create_user(name, "player")
    viewer = await tenant.login(name)
    await viewer.get("/api/v1/portal/betting")
    await set_balance(tenant, user["id"], funded)
    return user, viewer


async def _bet(viewer, fight, amount, side="meron"):
    r = await viewer.post(
        f"/api/v1/portal/matches/{fight['id']}/bets",
        json={"request_id": str(uuid.uuid4()), "side": side, "amount": amount},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.parametrize(
    "balance,stake,allowed",
    [
        ("0", "1000", True),  # A: everything staked
        ("500", "0", True),  # B: balance only
        ("0", "0", False),  # C: nothing
        ("100", "2000", True),  # D
        ("0", "500", True),  # E: balance reached zero by betting
        ("50", "0", False),  # below the minimum, no bet
        ("40", "60", True),  # balance and stakes together reach the minimum
        ("40", "50", False),  # together still below it
    ],
)
async def test_access_counts_balance_plus_open_stakes(tenant, mtx, balance, stake, allowed):
    _, event, fight, judge = await setup_desk(tenant)
    user, viewer = await _bettor(tenant, "stake_viewer", str(Decimal(balance) + Decimal(stake)))
    if Decimal(stake):
        assert (await judge.post(f"/api/v1/declarator/matches/{fight['id']}/betting/open")).status_code == 200
        placed = await _bet(viewer, fight, stake)
        # The stake moved from the balance into the open stakes, in one versioned wallet view.
        assert placed["wallet"]["balance"] == f"{Decimal(balance):.2f}"
        assert placed["wallet"]["open_stakes"] == f"{Decimal(stake):.2f}"
    access = (await viewer.get("/api/v1/video/access")).json()
    assert (access["balance"], access["open_stakes"]) == (f"{Decimal(balance):.2f}", f"{Decimal(stake):.2f}")
    assert (await viewer.get("/api/v1/portal/betting")).json()["wallet"]["open_stakes"] == f"{Decimal(stake):.2f}"
    response = await viewer.post(f"/api/v1/video/events/{event['id']}/playback")
    assert response.status_code == (200 if allowed else 403), response.text
    if not allowed:
        assert response.json()["error"]["code"] == "stream_insufficient_balance"
    me = (tenant.id, uuid.UUID(user["id"]))
    assert await streams.allowed_viewers({me}) == ({me} if allowed else set())


async def test_betting_the_whole_balance_keeps_playback(tenant, mtx):
    _, event, fight, judge = await setup_desk(tenant)
    _, viewer = await _bettor(tenant, "all_in_viewer", "150.00")
    row, path = await _bet_cam(tenant, event)
    mtx.live(row.path_id)
    await get_redis().delete("video:paths")
    token = _token((await viewer.post(f"/api/v1/video/events/{event['id']}/playback")).json()["playback"]["url"])
    sid = mtx.viewer("webrtcsessions", path, token)
    await judge.post(f"/api/v1/declarator/matches/{fight['id']}/betting/open")
    await _bet(viewer, fight, "50.00")
    await _bet(viewer, fight, "100.00")
    assert (await viewer.get("/api/v1/video/access")).json()["balance"] == "0.00"
    await streams.reconcile()
    assert ("webrtcsessions", sid) not in mtx.kicked
    body = {"action": "read", "protocol": "webrtc", "path": path, "query": f"token={token}"}
    assert await _hook(tenant.admin, body) == 200
    assert (await viewer.post(f"/api/v1/video/events/{event['id']}/refresh")).status_code == 200


@pytest.mark.parametrize("outcome,kept", [("wala", False), ("meron", True), ("cancelled", True), ("draw", True)])
async def test_settlement_rechecks_access_at_once(tenant, mtx, monkeypatch, outcome, kept):
    from app.modules.realtime import publisher
    from tests.test_declarator import proposal

    frames = []
    original = publisher.publish

    async def capture(tenant_id, event, data, user_id=None):
        frames.append((event, data, user_id))
        return await original(tenant_id, event, data, user_id)

    monkeypatch.setattr(publisher, "publish", capture)
    _, event, fight, judge = await setup_desk(tenant)
    user, viewer = await _bettor(tenant, "settled_viewer", "100.00")
    _, bystander = await _bettor(tenant, "bystander_viewer", "500.00")
    row, path = await _bet_cam(tenant, event)
    mtx.live(row.path_id)
    await get_redis().delete("video:paths")
    play = f"/api/v1/video/events/{event['id']}/playback"
    token = _token((await viewer.post(play)).json()["playback"]["url"])
    other = _token((await bystander.post(play)).json()["playback"]["url"])
    sid = mtx.viewer("webrtcsessions", path, token)
    other_sid = mtx.viewer("hlssessions", path, other)
    await judge.post(f"/api/v1/declarator/matches/{fight['id']}/betting/open")
    await _bet(viewer, fight, "100.00", "meron")
    await _bet(bystander, fight, "100.00", "wala")
    frames.clear()
    r = await judge.post(f"/api/v1/declarator/matches/{fight['id']}/declarations", json=proposal(outcome, reason="Official"))
    assert r.status_code == 200, r.text
    mine = [d for e, d, u in frames if e == "wallet.updated" and str(u) == user["id"]]
    # The last frame of the settlement carries the remaining open stakes with the final balance.
    assert mine and mine[-1]["open_stakes"] == "0.00" and mine[-1]["reason"] == "settlement"
    assert [d["version"] for d in mine] == sorted(d["version"] for d in mine)
    access = (await viewer.get("/api/v1/video/access")).json()
    assert access["open_stakes"] == "0.00"
    assert (("webrtcsessions", sid) in mtx.kicked) is not kept
    assert ("hlssessions", other_sid) not in mtx.kicked
    assert (await viewer.post(play)).status_code == (200 if kept else 403)


async def test_open_stakes_never_cross_tenants(tenant, other_tenant, mtx):
    _, event, fight, judge = await setup_desk(tenant)
    user, viewer = await _bettor(tenant, "home_viewer", "500.00")
    await judge.post(f"/api/v1/declarator/matches/{fight['id']}/betting/open")
    await _bet(viewer, fight, "500.00")
    me = (tenant.id, uuid.UUID(user["id"]))
    assert await streams.allowed_viewers({me}) == {me}
    # Signed claims naming another tenant never match this player's wallet or bets.
    assert await streams.allowed_viewers({(other_tenant.id, me[1])}) == set()


async def test_streaming_reads_existing_wallet_when_betting_is_disabled(tenant, mtx):
    _, event, _, _ = await setup_desk(tenant)
    user = await tenant.create_user("disabled_betting_viewer", "player")
    viewer = await tenant.login("disabled_betting_viewer")
    await viewer.get("/api/v1/portal/betting")
    await set_balance(tenant, user["id"], "100.00")
    assert (await tenant.admin.put("/api/v1/admin/betting/settings", json={"enabled": False})).status_code == 200
    assert (await viewer.get("/api/v1/video/access")).json()["balance"] == "100.00"
    assert (await viewer.post(f"/api/v1/video/events/{event['id']}/playback")).status_code == 200
