"""Reuse the immutable original Admin while isolating each streaming test's media state."""

import uuid

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.core.database import platform_session
from app.core.security import utcnow
from app.modules.tenants.models import Tenant
from app.modules.users.models import AccountType, TenantUser
from app.modules.video.models import VideoBroadcast, VideoRecording, VideoStream, VideoStreamOperator
from tests.conftest import PASSWORD, TenantHandle, client_for, make_tenant


@pytest.fixture
async def streaming_tenant(app, owner, plans):
    async with platform_session() as db:
        provider = await db.scalar(select(Tenant).where(Tenant.is_global_stream_provider))
    if provider is None:
        handle = await make_tenant(app, owner, plans["test-pro"])
    else:
        assert (await owner.client.post(f"/api/v1/platform/tenants/{provider.id}/activate", json={})).status_code == 200
        host = f"{provider.slug}.test.local"
        admin = client_for(app, host)
        login = await admin.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        assert login.status_code == 200, login.text
        admin.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        handle = TenantHandle(app, provider.id, provider.slug, host, admin, uuid.UUID(login.json()["user"]["id"]))
    # Test-only schema-owner cleanup: production roles intentionally cannot erase broadcast history.
    url = get_settings().database_url_migrations
    assert (make_url(url).database or "").startswith("esabong_test")
    engine = create_async_engine(url)
    async with AsyncSession(engine) as db:
        await db.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(handle.id)})
        for model in (VideoRecording, VideoBroadcast, VideoStreamOperator, VideoStream):
            await db.execute(delete(model).where(model.tenant_id == handle.id))
        await db.execute(
            update(TenantUser)
            .where(TenantUser.tenant_id == handle.id, TenantUser.account_type != AccountType.ADMIN)
            .values(deleted_at=utcnow())
        )
        await db.commit()
    await engine.dispose()
    try:
        yield handle
    finally:
        await handle.admin.aclose()
        for client in handle.clients:
            await client.aclose()
