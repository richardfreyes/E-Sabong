"""Simulated fight betting: live pools and odds, positions, limits, settlement, idempotency,
concurrency, isolation and realtime sync. Arena Points only; the retired points archive never changes."""

import asyncio
import json
import uuid
from decimal import Decimal as D

import pytest
from httpx_ws import aconnect_ws

from tests.conftest import legacy_points_rows
from tests.test_declarator import proposal, setup_desk
from tests.test_predictions import live_card
from tests.test_websocket import _receive, ws_client

ADMIN = "/api/v1/admin"
PORTAL = "/api/v1/portal"
START = D("10000.00")


async def enable(tenant, **overrides):  # type: ignore[no-untyped-def]
    # Nothing is granted by default: tests configure the starting balance like an administrator.
    r = await tenant.admin.put(f"{ADMIN}/betting/settings", json={"enabled": True, "starting_balance": str(START), **overrides})
    assert r.status_code == 200, r.text
    return r.json()


async def open_card(tenant, fights=1, **settings):  # type: ignore[no-untyped-def]
    await enable(tenant, **settings)
    event, matches = await live_card(tenant, fights)
    # Only one fight per event takes bets at a time: the first fight opens, later ones stay closed.
    r = await tenant.admin.post(f"{ADMIN}/matches/{matches[0]['id']}/betting/open")
    assert r.status_code == 200, r.text
    return event, matches


async def players(tenant, *names):  # type: ignore[no-untyped-def]
    for name in names:
        await tenant.create_user(name, "player")
    return [await tenant.login(name) for name in names]


async def bet(client, match_id, side, amount, request_id=None):  # type: ignore[no-untyped-def]
    body = {"side": side, "amount": str(amount), "request_id": request_id or str(uuid.uuid4())}
    return await client.post(f"{PORTAL}/matches/{match_id}/bets", json=body)


async def placed(client, match_id, side, amount):  # type: ignore[no-untyped-def]
    r = await bet(client, match_id, side, amount)
    assert r.status_code == 201, r.text
    return r.json()


async def view(client, match_id):  # type: ignore[no-untyped-def]
    r = await client.get(f"{PORTAL}/matches/{match_id}/book")
    assert r.status_code == 200, r.text
    return r.json()


async def balance(client) -> D:  # type: ignore[no-untyped-def]
    r = await client.get(f"{PORTAL}/betting")
    assert r.status_code == 200, r.text
    return D(r.json()["wallet"]["balance"])


def pools(snapshot) -> tuple[D, D, D]:  # type: ignore[no-untyped-def]
    p = snapshot["book"]["pools"] if "book" in snapshot else snapshot["pools"]
    return D(p["meron"]), D(p["wala"]), D(p["draw"])


def shown(snapshot) -> tuple[D | None, D | None]:  # type: ignore[no-untyped-def]
    o = snapshot["book"]["odds"]
    return tuple(None if o[k] is None else D(o[k]) for k in ("meron_display", "wala_display"))  # type: ignore[return-value]


async def declare(tenant, match_id, outcome):  # type: ignore[no-untyped-def]
    await tenant.admin.post(f"{ADMIN}/matches/{match_id}/start")
    r = await tenant.admin.post(f"{ADMIN}/matches/{match_id}/result", json={"outcome": outcome})
    assert r.status_code == 201, r.text


# ---- pools, odds and positions ----


async def test_pools_aggregate_all_players_and_positions_stay_personal(tenant):
    _, [fight] = await open_card(tenant)
    ana, ben, cy = await players(tenant, "ana", "ben", "cyd")
    await placed(ana, fight["id"], "meron", 100)
    await placed(ben, fight["id"], "meron", 100)
    out = await placed(cy, fight["id"], "wala", 166)
    assert pools(out) == (D(200), D(166), D(0))
    assert shown(out) == (D("1.65"), D("1.98"))
    assert D(out["wallet"]["balance"]) == START - 166

    a = await view(ana, fight["id"])
    # The all-player pool and the player's own stake are separate figures.
    assert pools(a) == (D(200), D(166), D(0))
    assert D(a["mine"]["meron"]["stake"]) == D(100)
    assert D(a["mine"]["meron"]["pays"]) == D("164.70")  # 100 x 1.647 (unrounded odds)
    assert (D(a["mine"]["wala"]["stake"]), D(a["mine"]["wala"]["pays"])) == (D(0), D(0))
    assert D(a["wallet"]["balance"]) == START - 100
    assert a["book"]["accepting"] is True and a["book"]["final"] is False and a["book"]["bet_count"] == 3
    # Nothing about other players is exposed.
    assert "ben" not in json.dumps(a) and "cyd" not in json.dumps(a)
    c = await view(cy, fight["id"])
    assert D(c["mine"]["wala"]["pays"]) == D("329.40") and D(c["mine"]["meron"]["stake"]) == 0

    # More money on WALA shortens WALA and lengthens MERON.
    await placed(ben, fight["id"], "wala", 34)
    assert shown(await view(ana, fight["id"])) == (D("1.80"), D("1.80"))  # 200 / 200


