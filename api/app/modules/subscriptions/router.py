import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import Numeric, case, cast, func, or_, select

from app.core.deps import (
    Meta,
    PlatformAdmin,
    PlatformBilling,
    PlatformDb,
    PlatformReader,
    PlatformSuperAdmin,
)
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.money import to_cents
from app.core.security import utcnow
from app.modules.audit import service as audit
from app.modules.subscriptions import service
from app.modules.subscriptions.models import (
    LIVE_SUBSCRIPTION_STATUSES,
    BillingCycle,
    Invoice,
    InvoiceStatus,
    SubscriptionPlan,
    SubscriptionStatus,
    TenantSubscription,
)
from app.modules.subscriptions.schemas import (
    CancelSubscription,
    ChangePlan,
    InvoiceOut,
    MarkPaid,
    PlanChangeResult,
    PlanCreate,
    PlanOut,
    PlanUpdate,
    SubscriptionOut,
    SubscriptionStart,
    VoidInvoice,
)
from app.modules.tenants.models import Tenant, TenantStatus
from app.shared.schemas import Page, PageDep, Schema

router = APIRouter(prefix="/platform", tags=["platform: billing"])


# ---------------------------------------------------------------- plans


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(_: PlatformReader, db: PlatformDb, include_inactive: bool = True) -> list[PlanOut]:
    stmt = select(SubscriptionPlan).order_by(
        SubscriptionPlan.kind, SubscriptionPlan.sort_order, SubscriptionPlan.price_monthly_cents
    )
    if not include_inactive:
        stmt = stmt.where(SubscriptionPlan.is_active.is_(True))
    return [PlanOut.model_validate(p) for p in (await db.execute(stmt)).scalars()]


@router.post("/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(body: PlanCreate, current: PlatformAdmin, db: PlatformDb, meta: Meta) -> PlanOut:
    if (await db.execute(select(SubscriptionPlan.id).where(SubscriptionPlan.code == body.code))).first():
        raise Conflict("A plan with this code already exists", code="plan_exists")
    plan = SubscriptionPlan(**body.model_dump())
    db.add(plan)
    await db.flush()
    await db.refresh(plan)
    await audit.record(
        db,
        actor=current.actor,
        action="plan.created",
        resource_type="subscription_plan",
        resource_id=plan.id,
        changes=body.model_dump(),
        meta=meta,
    )
    return PlanOut.model_validate(plan)


@router.patch("/plans/{plan_id}", response_model=PlanOut)
async def update_plan(plan_id: uuid.UUID, body: PlanUpdate, current: PlatformAdmin, db: PlatformDb, meta: Meta) -> PlanOut:
    plan = await db.get(SubscriptionPlan, plan_id)
    if plan is None:
        raise NotFound("Plan not found")
    fields = body.model_dump(exclude_unset=True)
    before = {k: getattr(plan, k) for k in fields}
    for k, v in fields.items():
        if v is not None:
            setattr(plan, k, v)
    await audit.record(
        db,
        actor=current.actor,
        action="plan.updated",
        resource_type="subscription_plan",
        resource_id=plan.id,
        changes=audit.diff(before, fields),
        meta=meta,
    )
    await db.flush()
    await db.refresh(plan)
    return PlanOut.model_validate(plan)


# ---------------------------------------------------------------- subscriptions


async def _sub_out(db: PlatformDb, subs: list[TenantSubscription]) -> list[SubscriptionOut]:
    tenants = {}
    if subs:
        tenants = {
            t.id: t for t in (await db.execute(select(Tenant).where(Tenant.id.in_({s.tenant_id for s in subs})))).scalars()
        }
    out = []
    for s in subs:
        item = SubscriptionOut.model_validate(s)
        t = tenants.get(s.tenant_id)
        item.tenant_name, item.tenant_slug = (t.name, t.slug) if t else (None, None)
        out.append(item)
    return out


async def _get_sub(db: PlatformDb, sub_id: uuid.UUID) -> TenantSubscription:
    sub = (
        await db.execute(select(TenantSubscription).where(TenantSubscription.id == sub_id).with_for_update())
    ).scalar_one_or_none()
    if sub is None:
        raise NotFound("Subscription not found")
    return sub


@router.get("/subscriptions", response_model=Page[SubscriptionOut])
async def list_subscriptions(
    _: PlatformReader,
    db: PlatformDb,
    params: PageDep,
    status_: Annotated[SubscriptionStatus | None, Query(alias="status")] = None,
    tenant_id: uuid.UUID | None = None,
) -> Page[SubscriptionOut]:
    stmt = select(TenantSubscription).join(Tenant, Tenant.id == TenantSubscription.tenant_id)
    if status_:
        stmt = stmt.where(TenantSubscription.status == status_)
    if tenant_id:
        stmt = stmt.where(TenantSubscription.tenant_id == tenant_id)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(Tenant.name.ilike(like), Tenant.slug.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(TenantSubscription.created_at.desc()).offset(params.offset).limit(params.page_size)))
        .scalars()
        .all()
    )
    return Page(items=await _sub_out(db, list(rows)), total=total, page=params.page, page_size=params.page_size)


