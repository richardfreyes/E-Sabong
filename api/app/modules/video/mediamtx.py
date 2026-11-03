"""Minimal client for the MediaMTX v3 control API (internal network only).

Every call is bounded by a short timeout so a stalled media server can never hold an API
request. Path names contain slashes (``live/<id>/rtc``); the v3 API accepts them as-is.
"""

from typing import Any

import httpx

from app.core.config import get_settings

SESSION_KINDS = ("webrtcsessions", "hlssessions", "rtmpconns")


class MediaMTXUnavailable(Exception):
    pass


# Tests replace the network with an in-memory MediaMTX; production leaves this None.
test_transport: httpx.AsyncBaseTransport | None = None


class MediaMTX:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        s = get_settings()
        if s.mediamtx_api_password is None:
            raise MediaMTXUnavailable("MediaMTX is not configured")
        self._client = httpx.AsyncClient(
            base_url=s.mediamtx_api_url.rstrip("/"),
            auth=(s.mediamtx_api_user, s.mediamtx_api_password.get_secret_value()),
            timeout=httpx.Timeout(3.0),
            trust_env=False,
            follow_redirects=False,
            transport=transport or test_transport,
        )

    async def __aenter__(self) -> "MediaMTX":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, json: dict[str, Any] | None = None) -> httpx.Response:
        try:
            response = await self._client.request(method, url, json=json)
        except httpx.HTTPError as exc:
            raise MediaMTXUnavailable(f"MediaMTX unreachable: {type(exc).__name__}") from None
        if response.status_code in (401, 403):
            raise MediaMTXUnavailable("MediaMTX refused the API credentials")
        return response

    async def _items(self, url: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 0
        while True:
            response = await self._request("GET", f"{url}?itemsPerPage=500&page={page}")
            if response.status_code != 200:
                raise MediaMTXUnavailable(f"MediaMTX {url} returned {response.status_code}")
            data = response.json()
            items.extend(data.get("items") or [])
            page += 1
            if page >= int(data.get("pageCount") or 1):
                return items

    async def paths(self) -> dict[str, dict[str, Any]]:
        return {item["name"]: item for item in await self._items("/v3/paths/list")}

    async def path(self, name: str) -> dict[str, Any] | None:
        response = await self._request("GET", f"/v3/paths/get/{name}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise MediaMTXUnavailable(f"MediaMTX path lookup returned {response.status_code}")
        return response.json()

    async def configured_paths(self) -> dict[str, dict[str, Any]]:
        return {item["name"]: item for item in await self._items("/v3/config/paths/list")}

    async def set_path(self, name: str, config: dict[str, Any]) -> None:
        """Create or replace a dynamic path configuration (kept in memory by MediaMTX)."""
        response = await self._request("POST", f"/v3/config/paths/replace/{name}", json=config)
        if response.status_code == 404:
            response = await self._request("POST", f"/v3/config/paths/add/{name}", json=config)
        if response.status_code != 200:
            raise MediaMTXUnavailable(f"MediaMTX rejected the path configuration ({response.status_code})")

    async def delete_path(self, name: str) -> None:
        response = await self._request("DELETE", f"/v3/config/paths/delete/{name}")
        if response.status_code not in (200, 404):
            raise MediaMTXUnavailable(f"MediaMTX path delete returned {response.status_code}")

    async def sessions(self, kind: str) -> list[dict[str, Any]]:
        return await self._items(f"/v3/{kind}/list")

    async def kick(self, kind: str, session_id: str) -> None:
        response = await self._request("POST", f"/v3/{kind}/kick/{session_id}")
        if response.status_code not in (200, 404):
            raise MediaMTXUnavailable(f"MediaMTX kick returned {response.status_code}")
