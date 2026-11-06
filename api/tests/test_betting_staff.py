"""Staff views of simulated betting and the realtime frames behind them: admin-configured grants,
the admin overview (fights, wallets, ledger, reconciliation), the Declarator monitor, the
betting.closed / fight.result_declared / fight.settled aliases, and demo-point transfer updates."""

import asyncio
import json
import uuid
from decimal import Decimal as D

from httpx_ws import aconnect_ws

from tests.test_betting import ADMIN, PORTAL, bet, declare, live_card, placed, players, view
from tests.test_declarator import proposal, setup_desk
from tests.test_websocket import _receive, ws_client


async def configure(tenant, **settings):  # type: ignore[no-untyped-def]
    r = await tenant.admin.put(f"{ADMIN}/betting/settings", json={"enabled": True, **settings})
    assert r.status_code == 200, r.text
    return r.json()


async def wallet(client) -> D:  # type: ignore[no-untyped-def]
    return D((await client.get(f"{PORTAL}/betting")).json()["wallet"]["balance"])


def token(client) -> str:  # type: ignore[no-untyped-def]
    return client.headers["Authorization"].split()[1]


async def drain(ws, until: str, limit: int = 12) -> list[dict]:  # type: ignore[no-untyped-def]
    frames = []
    for _ in range(limit):
        frames.append(await _receive(ws))
        if frames[-1]["type"] == until:
            return frames
    raise AssertionError(f"{until} not received: {[f['type'] for f in frames]}")


# ---- admin-controlled starting balance ----


async def test_no_balance_exists_until_an_administrator_configures_one(tenant):
    settings = await configure(tenant)
    assert settings["starting_balance"] == "0.00"  # no hardcoded grant
    [ana] = await players(tenant, "ana")
    assert await wallet(ana) == 0
    rows = (await tenant.admin.get(f"{ADMIN}/betting/entries")).json()
    assert rows["total"] == 0

    await configure(tenant, starting_balance="2500.00")
    assert await wallet(ana) == D("2500.00")  # the existing empty wallet receives it once
    await configure(tenant, starting_balance="9000.00")
    assert await wallet(ana) == D("2500.00")  # changing the setting never grants twice
    [grant] = (await tenant.admin.get(f"{ADMIN}/betting/entries", params={"kind": "grant"})).json()["items"]
    assert (grant["username"], D(grant["delta"]), grant["reference"]) == ("ana", D("2500.00"), f"grant:{grant['user_id']}")
    [ben] = await players(tenant, "ben")
    assert await wallet(ben) == D("9000.00")  # new wallets get the current setting
    logs = (await tenant.admin.get(f"{ADMIN}/audit-logs", params={"action": "betting.settings_updated"})).json()["items"]
    assert any(e["changes"].get("starting_balance") == ["2500.00", "9000.00"] for e in logs)  # who authorised it


# ---- admin overview ----


async def test_admin_overview_wallets_ledger_and_reconciliation(tenant):
    await configure(tenant, starting_balance="1000.00")
    _, [one, two] = await live_card(tenant, 2)
    assert (await tenant.admin.post(f"{ADMIN}/matches/{one['id']}/betting/open")).status_code == 200
    ana, ben, cyd = await players(tenant, "ana", "ben", "cyd")
    await placed(ana, one["id"], "meron", 200)
    await placed(ben, one["id"], "wala", 166)
    await placed(cyd, one["id"], "draw", 50)
    # One fight per event takes bets: the operator closes #1 before opening #2.
    assert (await tenant.admin.post(f"{ADMIN}/matches/{one['id']}/betting/close")).status_code == 200
    assert (await tenant.admin.post(f"{ADMIN}/matches/{two['id']}/betting/open")).status_code == 200
    await placed(ana, two["id"], "wala", 100)

    books = (await tenant.admin.get(f"{ADMIN}/betting/books", params={"live": "true"})).json()
    assert books["total"] == 2
    first = next(b for b in books["items"] if b["match_id"] == one["id"])
    assert first["pools"] == {"meron": "200.00", "wala": "166.00", "draw": "50.00"}
    assert (first["odds"]["meron_display"], first["odds"]["wala_display"]) == ("1.66", "2.00")
    assert D(first["stakes_total"]) == 416
    # Stake × the board price, capped at the pot: 200 × 1.66 = 332 and 166 × 2.00 = 332. DRAW is 8× plus refunds.
    assert first["obligations"] == {"meron": "332.00", "wala": "332.00", "draw": "766.00", "cancelled": "416.00"}
    assert first["settlement"] is None and first["event_name"] == "Derby"

    await declare(tenant, one["id"], "meron")
    settled = (await tenant.admin.get(f"{ADMIN}/betting/books", params={"state": "settled"})).json()["items"]
    [s] = settled
    assert s["obligations"] is None
    assert s["settlement"]["outcome"] == "meron"
    assert (D(s["settlement"]["paid_to_winners"]), D(s["settlement"]["refunds"]), D(s["settlement"]["commission"])) == (
        D("332.00"),
        0,
        D("34.00"),
    )

    walls = {w["username"]: w for w in (await tenant.admin.get(f"{ADMIN}/betting/wallets")).json()["items"]}
    assert (D(walls["ana"]["balance"]), D(walls["ana"]["granted"]), D(walls["ana"]["staked"])) == (D("1032.00"), 1000, 300)
    assert (D(walls["ana"]["returned"]), D(walls["ana"]["open_stakes"])) == (D("332.00"), 100)
    kinds = [
        e["kind"]
        for e in (await tenant.admin.get(f"{ADMIN}/betting/entries", params={"user_id": walls["ana"]["user_id"]})).json()["items"]
    ]
    assert kinds == ["payout", "stake", "stake", "grant"]

    rec = (await tenant.admin.get(f"{ADMIN}/betting/reconciliation")).json()
    assert rec["balanced"] is True and rec["checks"]["wallet_mismatches"] == 0 and rec["checks"]["pool_mismatches"] == 0
    assert (D(rec["granted"]), rec["grants"], D(rec["staked"]), D(rec["at_stake"])) == (3000, 3, 516, 100)
    assert (D(rec["paid_to_winners"]), D(rec["commission"])) == (D("332.00"), D("34.00"))
    # Every point is accounted for: issued = in wallets + still at stake + house result.
    assert D(rec["granted"]) == D(rec["in_wallets"]) + D(rec["at_stake"]) + D(rec["house_result"])
    assert D(rec["house_result"]) == D("84.00")  # the lost DRAW stake + the 34 the side pot did not pay