@router.post("/tenants/{tenant_id}/subscription", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
async def start_subscription(
    tenant_id: uuid.UUID, body: SubscriptionStart, current: PlatformBilling, db: PlatformDb, meta: Meta
) -> SubscriptionOut:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.deleted_at is not None:
        raise NotFound("Tenant not found")
    plan = await db.get(SubscriptionPlan, body.plan_id)
    if plan is None:
        raise Unprocessable("Plan not found", code="invalid_plan")
    sub = await service.start_subscription(
        db, tenant_id=tenant_id, plan=plan, cycle=body.billing_cycle, with_trial=body.with_trial, actor=current.actor, meta=meta
    )
    if tenant.status == TenantStatus.SUSPENDED and body.with_trial:
        # A new trial lifts a billing suspension; a paid start waits for the invoice.
        await service.reactivate_after_payment(db, tenant_id, current.actor)
    [out] = await _sub_out(db, [sub])
    return out


@router.post("/subscriptions/{sub_id}/change-plan", response_model=PlanChangeResult)
async def change_plan(
    sub_id: uuid.UUID, body: ChangePlan, current: PlatformBilling, db: PlatformDb, meta: Meta
) -> PlanChangeResult:
    sub = await _get_sub(db, sub_id)
    plan = await db.get(SubscriptionPlan, body.plan_id)
    if plan is None:
        raise Unprocessable("Plan not found", code="invalid_plan")
    sub, invoice, mode = await service.change_plan(db, sub, plan, body.billing_cycle, actor=current.actor, meta=meta)
    from app.core.redis import get_redis

    await get_redis().delete(f"tenant:modules:{sub.tenant_id}")
    [out] = await _sub_out(db, [sub])
    return PlanChangeResult(subscription=out, invoice=InvoiceOut.model_validate(invoice) if invoice else None, mode=mode)


@router.post("/subscriptions/{sub_id}/cancel", response_model=SubscriptionOut)
async def cancel_subscription(
    sub_id: uuid.UUID, body: CancelSubscription, current: PlatformBilling, db: PlatformDb, meta: Meta
) -> SubscriptionOut:
    sub = await service.cancel(db, await _get_sub(db, sub_id), immediately=body.immediately, actor=current.actor, meta=meta)
    [out] = await _sub_out(db, [sub])
    return out


@router.post("/subscriptions/{sub_id}/resume", response_model=SubscriptionOut)
async def resume_subscription(sub_id: uuid.UUID, current: PlatformBilling, db: PlatformDb, meta: Meta) -> SubscriptionOut:
    sub = await _get_sub(db, sub_id)
    if not sub.cancel_at_period_end or sub.status not in LIVE_SUBSCRIPTION_STATUSES:
        raise Unprocessable("Subscription is not scheduled for cancellation", code="not_cancelling")
    sub.cancel_at_period_end = False
    await audit.record(
        db,
        actor=current.actor,
        action="subscription.resumed",
        resource_type="tenant_subscription",
        resource_id=sub.id,
        tenant_id=sub.tenant_id,
        meta=meta,
    )
    await db.flush()
    [out] = await _sub_out(db, [sub])
    return out


class BillingRun(Schema):
    trials_converted: int
    renewed: int
    cancelled: int
    past_due: int
    expired: int


@router.post("/billing/run", response_model=BillingRun)
async def run_billing(_: PlatformSuperAdmin, db: PlatformDb) -> BillingRun:
    """Run the scheduled billing cycle now (it also runs hourly in the worker)."""
    result = await service.run_billing_cycle(db)
    return BillingRun(**result.__dict__)


# ---------------------------------------------------------------- invoices


async def _invoice_out(db: PlatformDb, invoices: list[Invoice]) -> list[InvoiceOut]:
    names = {}
    if invoices:
        names = dict(
            (await db.execute(select(Tenant.id, Tenant.name).where(Tenant.id.in_({i.tenant_id for i in invoices}))))
            .tuples()
            .all()
        )
    out = []
    for inv in invoices:
        item = InvoiceOut.model_validate(inv)
        item.tenant_name = names.get(inv.tenant_id)
        out.append(item)
    return out


@router.get("/invoices", response_model=Page[InvoiceOut])
async def list_invoices(
    _: PlatformReader,
    db: PlatformDb,
    params: PageDep,
    status_: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
    tenant_id: uuid.UUID | None = None,
    overdue: bool = False,
) -> Page[InvoiceOut]:
    stmt = select(Invoice).join(Tenant, Tenant.id == Invoice.tenant_id)
    if status_:
        stmt = stmt.where(Invoice.status == status_)
    if tenant_id:
        stmt = stmt.where(Invoice.tenant_id == tenant_id)
    if overdue:
        stmt = stmt.where(Invoice.status == InvoiceStatus.OPEN, Invoice.due_at < utcnow())
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(Invoice.number.ilike(like), Tenant.name.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(Invoice.issued_at.desc()).offset(params.offset).limit(params.page_size))).scalars().all()
    )
    return Page(items=await _invoice_out(db, list(rows)), total=total, page=params.page, page_size=params.page_size)


