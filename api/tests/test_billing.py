"""Subscription lifecycle, invoices and suspension for non-payment."""

from datetime import timedelta

from app.core.database import platform_session
from app.core.security import utcnow
from app.modules.subscriptions.models import PlanKind, SubscriptionPlan, TenantSubscription
from app.modules.subscriptions.service import add_months, run_billing_cycle
from tests.conftest import make_tenant, unique


async def _subscription(owner, tenant) -> dict:  # type: ignore[no-untyped-def]
    r = await owner.client.get("/api/v1/platform/subscriptions", params={"tenant_id": str(tenant.id)})
    return r.json()["items"][0]


async def _run(now):  # type: ignore[no-untyped-def]
    async with platform_session() as db:
        return await run_billing_cycle(db, now=now)


async def test_paid_start_issues_invoice(app, owner, plans) -> None:  # type: ignore[no-untyped-def]
    t = await make_tenant(app, owner, plans["test-pro"], with_trial=False)
    sub = await _subscription(owner, t)
    assert sub["status"] == "active" and sub["plan"]["code"] == "test-pro"
    invoices = (await owner.client.get("/api/v1/platform/invoices", params={"tenant_id": str(t.id)})).json()["items"]
    assert len(invoices) == 1
    inv = invoices[0]
    assert inv["status"] == "open" and inv["total_cents"] == 10000 and inv["number"].startswith("INV-")
    assert inv["currency"] == "PHP" and sub["plan"]["currency"] == "PHP"
    # Tenant admins can see their own invoices.
    mine = (await t.admin.get("/api/v1/admin/invoices")).json()["items"]
    assert [i["id"] for i in mine] == [inv["id"]]
    await t.admin.aclose()


async def test_trial_end_overdue_expiry_and_reinstatement(app, owner, plans) -> None:  # type: ignore[no-untyped-def]
    t = await make_tenant(app, owner, plans["test-pro"], with_trial=True)
    sub = await _subscription(owner, t)
    assert sub["status"] == "trialing"

    # Trial ends -> past_due with an invoice due immediately.
    async with platform_session() as db:
        row = await db.get(TenantSubscription, sub["id"])
        row.trial_ends_at = utcnow() - timedelta(minutes=1)
    await _run(utcnow())
    sub = await _subscription(owner, t)
    assert sub["status"] == "past_due"
    assert (await t.admin.get("/api/v1/auth/me")).status_code == 200  # grace period still running

    # Grace period (7 days) elapses -> expired and the tenant is suspended.
    result = await _run(utcnow() + timedelta(days=8))
    assert result.expired >= 1
    assert (await _subscription(owner, t))["status"] == "expired"
    r = await t.admin.get("/api/v1/auth/me")
    assert r.status_code == 403 and r.json()["error"]["code"] == "tenant_suspended"

    # Payment reinstates the subscription and the workspace.
    inv = (await owner.client.get("/api/v1/platform/invoices", params={"tenant_id": str(t.id), "status": "open"})).json()[
        "items"
    ][0]
    r = await owner.client.post(f"/api/v1/platform/invoices/{inv['id']}/mark-paid", json={"payment_reference": "BANK-123"})
    assert r.status_code == 200 and r.json()["status"] == "paid"
    assert (await _subscription(owner, t))["status"] == "active"
    assert (await t.admin.get("/api/v1/auth/me")).status_code == 200
    await t.admin.aclose()