async def test_reference_screenshot_values_through_the_api(tenant):
    big = {"starting_balance": "2000000.00", "max_bet": "1000000.00", "max_fight_stake": "1000000.00"}
    _, [fight] = await open_card(tenant, **big)
    red, blue = await players(tenant, "red", "blue")
    await placed(red, fight["id"], "meron", "780261.90")
    out = await placed(blue, fight["id"], "wala", "906433.56")
    assert pools(out)[:2] == (D("780261.90"), D("906433.56"))
    assert shown(out) == (D("1.95"), D("1.67"))
    assert D(out["book"]["odds"]["meron"]).quantize(D("0.000001")) == D("1.945534")


async def test_odds_are_unavailable_until_both_sides_have_stakes(tenant):
    _, [fight] = await open_card(tenant)
    [solo] = await players(tenant, "solo")
    v = await view(solo, fight["id"])
    assert v["book"]["odds"] == {"meron": None, "wala": None, "meron_display": None, "wala_display": None}
    assert (v["mine"]["meron"]["pays"], v["mine"]["draw"]["pays"]) == ("0.00", "0.00")
    out = await placed(solo, fight["id"], "meron", 50)
    assert out["book"]["odds"]["meron"] is None and out["mine"]["meron"]["pays"] is None
    draw = await placed(solo, fight["id"], "draw", 50)
    assert D(draw["mine"]["draw"]["pays"]) == D(400) and D(draw["book"]["draw_multiplier"]) == D(8)


# ---- settlement ----


async def test_meron_win_pays_the_distributable_pool_and_takes_losers_stakes(tenant):
    _, [fight] = await open_card(tenant)
    ana, ben, cy, dee = await players(tenant, "ana", "ben", "cyd", "dee")
    for client, side, amount in ((ana, "meron", 100), (ben, "meron", 100), (cy, "wala", 166), (dee, "draw", 50)):
        await placed(client, fight["id"], side, amount)
    await declare(tenant, fight["id"], "meron")

    assert await balance(ana) == START - 100 + D("164.70")
    assert await balance(ben) == START - 100 + D("164.70")
    assert await balance(cy) == START - 166
    assert await balance(dee) == START - 50
    a = await view(ana, fight["id"])
    assert a["book"]["state"] == "settled" and a["book"]["final"] is True and a["book"]["outcome"] == "meron"
    assert a["mine"]["meron"]["result"] == "won" and D(a["mine"]["meron"]["pays"]) == D("164.70")
    assert (D(a["mine"]["settled_payout"]), D(a["mine"]["settled_net"])) == (D("164.70"), D("64.70"))
    [row] = (await ana.get(f"{PORTAL}/bets", params={"match_id": fight["id"]})).json()["items"]
    assert (row["status"], D(row["payout"]), D(row["net_profit"])) == ("won", D("164.70"), D("64.70"))
    assert row["settled_at"] is not None and row["pricing"] == "pool" and row["multiplier"] is None

    admin_view = (await tenant.admin.get(f"{ADMIN}/matches/{fight['id']}/book")).json()
    assert D(admin_view["commission_amount"]) == D("36.60")
    assert D(admin_view["final_meron_odds"]).quantize(D("0.001")) == D("1.647")

    # A repeated declaration is refused and credits nothing twice.
    r = await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/result", json={"outcome": "meron"})
    assert r.status_code in (409, 422)
    assert await balance(ana) == START - 100 + D("164.70")
    # Betting only ever touches Arena Points: the retired points archive gains no row.
    assert await legacy_points_rows(tenant.id) == (0, 0, 0)


