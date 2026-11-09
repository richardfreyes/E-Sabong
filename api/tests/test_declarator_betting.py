"""Declarator betting control: Open -> Close -> Declare Meron, Wala, or Draw -> next fight.

Real PostgreSQL and Redis. Balances are simulated Arena Points (no cash value)."""

import asyncio
import json
from decimal import Decimal as D

from httpx_ws import aconnect_ws

from tests.test_betting import ADMIN, START, balance, bet, enable, placed, players, pools, view
from tests.test_declarator import proposal
from tests.test_events import _event, _match
from tests.test_predictions import live_card
from tests.test_websocket import _receive, ws_client

DESK = "/api/v1/declarator"


async def desk(tenant, fights=1):  # type: ignore[no-untyped-def]
    """A live event with scheduled (not started) fights and an assigned Declarator."""
    await enable(tenant)
    judge_user = await tenant.create_user("judge", "declarator")
    event = await _event(tenant)
    card = [await _match(tenant, event["id"]) for _ in range(fights)]
    for path in (f"/events/{event['id']}/publish", f"/events/{event['id']}/start"):
        assert (await tenant.admin.post(ADMIN + path)).status_code == 200
    assert (await tenant.admin.put(f"{DESK}/events/{event['id']}/assignments/{judge_user['id']}")).status_code == 200
    return event, card, await tenant.login("judge")


async def control(judge, event_id):  # type: ignore[no-untyped-def]
    r = await judge.get(f"{DESK}/events/{event_id}/monitor")
    assert r.status_code == 200, r.text
    return r.json()["betting"]


async def switch(judge, match_id, action, expect=200):  # type: ignore[no-untyped-def]
    r = await judge.post(f"{DESK}/matches/{match_id}/betting/{action}")
    assert r.status_code == expect, r.text
    return r.json()


async def audits(tenant, action):  # type: ignore[no-untyped-def]
    r = await tenant.admin.get(f"{ADMIN}/audit-logs", params={"action": action, "page_size": 100})
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_open_close_requires_a_winner_before_the_next_open(tenant):
    event, [fight], judge = await desk(tenant)
    ana, ben = await players(tenant, "ana", "ben")

    c = await control(judge, event["id"])
    assert (c["match_number"], c["status"], c["can_open"], c["can_close"]) == (1, "closed", True, False)
    assert (await bet(ana, fight["id"], "meron", 100)).status_code == 409  # never opened

    # Open: starts the scheduled fight and accepts bets on all three sides.
    c = await switch(judge, fight["id"], "open")
    assert (c["match_number"], c["status"], c["match_status"], c["can_close"]) == (1, "open", "in_progress", True)
    assert (await switch(judge, fight["id"], "open"))["status"] == "open"  # duplicate: no change
    await placed(ana, fight["id"], "meron", 200)
    await placed(ben, fight["id"], "wala", 100)
    await placed(ben, fight["id"], "draw", 10)

    # Close: new bets refused (also from a stale page), everything already placed stays visible.
    c = await switch(judge, fight["id"], "close")
    assert (c["match_number"], c["status"], c["can_open"], c["can_close"]) == (1, "closed", False, False)
    assert "Declare Meron, Wala, or Draw" in c["blocked_reason"]
    assert (await switch(judge, fight["id"], "close"))["status"] == "closed"  # duplicate: no change
    refused = await judge.post(f"{DESK}/matches/{fight['id']}/betting/open")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "result_required"
    for side in ("meron", "wala", "draw"):
        r = await bet(ana, fight["id"], side, 50)
        assert r.status_code == 409 and r.json()["error"]["code"] == "betting_closed"
    closed = await view(ana, fight["id"])
    assert pools(closed) == (D(200), D(100), D(10)) and closed["book"]["accepting"] is False
    assert D(closed["mine"]["meron"]["stake"]) == D(200) and closed["book"]["odds"]["meron_display"]
    assert await balance(ana) == START - 200

    # Declare: the result is final, settlement runs and the next fight is prepared automatically.
    d = (await judge.post(f"{DESK}/matches/{fight['id']}/declarations", json=proposal("meron"))).json()
    assert d["status"] == "finalized"
    assert await balance(ana) > START - 200  # MERON won: settled with simulated Arena Points
    r = await judge.post(f"{DESK}/matches/{fight['id']}/betting/open")
    assert r.status_code == 409 and r.json()["error"]["code"] == "result_finalized"

    monitor = (await judge.get(f"{DESK}/events/{event['id']}/monitor")).json()
    assert [m["match_number"] for m in monitor["matches"]] == [1, 2]
    following = monitor["matches"][1]
    assert following["status"] == "scheduled"
    c = monitor["betting"]
    assert (c["match_number"], c["match_id"], c["status"], c["can_open"]) == (2, following["id"], "closed", True)

    # Fight #2: open, bet, close; its result prepares fight #3.
    assert (await switch(judge, following["id"], "open"))["match_number"] == 2
    await placed(ben, following["id"], "wala", 100)
    await switch(judge, following["id"], "close")
    d = (await judge.post(f"{DESK}/matches/{following['id']}/declarations", json=proposal("wala"))).json()
    assert d["status"] == "finalized"
    assert (await control(judge, event["id"]))["match_number"] == 3

    # Every open and close is audited with the actor; duplicates were not.
    opened = await audits(tenant, "betting.opened")
    closes = await audits(tenant, "betting.closed")
    assert len(opened) == 2 and len(closes) == 2
    assert all(e["actor_label"] and e["changes"]["by"] == "declarator" and e["created_at"] for e in opened + closes)
    assert [e["changes"]["reopened"] for e in opened] == [False, False]
    assert len(await audits(tenant, "match.next_prepared")) == 2


