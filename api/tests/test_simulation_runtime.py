"""Real PostgreSQL/Redis engine tests: neutral outcomes, closing races, rooms and receipts."""

import asyncio
import json
import time
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from httpx_ws import WebSocketDisconnect, aconnect_ws
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.database import tenant_session
from app.modules.simulations.models import SimulationMatch, SimulationSettlement, SimulationStake
from tests.test_websocket import _receive, ws_client

BASE = "/api/v1/simulations"
D = Decimal


async def setup(tenant):  # type: ignore[no-untyped-def]
    a = tenant.admin
    arena = (await a.post(f"{BASE}/arenas", json={"name": "Tactical Simulation"})).json()
    r = await a.patch(f"{BASE}/arenas/{arena['id']}", json={"enabled": True})
    assert r.status_code == 200, r.text
    await a.put("/api/v1/admin/betting/settings", json={"enabled": True, "starting_balance": "100.00"})
    players = []
    for name in ("arcade-ana", "arcade-ben"):
        await tenant.create_user(name, "player")
        c = await tenant.login(name)
        assert (await c.get("/api/v1/portal/betting")).status_code == 200
        players.append(c)
    match = (
        await a.post(
            f"{BASE}/arenas/{arena['id']}/matches",
            json={
                "number": 1,
                "team_a": "Red Team",
                "team_b": "Blue Team",
                "simulation_fee_rate": "0.05",
            },
        )
    ).json()
    assert "id" in match, match
    return arena, match, *players