async def test_draw_pays_its_fixed_multiplier_and_refunds_meron_and_wala(tenant):
    _, [fight] = await open_card(tenant)
    ana, ben, dee = await players(tenant, "ana", "ben", "dee")
    await placed(ana, fight["id"], "meron", 100)
    await placed(ben, fight["id"], "wala", 200)
    await placed(dee, fight["id"], "draw", 50)
    await declare(tenant, fight["id"], "draw")
    assert await balance(dee) == START - 50 + 400
    assert await balance(ana) == START and await balance(ben) == START
    v = await view(ana, fight["id"])
    assert v["mine"]["meron"]["result"] == "refunded" and D(v["mine"]["settled_net"]) == 0


@pytest.mark.parametrize("how", ["no_contest", "cancel_fight", "cancel_event"])
async def test_cancellation_refunds_every_stake(tenant, how):
    event, [fight] = await open_card(tenant)
    ana, ben = await players(tenant, "ana", "ben")
    await placed(ana, fight["id"], "meron", 300)
    await placed(ana, fight["id"], "draw", 20)
    await placed(ben, fight["id"], "wala", 250)
    if how == "no_contest":
        await declare(tenant, fight["id"], "no_contest")
    elif how == "cancel_fight":
        r = await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/cancel", json={"reason": "Injury"})
        assert r.status_code == 200, r.text
    else:
        assert (await tenant.admin.post(f"{ADMIN}/events/{event['id']}/cancel")).status_code == 200
    assert await balance(ana) == START and await balance(ben) == START
    rows = (await ana.get(f"{PORTAL}/bets")).json()["items"]
    assert {r["status"] for r in rows} == {"refunded"}
    assert all(D(r["net_profit"]) == 0 for r in rows)


async def test_one_sided_book_refunds_instead_of_paying_below_stake(tenant):
    _, [fight] = await open_card(tenant)
    ana, dee = await players(tenant, "ana", "dee")
    await placed(ana, fight["id"], "meron", 500)
    await placed(dee, fight["id"], "draw", 10)
    await declare(tenant, fight["id"], "meron")
    assert await balance(ana) == START  # no WALA money: refunded, no commission taken
    assert await balance(dee) == START - 10


async def test_correction_moves_only_the_difference_and_never_goes_negative(tenant):
    _, [first, second] = await open_card(tenant, fights=2)
    ana, ben = await players(tenant, "ana", "ben")
    await placed(ana, first["id"], "meron", 1000)
    await placed(ben, first["id"], "wala", 1000)
    await declare(tenant, first["id"], "meron")
    assert await balance(ana) == START + 800  # wins 1800 on a 1000 stake
    assert await balance(ben) == START - 1000

    corrected = {"outcome": "wala", "reason": "Review"}
    r = await tenant.admin.put(f"{ADMIN}/matches/{first['id']}/result", json=corrected)
    assert r.status_code == 200, r.text
    assert await balance(ana) == START - 1000
    assert await balance(ben) == START + 800

    # Ben stakes everything on the next fight, then the first result flips back: the payout is
    # reclaimed only as far as the balance allows.
    assert (await tenant.admin.post(f"{ADMIN}/matches/{second['id']}/betting/open")).status_code == 200
    await placed(ben, second["id"], "meron", 10000)
    left = await balance(ben)
    assert left == 800
    r = await tenant.admin.put(f"{ADMIN}/matches/{first['id']}/result", json={"outcome": "meron", "reason": "Final"})
    assert r.status_code == 200, r.text
    assert await balance(ben) == 0
    assert await balance(ana) == START + 800
    logs = (await tenant.admin.get(f"{ADMIN}/audit-logs", params={"action": "betting.resettled"})).json()["items"]
    assert any(D(entry["changes"]["uncollected"]) == D(1000) for entry in logs)


# ---- the betting window ----


