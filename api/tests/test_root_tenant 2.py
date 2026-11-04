"""Root-domain tenant (ROOT_TENANT_SLUG): the bare base domain serves one configured tenant next
to the owner console at /owner/, without widening tenant isolation or platform access."""

from collections.abc import Iterator

import pytest

from app.core.config import get_settings
from tests.conftest import PASSWORD, client_for, unique

ROOT_HOST = "test.local"  # BASE_DOMAIN in tests/conftest.py


@pytest.fixture
def root_tenant(monkeypatch: pytest.MonkeyPatch, tenant) -> Iterator[object]:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "root_tenant_slug", tenant.slug)
    yield tenant


async def test_bare_domain_serves_no_tenant_by_default(app) -> None:  # type: ignore[no-untyped-def]
    assert get_settings().root_tenant_slug is None
    async with client_for(app, ROOT_HOST) as c:
        r = await c.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 404 and r.json()["error"]["code"] == "tenant_not_found"


async def test_bare_domain_resolves_the_root_tenant(app, root_tenant) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app, ROOT_HOST) as c:
        assert (await c.get("/api/v1/public/tenant")).json()["slug"] == root_tenant.slug
        r = await c.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}, headers={"X-Portal": "admin"})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        assert (await c.get("/api/v1/auth/me")).json()["id"] == str(root_tenant.admin_id)
    # The tenant's own subdomain keeps working.
    async with client_for(app, root_tenant.host) as c:
        assert (await c.get("/api/v1/public/tenant")).json()["slug"] == root_tenant.slug


async def test_root_domain_keeps_tenant_isolation(app, root_tenant, other_tenant) -> None:  # type: ignore[no-untyped-def]
    # A token minted for another tenant is refused on the root domain, and vice versa.
    async with client_for(app, ROOT_HOST, token=other_tenant.admin.headers["Authorization"].split()[1]) as c:
        r = await c.get("/api/v1/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"
    async with client_for(app, ROOT_HOST) as c:
        r = await c.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        root_token = r.json()["access_token"]
    async with client_for(app, other_tenant.host, token=root_token) as c:
        assert (await c.get("/api/v1/auth/me")).status_code == 401
    # Tenant users cannot use the platform (owner) API on the shared origin.
    async with client_for(app, ROOT_HOST, token=root_token) as c:
        assert (await c.get("/api/v1/platform/tenants")).status_code == 401


async def test_owner_console_works_on_the_root_domain(app, monkeypatch, root_tenant, owner) -> None:  # type: ignore[no-untyped-def]
    # Contabo layout: PLATFORM_HOST == BASE_DOMAIN; the owner portal lives at /owner/.
    monkeypatch.setattr(get_settings(), "platform_host", ROOT_HOST)
    async with client_for(app, ROOT_HOST) as c:
        r = await c.post("/api/v1/platform/auth/login", json={"email": owner.email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        platform_token = r.json()["access_token"]
        # Platform and tenant refresh cookies are separate and path-scoped.
        cookies = {h.split("=", 1)[0]: h for h in r.headers.get_list("set-cookie")}
        assert "Path=/api/v1/platform/auth" in cookies["esb_prt"]
        # The root tenant still resolves on the same host.
        assert (await c.get("/api/v1/public/tenant")).json()["slug"] == root_tenant.slug
    async with client_for(app, ROOT_HOST, token=platform_token) as c:
        assert (await c.get("/api/v1/platform/tenants")).status_code == 200
        # A platform token is not a tenant token.
        assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_root_tenant_urls_and_tls_gate(app, root_tenant, owner, plans) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app, "owner.test.local") as c:
        assert (await c.get("/api/v1/internal/tls-ask", params={"domain": ROOT_HOST})).status_code == 200
    from app.modules.tenants.service import tenant_base_url

    assert tenant_base_url(root_tenant.slug) == f"http://{ROOT_HOST}"
    assert tenant_base_url("someone-else") == "http://someone-else.test.local"


async def test_tls_gate_refuses_bare_domain_when_root_tenant_is_missing(app, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "root_tenant_slug", unique("missing"))
    async with client_for(app, "owner.test.local") as c:
        assert (await c.get("/api/v1/internal/tls-ask", params={"domain": ROOT_HOST})).status_code == 404
    async with client_for(app, ROOT_HOST) as c:
        r = await c.get("/api/v1/public/tenant")
    assert r.status_code == 404


async def test_environment_slugs_are_reserved(app, owner, plans) -> None:  # type: ignore[no-untyped-def]
    base = {"name": "X Co", "plan_id": str(plans["test-pro"].id), "admin_email": "x@example.com", "admin_name": "X"}
    for slug in ("uat", "staging"):
        r = await owner.client.post("/api/v1/platform/tenants", json={**base, "slug": slug})
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_slug", slug
