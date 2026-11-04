"""Real PostgreSQL, Redis, RBAC, assignment, approval, amendment and official projections."""

import asyncio
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.core.database import platform_session, tenant_session
from app.modules.users.models import Role, RolePermission
from tests.conftest import legacy_points_rows
from tests.test_events import _event, _match


async def setup_desk(tenant):
    user = await tenant.create_user("judge", "declarator")
    event = await _event(tenant)
    match = await _match(tenant, event["id"])
    for path in [f"events/{event['id']}/publish", f"events/{event['id']}/start", f"matches/{match['id']}/start"]:
        assert (await tenant.admin.post("/api/v1/admin/" + path)).status_code == 200
    assert (await tenant.admin.put(f"/api/v1/declarator/events/{event['id']}/assignments/{user['id']}")).status_code == 200
    return user, event, match, await tenant.login("judge")


def proposal(outcome="meron", **kwargs):
    return {"request_id": str(uuid.uuid4()), "outcome": outcome, **kwargs}


@pytest.mark.parametrize("outcome", ["meron", "wala", "draw", "no_contest", "cancelled"])
async def test_declarator_result_is_final_without_approval(tenant, outcome):
    user, event, match, judge = await setup_desk(tenant)
    base = f"/api/v1/declarator/matches/{match['id']}"
    points_before = await legacy_points_rows(tenant.id)  # retired archive: results must not add rows
    r = await judge.post(base + "/declarations", json=proposal(outcome, reason="Official decision"))
    assert r.status_code == 200, r.text
    decl = r.json()
    assert decl["status"] == "finalized" and decl["declared_by"] == "judge" and decl["approved_by"] is None
    assert decl["finalized_at"]
    visible = (await judge.get(f"/api/v1/portal/matches/{match['id']}")).json()
    assert visible["status"] == ("cancelled" if outcome == "cancelled" else "completed")
    if outcome != "cancelled":
        assert visible["result"]["outcome"] == outcome
        assert (await tenant.admin.get(f"/api/v1/admin/matches/{match['id']}")).json()["result"]["outcome"] == outcome
    assert await legacy_points_rows(tenant.id) == points_before
    history = (await judge.get(base + "/history")).json()
    assert [a["action"] for a in history["audit"]] == ["declared", "finalized"]
    rows = (await judge.get("/api/v1/declarator/records", params={"q": "1", "winner": outcome, "status": "finalized"})).json()
    assert rows["total"] == 1 and rows["items"][0]["official_winner"] == outcome
    # Nothing waits for approval, and the admin result endpoints cannot override the Declarator.
    queue = (await tenant.admin.get("/api/v1/declarator/records", params={"status": "pending_approval"})).json()
    assert queue["total"] == 0
    assert (await tenant.admin.post(f"/api/v1/declarator/declarations/{decl['id']}/approve")).json()["status"] == "finalized"
    assert (
        await tenant.admin.put(f"/api/v1/admin/matches/{match['id']}/result", json={"outcome": "wala", "reason": "Review"})
    ).status_code == 409


async def test_idempotent_retries_and_concurrent_declarations(tenant):
    _, _, match, judge = await setup_desk(tenant)
    path = f"/api/v1/declarator/matches/{match['id']}/declarations"
    body = proposal()
    first = await judge.post(path, json=body)
    duplicate = await judge.post(path, json=body)
    assert first.status_code == duplicate.status_code == 200
    assert first.json()["id"] == duplicate.json()["id"]
    # The result is already official: a second first-result is stale, a reused key with another outcome conflicts.
    assert (await judge.post(path, json=proposal("wala"))).status_code == 409
    assert (await judge.post(path, json={**body, "outcome": "wala"})).status_code == 409
    history = (await judge.get(f"/api/v1/declarator/matches/{match['id']}/history")).json()
    assert len(history["revisions"]) == 1


async def test_concurrent_first_declarations_publish_exactly_one_result(tenant):
    _, _, match, judge = await setup_desk(tenant)
    path = f"/api/v1/declarator/matches/{match['id']}/declarations"
    responses = await asyncio.gather(judge.post(path, json=proposal("meron")), judge.post(path, json=proposal("wala")))
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    history = (await judge.get(f"/api/v1/declarator/matches/{match['id']}/history")).json()
    assert len(history["revisions"]) == 1


