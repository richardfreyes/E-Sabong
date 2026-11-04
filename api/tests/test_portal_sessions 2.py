"""Admin and player portals share one origin (one cookie jar) but must keep separate sessions.

Each client below models ONE browser: both portals use the same httpx cookie jar, exactly as
the /admin/ and / apps do on a tenant host.
"""

import asyncio
from datetime import timedelta

from sqlalchemy import select, update

from app.core.database import platform_session
from app.core.security import sha256_hex, utcnow
from app.modules.auth.models import AuthSession
from tests.conftest import PASSWORD, client_for

ADMIN = {"X-Portal": "admin"}
PLAYER = {"X-Portal": "player"}


async def _login(c, username: str, portal: dict[str, str]) -> None:  # type: ignore[no-untyped-def]
    r = await c.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD}, headers=portal)
    assert r.status_code == 200, r.text


async def _who(c, portal: dict[str, str]) -> str | None:  # type: ignore[no-untyped-def]
    """Refresh as the portal does on page load and return the restored username."""
    r = await c.post("/api/v1/auth/refresh", headers=portal)
    if r.status_code != 200:
        return None
    me = await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.status_code == 200, me.text
    return me.json()["username"]


async def test_admin_and_player_sessions_are_independent_in_one_browser(app, tenant) -> None:  # type: ignore[no-untyped-def]
    await tenant.create_user("viewer1", "player")
    async with client_for(app, tenant.host) as browser:
        await _login(browser, "admin", ADMIN)
        await _login(browser, "viewer1", PLAYER)
        assert browser.cookies.get("esb_rt_admin") and browser.cookies.get("esb_rt_player")
        assert browser.cookies.get("esb_rt") is None
        # The reported bug: reloading the admin portal restored the player's session.
        assert await _who(browser, ADMIN) == "admin"
        assert await _who(browser, PLAYER) == "viewer1"
        assert await _who(browser, ADMIN) == "admin"

        # Signing out of the player portal ends only the player session.
        assert (await browser.post("/api/v1/auth/logout", headers=PLAYER)).status_code == 200
        assert await _who(browser, PLAYER) is None
        assert await _who(browser, ADMIN) == "admin"

        await _login(browser, "viewer1", PLAYER)
        assert (await browser.post("/api/v1/auth/logout", headers=ADMIN)).status_code == 200
        assert await _who(browser, ADMIN) is None
        assert await _who(browser, PLAYER) == "viewer1"


async def test_ending_one_portal_session_does_not_touch_the_other(app, tenant) -> None:  # type: ignore[no-untyped-def]
    await tenant.create_user("viewer2", "player")
    async with client_for(app, tenant.host) as browser:
        await _login(browser, "admin", ADMIN)
        await _login(browser, "viewer2", PLAYER)
        admin_token = browser.cookies.get("esb_rt_admin")
        async with platform_session() as db:
            family = await db.scalar(select(AuthSession.family_id).where(AuthSession.token_hash == sha256_hex(admin_token)))
            await db.execute(update(AuthSession).where(AuthSession.family_id == family).values(expires_at=utcnow()))
        assert await _who(browser, ADMIN) is None
        assert await _who(browser, PLAYER) == "viewer2"


async def test_unknown_portal_and_cross_tenant_cookies_are_rejected(app, tenant, other_tenant) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app, tenant.host) as browser:
        r = await browser.post(
            "/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}, headers={"X-Portal": "owner"}
        )
        assert r.status_code == 422
        await _login(browser, "admin", ADMIN)
        stolen = browser.cookies.get("esb_rt_admin")
    async with client_for(app, other_tenant.host) as elsewhere:
        elsewhere.cookies.set("esb_rt_admin", stolen, domain=other_tenant.host, path="/api/v1/auth")
        r = await elsewhere.post("/api/v1/auth/refresh", headers=ADMIN)
        assert r.status_code == 401


async def test_player_credentials_stay_forbidden_on_admin_endpoints(app, tenant) -> None:  # type: ignore[no-untyped-def]
    await tenant.create_user("viewer3", "player")
    async with client_for(app, tenant.host) as browser:
        r = await browser.post("/api/v1/auth/login", json={"username": "viewer3", "password": PASSWORD}, headers=PLAYER)
        token = r.json()["access_token"]
        denied = await browser.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token}"})
        assert denied.status_code == 403


