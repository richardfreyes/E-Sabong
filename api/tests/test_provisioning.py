"""Owner-side flows: signup approval, domains, branding, feature flags, audit."""

import io
from unittest import mock

from PIL import Image

from tests.conftest import PASSWORD, client_for, host_for, unique


async def test_signup_request_approval_provisions_tenant(app, owner, plans) -> None:  # type: ignore[no-untyped-def]
    slug = unique("acme")
    async with client_for(app, "owner.test.local") as public:
        plans_public = (await public.get("/api/v1/public/plans")).json()
        assert any(p["code"] == "test-pro" for p in plans_public)
        assert (await public.get("/api/v1/public/slug-availability", params={"slug": slug})).json()["available"] is True
        assert (await public.get("/api/v1/public/slug-availability", params={"slug": "admin"})).json()["available"] is False
        r = await public.post(
            "/api/v1/public/signup-requests",
            json={
                "company_name": "Acme Arena",
                "contact_name": "Jo Rivera",
                "contact_email": f"jo@{slug}.example.com",
                "desired_slug": slug,
                "plan_id": str(plans["test-pro"].id),
                "billing_cycle": "annual",
            },
        )
        assert r.status_code == 201, r.text
        request_id = r.json()["id"]

    pending = (await owner.client.get("/api/v1/platform/signup-requests", params={"status": "pending"})).json()["items"]
    assert request_id in {p["id"] for p in pending}
    with mock.patch("app.workers.tasks.email.send_email.delay") as send:
        r = await owner.client.post(f"/api/v1/platform/signup-requests/{request_id}/approve", json={"with_trial": True})
        assert r.status_code == 200, r.text
    provisioned = r.json()
    assert provisioned["tenant"]["slug"] == slug and provisioned["tenant"]["subscription_status"] == "trialing"
    assert provisioned["login_url"] == f"http://{slug}.test.local/admin/"
    send.assert_called_once()
    assert provisioned["temporary_password"] in send.call_args.args[2]
    assert (await owner.client.post(f"/api/v1/platform/signup-requests/{request_id}/approve", json={})).status_code == 409

    async with client_for(app, host_for(slug)) as c:
        r = await c.post("/api/v1/auth/login", json={"username": "admin", "password": provisioned["temporary_password"]})
        assert r.status_code == 200 and r.json()["user"]["must_change_password"] is True
        roles = set(r.json()["user"]["roles"])
    assert roles == {"tenant_admin"}


async def test_slug_rules(app, owner, plans, tenant) -> None:  # type: ignore[no-untyped-def]
    base = {"name": "X Co", "plan_id": str(plans["test-pro"].id), "admin_email": "x@example.com", "admin_name": "X"}
    for slug, code in (("www", "invalid_slug"), ("Bad_Slug", "invalid_slug"), (tenant.slug, "slug_taken")):
        r = await owner.client.post("/api/v1/platform/tenants", json={**base, "slug": slug})
        assert r.status_code in (409, 422), slug
        assert r.json()["error"]["code"] == code


async def test_custom_domains_and_tls_gate(app, owner, tenant) -> None:  # type: ignore[no-untyped-def]
    domain = f"{unique('play')}.example.org"
    r = await tenant.admin.post("/api/v1/admin/domains", json={"domain": f"https://{domain}/"})
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["domain"] == domain and row["verification_status"] == "pending"
    assert row["instructions"]["txt_name"] == f"_esabong-verify.{domain}"
    assert (await tenant.admin.post("/api/v1/admin/domains", json={"domain": domain})).status_code == 409
    assert (await tenant.admin.post("/api/v1/admin/domains", json={"domain": "evil.test.local"})).status_code == 422

    async with client_for(app, "owner.test.local") as c:
        assert (await c.get("/api/v1/internal/tls-ask", params={"domain": domain})).status_code == 404
        assert (await c.get("/api/v1/internal/tls-ask", params={"domain": tenant.host})).status_code == 200
        assert (await c.get("/api/v1/internal/tls-ask", params={"domain": "nope.test.local"})).status_code == 404

    token = row["instructions"]["txt_value"]
    with mock.patch("app.modules.tenants.service._lookup_txt", return_value=[token]):
        r = await owner.client.post(f"/api/v1/platform/domains/{row['id']}/verify")
    assert r.json()["verification_status"] == "verified"

    async with client_for(app, "owner.test.local") as c:
        assert (await c.get("/api/v1/internal/tls-ask", params={"domain": domain})).status_code == 200
    # The verified custom domain now resolves to the tenant.
    async with client_for(app, domain) as c:
        assert (await c.get("/api/v1/public/tenant")).json()["slug"] == tenant.slug
        assert (await c.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})).status_code == 200

    subdomain = next(d for d in (await tenant.admin.get("/api/v1/admin/domains")).json() if d["kind"] == "subdomain")
    assert (await tenant.admin.delete(f"/api/v1/admin/domains/{subdomain['id']}")).status_code == 422


