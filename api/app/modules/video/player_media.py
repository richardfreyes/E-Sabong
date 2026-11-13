"""Same-origin player proxy for legacy providers. Upstream URLs never reach player browsers.

MediaMTX keeps its existing transport. Only legacy provider playback uses this path, with
wallet/session checks on every manifest/segment and during long file responses.
"""

import hashlib
import hmac
import json
import re
import time
import uuid
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import httpx
from fastapi import Request, Response
from starlette.responses import StreamingResponse

from app.core.config import get_settings
from app.core.deps import CurrentUser, revoked_family_key
from app.core.errors import Forbidden, NotFound
from app.core.redis import get_redis
from app.modules.users.models import AccountType
from app.modules.video.access import BALANCE_MESSAGE, allowed_viewers
from app.modules.video.schemas import VideoResult
from app.modules.video.service import ProviderFailure, public_ip, validate_url
from app.modules.video.streams import _token_key, sign_token, verify_token

PURPOSE = b"esabong/player-media/v1"
COOKIE = "player_stream_access"
PREFIX = "/api/v1/video/media"
TTL = 86400
MAX_MANIFEST = 2 * 1024 * 1024
# Deterministic media-response transport for integration tests (production uses the network).
test_transport: httpx.AsyncBaseTransport | None = None
TEMPLATE = re.compile(r"\$(?:Number|Time|Bandwidth|RepresentationID)(?:%0\d+d)?\$")
HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


async def resource_url(url: str, claims: dict) -> str:
    validate_url(url, get_settings().video_media_hosts)
    identity = json.dumps({"url": url, "t": claims["t"], "u": claims["u"], "f": claims["f"]}, sort_keys=True)
    ref = hmac.new(_token_key(PURPOSE), identity.encode(), hashlib.sha256).hexdigest()
    slots = list(dict.fromkeys(TEMPLATE.findall(url)))
    await get_redis().set(f"video:media:{ref}", json.dumps({"url": url, "slots": slots, **claims}), ex=TTL)
    return f"{PREFIX}/{ref}/" + "/".join(slots)


async def protect(result: VideoResult, current: CurrentUser, response: Response, *, secure: bool) -> VideoResult:
    if current.user.account_type != AccountType.PLAYER or not result.playback:
        return result
    # The bundled development clip is public demonstration media, not a live stream.
    if result.playback.url == "/media/demo-loop.mp4":
        return result
    claims = {"t": str(current.tenant.id), "u": str(current.id), "f": str(current.session_family)}
    token, _ = sign_token(claims, PURPOSE, TTL)
    response.set_cookie(COOKIE, token, max_age=TTL, path=PREFIX, httponly=True, secure=secure, samesite="strict")
    playback = result.playback.model_copy(update={"url": await resource_url(result.playback.url, claims)})
    return result.model_copy(update={"playback": playback})


async def authorized(claims: dict) -> bool:
    from app.modules.tenants.rental import settings
    config = await settings()
    if config and config.shared_domain_enabled:
        return False  # Old tenant-provider URLs cannot bypass the selected global MediaMTX source.
    tid, uid, family = uuid.UUID(claims["t"]), uuid.UUID(claims["u"]), uuid.UUID(claims["f"])
    return not await get_redis().exists(revoked_family_key(family)) and (tid, uid) in await allowed_viewers({(tid, uid)})


async def rewrite_hls(text: str, url: str, claims: dict) -> str:
    lines = []
    for line in text.splitlines():
        if line and not line.startswith("#"):
            line = await resource_url(urljoin(url, line.strip()), claims)
        elif line.startswith("#"):
            for match in reversed(list(re.finditer(r'URI="([^"]+)"', line))):
                replacement = await resource_url(urljoin(url, match[1]), claims)
                line = line[: match.start(1)] + replacement + line[match.end(1) :]
        lines.append(line)
    return "\n".join(lines) + "\n"


