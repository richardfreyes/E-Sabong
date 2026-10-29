from tests.conftest import client_for


async def test_health_and_ready(app) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app, "owner.test.local") as c:
        assert (await c.get("/api/health")).json()["status"] == "ok"
        r = await c.get("/api/ready")
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "ok", "database": "ok", "redis": "ok"}


async def test_openapi_documents_all_modules(app) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app, "owner.test.local") as c:
        spec = (await c.get("/api/openapi.json")).json()
    tags = {t for ops in spec["paths"].values() for op in ops.values() for t in op.get("tags", [])}
    assert {"auth", "platform: tenants", "platform: billing", "admin: users", "admin: demonstration points", "portal"} <= tags


async def test_tenant_provisioning_and_public_branding(app, tenant) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app, tenant.host) as c:
        r = await c.get("/api/v1/public/tenant")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == tenant.slug
    assert body["branding"]["app_name"] == f"Tenant {tenant.slug}"
    assert set(body["modules"]) == {"events", "matches", "points", "reports", "notifications"}


async def test_unknown_host_has_no_tenant(app) -> None:  # type: ignore[no-untyped-def]
    async with client_for(app, "nobody.test.local") as c:
        r = await c.get("/api/v1/public/tenant")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "tenant_not_found"
