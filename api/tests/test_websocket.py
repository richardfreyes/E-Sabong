"""Realtime broadcasting over WebSocket is tenant-scoped."""

import asyncio
import json

import httpx
import pytest
from httpx_ws import WebSocketDisconnect, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport


def ws_client(app, host: str) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=ASGIWebSocketTransport(app=app), base_url=f"http://{host}")


async def _receive(ws, timeout: float = 5.0) -> dict:  # type: ignore[no-untyped-def]
    return json.loads(await asyncio.wait_for(ws.receive_text(), timeout))


async def test_tenant_receives_own_result_broadcast_only(app, tenant, other_tenant) -> None:  # type: ignore[no-untyped-def]
    a = tenant.admin
    event = (await a.post("/api/v1/admin/events", json={"name": "Live", "starts_at": "2030-01-01T10:00:00+00:00"})).json()
    match = (await a.post(f"/api/v1/admin/events/{event['id']}/matches", json={"meron_name": "Red", "wala_name": "Blue"})).json()
    await a.post(f"/api/v1/admin/events/{event['id']}/publish")
    await a.post(f"/api/v1/admin/events/{event['id']}/start")

    token_a = a.headers["Authorization"].split()[1]
    token_b = other_tenant.admin.headers["Authorization"].split()[1]
    async with ws_client(app, tenant.host) as ca, ws_client(app, other_tenant.host) as cb:
        async with aconnect_ws(f"/api/ws?token={token_a}", ca) as ws_a, aconnect_ws(f"/api/ws?token={token_b}", cb) as ws_b:
            assert (await _receive(ws_a))["type"] == "connected"
            assert (await _receive(ws_b))["type"] == "connected"
            await asyncio.sleep(0.2)  # let both subscriptions settle

            await a.post(f"/api/v1/admin/matches/{match['id']}/start")
            started = await _receive(ws_a)
            assert started["type"] == "match.updated" and started["data"]["status"] == "in_progress"
            await a.post(f"/api/v1/admin/matches/{match['id']}/result", json={"outcome": "wala"})
            declared = await _receive(ws_a)
            assert declared["type"] == "match.result_declared"
            assert declared["data"]["id"] == match["id"] and declared["data"]["result"]["outcome"] == "wala"

            await ws_b.send_text(json.dumps({"type": "ping"}))
            assert (await _receive(ws_b))["type"] == "pong"  # nothing from tenant A arrived first


async def test_personal_notifications_reach_only_recipient(app, tenant) -> None:  # type: ignore[no-untyped-def]
    """A Declarator's submission notifies the approvers (the admin) personally; nobody else hears it."""
    from tests.test_declarator import proposal, setup_desk

    _, _, match, judge = await setup_desk(tenant)
    await tenant.create_user("wsbystander", "master_agent")
    bc = await tenant.login("wsbystander")
    token_a = tenant.admin.headers["Authorization"].split()[1]
    token_b = bc.headers["Authorization"].split()[1]
    async with ws_client(app, tenant.host) as c1, ws_client(app, tenant.host) as c2:
        async with aconnect_ws(f"/api/ws?token={token_a}", c1) as ws_a, aconnect_ws(f"/api/ws?token={token_b}", c2) as ws_b:
            await _receive(ws_a)
            await _receive(ws_b)
            await asyncio.sleep(0.2)
            r = await judge.post(f"/api/v1/declarator/matches/{match['id']}/declarations", json=proposal(reason="Official"))
            assert r.status_code == 200, r.text
            seen = []
            for _ in range(6):
                frame = await _receive(ws_a)
                seen.append(frame["type"])
                if frame["type"] == "notification.created":
                    assert frame["data"]["type"] == "result.approval_required"
                    break
            assert "notification.created" in seen, seen
            await ws_b.send_text(json.dumps({"type": "ping"}))
            assert (await _receive(ws_b))["type"] == "pong"


@pytest.mark.parametrize("token", ["", "not-a-jwt"])
async def test_invalid_tokens_are_closed(app, tenant, token) -> None:  # type: ignore[no-untyped-def]
    async with ws_client(app, tenant.host) as c:
        async with aconnect_ws(f"/api/ws?token={token}", c) as ws:
            with pytest.raises(WebSocketDisconnect) as exc:
                await ws.receive_text()
    assert exc.value.code == 4401


async def test_token_from_other_tenant_is_closed(app, tenant, other_tenant) -> None:  # type: ignore[no-untyped-def]
    token = tenant.admin.headers["Authorization"].split()[1]
    async with ws_client(app, other_tenant.host) as c:
        async with aconnect_ws(f"/api/ws?token={token}", c) as ws:
            with pytest.raises(WebSocketDisconnect) as exc:
                await ws.receive_text()
    assert exc.value.code == 4401


async def test_websocket_events_are_logged_without_the_token(app, tenant, other_tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Monitoring counts ws_rejected / ws_connected / ws_closed; the query-string token never reaches a log."""
    from app.modules.realtime import router as realtime

    events: list[tuple[str, dict]] = []

    class Recorder:
        def info(self, event: str, **fields: object) -> None:
            events.append((event, fields))

        warning = info

    monkeypatch.setattr(realtime, "log", Recorder())
    token = tenant.admin.headers["Authorization"].split()[1]
    async with ws_client(app, other_tenant.host) as c:  # foreign-tenant token: rejected
        async with aconnect_ws(f"/api/ws?token={token}", c) as ws:
            with pytest.raises(WebSocketDisconnect):
                await ws.receive_text()
    async with ws_client(app, tenant.host) as c:
        async with aconnect_ws(f"/api/ws?token={token}", c) as ws:
            assert (await _receive(ws))["type"] == "connected"
    await asyncio.sleep(0.1)
    names = [e for e, _ in events]
    assert names[:2] == ["ws_rejected", "ws_connected"] and "ws_closed" in names
    assert events[0][1] == {"code": 4401, "reason": "invalid token", "tenant": other_tenant.slug}
    assert token not in repr(events)
