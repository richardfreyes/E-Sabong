"""Explicit JSON bearer adapter. No captured sessions, signing or media proxying."""

import asyncio
import ipaddress
import json
import socket
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.video.schemas import Playback, ProviderConfig, VideoResult


class ProviderFailure(Exception):
    def __init__(self, status: str, message: str):
        self.status, self.message = status, message


def cipher() -> Fernet:
    key = get_settings().video_credentials_key
    if not key:
        raise AppError("VIDEO_CREDENTIALS_KEY is required to store provider credentials", status_code=503)
    return Fernet(key.get_secret_value().encode())


def encrypt_credentials(username: str, password: str) -> str:
    return cipher().encrypt(json.dumps({"username": username, "password": password}).encode()).decode()


def validate_url(url: str, hosts: list[str]) -> str:
    try:
        p = urlsplit(url)
        if p.scheme != "https" or not p.hostname or p.username or p.password or p.fragment or p.port not in (None, 443):
            raise ValueError
        if p.hostname.lower() not in hosts or "\\" in url:
            raise ValueError
        return p.hostname
    except ValueError:
        raise ProviderFailure("invalid_destination", "Destination must be HTTPS on an operator-approved host") from None


async def public_ip(host: str) -> str:
    try:
        records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM), timeout=5)
    except (OSError, TimeoutError):
        raise ProviderFailure("provider_offline", "External provider offline") from None
    ips = [r[4][0] for r in records]
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ProviderFailure("invalid_destination", "Private and reserved destinations are prohibited")
    return ips[0]


async def request_json(method: str, url: str, *, token: str | None = None, payload: dict | None = None) -> dict:
    host = validate_url(url, get_settings().video_api_hosts)
    ip = await public_ip(host)
    p = urlsplit(url)
    # Pin the validated address through connect; retain original TLS SNI and Host.
    address = f"[{ip}]" if ":" in ip else ip
    pinned = urlunsplit(("https", address, p.path, p.query, ""))
    headers = {"Host": host, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with asyncio.timeout(12):
            async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
                async with client.stream(
                    method, pinned, headers=headers, json=payload, extensions={"sni_hostname": host}
                ) as response:
                    if response.status_code == 401:
                        raise ProviderFailure("auth_expired", "Provider authentication expired")
                    if response.status_code == 403:
                        raise ProviderFailure("authorization_denied", "Playback authorization denied")
                    if response.status_code != 200:
                        raise ProviderFailure("provider_offline", "External provider temporarily unavailable")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 65536:
                            raise ProviderFailure("invalid_response", "Provider response exceeds limit")
                    result = json.loads(data)
                    if not isinstance(result, dict):
                        raise ValueError
                    return result
    except (httpx.HTTPError, OSError, TimeoutError):
        raise ProviderFailure("connection_lost", "Connection lost to external provider") from None
    except (ValueError, TypeError):
        raise ProviderFailure("invalid_response", "Invalid provider response") from None


async def retrieve(config: ProviderConfig, credentials: str | None, action: str) -> VideoResult:
    if not config.enabled:
        return VideoResult(status="disabled", message="Video temporarily unavailable — provider disabled")
    if config.adapter == "demo":
        # The bundled demonstration clip is a development aid. Production never presents it as
        # a stream: an install still set to "demo" reports that no provider is configured.
        if get_settings().is_production:
            return VideoResult(status="disabled", message="Video temporarily unavailable — provider not configured")
        if action == "event":
            return VideoResult(status="demo", message="Licensed demonstration metadata", metadata={"name": "Demonstration"})
        return VideoResult(
            status="demo", message="Licensed demonstration video", playback=Playback(type="mp4", url="/media/demo-loop.mp4")
        )
    try:
        if not credentials:
            raise ProviderFailure("auth_expired", "Provider credentials are required")
        try:
            login = json.loads(cipher().decrypt(credentials.encode()))
        except (InvalidToken, ValueError):
            raise ProviderFailure("auth_expired", "Provider credentials could not be decrypted") from None
        base = config.api_base_url.rstrip("/")
        auth = await request_json("POST", base + config.login_endpoint, payload=login)
        token = auth.get("access_token")
        if not isinstance(token, str) or not token or len(token) > 8192 or any(c.isspace() for c in token):
            raise ProviderFailure("invalid_response", "Provider must return a documented bearer access token")
        endpoint = config.live_event_endpoint if action == "event" else config.playback_endpoint
        query = str(httpx.QueryParams({"game_id": config.game_id, "video_id": config.video_id}))
        data = await request_json("GET", base + endpoint + "?" + query, token=token)
        if action == "event":
            # Explicit projection: never echo cookies, tokens, or official results.
            metadata = {k: data[k] for k in ("name", "fight_number", "status") if k in data and type(data[k]) in (str, int)}
            return VideoResult(status="connected", message="External metadata refreshed", metadata=metadata)
        playback = Playback.model_validate(data)
        validate_url(playback.url, get_settings().video_media_hosts)
        await public_ip(urlsplit(playback.url).hostname)
        if playback.expires_at and (playback.expires_at.tzinfo is None or playback.expires_at <= datetime.now(UTC)):
            raise ProviderFailure("auth_expired", "Provider playback authorization expired")
        return VideoResult(status="authorized", message="Playback authorized; awaiting browser playback", playback=playback)
    except ValidationError:
        return VideoResult(status="invalid_response", message="Unsupported playback response; documented type and URL required")
    except ProviderFailure as exc:
        return VideoResult(status=exc.status, message=exc.message)