async def test_amendments_are_final_and_audit_reason(tenant):
    _, _, match, judge = await setup_desk(tenant)
    base = f"/api/v1/declarator/matches/{match['id']}"
    assert (await judge.post(base + "/declarations", json=proposal())).json()["status"] == "finalized"
    assert (await judge.post(base + "/declarations", json=proposal("wala", base_revision=1))).status_code == 422
    d = (
        await judge.post(base + "/declarations", json=proposal("wala", base_revision=1, reason="Independent video review"))
    ).json()
    assert d["status"] == "finalized"
    assert (await judge.get(f"/api/v1/portal/matches/{match['id']}")).json()["result"]["outcome"] == "wala"
    history = (await judge.get(base + "/history")).json()
    assert [(r["revision"], r["outcome"]) for r in history["revisions"]] == [(1, "meron"), (2, "wala")]
    assert history["revisions"][1]["reason"] == "Independent video review"
    assert (await judge.post(base + "/declarations", json=proposal("draw", base_revision=1, reason="Stale"))).status_code == 409


async def test_assignment_permissions_and_tenant_isolation(tenant, other_tenant):
    user, event, match, judge = await setup_desk(tenant)
    await tenant.create_user("viewer", "player")
    viewer = await tenant.login("viewer")
    assert (await viewer.get("/api/v1/declarator/events")).status_code == 403
    assert (await viewer.post(f"/api/v1/declarator/matches/{match['id']}/declarations", json=proposal())).status_code == 403
    unassigned = await _event(tenant)
    await _match(tenant, unassigned["id"])
    assert (await judge.get(f"/api/v1/declarator/events/{unassigned['id']}/monitor")).status_code == 404
    foreign = await _event(other_tenant)
    foreign_match = await _match(other_tenant, foreign["id"])
    assert (
        await judge.post(f"/api/v1/declarator/matches/{foreign_match['id']}/declarations", json=proposal())
    ).status_code == 404
    assert (await other_tenant.admin.get(f"/api/v1/declarator/events/{event['id']}/monitor")).status_code == 404
    # A revoked assignment cannot declare (the result is final, so the check happens at submission).
    await tenant.admin.delete(f"/api/v1/declarator/events/{event['id']}/assignments/{user['id']}")
    r = await judge.post(f"/api/v1/declarator/matches/{match['id']}/declarations", json=proposal())
    assert r.status_code in (403, 404), r.text
    assert (await tenant.admin.get(f"/api/v1/admin/matches/{match['id']}")).json()["result"] is None