async def test_only_assigned_declarators_control_betting(tenant, other_tenant):
    event, [fight], judge = await desk(tenant)
    [player] = await players(tenant, "ana")
    assert (await player.post(f"{DESK}/matches/{fight['id']}/betting/open")).status_code == 403
    # Administrators keep their own controls; the Declarator switch belongs to Declarators.
    r = await tenant.admin.post(f"{DESK}/matches/{fight['id']}/betting/open")
    assert r.status_code == 403, r.text
    await tenant.create_user("other_judge", "declarator")
    stranger = await tenant.login("other_judge")
    assert (await stranger.post(f"{DESK}/matches/{fight['id']}/betting/open")).status_code in (403, 404)
    foreign = await _match(other_tenant, (await _event(other_tenant))["id"])
    assert (await judge.post(f"{DESK}/matches/{foreign['id']}/betting/open")).status_code == 404
    assert (await control(judge, event["id"]))["status"] == "closed"
    assert await audits(tenant, "betting.opened") == []


async def test_one_fight_open_per_event_and_only_the_current_fight(tenant):
    event, [one, two], judge = await desk(tenant, fights=2)
    r = await judge.post(f"{DESK}/matches/{two['id']}/betting/open")
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_current_fight"
    # An operator pre-opened the second fight's book: the Declarator cannot open a second one.
    assert (await tenant.admin.post(f"{ADMIN}/matches/{two['id']}/betting/open")).status_code == 200
    r = await judge.post(f"{DESK}/matches/{one['id']}/betting/open")
    assert r.status_code == 409 and r.json()["error"]["code"] == "betting_open_elsewhere"
    c = await control(judge, event["id"])
    assert c["can_open"] is False and "Fight #2" in c["blocked_reason"]
    assert c["next_match_number"] == 2


async def test_concurrent_opens_open_one_fight_once(tenant):
    event, [fight], judge = await desk(tenant)
    url = f"{DESK}/matches/{fight['id']}/betting/open"
    responses = await asyncio.gather(*(judge.post(url) for _ in range(4)))
    assert [r.status_code for r in responses] == [200] * 4
    assert len(await audits(tenant, "betting.opened")) == 1
    assert len(await audits(tenant, "match.started")) == 1


async def test_disabled_settings_or_non_live_event_refuse_opening(tenant):
    event, [fight], judge = await desk(tenant)
    await enable(tenant, enabled=False)
    r = await judge.post(f"{DESK}/matches/{fight['id']}/betting/open")
    assert r.status_code == 409 and r.json()["error"]["code"] == "betting_disabled"
    assert (await control(judge, event["id"]))["can_open"] is False
    assert (await tenant.admin.get(f"{ADMIN}/matches/{fight['id']}")).json()["status"] == "scheduled"  # nothing started