async def _get_invoice(db: PlatformDb, invoice_id: uuid.UUID) -> Invoice:
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id).with_for_update())).scalar_one_or_none()
    if inv is None:
        raise NotFound("Invoice not found")
    return inv


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
async def get_invoice(invoice_id: uuid.UUID, _: PlatformReader, db: PlatformDb) -> InvoiceOut:
    [out] = await _invoice_out(db, [await _get_invoice(db, invoice_id)])
    return out


@router.post("/invoices/{invoice_id}/mark-paid", response_model=InvoiceOut)
async def mark_paid(invoice_id: uuid.UUID, body: MarkPaid, current: PlatformBilling, db: PlatformDb, meta: Meta) -> InvoiceOut:
    inv = await service.mark_paid(
        db, await _get_invoice(db, invoice_id), reference=body.payment_reference, actor=current.actor, meta=meta
    )
    [out] = await _invoice_out(db, [inv])
    return out


@router.post("/invoices/{invoice_id}/void", response_model=InvoiceOut)
async def void_invoice(
    invoice_id: uuid.UUID, body: VoidInvoice, current: PlatformBilling, db: PlatformDb, meta: Meta
) -> InvoiceOut:
    inv = await service.void_invoice(db, await _get_invoice(db, invoice_id), reason=body.reason, actor=current.actor, meta=meta)
    [out] = await _invoice_out(db, [inv])
    return out


# ---------------------------------------------------------------- analytics


class Overview(Schema):
    tenants_by_status: dict[str, int]
    subscriptions_by_status: dict[str, int]
    mrr_cents: dict[str, int]
    trials_ending_7d: int
    open_invoices: int
    overdue_invoices: int
    revenue_30d_cents: dict[str, int]
    total_users: int
    new_tenants_30d: int
    dedicated_deployments: int
    usage_30d: dict[str, int]


