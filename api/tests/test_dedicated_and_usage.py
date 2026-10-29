"""Dedicated-install license enforcement and per-tenant usage metering."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import platform_session
from app.core.redis import get_redis
from app.modules.licenses import signing
from app.modules.licenses.service import current_license_state
from app.modules.tenants.models import TenantUsage
from app.workers.tasks.usage import flush_usage
from tests.conftest import client_for


@pytest.fixture
def dedicated(monkeypatch):  # type: ignore[no-untyped-def]
    s = get_settings()
    monkeypatch.setattr(s, "deployment_mode", "dedicated")
    monkeypatch.setattr(s, "dedicated_tenant_slug", "acme")
    monkeypatch.setattr(s, "license_token", None)
    return s


def _token(tenant: str = "acme", days: int = 30) -> str:
    return signing.sign(
        {
            "lid": "l1",
            "tenant": tenant,
            "licensee": "Acme",
            "type": "term",
            "expires_at": (datetime.now(UTC) + timedelta(days=days)).isoformat(),
        }
    )


async def test_license_state_rules(app, dedicated, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    await get_redis().delete("license:token", "license:revoked")
    assert (await current_license_state())["reason"] == "No license token configured"

    monkeypatch.setattr(dedicated, "license_token", _token())
    assert (await current_license_state())["valid"] is True

    monkeypatch.setattr(dedicated, "license_token", _token(tenant="other"))
    assert "different workspace" in (await current_license_state())["reason"]

    monkeypatch.setattr(dedicated, "license_token", _token(days=-1))
    assert (await current_license_state())["reason"] == "License has expired"

    monkeypatch.setattr(dedicated, "license_token", _token())
    await get_redis().set("license:revoked", "1")
    assert (await current_license_state())["reason"] == "License has been revoked"
    await get_redis().delete("license:revoked")


async def test_unlicensed_dedicated_install_refuses_api_traffic(app, dedicated) -> None:  # type: ignore[no-untyped-def]
    from app.main import create_app

    dedicated_app = create_app()
    async with client_for(dedicated_app, "acme.example.com") as c:
        r = await c.get("/api/v1/portal/dashboard")
        assert r.status_code == 403 and r.json()["error"]["code"] == "license_invalid"
        assert (await c.get("/api/health")).status_code == 200
        status = (await c.get("/api/v1/license/status")).json()
        assert status == {**status, "mode": "dedicated", "valid": False}


async def test_request_metering_is_flushed_to_daily_usage(app, tenant) -> None:  # type: ignore[no-untyped-def]
    for _ in range(3):
        assert (await tenant.admin.get("/api/v1/auth/me")).status_code == 200
    await flush_usage()
    today = datetime.now(UTC).date()
    async with platform_session() as db:
        rows = {
            r.metric: r.value
            for r in (
                await db.execute(select(TenantUsage).where(TenantUsage.tenant_id == tenant.id, TenantUsage.period_date == today))
            ).scalars()
        }
    assert rows["api_requests"] >= 3
    assert rows["accounts_total"] == 1 and rows["active_accounts"] == 1
    # Re-running writes absolute values, not increments.
    await flush_usage()
    async with platform_session() as db:
        again = (
            await db.execute(
                select(TenantUsage.value).where(
                    TenantUsage.tenant_id == tenant.id, TenantUsage.period_date == today, TenantUsage.metric == "accounts_total"
                )
            )
        ).scalar_one()
    assert again == 1
    async with client_for(app, tenant.host) as c:
        assert (await c.get("/api/health")).status_code == 200
