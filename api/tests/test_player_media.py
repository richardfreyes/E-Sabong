import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.modules.video import player_media, router
from app.modules.video.schemas import Playback, VideoResult
from tests.streaming_fixtures import streaming_tenant
from tests.test_betting_staff import configure
from tests.test_declarator import setup_desk
from tests.test_video_balance import set_balance

tenant = streaming_tenant


@pytest.fixture
async def provider(tenant, monkeypatch):
    monkeypatch.setenv("VIDEO_MEDIA_HOSTS", '["media.authorized.example"]')
    get_settings.cache_clear()
    await configure(tenant, starting_balance="1000.00")
    _, event, _, _ = await setup_desk(tenant)
    await tenant.admin.put("/api/v1/admin/settings/video-provider", json={"enabled": True, "event_id": event["id"]})
    requests = []

    async def retrieve(config, credentials, action):
        return VideoResult(
            status="authorized",
            message="Playback authorized",
            playback=Playback(type="hls", url="https://media.authorized.example/private/index.m3u8?secret=provider-secret"),
        )

    async def ip(host):
        return "203.0.113.10"

    async def media(request):
        requests.append(request)
        if request.url.path.endswith("index.m3u8"):
            return httpx.Response(
                200,
                text='#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:1,\nsegment.ts\n',
                headers={"content-type": "application/vnd.apple.mpegurl"},
            )
        return httpx.Response(206, content=b"segment", headers={"content-type": "video/mp2t", "content-range": "bytes 0-6/7"})

    monkeypatch.setattr(router, "retrieve", retrieve)
    monkeypatch.setattr(player_media, "public_ip", ip)
    monkeypatch.setattr(player_media, "test_transport", httpx.MockTransport(media))
    user = await tenant.create_user("proxy_viewer", "player")
    viewer = await tenant.login("proxy_viewer")
    await viewer.get("/api/v1/portal/betting")
    yield event, user, viewer, requests
    get_settings.cache_clear()


async def legacy_grant(tenant, user, viewer):
    from app.core.security import decode_access_token
    from app.modules.video.streams import sign_token

    token = viewer.headers["Authorization"].split()[1]
    session = decode_access_token(token, "tenant")
    claims = {"t": str(tenant.id), "u": str(user["id"]), "f": str(session.session_family)}
    url = await player_media.resource_url("https://media.authorized.example/private/index.m3u8?secret=provider-secret", claims)
    cookie, _ = sign_token(claims, player_media.PURPOSE, player_media.TTL)
    viewer.cookies.set(player_media.COOKIE, cookie, path=player_media.PREFIX)
    return url


async def test_legacy_provider_cannot_bypass_global_source_with_a_valid_grant(tenant, other_tenant, provider):
    event, user, viewer, requests = provider
    response = await viewer.post(f"/api/v1/video/events/{event['id']}/playback")
    assert response.status_code == 200, response.text
    assert response.json()["playback"] is None
    assert "provider-secret" not in response.text and "media.authorized.example" not in response.text
    # Even an old correctly signed grant, healthy wallet and allowed upstream host must not bypass policy.
    url = await legacy_grant(tenant, user, viewer)
    assert (await viewer.get(url)).status_code == 403
    await tenant.create_user("proxy_other", "player")
    other = await tenant.login("proxy_other")
    assert (await other.get(url)).status_code == 403
    assert (await other_tenant.admin.get(url)).status_code == 403
    await set_balance(tenant, user["id"], "99.99")
    assert (await viewer.get(url)).status_code == 403
    await set_balance(tenant, user["id"], "100.00")
    assert (await viewer.get(url)).status_code == 403
    assert requests == []


async def test_legacy_media_is_denied_before_redirects_or_html_can_be_retrieved(tenant, provider, monkeypatch):
    _, user, viewer, requests = provider
    url = await legacy_grant(tenant, user, viewer)
    for status, headers in [(302, {"location": "http://127.0.0.1/private"}), (200, {"content-type": "text/html"})]:

        def attempted(request, status=status, headers=headers):
            requests.append(request)
            return httpx.Response(status, content=b"<html>not media</html>", headers=headers)

        monkeypatch.setattr(player_media, "test_transport", httpx.MockTransport(attempted))
        assert (await viewer.get(url)).status_code == 403
    assert requests == []


async def test_dash_rewrites_bases_templates_and_rejects_entity_documents(monkeypatch):
    monkeypatch.setenv("VIDEO_MEDIA_HOSTS", '["media.authorized.example"]')
    get_settings.cache_clear()
    claims = {key: str(uuid.uuid4()) for key in ("t", "u", "f")}
    xml = (
        "<MPD><BaseURL>https://media.authorized.example/private/</BaseURL><Period><AdaptationSet>"
        '<SegmentTemplate media="chunk-$Number%05d$.m4s" initialization="init.mp4"/>'
        "</AdaptationSet></Period></MPD>"
    )
    try:
        rewritten = (await player_media.rewrite_dash(xml, "https://media.authorized.example/index.mpd", claims)).decode()
        assert "media.authorized.example" not in rewritten and "$Number%05d$" in rewritten
        assert "/api/v1/video/media/" in rewritten
        with pytest.raises(ValueError):
            await player_media.rewrite_dash(
                '<!DOCTYPE MPD [<!ENTITY x "bad">]><MPD/>', "https://media.authorized.example/a.mpd", claims
            )
    finally:
        get_settings.cache_clear()