async def test_overview_permissions(tenant):
    await configure(tenant, starting_balance="1000.00")
    await tenant.create_user("op1", "operator")
    await tenant.create_user("ma1", "master_agent")
    op, ma = await tenant.login("op1"), await tenant.login("ma1")
    [ana] = await players(tenant, "ana")
    assert (await op.get(f"{ADMIN}/betting/books")).status_code == 200  # operators run fights
    for path in ("wallets", "entries", "reconciliation"):
        assert (await op.get(f"{ADMIN}/betting/{path}")).status_code == 403
        assert (await ma.get(f"{ADMIN}/betting/{path}")).status_code == 403
        assert (await ana.get(f"{ADMIN}/betting/{path}")).status_code == 403
        assert (await tenant.admin.get(f"{ADMIN}/betting/{path}")).status_code == 200


async def test_overview_is_tenant_scoped(tenant, other_tenant):
    await configure(tenant, starting_balance="1000.00")
    _, [fight] = await live_card(tenant)
    await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")
    [ana] = await players(tenant, "ana")
    await placed(ana, fight["id"], "meron", 100)
    await configure(other_tenant, starting_balance="1000.00")
    for path in ("books", "wallets", "entries"):
        assert (await other_tenant.admin.get(f"{ADMIN}/betting/{path}")).json()["total"] == 0
    assert D((await other_tenant.admin.get(f"{ADMIN}/betting/reconciliation")).json()["granted"]) == 0


# ---- declarator monitor ----


