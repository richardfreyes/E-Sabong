import socket
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import get_settings
from app.modules.video import service
from app.modules.video.schemas import ProviderConfig


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("VIDEO_CREDENTIALS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("VIDEO_API_HOSTS", '["provider.example"]')
    monkeypatch.setenv("VIDEO_MEDIA_HOSTS", '["media.example"]')
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "url",
    [
        "http://provider.example",
        "https://localhost",
        "https://provider.example:444",
        "https://user:pass@provider.example",
        "https://provider.example.evil.test",
        "https://127.0.0.1",
        "https://provider.example/#secret",
    ],
)
def test_ssrf_destinations(url):
    with pytest.raises(service.ProviderFailure):
        service.validate_url(url, ["provider.example"])


@pytest.mark.parametrize("path", ["//evil.test/x", "https://evil.test", "/x?secret=1", "/x#frag", "/x\\y"])
def test_paths_cannot_override_origin(path):
    with pytest.raises(ValidationError):
        ProviderConfig(playback_endpoint=path)


async def test_dns_private_or_mixed_rejected(monkeypatch):
    loop = service.asyncio.get_running_loop()
    monkeypatch.setattr(
        loop,
        "getaddrinfo",
        AsyncMock(
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            ]
        ),
    )
    with pytest.raises(service.ProviderFailure):
        await service.public_ip("provider.example")


async def test_encryption_projection_and_expiry(settings, monkeypatch):
    credentials = service.encrypt_credentials("private-user", "private-password")
    assert "private-user" not in credentials and "private-password" not in credentials
    config = ProviderConfig(enabled=True, adapter="json-bearer-v1", api_base_url="https://provider.example")
    upstream = AsyncMock(
        side_effect=[
            {"access_token": "private-token"},
            {
                "type": "dash",
                "url": "https://media.example/manifest.mpd",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            },
        ]
    )
    monkeypatch.setattr(service, "request_json", upstream)
    monkeypatch.setattr(service, "public_ip", AsyncMock(return_value="8.8.8.8"))
    result = await service.retrieve(config, credentials, "playback")
    assert result.status == "authorized" and result.playback.type == "dash"
    assert "private-" not in result.model_dump_json()
    upstream.side_effect = [
        {"access_token": "private-token"},
        {"type": "mp4", "url": "https://media.example/video.mp4", "expires_at": "2020-01-01T00:00:00Z"},
    ]
    assert (await service.retrieve(config, credentials, "reconnect")).status == "auth_expired"


async def test_failure_and_unsupported_do_not_retry(settings, monkeypatch):
    credentials = service.encrypt_credentials("u", "p")
    config = ProviderConfig(enabled=True, adapter="json-bearer-v1", api_base_url="https://provider.example")
    upstream = AsyncMock(side_effect=service.ProviderFailure("authorization_denied", "Playback authorization denied"))
    monkeypatch.setattr(service, "request_json", upstream)
    result = await service.retrieve(config, credentials, "playback")
    assert result.status == "authorization_denied" and not result.playback
    assert upstream.await_count == 1
    upstream.side_effect = [{"access_token": "token"}, {"type": "webrtc", "url": "https://media.example/x"}]
    assert (await service.retrieve(config, credentials, "playback")).status == "invalid_response"


async def test_demo_disabled_and_metadata_projection(settings, monkeypatch):
    assert not (await service.retrieve(ProviderConfig(), None, "playback")).playback
    demo = await service.retrieve(ProviderConfig(enabled=True), None, "playback")
    assert demo.status == "demo" and demo.playback.type == "mp4"
    upstream = AsyncMock(
        side_effect=[
            {"access_token": "token"},
            {"name": "External event", "fight_number": 4, "password": "hidden", "results": ["meron"]},
        ]
    )
    monkeypatch.setattr(service, "request_json", upstream)
    result = await service.retrieve(
        ProviderConfig(enabled=True, adapter="json-bearer-v1"), service.encrypt_credentials("u", "p"), "event"
    )
    assert result.metadata == {"name": "External event", "fight_number": 4}


async def test_http_boundary_pins_dns_and_normalizes_status(settings, monkeypatch):
    import httpx

    original_client = httpx.AsyncClient
    calls = []

    def response(request):
        calls.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "provider.example"
        assert request.extensions["sni_hostname"] == "provider.example"
        return httpx.Response(401, json={"private_details": "must not escape"})

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return original_client(**kwargs, transport=httpx.MockTransport(response))

    monkeypatch.setattr(service.httpx, "AsyncClient", client)
    monkeypatch.setattr(service, "public_ip", AsyncMock(return_value="8.8.8.8"))
    with pytest.raises(service.ProviderFailure) as failure:
        await service.request_json("POST", "https://provider.example/api/login", payload={"username": "u", "password": "p"})
    assert failure.value.status == "auth_expired"
    assert "private_details" not in failure.value.message
    assert len(calls) == 1


async def test_demo_clip_never_plays_in_production(settings, monkeypatch):
    monkeypatch.setattr(type(settings), "is_production", property(lambda _: True))
    for action in ("playback", "event", "refresh"):
        result = await service.retrieve(ProviderConfig(enabled=True), None, action)
        assert result.status == "disabled" and result.playback is None and not result.metadata
