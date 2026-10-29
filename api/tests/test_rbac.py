"""Role-based access control and hierarchy scoping."""

from tests.conftest import PASSWORD


async def _network(tenant):  # type: ignore[no-untyped-def]
    master = await tenant.create_user("master", "master_agent")
    agent = await tenant.create_user("agent", "agent", parent_id=master["id"])
    player = await tenant.create_user("player", "player", parent_id=agent["id"])
    other_master = await tenant.create_user("othermaster", "master_agent")
    other_agent = await tenant.create_user("otheragent", "agent", parent_id=other_master["id"])
    return master, agent, player, other_master, other_agent


async def test_player_has_no_admin_access(app, tenant) -> None:  # type: ignore[no-untyped-def]
    await tenant.create_user("plyr1", "player")
    c = await tenant.login("plyr1")
    for path in ("/api/v1/admin/users", "/api/v1/admin/events", "/api/v1/admin/dashboard", "/api/v1/admin/audit-logs"):
        r = await c.get(path)
        assert r.status_code == 403, path
        assert r.json()["error"]["code"] == "permission_denied"
    assert (await c.get("/api/v1/portal/dashboard")).status_code == 200


async def test_operator_runs_events_but_not_users(app, tenant) -> None:  # type: ignore[no-untyped-def]
    await tenant.create_user("oper", "operator")
    c = await tenant.login("oper")
    r = await c.post("/api/v1/admin/events", json={"name": "Op Event", "starts_at": "2030-01-01T10:00:00+00:00"})
    assert r.status_code == 201
    assert (await c.get("/api/v1/admin/users")).status_code == 403
    assert (
        await c.post(
            "/api/v1/admin/points/allocations",
            json={"user_id": tenant.admin_id.hex, "amount": 1},
            headers={"Idempotency-Key": "op-alloc-0001"},
        )
    ).status_code == 403


async def test_hierarchy_rules_enforced(app, tenant) -> None:  # type: ignore[no-untyped-def]
    master = await tenant.create_user("mstr", "master_agent")
    player = await tenant.create_user("plyr", "player")
    bad = [
        {"username": "agt1", "account_type": "agent"},  # agent needs a parent
        {"username": "agt2", "account_type": "agent", "parent_id": player["id"]},  # not under a player
        {"username": "mst2", "account_type": "master_agent", "parent_id": master["id"]},  # masters are top level
        {"username": "ply2", "account_type": "player", "parent_id": player["id"]},  # players have no downline
    ]
    for body in bad:
        r = await tenant.admin.post("/api/v1/admin/users", json={"full_name": "x", "password": PASSWORD, **body})
        assert r.status_code == 422, body
        assert r.json()["error"]["code"] == "invalid_parent"


