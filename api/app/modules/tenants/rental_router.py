"""OWNER rental controls reuse the existing subscription and video services."""

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter
from pydantic import ConfigDict, Field, model_validator
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.deps import Meta, PlatformDb, PlatformSuperAdmin
from app.core.errors import NotFound, Unprocessable
from app.core.security import utcnow
from app.modules.audit import service as audit
from app.modules.subscriptions import service as billing
from app.modules.subscriptions.models import BillingCycle, SubscriptionPlan
from app.modules.tenants.models import ALL_MODULES, Tenant
from app.modules.tenants.rental import RentalSettings
from app.modules.users.models import AccountType, TenantUser
from app.modules.users.schemas import UserOut
from app.shared.schemas import Page, PageDep, Schema, paginate

router = APIRouter(prefix="/platform/rental", tags=["platform: rental"])


class RentalConfig(Schema):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    shared_domain_enabled: bool = False
    default_tenant_id: uuid.UUID | None = None
    broadcast_tenant_id: uuid.UUID | None = None


class RentalConfigSaved(RentalConfig):
    pass


class RentalPrice(Schema):
    monthly_cents: int = Field(ge=0, le=100_000_000_000)
    next_billing_at: datetime | None = None
    activation_at: datetime | None = None

    @model_validator(mode="after")
    def dated(self):
        if self.activation_at and self.activation_at.tzinfo is None:
            raise ValueError("Activation date must include a timezone")
        if self.next_billing_at and (self.next_billing_at.tzinfo is None or self.next_billing_at <= utcnow()):
            raise ValueError("Next billing date must be a future timestamp with timezone")
        return self


@router.get("/settings", response_model=RentalConfig)
async def get_settings_config(_: PlatformSuperAdmin, db: PlatformDb):
    return await db.get(RentalSettings, 1) or RentalConfig()


@router.put("/settings", response_model=RentalConfigSaved)
async def save_settings(body: RentalConfig, current: PlatformSuperAdmin, db: PlatformDb, meta: Meta):
    existing = await db.get(RentalSettings, 1, with_for_update=True)
    if existing:
        if "broadcast_tenant_id" not in body.model_fields_set:
            body.broadcast_tenant_id = existing.broadcast_tenant_id
    for tid in (body.default_tenant_id, body.broadcast_tenant_id):
        tenant = await db.get(Tenant, tid) if tid else None
        if tid and (tenant is None or tenant.deleted_at):
            raise NotFound("Workspace not found")
    row = existing
    if row is None:
        row = RentalSettings(id=1)
        db.add(row)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    await audit.record(
        db,
        actor=current.actor,
        action="rental.settings_updated",
        resource_type="rental_settings",
        resource_id="1",
        changes=body.model_dump(mode="json"),
        meta=meta,
    )
    await db.flush()
    return RentalConfigSaved.model_validate(row)


@router.put("/tenants/{tenant_id}/price")
async def set_rental(tenant_id: uuid.UUID, body: RentalPrice, current: PlatformSuperAdmin, db: PlatformDb, meta: Meta):
    tenant = await db.get(Tenant, tenant_id, with_for_update=True)
    if tenant is None or tenant.deleted_at:
        raise NotFound("Tenant not found")
    sub = await billing.get_live(db, tenant_id)
    code = f"rental-{tenant_id.hex}"
    plan = await db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == code).with_for_update())
    if plan is None:
        plan = SubscriptionPlan(
            code=code,
            name=f"{tenant.name} monthly rental",
            price_monthly_cents=body.monthly_cents,
            price_annual_cents=body.monthly_cents * 12,
            currency=get_settings().default_currency,
            trial_days=0,
            modules=list(sub.plan.modules) if sub else list(ALL_MODULES),
            limits=dict(sub.plan.limits) if sub else {},
            is_active=False,
            prices_approved_at=utcnow(),
            prices_approved_by=current.id,
        )
        db.add(plan)
        await db.flush()
    plan.price_monthly_cents = body.monthly_cents
    plan.price_annual_cents = body.monthly_cents * 12
    plan.prices_approved_at, plan.prices_approved_by = utcnow(), current.id
    if sub:
        sub.plan_id, sub.billing_cycle = plan.id, BillingCycle.MONTHLY
        sub.pending_plan_id = sub.pending_billing_cycle = None
    else:
        # Private rental plans are never offered in public checkout.
        plan.is_active = True
        sub = await billing.start_subscription(
            db, tenant_id=tenant_id, plan=plan, cycle=BillingCycle.MONTHLY, with_trial=False, actor=current.actor, meta=meta
        )
        plan.is_active = False
    if body.next_billing_at:
        if body.next_billing_at <= sub.current_period_start:
            raise Unprocessable("Next billing must be after activation")
        sub.current_period_end = body.next_billing_at
    if body.activation_at:
        if body.activation_at >= sub.current_period_end:
            raise Unprocessable("Activation must be before the next billing date")
        sub.current_period_start = body.activation_at
    await audit.record(
        db,
        actor=current.actor,
        action="rental.price_updated",
        resource_type="tenant_subscription",
        resource_id=sub.id,
        tenant_id=tenant_id,
        changes={
            "monthly_cents": body.monthly_cents,
            "next_billing_at": sub.current_period_end.isoformat(),
            "activation_at": sub.current_period_start.isoformat(),
        },
        meta=meta,
    )
    return {"monthly_cents": body.monthly_cents, "next_billing_at": sub.current_period_end}