@router.get("/analytics/overview", response_model=Overview)
async def overview(_: PlatformReader, db: PlatformDb) -> Overview:
    from app.modules.licenses.models import DeploymentInstance, DeploymentStatus
    from app.modules.tenants.models import TenantUsage
    from app.modules.users.models import TenantUser

    now = utcnow()
    tenants_by_status = {s.value: 0 for s in TenantStatus}
    tenants_by_status.update(
        {
            s.value: int(n)
            for s, n in (
                await db.execute(select(Tenant.status, func.count()).where(Tenant.deleted_at.is_(None)).group_by(Tenant.status))
            )
            .tuples()
            .all()
        }
    )
    subs_by_status = {
        s.value: int(n)
        for s, n in (await db.execute(select(TenantSubscription.status, func.count()).group_by(TenantSubscription.status)))
        .tuples()
        .all()
    }
    monthly = case(
        (TenantSubscription.billing_cycle == BillingCycle.ANNUAL, cast(SubscriptionPlan.price_annual_cents, Numeric) / 12),
        else_=SubscriptionPlan.price_monthly_cents,
    )
    mrr = {
        c: to_cents(Decimal(v or 0))
        for c, v in (
            await db.execute(
                select(SubscriptionPlan.currency, func.sum(monthly))
                .select_from(TenantSubscription)
                .join(SubscriptionPlan, SubscriptionPlan.id == TenantSubscription.plan_id)
                .where(TenantSubscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE]))
                .group_by(SubscriptionPlan.currency)
            )
        )
        .tuples()
        .all()
    }
    trials_ending = int(
        (
            await db.execute(
                select(func.count())
                .select_from(TenantSubscription)
                .where(
                    TenantSubscription.status == SubscriptionStatus.TRIALING,
                    TenantSubscription.trial_ends_at <= now + timedelta(days=7),
                )
            )
        ).scalar_one()
    )
    open_inv = int(
        (await db.execute(select(func.count()).select_from(Invoice).where(Invoice.status == InvoiceStatus.OPEN))).scalar_one()
    )
    overdue_inv = int(
        (
            await db.execute(
                select(func.count()).select_from(Invoice).where(Invoice.status == InvoiceStatus.OPEN, Invoice.due_at < now)
            )
        ).scalar_one()
    )
    revenue = {
        c: int(v or 0)
        for c, v in (
            await db.execute(
                select(Invoice.currency, func.sum(Invoice.total_cents))
                .where(Invoice.status == InvoiceStatus.PAID, Invoice.paid_at >= now - timedelta(days=30))
                .group_by(Invoice.currency)
            )
        )
        .tuples()
        .all()
    }
    total_users = int(
        (await db.execute(select(func.count()).select_from(TenantUser).where(TenantUser.deleted_at.is_(None)))).scalar_one()
    )
    new_tenants = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Tenant)
                .where(Tenant.deleted_at.is_(None), Tenant.created_at >= now - timedelta(days=30))
            )
        ).scalar_one()
    )
    dedicated = int(
        (
            await db.execute(
                select(func.count())
                .select_from(DeploymentInstance)
                .where(DeploymentInstance.status != DeploymentStatus.DECOMMISSIONED)
            )
        ).scalar_one()
    )
    usage = {
        m: int(v or 0)
        for m, v in (
            await db.execute(
                select(TenantUsage.metric, func.sum(TenantUsage.value))
                .where(TenantUsage.period_date >= (now - timedelta(days=30)).date())
                .group_by(TenantUsage.metric)
            )
        )
        .tuples()
        .all()
    }
    return Overview(
        tenants_by_status=tenants_by_status,
        subscriptions_by_status=subs_by_status,
        mrr_cents=mrr,
        trials_ending_7d=trials_ending,
        open_invoices=open_inv,
        overdue_invoices=overdue_inv,
        revenue_30d_cents=revenue,
        total_users=total_users,
        new_tenants_30d=new_tenants,
        dedicated_deployments=dedicated,
        usage_30d=usage,
    )