async def test_agents_only_see_and_manage_their_downline(app, tenant) -> None:  # type: ignore[no-untyped-def]
    master, agent, player, other_master, other_agent = await _network(tenant)
    mc = await tenant.login("master")
    ac = await tenant.login("agent")

    visible = {u["username"] for u in (await mc.get("/api/v1/admin/users")).json()["items"]}
    assert visible == {"agent", "player"}
    assert {u["username"] for u in (await ac.get("/api/v1/admin/users")).json()["items"]} == {"player"}
    assert (await mc.get(f"/api/v1/admin/users/{other_agent['id']}")).status_code == 404
    assert (await ac.get(f"/api/v1/admin/users/{agent['id']}")).status_code == 404  # not their own downline

    # Master agents create agents and players beneath themselves only.
    r = await mc.post(
        "/api/v1/admin/users", json={"username": "agent2", "full_name": "A2", "account_type": "agent", "password": PASSWORD}
    )
    assert r.status_code == 201 and r.json()["user"]["parent_id"] == master["id"]
    r = await mc.post(
        "/api/v1/admin/users", json={"username": "mst3", "full_name": "M3", "account_type": "master_agent", "password": PASSWORD}
    )
    assert r.status_code == 403
    r = await mc.post(
        "/api/v1/admin/users", json={"username": "oper2", "full_name": "Op", "account_type": "operator", "password": PASSWORD}
    )
    assert r.status_code == 403
    r = await mc.post(
        "/api/v1/admin/users",
        json={
            "username": "stray",
            "full_name": "S",
            "account_type": "agent",
            "parent_id": other_master["id"],
            "password": PASSWORD,
        },
    )
    assert r.status_code == 403

    # Agents create players only.
    r = await ac.post(
        "/api/v1/admin/users", json={"username": "ply9", "full_name": "P9", "account_type": "player", "password": PASSWORD}
    )
    assert r.status_code == 201 and r.json()["user"]["parent_id"] == agent["id"]
    r = await ac.post(
        "/api/v1/admin/users", json={"username": "agt9", "full_name": "A9", "account_type": "agent", "password": PASSWORD}
    )
    assert r.status_code == 422  # an agent's parent must be a master agent

    # Hierarchy view is scoped.
    roots = {n["username"] for n in (await mc.get("/api/v1/admin/hierarchy")).json()}
    assert roots == {"agent", "agent2"}
    assert (await mc.get("/api/v1/admin/hierarchy", params={"parent_id": other_master["id"]})).status_code == 404


async def test_custom_role_limits_permissions(app, tenant) -> None:  # type: ignore[no-untyped-def]
    r = await tenant.admin.post("/api/v1/admin/roles", json={"name": "auditor", "permissions": ["dashboard.view", "audit.view"]})
    assert r.status_code == 201, r.text
    role_id = r.json()["id"]
    assert (
        await tenant.admin.post("/api/v1/admin/roles", json={"name": "bad", "permissions": ["root.everything"]})
    ).status_code == 422
    await tenant.create_user("auditor1", "operator", role_ids=[role_id])
    c = await tenant.login("auditor1")
    assert (await c.get("/api/v1/admin/audit-logs")).status_code == 200
    assert (
        await c.post("/api/v1/admin/events", json={"name": "Nope", "starts_at": "2030-01-01T10:00:00+00:00"})
    ).status_code == 403
    me = (await c.get("/api/v1/auth/me")).json()
    assert set(me["permissions"]) == {"dashboard.view", "audit.view"}
    assert (await tenant.admin.delete(f"/api/v1/admin/roles/{role_id}")).status_code == 409  # still assigned


async def test_system_admin_role_is_protected_and_last_admin_kept(app, tenant) -> None:  # type: ignore[no-untyped-def]
    roles = {r["name"]: r for r in (await tenant.admin.get("/api/v1/admin/roles")).json()}
    r = await tenant.admin.patch(f"/api/v1/admin/roles/{roles['tenant_admin']['id']}", json={"permissions": []})
    assert r.status_code == 422
    assert (await tenant.admin.delete(f"/api/v1/admin/roles/{roles['player']['id']}")).status_code == 422
    r = await tenant.admin.patch(f"/api/v1/admin/users/{tenant.admin_id}", json={"role_ids": [roles["operator"]["id"]]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "last_admin"


async def test_plan_user_limit(app, owner, plans) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import make_tenant

    small = await make_tenant(app, owner, plans["test-small"])  # max_users = 5, admin is #1
    for i in range(4):
        await small.create_user(f"user{i}", "player")
    r = await small.admin.post(
        "/api/v1/admin/users", json={"username": "user5", "full_name": "U5", "account_type": "player", "password": PASSWORD}
    )
    assert r.status_code == 402 and r.json()["error"]["code"] == "plan_limit_reached"
    # Module gating: the small plan has no points module.
    r = await small.admin.get("/api/v1/admin/points/summary")
    assert r.status_code == 403 and r.json()["error"]["code"] == "module_disabled"
    await small.admin.aclose()