async def test_declarator_monitor_shows_pools_obligations_and_settlement(tenant):
    await configure(tenant, starting_balance="1000.00")
    _, event, fight, judge = await setup_desk(tenant)
    assert (await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")).status_code == 200
    ana, ben = await players(tenant, "ana", "ben")
    await placed(ana, fight["id"], "meron", 300)
    await placed(ben, fight["id"], "wala", 300)

    monitor = (await judge.get(f"/api/v1/declarator/events/{event['id']}/monitor")).json()
    [book] = monitor["books"]
    assert (book["match_number"], book["state"], book["pools"]["meron"], book["pools"]["wala"]) == (1, "open", "300.00", "300.00")
    assert (book["odds"]["meron_display"], book["obligations"]["meron"]) == ("1.83", "549.00")
    by_player = {p["username"]: p for p in book["players"]}
    assert (by_player["ana"]["meron"], by_player["ana"]["wala"], by_player["ana"]["draw"]) == ("300.00", "0.00", "0.00")
    assert (by_player["ben"]["meron"], by_player["ben"]["wala"], by_player["ben"]["draw"]) == ("0.00", "300.00", "0.00")

    body = proposal("wala")
    decl = (await judge.post(f"/api/v1/declarator/matches/{fight['id']}/declarations", json=body)).json()
    assert decl["status"] == "finalized"
    [book] = (await judge.get(f"/api/v1/declarator/events/{event['id']}/monitor")).json()["books"]
    assert book["state"] == "settled" and book["settlement"]["outcome"] == "wala"
    assert D(book["settlement"]["paid_to_winners"]) == D(549)
    assert await wallet(ben) == D(1249)
    # Retrying the same declaration neither settles nor credits twice.
    assert (await judge.post(f"/api/v1/declarator/matches/{fight['id']}/declarations", json=body)).json()["id"] == decl["id"]
    assert await wallet(ben) == D(1249)


async def test_dummy_stake_changes_the_pool_and_odds_without_a_balance(app, tenant):
    await configure(tenant, starting_balance="1000.00")
    _, event, fight, judge = await setup_desk(tenant)
    assert (await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")).status_code == 200
    ana, ben = await players(tenant, "ana", "ben")
    await placed(ana, fight["id"], "meron", 300)
    before = await wallet(ana)
    body = {"request_id": "6b1d3c2a-7e54-4c1a-9a0e-1d2c3b4a5e6f", "side": "wala", "amount": "300.00"}
    async with ws_client(app, tenant.host) as client:
        async with aconnect_ws(f"/api/ws?token={token(ben)}", client) as player_ws:
            await _receive(player_ws)
            await asyncio.sleep(0.2)
            added = (await judge.post(f"/api/v1/declarator/matches/{fight['id']}/dummy-bets", json=body)).json()
            frames = await drain(player_ws, "book.updated")
    assert added["pools"] == {"meron": "300.00", "wala": "300.00", "draw": "0.00"}
    assert added["odds"]["meron_display"] == added["odds"]["wala_display"] == "1.83"
    # Every player, including one who did not bet, sees the dummy inside the shared pools and odds.
    live = frames[-1]["data"]
    assert live["pools"] == added["pools"]
    assert live["odds"]["meron_display"] == live["odds"]["wala_display"] == "1.83"
    seen = await view(ana, fight["id"])
    other = await view(ben, fight["id"])
    assert seen["book"]["pools"] == other["book"]["pools"] == added["pools"]
    assert seen["mine"]["meron"] == {"stake": "300.00", "pays": "549.00", "result": None}
    assert other["mine"]["meron"]["stake"] == other["mine"]["wala"]["stake"] == "0.00"
    assert await wallet(ana) == before
    assert await wallet(ben) == D("1000.00")
    # The same request does not add the amount again.
    again = (await judge.post(f"/api/v1/declarator/matches/{fight['id']}/dummy-bets", json=body)).json()
    assert again["pools"]["wala"] == "300.00"
    monitor = (await judge.get(f"/api/v1/declarator/events/{event['id']}/monitor")).json()
    [book] = monitor["books"]
    assert book["dummy_wala"] == "300.00" and book["players"][0]["username"] == "ana"
    assert book["players"][0]["meron"] == "300.00"
    declared = (await judge.post(f"/api/v1/declarator/matches/{fight['id']}/declarations", json=proposal("meron"))).json()
    assert declared["status"] == "finalized"
    # Ana's 300 locked 1.83 and returns 549. The dummy stake is not credited to anyone.
    assert await wallet(ana) == before + D("549.00")


async def test_quick_dummy_amounts_ignore_player_bet_limits_and_add_up(app, tenant):
    # The default player max bet is 10,000; quick dummy buttons send 50K-500K.
    await configure(tenant, starting_balance="1000.00")
    _, _event, fight, judge = await setup_desk(tenant)
    assert (await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")).status_code == 200
    url = f"/api/v1/declarator/matches/{fight['id']}/dummy-bets"
    first = await judge.post(url, json={"request_id": str(uuid.uuid4()), "side": "meron", "amount": "500000.00"})
    assert first.status_code == 200, first.text
    second = (await judge.post(url, json={"request_id": str(uuid.uuid4()), "side": "meron", "amount": "50000.00"})).json()
    assert second["pools"]["meron"] == "550000.00"
    zero = await judge.post(url, json={"request_id": str(uuid.uuid4()), "side": "wala", "amount": "0"})
    assert zero.status_code == 422


# ---- realtime ----


async def test_result_and_settlement_aliases_arrive_in_order_and_ledger_updates_reach_staff_only(app, tenant):
    await configure(tenant, starting_balance="1000.00")
    _, [fight] = await live_card(tenant)
    await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")
    ana, ben = await players(tenant, "ana", "ben")
    assert await wallet(ana) and await wallet(ben)
    await placed(ana, fight["id"], "meron", 100)
    await placed(ben, fight["id"], "wala", 100)
    await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/start")
    async with ws_client(app, tenant.host) as c1, ws_client(app, tenant.host) as c2:
        async with (
            aconnect_ws(f"/api/ws?token={token(tenant.admin)}", c1) as staff,
            aconnect_ws(f"/api/ws?token={token(ben)}", c2) as player,
        ):
            await _receive(staff)
            await _receive(player)
            await asyncio.sleep(0.2)
            r = await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/result", json={"outcome": "meron"})
            assert r.status_code == 201
            types = [f["type"] for f in await drain(player, "fight.settled")]
            # Last call, then the official result, then the settlement totals.
            assert types.index("betting.closed") < types.index("fight.result_declared") < types.index("fight.settled")
            settled = await drain(staff, "fight.settled")
            totals = settled[-1]["data"]
            assert (totals["outcome"], totals["stakes"], totals["payouts"], totals["commission"]) == (
                "meron",
                "200.00",
                "183.00",
                "17.00",
            )
            staff_types = [f["type"] for f in settled + await drain(staff, "betting.ledger_updated")]
            assert "betting.ledger_updated" in staff_types
            await player.send_text(json.dumps({"type": "ping"}))
            rest = [f["type"] for f in await drain(player, "pong")]
            assert "betting.ledger_updated" not in rest


async def test_declarator_odds_set_the_payout_and_stop_when_betting_closes(app, tenant):
    await configure(tenant, starting_balance="1000.00")
    _, _event, fight, judge = await setup_desk(tenant)
    assert (await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")).status_code == 200
    ana, ben = await players(tenant, "ana", "ben")
    await placed(ana, fight["id"], "meron", 100)
    # A player cannot set the board, and a pair over 3.66 is refused.
    refused = await ana.put(f"/api/v1/declarator/matches/{fight['id']}/odds", json={"meron": "1.50", "wala": "2.16"})
    assert refused.status_code == 403
    over = await judge.put(f"/api/v1/declarator/matches/{fight['id']}/odds", json={"meron": "2.00", "wala": "2.00"})
    assert over.status_code == 422 and over.json()["error"]["code"] == "odds_invalid"

    async with ws_client(app, tenant.host) as client:
        async with aconnect_ws(f"/api/ws?token={token(ben)}", client) as player_ws:
            await _receive(player_ws)
            await asyncio.sleep(0.2)
            quoted = await judge.put(
                f"/api/v1/declarator/matches/{fight['id']}/odds", json={"meron": "1.50", "wala": "2.16"}
            )
            assert quoted.status_code == 200, quoted.text
            frames = await drain(player_ws, "book.updated")
    board = quoted.json()
    assert (board["odds"]["meron_display"], board["odds"]["wala_display"]) == ("1.50", "2.16")
    assert D(board["odds"]["meron_probability"]) + D(board["odds"]["wala_probability"]) == 1
    live = frames[-1]["data"]
    assert live["match_id"] == fight["id"] and live["odds"]["meron_display"] == "1.50"
    # Ana's bet records the 1.83 it was placed at; she is paid the board price at close.
    [ana_bet] = (await ana.get(f"{PORTAL}/bets", params={"match_id": fight["id"]})).json()["items"]
    assert D(ana_bet["multiplier"]) == D("1.83")

    await placed(ben, fight["id"], "wala", 100)
    [ben_bet] = (await ben.get(f"{PORTAL}/bets", params={"match_id": fight["id"]})).json()["items"]
    assert D(ben_bet["multiplier"]) == D("2.16")
    # Pays follows the board: 100 × 1.50 = 150.
    assert D((await view(ana, fight["id"]))["mine"]["meron"]["pays"]) == D("150.00")

    assert (await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/close")).status_code == 200
    locked = await judge.put(f"/api/v1/declarator/matches/{fight['id']}/odds", json={"meron": "1.20", "wala": "2.46"})
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "betting_closed"
    closed = await view(ana, fight["id"])
    assert (closed["book"]["odds"]["meron_display"], closed["book"]["odds"]["wala_display"]) == ("1.50", "2.16")

    declared = await judge.post(f"/api/v1/declarator/matches/{fight['id']}/declarations", json=proposal("meron"))
    assert declared.status_code == 200, declared.text
    assert await wallet(ana) == D("1050.00")  # 1000 − 100 + 100 × 1.50
    assert await wallet(ben) == D("900.00")
    logs = (await tenant.admin.get(f"{ADMIN}/audit-logs", params={"action": "betting.odds_set"})).json()["items"]
    assert logs and D(logs[0]["changes"]["meron"][1]) == D("1.50")


async def test_bets_on_unconfigured_wallets_are_refused(tenant):
    await configure(tenant)
    _, [fight] = await live_card(tenant)
    await tenant.admin.post(f"{ADMIN}/matches/{fight['id']}/betting/open")
    [ana] = await players(tenant, "ana")
    r = await bet(ana, fight["id"], "meron", 100)
    assert r.status_code == 422 and r.json()["error"]["code"] == "insufficient_balance"
    assert (await view(ana, fight["id"]))["wallet"]["balance"] == "0.00"
