import re
import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.modules.video import player_media, router
from app.modules.video.schemas import Playback, VideoResult
from tests.test_betting_staff import configure
from tests.test_declarator import setup_desk
from tests.test_video_balance import set_balance


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


async def test_provider_media_hides_sources_and_checks_each_resource(tenant, other_tenant, provider):
    event, user, viewer, requests = provider
    result = await viewer.post(f"/api/v1/video/events/{event['id']}/playback")
    assert result.status_code == 200, result.text
    assert "provider-secret" not in result.text and "media.authorized.example" not in result.text
    url = result.json()["playback"]["url"]
    assert "HttpOnly" in result.headers["set-cookie"]
    manifest = await viewer.get(url)
    assert manifest.status_code == 200, manifest.text
    assert "secret" not in manifest.text and "media.authorized.example" not in manifest.text
    links = re.findall(r"/api/v1/video/media/[a-f0-9]+/", manifest.text)
    assert len(links) == 2
    segment = await viewer.get(links[-1], headers={"Range": "bytes=0-6"})
    assert segment.status_code == 206 and segment.content == b"segment"
    assert requests[-1].headers["host"] == "media.authorized.example"
    assert requests[-1].headers["range"] == "bytes=0-6"
    assert "no-store" in segment.headers["cache-control"]
    # A copied URL alone is not a viewing grant, even for another player of the same tenant.
    await tenant.create_user("proxy_other", "player")
    other = await tenant.login("proxy_other")
    assert (await other.get(url)).status_code == 403
    assert (await other_tenant.admin.get(url)).status_code == 403
    await set_balance(tenant, user["id"], "99.99")
    before = len(requests)
    assert (await viewer.get(url)).status_code == 403
    assert (await viewer.get(links[-1])).status_code == 403
    assert len(requests) == before  # no upstream connection while denied
    await set_balance(tenant, user["id"], "100.00")
    assert (await viewer.get(url)).status_code == 200
    assert (await viewer.post("/api/v1/auth/logout")).status_code == 200
    assert (await viewer.get(url)).status_code == 403


async def test_provider_redirects_and_html_fail_closed(tenant, provider, monkeypatch):
    event, _, viewer, _ = provider
    url = (await viewer.post(f"/api/v1/video/events/{event['id']}/playback")).json()["playback"]["url"]
    for status, headers in [(302, {"location": "http://127.0.0.1/private"}), (200, {"content-type": "text/html"})]:
        monkeypatch.setattr(
            player_media,
            "test_transport",
            httpx.MockTransport(
                lambda request, status=status, headers=headers: httpx.Response(
                    status, content=b"<html>not media</html>", headers=headers
                )
            ),
        )
        assert (await viewer.get(url)).status_code == 502


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