async def rewrite_dash(text: str, url: str, claims: dict) -> bytes:
    # Do not process document types, entities or remote XML includes.
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("Unsupported manifest")
    root = ET.fromstring(text)  # noqa: S314 -- DTD/entities rejected above; bounded document
    if root.tag.rsplit("}", 1)[-1] != "MPD":
        raise ValueError("Expected a DASH manifest")

    async def walk(node: ET.Element, base: str) -> None:
        bases = [child for child in node if child.tag.rsplit("}", 1)[-1] == "BaseURL"]
        effective = urljoin(base, bases[0].text.strip()) if bases and bases[0].text else base
        for child in bases:
            if child.text:
                child.text = await resource_url(urljoin(base, child.text.strip()), claims)
        for attr in ("media", "initialization", "sourceURL", "index"):
            if node.get(attr):
                node.set(attr, await resource_url(urljoin(effective, node.get(attr)), claims))
        # A dynamic MPD may advertise a new manifest location or a remote clock.
        for child in node:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "Location" and child.text:
                child.text = await resource_url(urljoin(effective, child.text.strip()), claims)
            elif tag == "UTCTiming" and child.get("value", "").startswith("https://"):
                child.set("value", await resource_url(child.get("value"), claims))
            elif tag != "BaseURL":
                await walk(child, effective)

    await walk(root, url)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


async def serve(request: Request, tenant_id: uuid.UUID, ref: str, values: str) -> Response:
    claims = verify_token(request.cookies.get(COOKIE), PURPOSE)
    if not claims or claims.get("t") != str(tenant_id) or not await authorized(claims):
        raise Forbidden(BALANCE_MESSAGE, code="stream_access_denied")
    raw = await get_redis().get(f"video:media:{ref}")
    if not raw:
        raise NotFound("Media authorization expired")
    resource = json.loads(raw)
    if any(resource.get(key) != claims.get(key) for key in ("t", "u", "f")):
        raise Forbidden("Media belongs to another viewer")
    parts = values.split("/") if values else []
    if len(parts) != len(resource["slots"]) or any(not re.fullmatch(r"[\w.-]{1,200}", value) for value in parts):
        raise NotFound("Invalid media resource")
    url = resource["url"]
    for slot, value in zip(resource["slots"], parts, strict=True):
        url = url.replace(slot, value)
    client = None
    upstream = None
    try:
        host = validate_url(url, get_settings().video_media_hosts)
        ip = await public_ip(host)
        target = urlsplit(url)
        address = f"[{ip}]" if ":" in ip else ip
        pinned = urlunsplit(("https", address, target.path, target.query, ""))
        headers = {"Host": host, "Accept-Encoding": "identity"}
        if request.headers.get("range") and re.fullmatch(r"bytes=\d*-\d*", request.headers["range"]):
            headers["Range"] = request.headers["range"]
        client = httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False, transport=test_transport)
        upstream = await client.send(
            client.build_request("GET", pinned, headers=headers, extensions={"sni_hostname": host}), stream=True
        )
        if upstream.status_code not in (200, 206):
            raise ProviderFailure("provider_offline", "Media source unavailable")
        content_type = upstream.headers.get("content-type", "application/octet-stream").split(";")[0].lower()
        manifest = target.path.lower().endswith((".m3u8", ".mpd")) or content_type in (
            "application/vnd.apple.mpegurl",
            "application/x-mpegurl",
            "application/dash+xml",
        )
        if manifest:
            body = bytearray()
            async for chunk in upstream.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_MANIFEST:
                    raise ValueError("Manifest too large")
            text = body.decode("utf-8-sig")
            if text.lstrip().startswith("#EXTM3U"):
                body = (await rewrite_hls(text, url, claims)).encode()
                content_type = "application/vnd.apple.mpegurl"
            else:
                body = await rewrite_dash(text, url, claims)
                content_type = "application/dash+xml"
            await upstream.aclose()
            await client.aclose()
            return Response(body, media_type=content_type, headers=HEADERS)
        if not (
            content_type.startswith(("video/", "audio/"))
            or content_type in ("application/octet-stream", "application/mp4", "binary/octet-stream")
        ):
            raise ValueError("Unsupported media content")
    except (ProviderFailure, httpx.HTTPError, ValueError, ET.ParseError):
        if upstream:
            await upstream.aclose()
        if client:
            await client.aclose()
        return Response(status_code=502, headers=HEADERS)

    async def chunks():
        checked = time.monotonic()
        try:
            async for chunk in upstream.aiter_bytes(64 * 1024):
                if time.monotonic() - checked >= 1:
                    if not await authorized(claims):
                        break
                    checked = time.monotonic()
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    passthrough = {k: upstream.headers[k] for k in ("content-range", "accept-ranges") if k in upstream.headers}
    return StreamingResponse(
        chunks(), status_code=upstream.status_code, media_type=content_type, headers={**passthrough, **HEADERS}
    )