def _png(size: tuple[int, int] = (64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


async def test_branding_update_and_asset_upload(app, tenant) -> None:  # type: ignore[no-untyped-def]
    r = await tenant.admin.put(
        "/api/v1/admin/branding", json={"app_name": "Sabong Live", "primary_color": "#0F766E", "login_title": "Welcome"}
    )
    assert r.status_code == 200 and r.json()["primary_color"] == "#0F766E"
    assert (await tenant.admin.put("/api/v1/admin/branding", json={"primary_color": "teal"})).status_code == 422

    r = await tenant.admin.post("/api/v1/admin/branding/assets/logo", files={"file": ("logo.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    logo_url = r.json()["logo_url"]
    assert logo_url.startswith(f"/api/media/tenants/{tenant.id}/logo-")
    r = await tenant.admin.post(
        "/api/v1/admin/branding/assets/favicon", files={"file": ("x.svg", b"<svg onload=alert(1)>", "image/svg+xml")}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_image"

    async with client_for(app, tenant.host) as c:
        public = (await c.get("/api/v1/public/tenant")).json()
        assert public["branding"]["app_name"] == "Sabong Live" and public["branding"]["logo_url"] == logo_url
        img = await c.get(logo_url)
        assert img.status_code == 200 and img.headers["content-type"] == "image/png"


async def test_feature_flag_overrides(app, owner, tenant) -> None:  # type: ignore[no-untyped-def]
    key = unique("flag_")
    r = await owner.client.post(
        "/api/v1/platform/feature-flags", json={"key": key, "description": "test", "default_enabled": False}
    )
    assert r.status_code == 201
    r = await owner.client.put(f"/api/v1/platform/tenants/{tenant.id}/feature-flags", json={"overrides": {key: True}})
    state = {f["key"]: f for f in r.json()}
    assert state[key]["override"] is True and state[key]["effective"] is True
    async with client_for(app, tenant.host) as c:
        assert (await c.get("/api/v1/public/tenant")).json()["feature_flags"][key] is True
    assert (
        await owner.client.put(f"/api/v1/platform/tenants/{tenant.id}/feature-flags", json={"overrides": {"nope": True}})
    ).status_code == 422


async def test_module_toggle_hides_routes(app, tenant) -> None:  # type: ignore[no-untyped-def]
    r = await tenant.admin.put("/api/v1/admin/settings", json={"enabled_modules": ["events", "matches"]})
    assert r.status_code == 200
    r = await tenant.admin.get("/api/v1/admin/points/summary")
    assert r.status_code == 403 and r.json()["error"]["code"] == "module_disabled"
    assert (await tenant.admin.put("/api/v1/admin/settings", json={"enabled_modules": ["gambling"]})).status_code == 422


async def test_platform_lists_and_audit(app, owner, tenant) -> None:  # type: ignore[no-untyped-def]
    r = await owner.client.get("/api/v1/platform/tenants", params={"q": tenant.slug})
    assert [t["slug"] for t in r.json()["items"]] == [tenant.slug]
    assert r.json()["items"][0]["user_count"] == 1
    detail = (await owner.client.get(f"/api/v1/platform/tenants/{tenant.id}")).json()
    assert detail["primary_domain"] == tenant.host and detail["settings"]["timezone"] == "Asia/Manila"
    audit = (await owner.client.get("/api/v1/platform/audit-logs", params={"tenant_id": str(tenant.id)})).json()["items"]
    assert "tenant.provisioned" in {a["action"] for a in audit}
    overview = (await owner.client.get("/api/v1/platform/analytics/overview")).json()
    assert overview["tenants_by_status"]["active"] >= 1


async def test_csv_export_flag(app, owner, tenant) -> None:  # type: ignore[no-untyped-def]
    await owner.client.post("/api/v1/platform/feature-flags", json={"key": "csv_exports", "default_enabled": True})
    assert (await tenant.admin.get("/api/v1/admin/reports/points", params={"format": "csv"})).status_code == 200
    r = await owner.client.put(f"/api/v1/platform/tenants/{tenant.id}/feature-flags", json={"overrides": {"csv_exports": False}})
    assert r.status_code == 200
    r = await tenant.admin.get("/api/v1/admin/reports/points", params={"format": "csv"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "feature_disabled"
    assert (await tenant.admin.get("/api/v1/admin/reports/points")).status_code == 200
