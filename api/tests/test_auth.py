import uuid

import httpx

from app.core.config import get_settings
from tests.conftest import PASSWORD, client_for, unique


async def test_login_rejects_bad_password_and_locks_out(app, tenant) -> None:  # type: ignore[no-untyped-def]
    await tenant.create_user("lockme", "player")
    async with client_for(app, tenant.host) as c:
        for _ in range(5):
            r = await c.post("/api/v1/auth/login", json={"username": "lockme", "password": "wrong-password"})
            assert r.status_code == 401
            assert r.json()["error"]["code"] == "invalid_credentials"
        # Even the correct password is refused while locked.
        r = await c.post("/api/v1/auth/login", json={"username": "lockme", "password": PASSWORD})
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "login_locked"
        assert int(r.headers["Retry-After"]) > 0


async def test_unknown_user_gets_same_error_as_bad_password(app, tenant) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app, tenant.host) as c:
        r = await c.post("/api/v1/auth/login", json={"username": "ghost", "password": PASSWORD})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


async def test_login_by_email_and_profile(app, tenant) -> None:  # type: ignore[no-untyped-def]
    await tenant.create_user("emailer", "player", email="emailer@example.com")
    c = await tenant.login("EMAILER@example.com")
    me = (await c.get("/api/v1/auth/me")).json()
    assert me["username"] == "emailer"
    assert me["account_type"] == "player"
    assert me["tenant"]["slug"] == tenant.slug
    r = await c.patch("/api/v1/auth/me", json={"full_name": "Renamed Player", "phone": "+63 900 000 0000"})
    assert r.status_code == 200 and r.json()["full_name"] == "Renamed Player"


