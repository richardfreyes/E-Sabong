"""Owner image management preserves tenant branding and permission boundaries."""

import uuid

import pytest

from app.core.config import get_settings
from app.modules.auth.models import PlatformRole
from tests.conftest import client_for, make_platform_user
from tests.test_provisioning import _png


async def upload(owner, tenant, size=(64, 64), kind="logo"):
    return await owner.client.post(
        f"/api/v1/platform/tenants/{tenant.id}/branding/assets/{kind}",
        files={"file": ("brand.png", _png(size), "image/png")},
    )


async def test_owner_sets_independent_logos_and_public_pages_receive_them(app, owner, tenant, other_tenant):
    first = await upload(owner, tenant)
    second = await upload(owner, other_tenant, (100, 40))
    assert first.status_code == second.status_code == 200
    first_url, second_url = first.json()["logo_url"], second.json()["logo_url"]
    assert first_url != second_url
    assert first_url.startswith(f"/api/media/tenants/{tenant.id}/logo-")
    assert second_url.startswith(f"/api/media/tenants/{other_tenant.id}/logo-")
    for t, url in ((tenant, first_url), (other_tenant, second_url)):
        async with client_for(app, t.host) as c:
            public = await c.get("/api/v1/public/tenant")
            assert public.json()["branding"]["logo_url"] == url
            image = await c.get(url)
            assert image.status_code == 200 and image.headers["content-type"] == "image/png"
        detail = (await owner.client.get(f"/api/v1/platform/tenants/{t.id}")).json()
        assert detail["branding"]["logo_url"] == url

    replaced = await upload(owner, tenant, (32, 80))
    assert replaced.status_code == 200 and replaced.json()["logo_url"] != first_url
    async with client_for(app, tenant.host) as c:
        assert (await c.get(first_url)).status_code == 404
        assert (await c.get(replaced.json()["logo_url"])).status_code == 200
    assert (await other_tenant.admin.get("/api/v1/admin/branding")).json()["logo_url"] == second_url
    removed = await owner.client.delete(f"/api/v1/platform/tenants/{tenant.id}/branding/assets/logo")
    assert removed.status_code == 200 and removed.json()["logo_url"] is None
    assert (await tenant.admin.get("/api/v1/admin/branding")).json()["logo_url"] is None


async def test_tenant_admin_can_replace_owner_uploaded_logo_without_affecting_another_tenant(owner, tenant, other_tenant):
    original = (await upload(owner, tenant)).json()["logo_url"]
    other = (await upload(owner, other_tenant)).json()["logo_url"]
    updated = await tenant.admin.post("/api/v1/admin/branding/assets/logo", files={"file": ("new.png", _png(), "image/png")})
    assert updated.status_code == 200 and updated.json()["logo_url"] != original
    assert (await other_tenant.admin.get("/api/v1/admin/branding")).json()["logo_url"] == other
    removed = await tenant.admin.delete("/api/v1/admin/branding/assets/logo")
    assert removed.status_code == 200 and removed.json()["logo_url"] is None


async def test_owner_asset_access_requires_platform_admin(app, owner, tenant):
    support = await make_platform_user(app, PlatformRole.SUPPORT)
    path = f"/api/v1/platform/tenants/{tenant.id}/branding/assets/logo"
    try:
        assert (await support.client.post(path, files={"file": ("x.png", _png(), "image/png")})).status_code == 403
        assert (await support.client.delete(path)).status_code == 403
        assert (await tenant.admin.post(path, files={"file": ("x.png", _png(), "image/png")})).status_code == 404
        async with client_for(app, "owner.test.local", token=tenant.admin.headers["Authorization"].split()[1]) as c:
            assert (await c.post(path, files={"file": ("x.png", _png(), "image/png")})).status_code == 401
    finally:
        await support.client.aclose()


async def test_rejects_bad_or_oversized_images_and_unknown_tenants(owner, tenant, monkeypatch):
    original = (await upload(owner, tenant)).json()["logo_url"]
    path = f"/api/v1/platform/tenants/{tenant.id}/branding/assets/logo"
    bad = await owner.client.post(path, files={"file": ("unsafe.svg", b"<svg onload='alert(1)'/>", "image/svg+xml")})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_image"
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 16)
    oversized = await upload(owner, tenant)
    assert oversized.status_code == 422 and oversized.json()["error"]["code"] == "file_too_large"
    assert (await tenant.admin.get("/api/v1/admin/branding")).json()["logo_url"] == original
    missing = await owner.client.post(
        f"/api/v1/platform/tenants/{uuid.uuid4()}/branding/assets/logo", files={"file": ("x.png", _png(), "image/png")}
    )
    assert missing.status_code == 404


@pytest.mark.parametrize("kind,field", [("favicon", "favicon_url"), ("login_background", "login_background_url")])
async def test_brand_image_kinds(owner, tenant, kind, field):
    result = await upload(owner, tenant, kind=kind)
    assert result.status_code == 200 and result.json()[field]
    removed = await owner.client.delete(f"/api/v1/platform/tenants/{tenant.id}/branding/assets/{kind}")
    assert removed.status_code == 200 and removed.json()[field] is None