async def test_declarator_role_is_reserved_for_declarator_accounts(tenant):
    roles = {r["name"]: r["id"] for r in (await tenant.admin.get("/api/v1/admin/roles")).json()}
    player = await tenant.create_user("lookalike", "player")
    # Reproduces the reported account: a player whose role was later switched to Declarator.
    r = await tenant.admin.patch(f"/api/v1/admin/users/{player['id']}", json={"role_ids": [roles["declarator"]]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_role", r.text
    r = await tenant.admin.post(
        "/api/v1/admin/users",
        json={"username": "lookalike2", "full_name": "Lookalike", "account_type": "operator", "role_ids": [roles["declarator"]]},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_role", r.text
    declarator = await tenant.create_user("judge2", "declarator", role_ids=[roles["declarator"]])
    assert [x["name"] for x in declarator["roles"]] == ["declarator"]
    judge = await tenant.login("judge2")
    assert (await judge.get("/api/v1/declarator/events")).json() == []


async def test_self_approval_forbidden_even_with_approver_permission(tenant):
    _, _, match, judge = await setup_desk(tenant)
    async with platform_session() as db:
        role = (await db.execute(select(Role).where(Role.tenant_id == tenant.id, Role.name == "declarator"))).scalar_one()
        db.add(RolePermission(tenant_id=tenant.id, role_id=role.id, permission="results.approve"))
    d = (await judge.post(f"/api/v1/declarator/matches/{match['id']}/declarations", json=proposal())).json()
    r = await judge.post(f"/api/v1/declarator/declarations/{d['id']}/approve")
    assert r.status_code == 403 and r.json()["error"]["code"] == "self_approval"


async def test_new_tables_have_rls_and_append_only_audit(tenant, other_tenant):
    _, event, _, _ = await setup_desk(other_tenant)
    async with tenant_session(tenant.id) as db:
        assert (await db.execute(text("SELECT count(*) FROM event_declarators"))).scalar_one() == 0
    with pytest.raises(DBAPIError):
        async with tenant_session(tenant.id) as db:
            await db.execute(
                text("INSERT INTO event_declarators (tenant_id,event_id,user_id,assigned_by_id) VALUES (:tid,:eid,:uid,:uid)"),
                {"tid": other_tenant.id, "eid": event["id"], "uid": other_tenant.admin_id},
            )
    for sql in ["UPDATE declaration_audit SET action=action", "DELETE FROM declaration_audit"]:
        with pytest.raises(ProgrammingError):
            async with tenant_session(tenant.id) as db:
                await db.execute(text(sql))


async def test_final_result_is_broadcast_without_an_approval_request(app, tenant):
    import json

    from httpx_ws import aconnect_ws

    from tests.test_websocket import _receive, ws_client

    _, _, match, judge = await setup_desk(tenant)
    await tenant.create_user("spectator", "player")
    spectator = await tenant.login("spectator")
    admin_token = tenant.admin.headers["Authorization"].split()[1]
    viewer_token = spectator.headers["Authorization"].split()[1]
    async with ws_client(app, tenant.host) as admin_socket, ws_client(app, tenant.host) as viewer_socket:
        async with (
            aconnect_ws(f"/api/ws?token={admin_token}", admin_socket) as admin_ws,
            aconnect_ws(f"/api/ws?token={viewer_token}", viewer_socket) as viewer_ws,
        ):
            await _receive(admin_ws)
            await _receive(viewer_ws)
            await asyncio.sleep(0.2)
            r = await judge.post(f"/api/v1/declarator/matches/{match['id']}/declarations", json=proposal("wala"))
            assert r.status_code == 200 and r.json()["status"] == "finalized"
            frames = []
            while "match.result_declared" not in [f["type"] for f in frames]:
                frames.append(await _receive(viewer_ws))
            final = next(f for f in frames if f["type"] == "match.result_declared")
            assert final["data"]["result"] == {"outcome": "wala", "revision": 1}
            admin_frames = []
            while "declaration.updated" not in [f["type"] for f in admin_frames]:
                admin_frames.append(await _receive(admin_ws))
            assert "notification.created" not in [f["type"] for f in admin_frames]
            await viewer_ws.send_text(json.dumps({"type": "ping"}))
            while (await _receive(viewer_ws))["type"] != "pong":
                pass


async def test_monitor_advances_on_canonical_start_and_refresh_preserves_state(tenant):
    _, event, match, judge = await setup_desk(tenant)
    following = await _match(tenant, event["id"])
    base = f"/api/v1/declarator/events/{event['id']}/monitor"
    assert (await judge.get(base)).json()["current"]["match"]["id"] == match["id"]
    d = (await judge.post(f"/api/v1/declarator/matches/{match['id']}/declarations", json=proposal())).json()
    await tenant.admin.post(f"/api/v1/declarator/declarations/{d['id']}/approve")
    assert (await judge.get(base)).json()["current"]["match"]["id"] == match["id"]
    await tenant.admin.post(f"/api/v1/admin/matches/{following['id']}/start")
    assert (await judge.get(base)).json()["current"]["match"]["id"] == following["id"]
    assert len((await judge.get(base)).json()["matches"]) == 2


async def test_revoking_all_assignments_cannot_bypass_verification(tenant):
    user, event, match, _ = await setup_desk(tenant)
    await tenant.admin.delete(f"/api/v1/declarator/events/{event['id']}/assignments/{user['id']}")
    r = await tenant.admin.post(f"/api/v1/admin/matches/{match['id']}/result", json={"outcome": "meron"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "verification_required"
    await tenant.create_user("operator", "operator")
    operator = await tenant.login("operator")
    r = await operator.post(f"/api/v1/admin/events/{event['id']}/cancel")
    assert r.status_code == 403 and r.json()["error"]["code"] == "approval_required"