async def test_parallel_refresh_from_two_tabs_keeps_the_session(app, tenant) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app, tenant.host) as browser:
        await _login(browser, "admin", ADMIN)
        for _ in range(3):
            first, second = await asyncio.gather(
                browser.post("/api/v1/auth/refresh", headers=ADMIN), browser.post("/api/v1/auth/refresh", headers=ADMIN)
            )
            assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
        assert await _who(browser, ADMIN) == "admin"


async def test_refresh_reuse_outside_the_grace_window_or_after_logout_still_revokes(app, tenant) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app, tenant.host) as browser:
        await _login(browser, "admin", ADMIN)
        rotated = browser.cookies.get("esb_rt_admin")
        assert await _who(browser, ADMIN) == "admin"
        live = browser.cookies.get("esb_rt_admin")
        async with platform_session() as db:
            await db.execute(
                update(AuthSession)
                .where(AuthSession.token_hash == sha256_hex(rotated))
                .values(revoked_at=utcnow() - timedelta(seconds=11))
            )
        browser.cookies.set("esb_rt_admin", rotated, domain=tenant.host, path="/api/v1/auth")
        r = await browser.post("/api/v1/auth/refresh", headers=ADMIN)
        assert r.status_code == 401 and r.json()["error"]["code"] == "refresh_token_reused"
        browser.cookies.set("esb_rt_admin", live, domain=tenant.host, path="/api/v1/auth")
        assert await _who(browser, ADMIN) is None

        # Within the window, a token whose family was signed out is not brought back.
        await _login(browser, "admin", ADMIN)
        rotated = browser.cookies.get("esb_rt_admin")
        assert await _who(browser, ADMIN) == "admin"
        assert (await browser.post("/api/v1/auth/logout", headers=ADMIN)).status_code == 200
        browser.cookies.set("esb_rt_admin", rotated, domain=tenant.host, path="/api/v1/auth")
        assert (await browser.post("/api/v1/auth/refresh", headers=ADMIN)).status_code == 401


async def test_admin_portal_refuses_accounts_without_administration_access(app, tenant) -> None:  # type: ignore[no-untyped-def]
    await tenant.create_user("viewer4", "player")
    async with client_for(app, tenant.host) as browser:
        await _login(browser, "admin", ADMIN)
        admin_cookie = browser.cookies.get("esb_rt_admin")
        r = await browser.post("/api/v1/auth/login", json={"username": "viewer4", "password": PASSWORD}, headers=ADMIN)
        assert r.status_code == 403 and r.json()["error"]["code"] == "portal_not_permitted"
        # No session was issued and the admin portal's own session is untouched.
        assert browser.cookies.get("esb_rt_admin") == admin_cookie
        assert await _who(browser, ADMIN) == "admin"
        # The same account signs in to the player portal as before.
        await _login(browser, "viewer4", PLAYER)
        assert await _who(browser, PLAYER) == "viewer4"
        r = await browser.post(
            "/api/v1/auth/register",
            json={"username": "selfreg", "full_name": "Self Reg", "password": PASSWORD},
            headers=ADMIN,
        )
        assert r.status_code == 403


async def test_the_portal_header_never_widens_access(app, tenant) -> None:  # type: ignore[no-untyped-def]
    """X-Portal only chooses which refresh cookie to use; every request is still authorized
    by the account's own permissions."""
    await tenant.create_user("viewer6", "player")
    async with client_for(app, tenant.host) as browser:
        await _login(browser, "viewer6", PLAYER)
        token = (await browser.post("/api/v1/auth/refresh", headers=PLAYER)).json()["access_token"]
        for headers in ({}, ADMIN):
            r = await browser.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token}", **headers})
            assert r.status_code == 403, headers
        # Claiming to be the admin portal does not turn the player's cookie into an admin session.
        assert (await browser.post("/api/v1/auth/refresh", headers=ADMIN)).status_code == 401