async def test_status_changes_reach_players_over_websockets(app, tenant):
    event, [fight], judge = await desk(tenant)
    [ana] = await players(tenant, "ana")
    token = ana.headers["Authorization"].split()[1]
    async with ws_client(app, tenant.host) as c, aconnect_ws(f"/api/ws?token={token}", c) as ws:
        await _receive(ws)
        await asyncio.sleep(0.2)

        async def until(kind, accepting):  # type: ignore[no-untyped-def]
            for _ in range(12):
                frame = await _receive(ws)
                if frame["type"] == kind and frame["data"].get("match_id") == fight["id"]:
                    if accepting is None or frame["data"]["accepting"] is accepting:
                        return frame
            raise AssertionError(f"{kind} not received")

        await switch(judge, fight["id"], "open")
        frame = await until("book.updated", True)
        assert frame["data"]["state"] == "open" and frame["data"]["event_id"] == event["id"]
        await switch(judge, fight["id"], "close")
        assert (await until("book.updated", False))["data"]["state"] == "closed"
        await until("betting.closed", None)
        assert "judge" not in json.dumps(frame)


# ---- sequential progression, one open fight per event, fight preparation ----


async def declare_final(tenant, judge, match_id, outcome):  # type: ignore[no-untyped-def]
    extra = {"reason": "Fight called off"} if outcome == "cancelled" else {}
    body = proposal(outcome, **extra)
    d = (await judge.post(f"{DESK}/matches/{match_id}/declarations", json=body)).json()
    assert d.get("status") == "finalized", d
    # Retrying the same declaration never finalizes or settles twice.
    assert (await judge.post(f"{DESK}/matches/{match_id}/declarations", json=body)).json()["id"] == d["id"]
    return d


async def test_fight_35_to_36_to_37_with_cancel_refund_and_entries_from_the_declarator(tenant):
    await enable(tenant)
    judge_user = await tenant.create_user("judge", "declarator")
    event = await _event(tenant)
    f35 = await _match(tenant, event["id"], match_number=35)
    for path in (f"/events/{event['id']}/publish", f"/events/{event['id']}/start"):
        assert (await tenant.admin.post(ADMIN + path)).status_code == 200
    assert (await tenant.admin.put(f"{DESK}/events/{event['id']}/assignments/{judge_user['id']}")).status_code == 200
    judge = await tenant.login("judge")
    ana, ben = await players(tenant, "ana", "ben")

    # #35: open, bets on all sides, close (stale bets refused), declare MERON, approve.
    assert (await switch(judge, f35["id"], "open"))["match_number"] == 35
    await placed(ana, f35["id"], "meron", 100)
    await placed(ben, f35["id"], "wala", 100)
    await placed(ben, f35["id"], "draw", 10)
    await switch(judge, f35["id"], "close")
    assert (await bet(ana, f35["id"], "meron", 50)).status_code == 409
    await declare_final(tenant, judge, f35["id"], "meron")
    assert await balance(ana) > START  # won
    c = await control(judge, event["id"])
    assert (c["match_number"], c["status"], c["can_open"]) == (36, "closed", True)  # prepared, NOT opened
    f36 = c["match_id"]
    assert (await bet(ana, f36, "meron", 50)).status_code == 409  # a prepared fight takes no bets

    # The Declarator enters the announced entries of #36 before opening it.
    r = await judge.patch(f"{DESK}/matches/{f36}", json={"meron_name": "Entry A", "wala_name": "Entry B"})
    assert r.status_code == 200, r.text
    assert (r.json()["meron_name"], r.json()["wala_name"], r.json()["match_number"]) == ("Entry A", "Entry B", 36)

    # #36: open, bet, close (a closed fight stays locked until its result), declare CANCEL:
    # every stake refunded.
    assert (await switch(judge, f36, "open"))["match_number"] == 36
    r = await judge.patch(f"{DESK}/matches/{f36}", json={"meron_name": "Late"})
    assert r.status_code == 409  # entries lock once betting opened
    before_ana, before_ben = await balance(ana), await balance(ben)
    await placed(ana, f36, "meron", 70)
    await placed(ben, f36, "draw", 30)
    await switch(judge, f36, "close")
    assert (await switch(judge, f36, "open", expect=409))["error"]["code"] == "result_required"
    await declare_final(tenant, judge, f36, "cancelled")
    assert (await balance(ana), await balance(ben)) == (before_ana, before_ben)
    assert (await tenant.admin.get(f"{ADMIN}/matches/{f36}")).json()["status"] == "cancelled"

    c = await control(judge, event["id"])
    assert (c["match_number"], c["status"]) == (37, "closed")
    numbers = [m["match_number"] for m in (await judge.get(f"{DESK}/events/{event['id']}/monitor")).json()["matches"]]
    assert numbers == [35, 36, 37]

    # Completing the event discards the unopened prepared #37 (soft delete, audited); history stays.
    assert (await tenant.admin.post(f"{ADMIN}/events/{event['id']}/complete")).status_code == 200, "complete"
    assert [e["changes"]["match_number"] for e in await audits(tenant, "match.prepared_discarded")] == [37]
    left = [m["match_number"] for m in (await tenant.admin.get(f"{ADMIN}/events/{event['id']}/matches")).json()]
    assert left == [35, 36]