@router.get("/tenants/{tenant_id}/summary")
async def tenant_summary(tenant_id: uuid.UUID, _: PlatformSuperAdmin, db: PlatformDb):
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.deleted_at:
        raise NotFound("Tenant not found")
    counts = dict(
        (
            await db.execute(
                select(TenantUser.account_type, func.count())
                .where(TenantUser.tenant_id == tenant_id, TenantUser.deleted_at.is_(None))
                .group_by(TenantUser.account_type)
            )
        ).all()
    )
    sub = await billing.get_live(db, tenant_id)
    admin = await db.scalar(
        select(TenantUser)
        .where(TenantUser.tenant_id == tenant_id, TenantUser.account_type == AccountType.ADMIN, TenantUser.deleted_at.is_(None))
        .order_by(TenantUser.created_at)
    )
    s = get_settings()
    return {
        "tenant_id": tenant_id,
        "accounts": counts,
        "monthly_cents": sub.plan.price_monthly_cents if sub else None,
        "next_billing_at": sub.current_period_end if sub else None,
        "activation_at": sub.current_period_start if sub else None,
        "subscription_status": sub.status if sub else None,
        "player_registration_url": f"{s.public_scheme}://{s.base_domain}/register?ref={admin.id}" if admin else None,
        "admin_login": f"{admin.username}@{tenant.slug}" if admin else None,
    }


@router.get("/tenants/{tenant_id}/accounts", response_model=Page[UserOut])
async def tenant_accounts(tenant_id: uuid.UUID, _: PlatformSuperAdmin, db: PlatformDb, params: PageDep):
    from app.modules.users.service import to_out

    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.deleted_at:
        raise NotFound("Tenant not found")
    query = select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.deleted_at.is_(None))
    if params.q:
        query = query.where(TenantUser.username.ilike(f"%{params.q}%"))
    rows, total = await paginate(db, query.order_by(TenantUser.created_at, TenantUser.id), params)
    return Page(items=await to_out(db, tenant_id, rows), total=total, page=params.page, page_size=params.page_size)


@router.get("/alerts")
async def billing_alerts(_: PlatformSuperAdmin, db: PlatformDb):
    from app.modules.subscriptions.models import Invoice, InvoiceStatus

    now = utcnow()
    rows = (
        await db.execute(
            select(Invoice, Tenant.name)
            .join(Tenant, Tenant.id == Invoice.tenant_id)
            .where(
                Invoice.status == InvoiceStatus.OPEN,
                Invoice.due_at <= now + timedelta(days=7),
                Tenant.deleted_at.is_(None),
            )
            .order_by(Invoice.due_at)
            .limit(100)
        )
    ).all()
    return [
        {
            "tenant_id": inv.tenant_id,
            "tenant_name": name,
            "number": inv.number,
            "due_at": inv.due_at,
            "total_cents": inv.total_cents,
            "status": "overdue" if inv.due_at < now else "upcoming",
        }
        for inv, name in rows
    ]
