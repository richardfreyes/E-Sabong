"""Server-side non-monetary predictions: window, one-per-fight, outcomes, isolation, history."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.database import tenant_session
from tests.test_declarator import proposal, setup_desk
from tests.test_events import _event, _match

ADMIN = "/api/v1/admin"


async def live_card(tenant, fights=1):  # type: ignore[no-untyped-def]
    event = await _event(tenant)
    matches = [await _match(tenant, event["id"]) for _ in range(fights)]
    for path in (f"/events/{event['id']}/publish", f"/events/{event['id']}/start"):
        assert (await tenant.admin.post(ADMIN + path)).status_code == 200
    return event, matches


async def predict(client, match_id, side):  # type: ignore[no-untyped-def]
    return await client.put(f"/api/v1/portal/matches/{match_id}/prediction", json={"side": side})


async def mine(client, **params):  # type: ignore[no-untyped-def]
    r = await client.get("/api/v1/portal/predictions", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# The product rule table: pick x official result.
RULES = [
    ("meron", "meron", "win"), ("wala", "meron", "lose"), ("draw", "meron", "lose"),
    ("wala", "wala", "win"), ("meron", "wala", "lose"), ("draw", "wala", "lose"),
    ("draw", "draw", "win"), ("meron", "draw", "lose"), ("wala", "draw", "lose"),
    ("meron", "no_contest", "void"), ("draw", "no_contest", "void"),
]  # fmt: skip


@pytest.mark.parametrize(("side", "official", "expected"), RULES)
async def test_official_result_decides_each_prediction(tenant, side, official, expected):
    _, [match] = await live_card(tenant)
    await tenant.create_user("fan", "player")
    fan = await tenant.login("fan")
    r = await predict(fan, match["id"], side)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending" and r.json()["open"] is True
    assert (await tenant.admin.post(f"{ADMIN}/matches/{match['id']}/start")).status_code == 200
    r = await tenant.admin.post(f"{ADMIN}/matches/{match['id']}/result", json={"outcome": official})
    assert r.status_code == 201, r.text
    [row] = (await mine(fan))["items"]
    assert (row["side"], row["status"], row["official_outcome"], row["open"]) == (side, expected, official, False)
    assert row["decided_at"] is not None
    # Closed for good: no new or changed prediction once the result is official.
    r = await predict(fan, match["id"], "wala")
    assert r.status_code == 409 and r.json()["error"]["code"] == "prediction_closed"


async def test_change_side_until_a_result_is_proposed_then_resolve_on_approval(tenant):
    _, _, match, judge = await setup_desk(tenant)
    await tenant.create_user("fan", "player")
    fan = await tenant.login("fan")
    points_before = (await fan.get("/api/v1/portal/points")).json()["balance"]
    first = (await predict(fan, match["id"], "meron")).json()
    again = (await predict(fan, match["id"], "wala")).json()
    same = (await predict(fan, match["id"], "wala")).json()
    assert first["id"] == again["id"] == same["id"]
    assert (again["side"], again["changes"], same["changes"]) == ("wala", 1, 1)

    decl = (await judge.post(f"/api/v1/declarator/matches/{match['id']}/declarations", json=proposal("wala"))).json()
    # A pending proposal closes the window but is not official, so nothing is decided yet.
    assert (await predict(fan, match["id"], "draw")).status_code == 409
    [row] = (await mine(fan))["items"]
    assert (row["status"], row["open"]) == ("pending", False)

    assert (await tenant.admin.post(f"/api/v1/declarator/declarations/{decl['id']}/approve")).status_code == 200
    [row] = (await mine(fan))["items"]
    assert (row["side"], row["status"]) == ("wala", "win")
    assert (await fan.get("/api/v1/portal/points")).json()["balance"] == points_before


async def test_correction_recomputes_and_cancellation_voids(tenant):
    _, [a, b, c] = await live_card(tenant, fights=3)
    await tenant.create_user("fan", "player")
    fan = await tenant.login("fan")
    for m, side in ((a, "meron"), (b, "wala"), (c, "draw")):
        assert (await predict(fan, m["id"], side)).status_code == 200
    # a: declared WALA (LOSE), then corrected to MERON (WIN).
    assert (await tenant.admin.post(f"{ADMIN}/matches/{a['id']}/start")).status_code == 200
    assert (await tenant.admin.post(f"{ADMIN}/matches/{a['id']}/result", json={"outcome": "wala"})).status_code == 201
    assert (await mine(fan, match_id=a["id"]))["items"][0]["status"] == "lose"
    r = await tenant.admin.put(f"{ADMIN}/matches/{a['id']}/result", json={"outcome": "meron", "reason": "Video review"})
    assert r.status_code == 200, r.text
    assert (await mine(fan, match_id=a["id"]))["items"][0]["status"] == "win"
    # b: the fight is cancelled.
    r = await tenant.admin.post(f"{ADMIN}/matches/{b['id']}/cancel", json={"reason": "Injury"})
    assert r.status_code == 200, r.text
    assert (await mine(fan, match_id=b["id"]))["items"][0]["status"] == "void"
    # c: the whole event is cancelled; still-scheduled fights become void too.
    event_id = c["event_id"]
    assert (await tenant.admin.post(f"{ADMIN}/events/{event_id}/cancel")).status_code == 200
    statuses = {p["match_id"]: p["status"] for p in (await mine(fan))["items"]}
    assert statuses == {a["id"]: "win", b["id"]: "void", c["id"]: "void"}


async def test_one_prediction_per_fight_under_concurrent_requests(tenant):
    _, [match] = await live_card(tenant)
    await tenant.create_user("fan", "player")
    fan = await tenant.login("fan")
    sides = ["meron", "wala", "draw"] * 4
    responses = await asyncio.gather(*(predict(fan, match["id"], s) for s in sides))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses if r.status_code != 200]
    assert len({r.json()["id"] for r in responses}) == 1
    async with tenant_session(tenant.id) as db:
        count = (await db.execute(text("SELECT count(*) FROM predictions WHERE match_id = :m"), {"m": match["id"]})).scalar_one()
    assert count == 1


async def test_only_players_on_published_fights_and_only_their_own_rows(tenant, other_tenant):
    _, [match] = await live_card(tenant)
    await tenant.create_user("fan", "player")
    await tenant.create_user("rival", "player")
    fan, rival = await tenant.login("fan"), await tenant.login("rival")
    assert (await predict(fan, match["id"], "meron")).status_code == 200
    # Staff accounts cannot predict.
    r = await predict(tenant.admin, match["id"], "meron")
    assert r.status_code == 403 and r.json()["error"]["code"] == "players_only"
    # Draft events are invisible to players.
    draft = await _event(tenant)
    draft_match = await _match(tenant, draft["id"])
    assert (await predict(fan, draft_match["id"], "meron")).status_code == 404
    # Another player in the same tenant never sees fan's prediction.
    assert (await mine(rival))["total"] == 0
    # Another tenant cannot reach this fight at all.
    await other_tenant.create_user("outsider", "player")
    outsider = await other_tenant.login("outsider")
    assert (await predict(outsider, match["id"], "wala")).status_code == 404
    assert (await mine(outsider))["total"] == 0


async def test_predictions_table_has_rls_and_cannot_be_deleted(tenant, other_tenant):
    _, [match] = await live_card(other_tenant)
    await other_tenant.create_user("fan", "player")
    fan = await other_tenant.login("fan")
    assert (await predict(fan, match["id"], "draw")).status_code == 200
    async with tenant_session(tenant.id) as db:
        assert (await db.execute(text("SELECT count(*) FROM predictions"))).scalar_one() == 0
    with pytest.raises(DBAPIError):
        async with tenant_session(other_tenant.id) as db:
            await db.execute(text("DELETE FROM predictions"))


async def test_history_search_filters_and_pagination(tenant):
    event, fights = await live_card(tenant, fights=4)
    await tenant.create_user("fan", "player")
    fan = await tenant.login("fan")
    for m, side in zip(fights, ("meron", "wala", "draw", "meron"), strict=True):
        assert (await predict(fan, m["id"], side)).status_code == 200
    first = fights[0]
    assert (await tenant.admin.post(f"{ADMIN}/matches/{first['id']}/start")).status_code == 200
    assert (await tenant.admin.post(f"{ADMIN}/matches/{first['id']}/result", json={"outcome": "meron"})).status_code == 201

    page = await mine(fan, page_size=3)
    assert (page["total"], len(page["items"])) == (4, 3)
    assert len((await mine(fan, page_size=3, page=2))["items"]) == 1
    assert {p["match_number"] for p in (await mine(fan, side="meron"))["items"]} == {1, 4}
    assert [p["match_number"] for p in (await mine(fan, status="win"))["items"]] == [1]
    assert (await mine(fan, status="pending"))["total"] == 3
    assert [p["match_number"] for p in (await mine(fan, q="#3"))["items"]] == [3]
    assert (await mine(fan, event_id=event["id"]))["total"] == 4
    assert (await fan.get("/api/v1/portal/predictions", params={"status": "paid"})).status_code == 422