async def test_upgrade_prorates_and_downgrade_is_scheduled(app, owner, plans) -> None:  # type: ignore[no-untyped-def]
    t = await make_tenant(app, owner, plans["test-pro"], with_trial=False)
    sub = await _subscription(owner, t)
    r = await owner.client.post(
        f"/api/v1/platform/subscriptions/{sub['id']}/change-plan",
        json={"plan_id": str(plans["test-big"].id), "billing_cycle": "monthly"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "upgrade" and body["subscription"]["plan"]["code"] == "test-big"
    kinds = {line["kind"]: line["amount_cents"] for line in body["invoice"]["lines"]}
    assert kinds["credit"] < 0 and kinds["proration"] > 0
    # Nearly a full period remains, so the charge is close to the price difference.
    assert 19_000 <= body["invoice"]["total_cents"] <= 20_000

    r = await owner.client.post(
        f"/api/v1/platform/subscriptions/{sub['id']}/change-plan",
        json={"plan_id": str(plans["test-small"].id), "billing_cycle": "monthly"},
    )
    assert r.json()["mode"] == "downgrade_scheduled" and r.json()["invoice"] is None
    assert r.json()["subscription"]["plan"]["code"] == "test-big"
    assert r.json()["subscription"]["pending_plan"]["code"] == "test-small"

    # At period end the downgrade applies and a renewal invoice is issued.
    end = utcnow() + timedelta(days=32)
    result = await _run(end)
    assert result.renewed >= 1
    sub = await _subscription(owner, t)
    assert sub["plan"]["code"] == "test-small" and sub["pending_plan"] is None
    await t.admin.aclose()


async def test_cancel_at_period_end_suspends(app, owner, plans) -> None:  # type: ignore[no-untyped-def]
    t = await make_tenant(app, owner, plans["test-pro"], with_trial=False)
    sub = await _subscription(owner, t)
    assert (await owner.client.post(f"/api/v1/platform/subscriptions/{sub['id']}/cancel", json={})).json()[
        "cancel_at_period_end"
    ] is True
    await _run(utcnow() + timedelta(days=32))
    assert (await _subscription(owner, t))["status"] == "cancelled"
    assert (await t.admin.get("/api/v1/auth/me")).status_code == 403
    await t.admin.aclose()


async def test_billing_roles(app, owner, plans) -> None:  # type: ignore[no-untyped-def]
    from app.modules.auth.models import PlatformRole
    from tests.conftest import make_platform_user

    support = await make_platform_user(app, PlatformRole.SUPPORT)
    assert (await support.client.get("/api/v1/platform/invoices")).status_code == 200
    r = await support.client.post(
        "/api/v1/platform/plans", json={"code": "x", "name": "x", "price_monthly_cents": 1, "price_annual_cents": 1}
    )
    assert r.status_code == 403
    await support.client.aclose()


def test_add_months_clamps_day() -> None:
    from datetime import UTC, datetime

    assert add_months(datetime(2026, 1, 31, tzinfo=UTC), 1) == datetime(2026, 2, 28, tzinfo=UTC)
    assert add_months(datetime(2028, 1, 31, tzinfo=UTC), 1) == datetime(2028, 2, 29, tzinfo=UTC)
    assert add_months(datetime(2026, 11, 15, tzinfo=UTC), 12) == datetime(2027, 11, 15, tzinfo=UTC)


async def test_plan_currency_defaults_to_php_and_rejects_unsupported(app, owner) -> None:  # type: ignore[no-untyped-def]
    base = {"name": "Peso plan", "price_monthly_cents": 500_000, "price_annual_cents": 5_000_000}
    r = await owner.client.post("/api/v1/platform/plans", json={**base, "code": unique("php")})
    assert r.status_code == 201, r.text
    assert r.json()["currency"] == "PHP" and r.json()["price_monthly_cents"] == 500_000
    for bad in ("USD", "php", "XYZ"):
        r = await owner.client.post("/api/v1/platform/plans", json={**base, "code": unique("bad"), "currency": bad})
        assert r.status_code == 422, (bad, r.text)


async def test_legacy_usd_plan_keeps_its_currency(app, owner) -> None:  # type: ignore[no-untyped-def]
    """Historical USD records are never relabelled as PHP."""
    async with platform_session() as db:
        plan = SubscriptionPlan(
            code=unique("legacy-usd"),
            name="Legacy USD",
            kind=PlanKind.SAAS,
            price_monthly_cents=4900,
            price_annual_cents=49000,
            currency="USD",
            trial_days=0,
            limits={},
            modules=["events"],
        )
        db.add(plan)
    t = await make_tenant(app, owner, plan, with_trial=False)
    assert (await _subscription(owner, t))["plan"]["currency"] == "USD"
    inv = (await owner.client.get("/api/v1/platform/invoices", params={"tenant_id": str(t.id)})).json()["items"][0]
    assert inv["currency"] == "USD" and inv["total_cents"] == 4900
    overview = (await owner.client.get("/api/v1/platform/analytics/overview")).json()
    assert set(overview["mrr_cents"]) <= {"PHP", "USD"}
    await t.admin.aclose()
