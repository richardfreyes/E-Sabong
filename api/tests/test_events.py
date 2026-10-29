"""Event and match lifecycle, official results and the player-facing views."""


async def _event(tenant, **extra):  # type: ignore[no-untyped-def]
    r = await tenant.admin.post(
        "/api/v1/admin/events", json={"name": "Derby", "venue": "Arena", "starts_at": "2030-05-01T09:00:00+08:00", **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _match(tenant, event_id, **extra):  # type: ignore[no-untyped-def]
    r = await tenant.admin.post(
        f"/api/v1/admin/events/{event_id}/matches", json={"meron_name": "Red", "wala_name": "Blue", **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_full_event_lifecycle_with_results(app, tenant) -> None:  # type: ignore[no-untyped-def]
    a = tenant.admin
    event = await _event(tenant)
    assert event["status"] == "draft"
    m1 = await _match(tenant, event["id"])
    m2 = await _match(tenant, event["id"], meron_name="Gold", wala_name="Silver")
    assert (m1["match_number"], m2["match_number"]) == (1, 2)
    r = await a.post(f"/api/v1/admin/events/{event['id']}/matches", json={"match_number": 2, "meron_name": "a", "wala_name": "b"})
    assert r.status_code == 409

    # Matches cannot start before the event is live.
    r = await a.post(f"/api/v1/admin/matches/{m1['id']}/start")
    assert r.status_code == 422 and r.json()["error"]["code"] == "event_not_live"
    assert (await a.post(f"/api/v1/admin/events/{event['id']}/publish")).json()["status"] == "scheduled"
    assert (await a.post(f"/api/v1/admin/events/{event['id']}/start")).json()["status"] == "live"

    # Results only for matches in progress.
    r = await a.post(f"/api/v1/admin/matches/{m1['id']}/result", json={"outcome": "meron"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_match_state"
    assert (await a.post(f"/api/v1/admin/matches/{m1['id']}/start")).json()["status"] == "in_progress"
    r = await a.post(f"/api/v1/admin/matches/{m2['id']}/start")
    assert r.status_code == 409 and r.json()["error"]["code"] == "match_in_progress"

    r = await a.post(f"/api/v1/admin/matches/{m1['id']}/result", json={"outcome": "meron", "notes": "clean win"})
    assert r.status_code == 201
    assert r.json()["status"] == "completed" and r.json()["result"]["outcome"] == "meron"
    assert (await a.post(f"/api/v1/admin/matches/{m1['id']}/result", json={"outcome": "wala"})).status_code == 422

    # Corrections require a reason and append to the history.
    assert (await a.put(f"/api/v1/admin/matches/{m1['id']}/result", json={"outcome": "wala"})).status_code == 422
    r = await a.put(f"/api/v1/admin/matches/{m1['id']}/result", json={"outcome": "wala", "reason": "Judges review"})
    assert r.status_code == 200 and r.json()["result"] == {**r.json()["result"], "outcome": "wala", "revision": 2}
    history = (await a.get(f"/api/v1/admin/matches/{m1['id']}/result/history")).json()
    assert [(h["revision"], h["outcome"], h["previous_outcome"]) for h in history] == [(1, "meron", None), (2, "wala", "meron")]
    assert history[1]["reason"] == "Judges review" and history[1]["changed_by_username"] == "admin"

    # Event cannot complete with open matches; cancel the rest and complete.
    r = await a.post(f"/api/v1/admin/events/{event['id']}/complete")
    assert r.status_code == 422 and r.json()["error"]["details"]["open_matches"] == 1
    assert (await a.post(f"/api/v1/admin/matches/{m2['id']}/cancel", json={"reason": "Withdrawn"})).json()[
        "status"
    ] == "cancelled"
    done = (await a.post(f"/api/v1/admin/events/{event['id']}/complete")).json()
    assert done["status"] == "completed" and done["match_count"] == 2 and done["completed_match_count"] == 1
    assert (await a.patch(f"/api/v1/admin/events/{event['id']}", json={"name": "Renamed"})).status_code == 422

    audit = (await a.get("/api/v1/admin/audit-logs", params={"action": "result."})).json()["items"]
    assert {e["action"] for e in audit} == {"result.declared", "result.corrected"}


async def test_cancelling_event_cancels_open_matches(app, tenant) -> None:  # type: ignore[no-untyped-def]
    event = await _event(tenant)
    m = await _match(tenant, event["id"])
    assert (await tenant.admin.post(f"/api/v1/admin/events/{event['id']}/cancel")).json()["status"] == "cancelled"
    assert (await tenant.admin.get(f"/api/v1/admin/matches/{m['id']}")).json()["status"] == "cancelled"
    r = await tenant.admin.post(f"/api/v1/admin/events/{event['id']}/start")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_transition"


async def test_portal_only_shows_published_events(app, tenant) -> None:  # type: ignore[no-untyped-def]
    draft = await _event(tenant, name="Secret Draft")
    public = await _event(tenant, name="Public Derby")
    m = await _match(tenant, public["id"], meron_name="Thunder", wala_name="Storm")
    await tenant.admin.post(f"/api/v1/admin/events/{public['id']}/publish")
    await tenant.admin.post(f"/api/v1/admin/events/{public['id']}/start")
    await tenant.admin.post(f"/api/v1/admin/matches/{m['id']}/start")
    await tenant.admin.post(f"/api/v1/admin/matches/{m['id']}/result", json={"outcome": "draw"})

    await tenant.create_user("viewer", "player")
    p = await tenant.login("viewer")
    names = {e["name"] for e in (await p.get("/api/v1/portal/events")).json()["items"]}
    assert names == {"Public Derby"}
    assert (await p.get(f"/api/v1/portal/events/{draft['id']}")).status_code == 404
    matches = (await p.get(f"/api/v1/portal/events/{public['id']}/matches")).json()
    assert matches[0]["meron_name"] == "Thunder" and matches[0]["result"]["outcome"] == "draw"
    results = (await p.get("/api/v1/portal/results")).json()["items"]
    assert [r["event_name"] for r in results] == ["Public Derby"]
    dash = (await p.get("/api/v1/portal/dashboard")).json()
    assert dash["latest_results"][0]["id"] == m["id"]
    assert "no monetary value" in dash["points_disclaimer"]


async def test_event_validation(app, tenant) -> None:  # type: ignore[no-untyped-def]
    r = await tenant.admin.post(
        "/api/v1/admin/events",
        json={"name": "Bad", "starts_at": "2030-01-02T00:00:00+00:00", "ends_at": "2030-01-01T00:00:00+00:00"},
    )
    assert r.status_code == 422
    r = await tenant.admin.post("/api/v1/admin/events", json={"name": "Naive", "starts_at": "2030-01-02T00:00:00"})
    assert r.status_code == 422


async def test_reports_and_dashboard(app, tenant) -> None:  # type: ignore[no-untyped-def]
    event = await _event(tenant, starts_at="2026-10-20T09:00:00+08:00")
    await _match(tenant, event["id"])
    dash = (await tenant.admin.get("/api/v1/admin/dashboard")).json()
    assert dash["users_by_type"]["admin"] == 1
    assert dash["events_by_status"]["draft"] >= 1
    report = (await tenant.admin.get("/api/v1/admin/reports/activity")).json()
    assert len(report["rows"]) == 30 and report["totals"]["logins"] >= 1
    matches = (await tenant.admin.get("/api/v1/admin/reports/matches", params={"from": "2026-10-01", "to": "2026-10-31"})).json()
    assert matches["rows"][0]["matches"] == 1
    csv = await tenant.admin.get("/api/v1/admin/reports/points", params={"format": "csv"})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("user_id,username")
