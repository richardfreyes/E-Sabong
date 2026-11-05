"""Declarator desk: event header (end, refresh, online players), jump to fight, change team, auto ghost betting."""

import asyncio
from decimal import Decimal as D

from httpx_ws import aconnect_ws

from app.modules.declarator.desk import run_due_jobs
from tests.test_betting import ADMIN, placed, players
from tests.test_betting_staff import configure, token, wallet
from tests.test_declarator import proposal, setup_desk
from tests.test_websocket import _receive, ws_client

DESK = "/api/v1/declarator"


async def _book(judge, event_id: str, match_id: str) -> dict:  # type: ignore[no-untyped-def]
    monitor = (await judge.get(f"{DESK}/events/{event_id}/monitor")).json()
    return next(b for b in monitor["books"] if b["match_id"] == match_id)


async def _next_fight(judge, event_id: str) -> dict:  # type: ignore[no-untyped-def]
    monitor = (await judge.get(f"{DESK}/events/{event_id}/monitor")).json()
    return next(m for m in monitor["matches"] if m["status"] == "scheduled")


async def test_jump_to_fight_numbers_the_next_fight_and_keeps_history(tenant):
    _, event, fight, judge = await setup_desk(tenant)
    jump = f"{DESK}/events/{event['id']}/jump"
    busy = await judge.post(jump, json={"match_number": 196})
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "fight_in_progress"
    assert (await judge.post(f"{DESK}/matches/{fight['id']}/declarations", json=proposal("meron"))).status_code == 200
    low = await judge.post(jump, json={"match_number": 1})
    assert low.status_code == 422 and low.json()["error"]["code"] == "fight_number_too_low"
    r = await judge.post(jump, json={"match_number": 196})
    assert r.status_code == 200, r.text
    assert r.json()["match_number"] == 196
    monitor = (await judge.get(f"{DESK}/events/{event['id']}/monitor")).json()
    numbers = {m["match_number"]: m["status"] for m in monitor["matches"]}
    assert numbers == {1: "completed", 196: "scheduled"}
    assert monitor["betting"]["match_number"] == 196
    # The earlier fight keeps its number and result.
    assert next(m for m in monitor["matches"] if m["match_number"] == 1)["result"]["outcome"] == "meron"
    # Players cannot use the desk.
    [ana] = await players(tenant, "ana")
    assert (await ana.post(jump, json={"match_number": 300})).status_code == 403


async def test_change_team_renames_the_current_fight_only_until_its_result(tenant):
    _, event, fight, judge = await setup_desk(tenant)
    teams = f"{DESK}/matches/{fight['id']}/teams"
    r = await judge.put(teams, json={"meron_name": "Delta Alpha", "wala_name": "Silver Stallion"})
    assert r.status_code == 200, r.text
    assert (r.json()["meron_name"], r.json()["wala_name"]) == ("Delta Alpha", "Silver Stallion")
    assert (await judge.put(teams, json={"meron_name": "", "wala_name": "X"})).status_code == 422
    assert (await judge.post(f"{DESK}/matches/{fight['id']}/declarations", json=proposal("wala"))).status_code == 200
    locked = await judge.put(teams, json={"meron_name": "Changed", "wala_name": "Later"})
    assert locked.status_code == 409
    after = (await tenant.admin.get(f"/api/v1/admin/matches/{fight['id']}")).json()
    assert (after["meron_name"], after["wala_name"]) == ("Delta Alpha", "Silver Stallion")
    assert after["result"]["outcome"] == "wala"


async def test_end_event_requires_the_fight_to_be_settled_then_completes(tenant):
    _, event, fight, judge = await setup_desk(tenant)
    end = f"{DESK}/events/{event['id']}/end"
    blocked = await judge.post(end)
    assert blocked.status_code == 422, blocked.text
    assert (await judge.post(f"{DESK}/matches/{fight['id']}/declarations", json=proposal("draw"))).status_code == 200
    r = await judge.post(end)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"
    assert (await judge.post(end)).status_code == 422


