"""Reuse the immutable original Admin while isolating each streaming test's media state."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.core.database import platform_session
from app.core.redis import get_redis
from app.core.security import utcnow
from app.modules.subscriptions.models import LIVE_SUBSCRIPTION_STATUSES, BillingCycle, SubscriptionStatus, TenantSubscription
from app.modules.tenants.models import ALL_MODULES, Tenant, TenantSettings
from app.modules.users.models import AccountType, TenantUser
from app.modules.video.models import VideoBroadcast, VideoProvider, VideoRecording, VideoStream, VideoStreamOperator
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
    # The original workspace survives tests that intentionally disable modules or expire its plan.
    # Reset its test entitlement explicitly; activation alone does not restore a subscription.
    async with platform_session() as db:
        await db.execute(
            update(TenantSettings).where(TenantSettings.tenant_id == handle.id).values(enabled_modules=list(ALL_MODULES))
        )
        await db.execute(
            update(TenantSubscription)
            .where(TenantSubscription.tenant_id == handle.id, TenantSubscription.status.in_(LIVE_SUBSCRIPTION_STATUSES))
            .values(status=SubscriptionStatus.EXPIRED)
        )
        sub = await db.scalar(
            select(TenantSubscription)
            .where(TenantSubscription.tenant_id == handle.id)
            .order_by(TenantSubscription.created_at.desc())
            .limit(1)
        )
        if sub is None:
            sub = TenantSubscription(tenant_id=handle.id, billing_cycle=BillingCycle.MONTHLY)
            db.add(sub)
        sub.plan_id = plans["test-pro"].id
        sub.status = SubscriptionStatus.ACTIVE
        sub.current_period_start = utcnow()
        sub.current_period_end = utcnow() + timedelta(days=30)
        sub.cancel_at_period_end = False
        sub.cancelled_at = None
        sub.expired_at = None
    await get_redis().delete(f"tenant:modules:{handle.id}")
    # Test-only schema-owner cleanup: production roles intentionally cannot erase broadcast history.
    url = get_settings().database_url_migrations
    assert (make_url(url).database or "").startswith("esabong_test")
    engine = create_async_engine(url)
    async with AsyncSession(engine) as db:
        await db.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(handle.id)})
        for model in (VideoRecording, VideoBroadcast, VideoStreamOperator, VideoStream, VideoProvider):
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