async def test_refresh_rotation_and_reuse_detection(app, tenant) -> None:  # type: ignore[no-untyped-def]
    await tenant.create_user("rotator", "player")
    async with client_for(app, tenant.host) as c:
        r = await c.post("/api/v1/auth/login", json={"username": "rotator", "password": PASSWORD})
        assert r.status_code == 200
        first_refresh = c.cookies.get("esb_rt")
        access = r.json()["access_token"]
        assert first_refresh

        r = await c.post("/api/v1/auth/refresh")
        assert r.status_code == 200, r.text
        second_refresh = c.cookies.get("esb_rt")
        assert second_refresh and second_refresh != first_refresh

        # Replaying the rotated token from another client is treated as theft: the whole family dies.
        c.cookies.set("esb_rt", first_refresh, domain=tenant.host, path="/api/v1/auth")
        r = await c.post("/api/v1/auth/refresh", headers={"User-Agent": "replaying-client/1.0"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "refresh_token_reused"

        c.cookies.set("esb_rt", second_refresh, domain=tenant.host, path="/api/v1/auth")
        r = await c.post("/api/v1/auth/refresh")
        assert r.status_code == 401
        r = await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "session_revoked"


async def test_refresh_requires_csrf_header(app, tenant) -> None:  # type: ignore[no-untyped-def]
    await tenant.create_user("csrf", "player")
    async with client_for(app, tenant.host) as c:
        await c.post("/api/v1/auth/login", json={"username": "csrf", "password": PASSWORD})
        r = await c.post("/api/v1/auth/refresh", headers={"X-Requested-With": ""})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "csrf_check_failed"


async def test_logout_revokes_access_token(app, tenant) -> None:  # type: ignore[no-untyped-def]
    await tenant.create_user("leaver", "player")
    async with client_for(app, tenant.host) as c:
        token = (await c.post("/api/v1/auth/login", json={"username": "leaver", "password": PASSWORD})).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"
        assert (await c.post("/api/v1/auth/logout")).status_code == 200
        assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_temporary_password_must_be_changed(app, tenant) -> None:  # type: ignore[no-untyped-def]
    r = await tenant.admin.post(
        "/api/v1/admin/users", json={"username": "newbie", "full_name": "Newbie", "account_type": "player"}
    )
    assert r.status_code == 201
    temp = r.json()["temporary_password"]
    assert temp and r.json()["user"]["must_change_password"] is True
    c = await tenant.login("newbie", temp)
    r = await c.get("/api/v1/portal/dashboard")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "password_change_required"
    assert (await c.post("/api/v1/auth/me/password", json={"current_password": temp, "new_password": "weak"})).status_code == 422
    assert (
        await c.post("/api/v1/auth/me/password", json={"current_password": temp, "new_password": PASSWORD})
    ).status_code == 200
    assert (await c.get("/api/v1/portal/dashboard")).status_code == 200


async def test_token_audiences_are_not_interchangeable(app, tenant, owner) -> None:  # type: ignore[no-untyped-def]
    platform_token = owner.client.headers["Authorization"]
    async with client_for(app, tenant.host) as c:
        r = await c.get("/api/v1/auth/me", headers={"Authorization": platform_token})
    assert r.status_code == 401

    tenant_token = tenant.admin.headers["Authorization"]
    async with client_for(app, "owner.test.local") as c:
        r = await c.get("/api/v1/platform/tenants", headers={"Authorization": tenant_token})
    assert r.status_code == 401


async def test_self_registration_respects_setting(app, tenant) -> None:  # type: ignore[no-untyped-def]
    body = {"username": unique("reg"), "full_name": "Registrant", "password": PASSWORD}
    async with client_for(app, tenant.host) as c:
        r = await c.post("/api/v1/auth/register", json=body)
        assert r.status_code == 403 and r.json()["error"]["code"] == "registration_closed"
        assert (await tenant.admin.put("/api/v1/admin/settings", json={"allow_self_registration": True})).status_code == 200
        r = await c.post("/api/v1/auth/register", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["user"]["account_type"] == "player"
        r = await c.post("/api/v1/auth/register", json=body)
        assert r.status_code == 409


async def test_referral_link_registers_under_the_agent_even_when_open_registration_is_off(app, tenant) -> None:  # type: ignore[no-untyped-def]
    master = await tenant.create_user("RefMaster", "master_agent")
    agent = await tenant.create_user("refagent", "agent", parent_id=master["id"])
    player = await tenant.create_user("refplayer", "player")

    async def register(ref: str | None):  # type: ignore[no-untyped-def]
        body = {"username": unique("ref"), "full_name": "Referred", "password": PASSWORD, "ref": ref}
        async with client_for(app, tenant.host) as c:
            return await c.post("/api/v1/auth/register", json=body)

    async def parent_of(response) -> str | None:  # type: ignore[no-untyped-def]
        assert response.status_code == 201, response.text
        user_id = response.json()["user"]["id"]
        return (await tenant.admin.get(f"/api/v1/admin/users/{user_id}")).json()["parent_id"]

    # Open self-registration is off: only a valid referral link registers.
    closed = await register(None)
    assert closed.status_code == 403 and closed.json()["error"]["code"] == "registration_closed"
    # Referral codes are usernames, matched without regard to case.
    assert await parent_of(await register("refmaster")) == master["id"]
    assert await parent_of(await register("refagent")) == agent["id"]
    # The new player sees their agent; the master agent sees the player in their network, marked as referred.
    referred = await register("RefMaster")
    profile = referred.json()["user"]
    assert (profile["parent_username"], profile["parent_account_type"]) == ("RefMaster", "master_agent")
    master_client = await tenant.login("RefMaster")
    listed = (await master_client.get("/api/v1/admin/users", params={"account_type": "player"})).json()["items"]
    row = next(u for u in listed if u["username"] == profile["username"])
    assert row["via_referral"] is True and row["parent_username"] == "RefMaster"
    assert all(u["username"] != "refplayer" for u in listed)  # not in this master agent's network
    assert (await tenant.admin.put("/api/v1/admin/settings", json={"allow_self_registration": True})).status_code == 200
    assert await parent_of(await register(None)) is None
    assert await parent_of(await register("")) is None
    # Only an active master agent or agent can refer: anything else is refused, not silently unassigned.
    for bad in ("nobody", player["username"]):
        r = await register(bad)
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_referral"
    assert (await tenant.admin.post(f"/api/v1/admin/users/{agent['id']}/suspend")).status_code == 200
    r = await register("refagent")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_referral"


async def test_agent_application_link_creates_an_agent_under_the_master_agent(app, tenant) -> None:  # type: ignore[no-untyped-def]
    master = await tenant.create_user("AppMaster", "master_agent")
    agent = await tenant.create_user("appagent", "agent", parent_id=master["id"])

    async def apply(ref: str):  # type: ignore[no-untyped-def]
        body = {"username": unique("app"), "full_name": "Applicant", "password": PASSWORD, "ref": ref, "apply": "agent"}
        async with client_for(app, tenant.host) as c:
            return await c.post("/api/v1/auth/register", json=body)

    r = await apply("appmaster")
    assert r.status_code == 201, r.text
    user = r.json()["user"]
    assert (user["account_type"], user["parent_username"]) == ("agent", "AppMaster")
    assert "users.view_downline" in user["permissions"]  # the agent role, not the player role
    # An agent's link cannot create agents, and an application needs a referrer at all.
    for ref in ("appagent", ""):
        bad = await apply(ref)
        assert bad.status_code in (403, 422)
    assert agent


async def test_suspended_user_cannot_sign_in(app, tenant) -> None:  # type: ignore[no-untyped-def]
    user = await tenant.create_user("suspendme", "player")
    c = await tenant.login("suspendme")
    assert (await tenant.admin.post(f"/api/v1/admin/users/{user['id']}/suspend")).status_code == 200
    assert (await c.get("/api/v1/auth/me")).status_code == 401
    async with client_for(app, tenant.host) as fresh:
        r = await fresh.post("/api/v1/auth/login", json={"username": "suspendme", "password": PASSWORD})
    assert r.status_code == 403 and r.json()["error"]["code"] == "account_suspended"


def _client_from(app, host: str, ip: str):  # type: ignore[no-untyped-def]
    transport = httpx.ASGITransport(app=app, client=(ip, 40000))
    return httpx.AsyncClient(transport=transport, base_url=f"http://{host}", headers={"X-Requested-With": "XMLHttpRequest"})


async def test_password_spraying_from_one_address_is_throttled(app, tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Rotating usernames dodges the per-account lockout; the per-address counter still stops it."""
    monkeypatch.setattr(get_settings(), "login_ip_max_failures", 3)
    await tenant.create_user("sprayed", "player")
    ip = f"203.0.113.{uuid.uuid4().int % 250 + 1}"
    async with _client_from(app, tenant.host, ip) as c:
        for i in range(3):
            r = await c.post("/api/v1/auth/login", json={"username": f"guess{i}", "password": "wrong-password"})
            assert r.status_code == 401
        # A valid password for a different, never-failed account is refused from this address too.
        r = await c.post("/api/v1/auth/login", json={"username": "sprayed", "password": PASSWORD})
        assert r.status_code == 429 and r.json()["error"]["code"] == "login_rate_limited"
        assert int(r.headers["Retry-After"]) > 0
        # The platform console shares the address counter.
        async with _client_from(app, get_settings().platform_host, ip) as p:
            r = await p.post("/api/v1/platform/auth/login", json={"email": "x@example.com", "password": "wrong-password"})
            assert r.status_code == 429
    # Other addresses are unaffected.
    async with _client_from(app, tenant.host, "198.51.100.7") as other:
        assert (await other.post("/api/v1/auth/login", json={"username": "sprayed", "password": PASSWORD})).status_code == 200


async def test_self_registration_is_throttled_per_address(app, tenant, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "register_ip_max_per_hour", 2)
    assert (await tenant.admin.put("/api/v1/admin/settings", json={"allow_self_registration": True})).status_code == 200
    ip = f"192.0.2.{uuid.uuid4().int % 250 + 1}"
    async with _client_from(app, tenant.host, ip) as c:
        for _ in range(2):
            body = {"username": unique("bulk"), "full_name": "Registrant", "password": PASSWORD}
            assert (await c.post("/api/v1/auth/register", json=body)).status_code == 201
        body = {"username": unique("bulk"), "full_name": "Registrant", "password": PASSWORD}
        r = await c.post("/api/v1/auth/register", json=body)
        assert r.status_code == 429 and r.json()["error"]["code"] == "registration_rate_limited"