async def test_draw_outcome_settles_through_the_declarator_flow(tenant):
    event, [fight], judge = await desk(tenant)
    ana, ben = await players(tenant, "ana", "ben")
    await switch(judge, fight["id"], "open")
    await placed(ana, fight["id"], "draw", 10)
    await placed(ben, fight["id"], "meron", 100)
    await switch(judge, fight["id"], "close")
    await declare_final(tenant, judge, fight["id"], "draw")
    assert await balance(ana) > START  # DRAW pays its multiplier
    assert await balance(ben) == START  # side stakes refunded on DRAW
    assert (await control(judge, event["id"]))["match_number"] == 2


async def test_completing_an_event_keeps_a_scheduled_fight_that_was_not_auto_prepared(tenant):
    event, [fight], judge = await desk(tenant)
    r = await tenant.admin.post(f"{ADMIN}/events/{event['id']}/complete")
    assert r.status_code == 422 and r.json()["error"]["code"] == "matches_open"
    assert (await tenant.admin.get(f"{ADMIN}/matches/{fight['id']}")).json()["status"] == "scheduled"


async def test_admin_endpoints_also_allow_only_one_open_fight_per_event(tenant):
    event, [one, two] = await live_card(tenant, 2)
    await enable(tenant)
    assert (await tenant.admin.post(f"{ADMIN}/matches/{one['id']}/betting/open")).status_code == 200
    r = await tenant.admin.post(f"{ADMIN}/matches/{two['id']}/betting/open")
    assert r.status_code == 409 and r.json()["error"]["code"] == "betting_open_elsewhere"
    assert r.json()["error"]["details"]["match_number"] == 1
    # Repeating the open on the already open fight is still idempotent.
    assert (await tenant.admin.post(f"{ADMIN}/matches/{one['id']}/betting/open")).status_code == 200

    # Emergency override needs a reason; it closes #1 first and is audited.
    r = await tenant.admin.post(f"{ADMIN}/matches/{two['id']}/betting/open", json={"supersede": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "reason_required"
    r = await tenant.admin.post(f"{ADMIN}/matches/{two['id']}/betting/open", json={"supersede": True, "reason": "Wrong fight"})
    assert r.status_code == 200, r.text
    books = {b["match_id"]: b["state"] for b in (await tenant.admin.get(f"{ADMIN}/events/{event['id']}/books")).json()}
    assert (books[one["id"]], books[two["id"]]) == ("closed", "open")
    [sup] = await audits(tenant, "betting.superseded")
    assert (sup["changes"]["match_number"], sup["changes"]["replaced_by"], sup["changes"]["reason"]) == (1, 2, "Wrong fight")
    [player] = await players(tenant, "ana")
    r = await player.post(f"{ADMIN}/matches/{one['id']}/betting/open", json={"supersede": True, "reason": "x" * 5})
    assert r.status_code == 403


async def test_concurrent_opens_of_different_fights_leave_exactly_one_open(tenant):
    event, card = await live_card(tenant, 3)
    await enable(tenant)
    responses = await asyncio.gather(*(tenant.admin.post(f"{ADMIN}/matches/{m['id']}/betting/open") for m in card))
    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 409, 409], [r.text for r in responses]
    books = (await tenant.admin.get(f"{ADMIN}/events/{event['id']}/books")).json()
    assert [b["state"] for b in books].count("open") == 1


async def test_declarator_prepares_a_fight_only_when_the_card_has_none(tenant, other_tenant):
    event, [fight], judge = await desk(tenant)
    body = {"meron_name": "Entry A", "wala_name": "Entry B"}
    r = await judge.post(f"{DESK}/events/{event['id']}/fights", json=body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "fight_already_prepared"
    # Clear the card (an unopened scheduled fight), then the Declarator prepares the next number.
    assert (await tenant.admin.delete(f"{ADMIN}/matches/{fight['id']}")).status_code == 200
    c = await control(judge, event["id"])
    assert c["match_id"] is None and c["can_open"] is True and c["blocked_reason"] is None
    r = await judge.post(f"{DESK}/events/{event['id']}/fights", json=body)
    assert r.status_code == 201, r.text
    assert (r.json()["match_number"], r.json()["meron_name"], r.json()["status"]) == (1, "Entry A", "scheduled")
    assert (await control(judge, event["id"]))["match_number"] == 1
    # Non-declarators and other tenants cannot prepare fights here.
    [player] = await players(tenant, "ana")
    assert (await player.post(f"{DESK}/events/{event['id']}/fights", json=body)).status_code == 403
    assert (await tenant.admin.post(f"{DESK}/events/{event['id']}/fights", json=body)).status_code == 403
    foreign = await _event(other_tenant)
    assert (await judge.post(f"{DESK}/events/{foreign['id']}/fights", json=body)).status_code == 404


async def test_open_betting_on_an_empty_card_prepares_the_next_fight(tenant, other_tenant):
    event, [fight], judge = await desk(tenant)
    assert (await tenant.admin.delete(f"{ADMIN}/matches/{fight['id']}")).status_code == 200
    ana, ben = await players(tenant, "ana", "ben")
    # Non-declarators and other tenants cannot use it.
    assert (await ana.post(f"{DESK}/events/{event['id']}/betting/open")).status_code == 403
    assert (await tenant.admin.post(f"{DESK}/events/{event['id']}/betting/open")).status_code == 403
    foreign = await _event(other_tenant)
    assert (await judge.post(f"{DESK}/events/{foreign['id']}/betting/open")).status_code == 404

    r = await judge.post(f"{DESK}/events/{event['id']}/betting/open")
    assert r.status_code == 200, r.text
    c = r.json()
    assert (c["match_number"], c["status"], c["match_status"], c["can_close"]) == (1, "open", "in_progress", True)
    [prepared] = await audits(tenant, "match.next_prepared")
    assert prepared["changes"]["match_number"] == 1 and prepared["changes"]["by"] == "declarator"
    # Repeating it opens nothing new: same fight, no second fight on the card.
    again = (await judge.post(f"{DESK}/events/{event['id']}/betting/open")).json()
    assert (again["match_id"], again["status"]) == (c["match_id"], "open")
    assert len(await audits(tenant, "match.next_prepared")) == 1
    await placed(ben, c["match_id"], "wala", 100)

    # Close, declare (final): the progression prepares fight #2; opening it starts it.
    assert (await switch(judge, c["match_id"], "close"))["status"] == "closed"
    d = (await judge.post(f"{DESK}/matches/{c['match_id']}/declarations", json=proposal("wala"))).json()
    assert d.get("status") == "finalized", d
    nxt = (await judge.post(f"{DESK}/events/{event['id']}/betting/open")).json()
    assert (nxt["match_number"], nxt["status"]) == (2, "open")
