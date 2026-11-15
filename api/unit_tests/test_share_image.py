"""Link-preview redirects follow tenant logo changes without an authenticated browser."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.tenants.models import TenantBranding
from app.modules.tenants.public_router import share_image


async def test_preview_tracks_current_upload_and_removal():
    tenant = SimpleNamespace(id=uuid.uuid4())
    branding = SimpleNamespace(logo_url=f"/api/media/tenants/{tenant.id}/logo-first.png")
    db = SimpleNamespace(get=AsyncMock(return_value=branding))
    first = await share_image(db, tenant)
    assert first.status_code == 302
    assert first.headers["location"] == branding.logo_url
    assert first.headers["cache-control"] == "no-store"
    db.get.assert_awaited_once_with(TenantBranding, tenant.id)

    branding.logo_url = f"/api/media/tenants/{tenant.id}/logo-second.png"
    assert (await share_image(db, tenant)).headers["location"] == branding.logo_url
    branding.logo_url = None
    assert (await share_image(db, tenant)).headers["location"] == "/assets/brand/png/swc-logo-128.png"


async def test_unknown_host_uses_system_asset_without_reading_another_tenant():
    db = SimpleNamespace(get=AsyncMock())
    response = await share_image(db, None)
    assert response.headers["location"] == "/assets/brand/png/swc-logo-128.png"
    db.get.assert_not_awaited()