async def test_close_freezes_pools_and_locks_final_odds_then_reopen(tenant):
    _, [fight] = await open_card(tenant)
    ana, ben = await players(tenant, "ana", "ben")
    await placed(ana, fight["id"], "meron", 200)
    await placed(ben, fight["id"], "wala", 166)
    closed = (await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/close")).json()
    assert closed["state"] == "closed" and closed["final"] is True and closed["accepting"] is False
    assert D(closed["odds"]["meron_display"]) == D("1.65")
    r = await bet(ana, fight["id"], "wala", 50)
    assert r.status_code == 409 and r.json()["error"]["code"] == "betting_closed"
    assert (await view(ana, fight["id"]))["book"]["final"] is True
    # Closing twice is harmless; reopening makes the odds provisional again.
    assert (await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/close")).status_code == 200
    reopened = (await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")).json()
    assert reopened["state"] == "open" and reopened["final"] is False and reopened["version"] > closed["version"]
    await placed(ana, fight["id"], "wala", 34)


async def test_submitted_declaration_closes_betting_and_approval_settles(tenant):
    await enable(tenant)
    _, event, fight, judge = await setup_desk(tenant)
    assert (await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")).status_code == 200
    ana, ben = await players(tenant, "ana", "ben")
    await placed(ana, fight["id"], "meron", 100)
    await placed(ben, fight["id"], "wala", 100)
    decl = (await judge.post(f"/api/v1/declarator/matches/{fight['id']}/declarations", json=proposal("wala"))).json()
    v = await view(ana, fight["id"])
    assert v["book"]["state"] == "closed" and v["book"]["accepting"] is False
    assert (await bet(ana, fight["id"], "meron", 10)).status_code == 409
    assert await balance(ben) == START - 100  # pending, not official: nothing settled yet
    r = await tenant.admin.post(f"/api/v1/declarator/declarations/{decl['id']}/approve")
    assert r.status_code == 200, r.text
    assert await balance(ben) == START + 80  # 200 x 0.9 = 180 on a 100 stake
    assert await balance(ana) == START - 100
    assert event


async def test_betting_needs_an_open_book_on_a_live_event(tenant):
    await enable(tenant)
    event, [fight] = await live_card(tenant)
    [ana] = await players(tenant, "ana")
    r = await bet(ana, fight["id"], "meron", 100)
    assert r.status_code == 409 and r.json()["error"]["code"] == "betting_closed"
    assert (await view(ana, fight["id"]))["book"]["state"] is None
    await tenant.admin.put(f"{ADMIN}/betting/settings", json={"enabled": False})
    r = await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")
    assert r.status_code == 409 and r.json()["error"]["code"] == "betting_disabled"
    assert event


# ---- validation and limits ----


async def test_limits_balance_and_all_in(tenant):
    _, [fight] = await open_card(
        tenant, min_bet="20.00", max_bet="5000.00", max_fight_stake="8000.00", starting_balance="6000.35"
    )
    [ana] = await players(tenant, "ana")
    for amount, code in (("19.99", "bet_below_minimum"), ("5000.01", "bet_above_maximum")):
        r = await bet(ana, fight["id"], "meron", amount)
        assert r.status_code == 422 and r.json()["error"]["code"] == code
    r = await bet(ana, fight["id"], "meron", "10.001")
    assert r.status_code == 422  # more than two decimal places
    await placed(ana, fight["id"], "meron", 5000)
    # ALL IN: the remaining balance exactly.
    out = await placed(ana, fight["id"], "wala", "1000.35")
    assert D(out["wallet"]["balance"]) == 0 and D(out["limits"]["remaining_fight_stake"]) == D("1999.65")
    r = await bet(ana, fight["id"], "wala", 20)
    assert r.status_code == 422 and r.json()["error"]["code"] == "insufficient_balance"
    assert await balance(ana) == 0


async def test_per_fight_limit(tenant):
    _, [fight] = await open_card(tenant, max_bet="1000.00", max_fight_stake="1500.00")
    [ana] = await players(tenant, "ana")
    await placed(ana, fight["id"], "meron", 1000)
    r = await bet(ana, fight["id"], "wala", 600)
    assert r.status_code == 422 and r.json()["error"]["code"] == "fight_limit_reached"
    assert r.json()["error"]["details"]["remaining"] == "500.00"
    await placed(ana, fight["id"], "wala", 500)


async def test_only_players_bet_and_only_admins_configure(tenant):
    _, [fight] = await open_card(tenant)
    await tenant.create_user("agent1", "master_agent")
    agent = await tenant.login("agent1")
    r = await bet(agent, fight["id"], "meron", 100)
    assert r.status_code == 403 and r.json()["error"]["code"] == "players_only"
    [ana] = await players(tenant, "ana")
    assert (await ana.put(f"{ADMIN}/betting/settings", json={"enabled": False})).status_code == 403
    assert (await ana.post(f"{ADMIN}/matches/{fight['id']}/betting/close")).status_code == 403
    r = await tenant.admin.put(f"{ADMIN}/betting/settings", json={"min_bet": "500.00", "max_bet": "100.00"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_limits"


async def test_setting_changes_never_rewrite_accepted_bets(tenant):
    _, [fight] = await open_card(tenant)
    [ana, ben] = await players(tenant, "ana", "ben")
    await placed(ana, fight["id"], "draw", 50)  # locked at 8x
    await enable(tenant, draw_multiplier="10.00", commission_rate="0.2000")
    out = await placed(ana, fight["id"], "draw", 10)  # accepted at 10x
    assert D(out["mine"]["draw"]["pays"]) == D(400) + D(100)
    # The open book keeps the commission it opened with.
    await placed(ana, fight["id"], "meron", 100)
    out = await placed(ben, fight["id"], "wala", 100)
    assert D(out["book"]["commission_rate"]) == D("0.1") and shown(out) == (D("1.80"), D("1.80"))
    await declare(tenant, fight["id"], "draw")
    assert await balance(ana) == START - 60 + 500


# ---- idempotency and concurrency ----


async def test_resent_request_replays_the_original_bet(tenant):
    _, [fight] = await open_card(tenant)
    [ana] = await players(tenant, "ana")
    key = str(uuid.uuid4())
    first = await bet(ana, fight["id"], "meron", 100, key)
    again = await bet(ana, fight["id"], "meron", 100, key)
    assert first.status_code == again.status_code == 201
    assert first.json()["bet"]["id"] == again.json()["bet"]["id"] and again.json()["replayed"] is True
    assert await balance(ana) == START - 100
    r = await bet(ana, fight["id"], "meron", 200, key)
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_conflict"
    # Simultaneous duplicates of a new key also place a single bet.
    key = str(uuid.uuid4())
    results = await asyncio.gather(*(bet(ana, fight["id"], "wala", 75, key) for _ in range(5)))
    assert {r.status_code for r in results} == {201}
    assert len({r.json()["bet"]["id"] for r in results}) == 1
    assert await balance(ana) == START - 175
    assert pools(await view(ana, fight["id"])) == (D(100), D(75), D(0))


async def test_concurrent_bets_never_overdraw_and_pools_add_up(tenant):
    _, [fight] = await open_card(tenant, starting_balance="1000.00")
    clients = await players(tenant, "pl1", "pl2", "pl3", "pl4")
    # One player racing ten bets against a balance that covers six.
    results = await asyncio.gather(*(bet(clients[0], fight["id"], "meron", 150) for _ in range(10)))
    accepted = [r for r in results if r.status_code == 201]
    assert len(accepted) == 6
    assert {r.json()["error"]["code"] for r in results if r.status_code != 201} == {"insufficient_balance"}
    assert await balance(clients[0]) == 100
    # Many players at once: every accepted stake is in the pool exactly once.
    sides = ["meron", "wala", "draw"]
    results = await asyncio.gather(*(bet(c, fight["id"], sides[i % 3], 10 + i) for c in clients[1:] for i in range(6)))
    assert all(r.status_code == 201 for r in results), [r.text for r in results if r.status_code != 201]
    expected = {s: D(0) for s in sides}
    expected["meron"] += 900
    for _c in clients[1:]:
        for i in range(6):
            expected[sides[i % 3]] += 10 + i
    final = await view(clients[1], fight["id"])
    assert pools(final) == (expected["meron"], expected["wala"], expected["draw"])
    assert final["book"]["bet_count"] == 6 + 18


async def test_bets_and_settlement_race_safely(tenant):
    _, [fight] = await open_card(tenant)
    ana, ben = await players(tenant, "ana", "ben")
    await placed(ben, fight["id"], "wala", 100)
    await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/start")
    racing = [bet(ana, fight["id"], "meron", 10) for _ in range(8)]
    declaring = tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/result", json={"outcome": "meron"})
    *bets, declared = await asyncio.gather(*racing, declaring)
    assert declared.status_code == 201, declared.text
    accepted = sum(1 for r in bets if r.status_code == 201)
    assert all(r.status_code in (201, 409) for r in bets)
    # Every accepted bet made it into the frozen pool and was settled.
    v = await view(ana, fight["id"])
    assert pools(v)[0] == D(10 * accepted)
    if accepted:
        assert v["mine"]["meron"]["result"] == "won"
        assert await balance(ana) == START - 10 * accepted + D(v["mine"]["meron"]["pays"])
    else:
        assert await balance(ana) == START


# ---- isolation ----


async def test_tenants_and_fights_are_isolated(tenant, other_tenant):
    _, [one, two] = await open_card(tenant, fights=2)
    ana, ben = await players(tenant, "ana", "ben")
    await placed(ana, one["id"], "meron", 100)
    await placed(ben, one["id"], "wala", 100)
    assert pools(await view(ana, two["id"])) == (0, 0, 0)  # another fight's book is untouched
    # A player only ever sees their own bets.
    assert {b["side"] for b in (await ana.get(f"{PORTAL}/bets")).json()["items"]} == {"meron"}
    [outsider] = await players(other_tenant, "outsider")
    r = await outsider.get(f"{PORTAL}/matches/{one['id']}/book")
    assert r.status_code == 404
    r = await bet(outsider, one["id"], "meron", 100)
    assert r.status_code in (404, 409)
    assert pools(await view(ana, one["id"])) == (D(100), D(100), 0)


# ---- realtime ----


async def test_pool_updates_reach_every_player_and_wallets_only_their_owner(app, tenant):
    _, [fight] = await open_card(tenant)
    ana, ben = await players(tenant, "ana", "ben")
    assert await balance(ana) == START and await balance(ben) == START  # wallets exist, as when the arena loads
    token = lambda c: c.headers["Authorization"].split()[1]  # noqa: E731
    async with ws_client(app, tenant.host) as c1, ws_client(app, tenant.host) as c2:
        async with aconnect_ws(f"/api/ws?token={token(ana)}", c1) as ws_a, aconnect_ws(f"/api/ws?token={token(ben)}", c2) as ws_b:
            assert (await _receive(ws_a))["type"] == "connected"
            assert (await _receive(ws_b))["type"] == "connected"
            await asyncio.sleep(0.2)
            await placed(ana, fight["id"], "meron", 120)

            seen_b = await _receive(ws_b)
            assert seen_b["type"] == "book.updated" and seen_b["data"]["match_id"] == fight["id"]
            assert seen_b["data"]["pools"]["meron"] == "120.00" and seen_b["data"]["version"] >= 2
            assert "user" not in json.dumps(seen_b) and "balance" not in json.dumps(seen_b)
            frames_a = {}
            for _ in range(3):
                msg = await _receive(ws_a)
                frames_a[msg["type"]] = msg["data"]
            assert set(frames_a) == {"wallet.updated", "book.updated", "bets.updated"}
            assert D(frames_a["wallet.updated"]["balance"]) == START - 120
            # Ben got the shared totals but nothing personal of Ana's.
            await ws_b.send_text(json.dumps({"type": "ping"}))
            assert (await _receive(ws_b))["type"] == "pong"

            await placed(ben, fight["id"], "wala", 80)
            newer = await _receive(ws_a)
            assert newer["type"] == "book.updated" and newer["data"]["version"] > seen_b["data"]["version"]
            assert newer["data"]["odds"]["meron_display"] == "1.50"

        # After a reconnect the client resynchronises from the API: same version as the last frame.
        snapshot = await view(ana, fight["id"])
        assert snapshot["book"]["version"] == newer["data"]["version"]
        async with ws_client(app, tenant.host) as c3, aconnect_ws(f"/api/ws?token={token(ana)}", c3) as ws_again:
            assert (await _receive(ws_again))["type"] == "connected"


async def test_switching_betting_on_reaches_open_player_sessions(app, tenant):
    [ana] = await players(tenant, "ana")
    token = ana.headers["Authorization"].split()[1]
    async with ws_client(app, tenant.host) as c, aconnect_ws(f"/api/ws?token={token}", c) as ws:
        assert (await _receive(ws))["type"] == "connected"
        await asyncio.sleep(0.2)
        await enable(tenant)
        frame = await _receive(ws)
        assert frame == {
            "type": "betting.config_updated",
            "data": {"enabled": True, "draw_multiplier": "8.00", "commission_rate": "0.1000"},
        }
        # Saving unchanged settings is silent.
        await enable(tenant)
        await ws.send_text(json.dumps({"type": "ping"}))
        assert (await _receive(ws))["type"] == "pong"
    config = (await ana.get(f"{PORTAL}/betting")).json()
    assert config["enabled"] is True and D(config["wallet"]["balance"]) == START