async def step(client, match, target, **extra):  # type: ignore[no-untyped-def]
    r = await client.post(
        f"{BASE}/matches/{match['id']}/transition",
        json={
            "request_id": str(uuid.uuid4()),
            "expected_version": match["version"],
            "new_state": target,
            **extra,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


async def place(client, match, side, amount="10.00", key=None):  # type: ignore[no-untyped-def]
    return await client.post(
        f"{BASE}/matches/{match['id']}/wagers",
        json={
            "request_id": key or str(uuid.uuid4()),
            "side": side,
            "amount": amount,
        },
    )


async def current(client, match):  # type: ignore[no-untyped-def]
    r = await client.get(f"{BASE}/matches/{match['id']}")
    assert r.status_code == 200, r.text
    return r.json()


async def finish(client, match, outcome):  # type: ignore[no-untyped-def]
    match = await current(client, match)
    for target in ("WAGERING_CLOSED", "SIMULATION_RUNNING"):
        match = (await step(client, match, target))["match"]
    match = (await step(client, match, "RESULT_DECLARED", outcome=outcome))["match"]
    return await step(client, match, "SETTLED")


async def balance(client):  # type: ignore[no-untyped-def]
    return D((await client.get("/api/v1/portal/betting")).json()["wallet"]["balance"])


@pytest.mark.parametrize(
    "outcome,ana,ben,kind",
    [
        ("SIDE_A", "109.00", "90.00", "SETTLEMENT_WIN"),
        ("SIDE_B", "90.00", "109.00", "SETTLEMENT_LOSS"),
        ("DRAW", "100.00", "100.00", "SETTLEMENT_REFUND"),
        ("CANCELLED", "100.00", "100.00", "SETTLEMENT_REFUND"),
    ],
)
async def test_neutral_settlement_conserves_points_and_replays(tenant, outcome, ana, ben, kind):  # type: ignore[no-untyped-def]
    arena, match, a, b = await setup(tenant)
    match = (await step(tenant.admin, match, "WAGERING_OPEN"))["match"]
    assert (await place(a, match, "SIDE_A")).status_code == 201
    assert (await place(b, match, "SIDE_B")).status_code == 201
    report = (await tenant.admin.get("/api/v1/admin/betting/reconciliation")).json()
    assert report["at_stake"] == "20.00" and report["house_result"] == "0.00" and report["balanced"]
    result = await finish(tenant.admin, match, outcome)
    receipt = result["settlement"]
    assert result["match"]["state"] == "settled"
    assert await balance(a) == D(ana) and await balance(b) == D(ben)
    assert D(receipt["total_staked"]) == D(receipt["fee_amount"]) + D(receipt["distributed"])
    assert D(receipt["fee_amount"]) == (D("0.00") if outcome in ("DRAW", "CANCELLED") else D("1.00"))
    own = (await a.get(f"{BASE}/matches/{match['id']}/receipts", params={"player_id": "somebody-else"})).json()
    assert len(own) == 1 and own[0]["kind"] == kind
    replies = await asyncio.gather(*(step(tenant.admin, result["match"], "SETTLED") for _ in range(6)))
    assert all(r["replayed"] and r["settlement"]["id"] == receipt["id"] for r in replies)
    assert await balance(a) == D(ana) and await balance(b) == D(ben)
    report = (await tenant.admin.get("/api/v1/admin/betting/reconciliation")).json()
    assert report["at_stake"] == "0.00" and report["balanced"]
    assert D(report["house_result"]) == D(receipt["fee_amount"])
    async with tenant_session(tenant.id) as db:
        assert await db.scalar(select(func.count()).select_from(SimulationSettlement)) == 1
        for sql in ("UPDATE simulation_settlements SET fee_amount = 0", "DELETE FROM simulation_settlements"):
            with pytest.raises(DBAPIError):
                async with tenant_session(tenant.id) as blocked:
                    await blocked.execute(text(sql))


async def test_transitions_rbac_versions_and_payload_replay(tenant, other_tenant):  # type: ignore[no-untyped-def]
    arena, match, player, _ = await setup(tenant)
    await tenant.create_user("arcade-judge", "declarator")
    judge = await tenant.login("arcade-judge")
    payload = {"request_id": str(uuid.uuid4()), "expected_version": 0, "new_state": "WAGERING_OPEN"}
    path = f"{BASE}/matches/{match['id']}/transition"
    assert (await player.post(path, json=payload)).status_code == 403
    assert (await judge.post(path, json=payload)).status_code == 404
    assert (await other_tenant.admin.post(path, json=payload)).status_code == 404
    await tenant.admin.put(f"{BASE}/arenas/{arena['id']}/controllers/{(await judge.get('/api/v1/auth/me')).json()['id']}")
    r = await judge.post(path, json=payload)
    assert r.status_code == 200, r.text
    replay = await judge.post(path, json=payload)
    assert replay.json()["replayed"] is True
    assert (await judge.post(path, json={**payload, "new_state": "FINAL_CALL"})).status_code == 409
    match = r.json()["match"]
    r = await judge.post(path, json={**payload, "request_id": str(uuid.uuid4()), "new_state": "FINAL_CALL"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_match"
    assert (
        await judge.post(
            path, json={**payload, "request_id": str(uuid.uuid4()), "expected_version": match["version"], "new_state": "SETTLED"}
        )
    ).status_code == 409
    match = (await step(judge, match, "FINAL_CALL", final_call_seconds=5))["match"]
    assert match["final_call_ends_at"] is not None
    assert (await place(player, match, "SIDE_A")).status_code == 400
    match = (await step(judge, match, "WAGERING_OPEN"))["match"]
    assert match["final_call_ends_at"] is None
    key = str(uuid.uuid4())
    first, second = await asyncio.gather(place(player, match, "SIDE_A", key=key), place(player, match, "SIDE_A", key=key))
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert sorted([first.json()["replayed"], second.json()["replayed"]]) == [False, True]
    assert (await place(player, match, "SIDE_B", key=key)).status_code == 409
    match = (await step(judge, await current(judge, match), "WAGERING_CLOSED"))["match"]
    replay = await place(player, match, "SIDE_A", key=key)
    assert replay.status_code == 201 and replay.json()["replayed"]
    assert (await place(player, match, "SIDE_A")).status_code == 400


async def test_concurrent_stakes_and_closing_never_overspend_or_accept_late_wagers(tenant):  # type: ignore[no-untyped-def]
    _, match, player, _ = await setup(tenant)
    match = (await step(tenant.admin, match, "WAGERING_OPEN"))["match"]
    replies = await asyncio.gather(*(place(player, match, "SIDE_A", "30.00") for _ in range(12)))
    assert [r.status_code for r in replies].count(201) == 3
    assert all(r.status_code in (201, 422) for r in replies), [r.text for r in replies]
    assert await balance(player) == D("10.00")
    snapshot = await current(tenant.admin, match)
    # Race one close against several new stakes. Stale versions may reject close; retry
    # that exact semantic action with the newly observed version, then prove the boundary.
    close = {"request_id": str(uuid.uuid4()), "expected_version": snapshot["version"], "new_state": "WAGERING_CLOSED"}
    outcomes = await asyncio.gather(
        tenant.admin.post(f"{BASE}/matches/{match['id']}/transition", json=close),
        *(place(player, match, "SIDE_A", "1.00") for _ in range(6)),
    )
    if outcomes[0].status_code == 409:
        await step(tenant.admin, await current(tenant.admin, match), "WAGERING_CLOSED")
    else:
        assert outcomes[0].status_code == 200, outcomes[0].text
    assert all(r.status_code in (201, 400) for r in outcomes[1:]), [r.text for r in outcomes]
    assert (await place(player, match, "SIDE_A", "1.00")).status_code == 400
    view = await current(tenant.admin, match)
    async with tenant_session(tenant.id) as db:
        total = await db.scalar(
            select(func.sum(SimulationStake.amount)).where(SimulationStake.match_id == uuid.UUID(match["id"]))
        )
        assert total == D(view["team_a_pool"])
        assert await balance(player) + total == D("100.00")
    # The same state gate exists in PostgreSQL even for a direct application insert.
    with pytest.raises(DBAPIError, match="wagering is closed"):
        async with tenant_session(tenant.id) as db:
            uid = uuid.UUID((await player.get("/api/v1/auth/me")).json()["id"])
            db.add(
                SimulationStake(
                    tenant_id=tenant.id,
                    match_id=uuid.UUID(match["id"]),
                    player_id=uid,
                    request_id=uuid.uuid4(),
                    side="team_a",
                    amount=D("0.01"),
                )
            )
            await db.flush()


async def test_rollback_discards_state_ledger_and_broadcasts(tenant, monkeypatch):  # type: ignore[no-untyped-def]
    from app.modules.simulations import service

    _, match, player, _ = await setup(tenant)
    match = (await step(tenant.admin, match, "WAGERING_OPEN"))["match"]
    original = service._post

    async def fail_after_post(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("Injected transaction failure")

    monkeypatch.setattr(service, "_post", fail_after_post)
    # A real ASGI request re-raises the injected exception, after rollback.
    with pytest.raises(RuntimeError, match="Injected transaction failure"):
        await place(player, match, "SIDE_A")
    assert await balance(player) == D("100.00")
    assert (await current(tenant.admin, match))["team_a_pool"] == "0.00"
    async with tenant_session(tenant.id) as db:
        assert await db.scalar(select(func.count()).select_from(SimulationStake)) == 0


async def event(ws, kind):  # type: ignore[no-untyped-def]
    for _ in range(20):
        frame = await _receive(ws, 3)
        if frame["type"] == kind:
            return frame
    raise AssertionError(f"Missing {kind}")


async def test_websocket_rooms_odds_countdown_and_isolation(tenant, other_tenant, app):  # type: ignore[no-untyped-def]
    arena, match, player, _ = await setup(tenant)
    other_arena = (await tenant.admin.post(f"{BASE}/arenas", json={"name": "Other Simulation"})).json()
    await tenant.admin.patch(f"{BASE}/arenas/{other_arena['id']}", json={"enabled": True})
    token = player.headers["Authorization"].split()[1]
    async with ws_client(app, tenant.host) as c1, ws_client(app, tenant.host) as c2:
        async with (
            aconnect_ws(f"/api/ws/simulations?token={token}", c1) as ws,
            aconnect_ws(f"/api/ws/simulations?token={token}", c2) as other,
        ):
            assert (await _receive(ws))["type"] == "CONNECTED"
            await _receive(other)
            await ws.send_json({"type": "SUBSCRIBE", "arenaId": arena["id"]})
            joined = await event(ws, "SUBSCRIBED")
            assert joined["room"] == f"tenant:{tenant.id}:arena:{arena['id']}"
            await other.send_json({"type": "SUBSCRIBE", "arenaId": other_arena["id"]})
            await event(other, "SUBSCRIBED")
            start = time.monotonic()
            match = (await step(tenant.admin, match, "WAGERING_OPEN"))["match"]
            changed = await event(ws, "MATCH_STATE_CHANGED")
            assert changed["data"]["newState"] == "WAGERING_OPEN"
            assert time.monotonic() - start < 1.0
            assert (await place(player, match, "SIDE_A")).status_code == 201
            odds = await event(ws, "ODDS_UPDATED")
            assert odds["data"]["totalSideA"] == "10.00" and "player_id" not in json.dumps(odds)
            await other.send_json({"type": "PING"})
            assert (await _receive(other))["type"] == "PONG"
            await ws.send_json({"type": "SUBSCRIBE", "arenaId": arena["id"], "tenantId": str(other_tenant.id)})
            assert (await event(ws, "ERROR"))["code"] == "forbidden"
            match = (await step(tenant.admin, await current(tenant.admin, match), "FINAL_CALL", final_call_seconds=2))["match"]
            tick = await event(ws, "COUNTDOWN_TICK")
            assert 0 <= tick["data"]["remainingSeconds"] <= 2
            assert datetime.fromisoformat(tick["data"]["endsAt"]) == datetime.fromisoformat(match["final_call_ends_at"])
    # A valid tenant A token cannot open a socket on tenant B's host.
    async with ws_client(app, other_tenant.host) as c:
        async with aconnect_ws(f"/api/ws/simulations?token={token}", c) as ws:
            with pytest.raises(WebSocketDisconnect) as exc:
                await ws.receive_text()
            assert exc.value.code == 4401


async def test_close_in_another_worker_forces_serialization_retry_then_rejects(tenant, monkeypatch):  # type: ignore[no-untyped-def]
    from app.modules.simulations import service

    _, match, player, _ = await setup(tenant)
    match = (await step(tenant.admin, match, "WAGERING_OPEN"))["match"]
    snapshot_started, proceed = asyncio.Event(), asyncio.Event()
    original, calls = service.actor_now, 0

    async def checked(db, actor):
        nonlocal calls
        value = await original(db, actor)
        calls += 1
        snapshot_started.set()
        await proceed.wait()
        return value

    monkeypatch.setattr(service, "actor_now", checked)
    # This connection deliberately bypasses the process mutex, like another API replica.
    async with tenant_session(tenant.id) as closing:
        row = await closing.scalar(select(SimulationMatch).where(SimulationMatch.id == uuid.UUID(match["id"])).with_for_update())
        row.state = "wagering_closed"
        row.version += 1
        await closing.flush()
        pending = asyncio.create_task(place(player, match, "SIDE_A"))
        await asyncio.wait_for(snapshot_started.wait(), 3)
    proceed.set()
    r = await pending
    assert r.status_code == 400 and r.json()["error"]["code"] == "wagering_closed"
    assert calls >= 2  # The stale serializable snapshot was discarded and retried.
    assert await balance(player) == D("100.00")


async def test_settlement_failure_rolls_back_all_wallets_and_receipts(tenant, monkeypatch):  # type: ignore[no-untyped-def]
    from app.modules.simulations import service

    _, match, a, b = await setup(tenant)
    match = (await step(tenant.admin, match, "WAGERING_OPEN"))["match"]
    for client, side in ((a, "SIDE_A"), (a, "SIDE_A"), (b, "SIDE_B")):
        assert (await place(client, match, side)).status_code == 201
    match = await current(tenant.admin, match)
    for target in ("WAGERING_CLOSED", "SIMULATION_RUNNING"):
        match = (await step(tenant.admin, match, target))["match"]
    match = (await step(tenant.admin, match, "RESULT_DECLARED", outcome="SIDE_A"))["match"]
    before = await balance(a), await balance(b)
    key = str(uuid.uuid4())
    payload = {"request_id": key, "expected_version": match["version"], "new_state": "SETTLED"}
    original, calls = service._post, 0

    async def partial(*args, **kwargs):
        nonlocal calls
        result = await original(*args, **kwargs)
        calls += 1
        if calls == 2:
            raise RuntimeError("Settlement interrupted")
        return result

    monkeypatch.setattr(service, "_post", partial)
    with pytest.raises(RuntimeError, match="Settlement interrupted"):
        await tenant.admin.post(f"{BASE}/matches/{match['id']}/transition", json=payload)
    assert (await balance(a), await balance(b)) == before
    assert (await current(tenant.admin, match))["state"] == "result_declared"
    assert (await tenant.admin.get(f"{BASE}/matches/{match['id']}/settlement")).status_code == 404
    monkeypatch.setattr(service, "_post", original)
    result = await tenant.admin.post(f"{BASE}/matches/{match['id']}/transition", json=payload)
    assert result.status_code == 200, result.text
    assert await balance(a) == D("108.50") and await balance(b) == D("90.00")


async def test_revoking_a_declarator_assignment_disconnects_the_room(tenant, app):  # type: ignore[no-untyped-def]
    arena, _, _, _ = await setup(tenant)
    user = await tenant.create_user("room-judge", "declarator")
    judge = await tenant.login("room-judge")
    assignment = f"{BASE}/arenas/{arena['id']}/controllers/{user['id']}"
    await tenant.admin.put(assignment)
    token = judge.headers["Authorization"].split()[1]
    async with ws_client(app, tenant.host) as c:
        async with aconnect_ws(f"/api/ws/simulations?token={token}", c) as ws:
            await _receive(ws)
            await ws.send_json({"type": "SUBSCRIBE", "arenaId": arena["id"]})
            await event(ws, "SUBSCRIBED")
            assert (await tenant.admin.delete(assignment)).status_code == 200
            with pytest.raises(WebSocketDisconnect) as exc:
                await asyncio.wait_for(ws.receive_text(), 3)
            assert exc.value.code == 4403


async def test_subscribed_clients_receive_nothing_from_a_rolled_back_wager(tenant, app, monkeypatch):  # type: ignore[no-untyped-def]
    from app.modules.simulations import service

    arena, match, player, _ = await setup(tenant)
    match = (await step(tenant.admin, match, "WAGERING_OPEN"))["match"]
    original = service.emit

    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("After broadcast registration")

    token = player.headers["Authorization"].split()[1]
    async with ws_client(app, tenant.host) as c:
        async with aconnect_ws(f"/api/ws/simulations?token={token}", c) as ws:
            await _receive(ws)
            await ws.send_json({"type": "SUBSCRIBE", "arenaId": arena["id"]})
            await event(ws, "SUBSCRIBED")
            monkeypatch.setattr(service, "emit", interrupted)
            with pytest.raises(RuntimeError, match="After broadcast registration"):
                await place(player, match, "SIDE_A")
            await ws.send_json({"type": "PING"})
            assert (await _receive(ws))["type"] == "PONG"
            assert await balance(player) == D("100.00")


@pytest.mark.parametrize("token", ["", "not-a-jwt"])
async def test_simulation_socket_rejects_invalid_jwt(tenant, app, token):  # type: ignore[no-untyped-def]
    async with ws_client(app, tenant.host) as c:
        async with aconnect_ws(f"/api/ws/simulations?token={token}", c) as ws:
            with pytest.raises(WebSocketDisconnect) as exc:
                await ws.receive_text()
            assert exc.value.code == 4401


async def test_opt_in_precision_validation_and_one_active_match(tenant, other_tenant):  # type: ignore[no-untyped-def]
    arena, match, player, _ = await setup(tenant)
    await tenant.admin.patch(f"{BASE}/arenas/{arena['id']}", json={"enabled": False})
    payload = {"request_id": str(uuid.uuid4()), "expected_version": 0, "new_state": "WAGERING_OPEN"}
    r = await tenant.admin.post(f"{BASE}/matches/{match['id']}/transition", json=payload)
    assert r.status_code == 409 and r.json()["error"]["code"] == "arena_disabled"
    assert (await player.get(f"{BASE}/arenas")).json()["total"] == 0
    await tenant.admin.patch(f"{BASE}/arenas/{arena['id']}", json={"enabled": True})
    match = (await step(tenant.admin, match, "WAGERING_OPEN"))["match"]
    for amount in (1, "0.00", "-0.01", "NaN", "0.001"):
        assert (await place(player, match, "SIDE_A", amount)).status_code == 422
    assert (await place(other_tenant.admin, match, "SIDE_A")).status_code == 403
    other_player = await other_tenant.create_user("foreign-arcade-player", "player")
    foreign = await other_tenant.login(other_player["username"])
    assert (await place(foreign, match, "SIDE_A")).status_code == 404
    assert await balance(player) == D("100.00")
    second = (
        await tenant.admin.post(
            f"{BASE}/arenas/{arena['id']}/matches",
            json={
                "number": 2,
                "team_a": "Alpha",
                "team_b": "Beta",
            },
        )
    ).json()
    r = await tenant.admin.post(
        f"{BASE}/matches/{second['id']}/transition",
        json={
            **payload,
            "request_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 409
    assert (await current(tenant.admin, second))["state"] == "pending"


@pytest.mark.parametrize("has_stake", [False, True])
async def test_empty_and_one_sided_matches_refund_without_fee(tenant, has_stake):  # type: ignore[no-untyped-def]
    _, match, player, _ = await setup(tenant)
    match = (await step(tenant.admin, match, "WAGERING_OPEN"))["match"]
    if has_stake:
        assert (await place(player, match, "SIDE_A")).status_code == 201
    result = await finish(tenant.admin, match, "SIDE_A")
    assert result["settlement"]["refunded"] and D(result["settlement"]["fee_amount"]) == 0
    assert await balance(player) == D("100.00")


async def test_disabled_arena_keeps_each_players_own_receipts_readable(tenant, other_tenant):  # type: ignore[no-untyped-def]
    arena, match, a, b = await setup(tenant)
    match = (await step(tenant.admin, match, "WAGERING_OPEN"))["match"]
    assert (await place(a, match, "SIDE_A")).status_code == 201
    assert (await place(b, match, "SIDE_B")).status_code == 201
    await finish(tenant.admin, match, "DRAW")
    await tenant.admin.patch(f"{BASE}/arenas/{arena['id']}", json={"enabled": False})
    own_a = await a.get(f"{BASE}/matches/{match['id']}/receipts")
    own_b = await b.get(f"{BASE}/matches/{match['id']}/receipts")
    assert own_a.status_code == own_b.status_code == 200
    assert len(own_a.json()) == len(own_b.json()) == 1
    assert own_a.json()[0]["stake_id"] != own_b.json()[0]["stake_id"]
    assert (await other_tenant.admin.get(f"{BASE}/matches/{match['id']}/receipts")).status_code == 404


async def test_neutral_wallet_grant_is_once_and_shared_with_existing_gameplay(tenant):  # type: ignore[no-untyped-def]
    arena, _, _, _ = await setup(tenant)
    await tenant.admin.put("/api/v1/admin/betting/settings", json={"enabled": False, "starting_balance": "75.00"})
    await tenant.create_user("neutral-wallet-player", "player")
    player = await tenant.login("neutral-wallet-player")
    first = await player.get(f"{BASE}/wallet", params={"user_id": str(tenant.admin_id)})
    assert first.status_code == 200 and first.json()["balance"] == "75.00"
    assert (await player.get(f"{BASE}/wallet")).json()["version"] == first.json()["version"]
    await tenant.admin.patch(f"{BASE}/arenas/{arena['id']}", json={"enabled": False})
    await tenant.admin.put("/api/v1/admin/betting/settings", json={"enabled": True, "starting_balance": "900.00"})
    assert (await player.get(f"{BASE}/wallet")).json()["balance"] == "75.00"
    assert (await player.get("/api/v1/portal/betting")).json()["wallet"]["balance"] == "75.00"