async def test_refresh_all_reaches_players_and_online_counts_connected_players(app, tenant):
    _, event, _fight, judge = await setup_desk(tenant)
    [ana] = await players(tenant, "ana")
    online = f"{DESK}/events/{event['id']}/online"
    assert (await judge.get(online)).json() == {"players": 0}
    async with ws_client(app, tenant.host) as client:
        async with aconnect_ws(f"/api/ws?token={token(ana)}", client) as player_ws:
            await _receive(player_ws)
            assert (await judge.get(online)).json() == {"players": 1}
            # The Declarator's own socket is not a player.
            async with aconnect_ws(f"/api/ws?token={token(judge)}", client) as desk_ws:
                await _receive(desk_ws)
                assert (await judge.get(online)).json() == {"players": 1}
            await asyncio.sleep(0.2)
            assert (await judge.post(f"{DESK}/events/{event['id']}/refresh")).status_code == 200
            frame = await _receive(player_ws)
            while frame["type"] != "event.updated":
                frame = await _receive(player_ws)
            assert frame["data"]["id"] == event["id"]
    await asyncio.sleep(0.2)
    assert (await judge.get(online)).json() == {"players": 0}
    assert (await ana.post(f"{DESK}/events/{event['id']}/refresh")).status_code == 403


async def test_auto_ghost_adds_dummy_points_on_schedule_and_stops_when_betting_closes(tenant):
    await configure(tenant, starting_balance="1000.00")
    _, event, fight, judge = await setup_desk(tenant)
    ghost = f"{DESK}/events/{event['id']}/ghost-auto"
    body = {"meron_amount": "3628.00", "wala_amount": "3888.00", "interval_seconds": 1}
    closed = await judge.post(f"{ghost}/start", json=body)
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "betting_closed"
    assert (await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")).status_code == 200
    [ana] = await players(tenant, "ana")
    await placed(ana, fight["id"], "meron", 100)
    before = await wallet(ana)

    started = await judge.post(f"{ghost}/start", json=body)
    assert started.status_code == 200, started.text
    assert started.json()["active"] is True and started.json()["started_by"] == "judge"
    twice = await judge.post(f"{ghost}/start", json=body)
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "ghost_auto_running"
    assert (await judge.post(f"{ghost}/start", json={**body, "meron_amount": "0", "wala_amount": "0"})).status_code == 422

    assert await run_due_jobs() == 1
    assert await run_due_jobs() == 0  # not due again for a second
    book = await _book(judge, event["id"], fight["id"])
    assert (D(book["dummy_meron"]), D(book["dummy_wala"])) == (D("3628.00"), D("3888.00"))
    assert D(book["pools"]["meron"]) == D("3728.00")
    await asyncio.sleep(1.05)
    assert await run_due_jobs() == 1
    status = (await judge.get(ghost)).json()
    assert status["runs"] == 2 and D(status["added_meron"]) == D("7256.00")
    assert await wallet(ana) == before  # ghost amounts never touch a balance

    assert (await judge.post(f"{DESK}/matches/{fight['id']}/betting/close")).status_code == 200
    status = (await judge.get(ghost)).json()
    assert status["active"] is False and "Betting closed" in status["stop_reason"]
    await asyncio.sleep(1.05)
    assert await run_due_jobs() == 0
    assert D((await _book(judge, event["id"], fight["id"]))["dummy_meron"]) == D("7256.00")


async def test_auto_ghost_stop_button_and_runner_safety_stop(tenant):
    await configure(tenant)
    _, event, fight, judge = await setup_desk(tenant)
    ghost = f"{DESK}/events/{event['id']}/ghost-auto"
    assert (await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")).status_code == 200
    body = {"meron_amount": "100.00", "wala_amount": "0", "interval_seconds": 1}
    assert (await judge.post(f"{ghost}/start", json=body)).status_code == 200
    stopped = (await judge.post(f"{ghost}/stop")).json()
    assert stopped["active"] is False and stopped["stop_reason"] == "Stopped by judge"
    assert (await judge.post(f"{ghost}/stop")).status_code == 200  # idempotent
    assert await run_due_jobs() == 0
    # Betting closed by someone else (here the admin): the runner notices and stops the job itself.
    assert (await judge.post(f"{ghost}/start", json=body)).status_code == 200
    assert (await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/close")).status_code == 200
    assert await run_due_jobs() == 1
    status = (await judge.get(ghost)).json()
    assert status["active"] is False and status["runs"] == 0 and "Betting closed" in status["stop_reason"]
