"""Software subscription lifecycle: trials, renewals, plan changes, invoices, expiry.

All functions run on the platform session (billing is owned by the platform),
except read helpers that are also used from tenant routes.
"""

import calendar
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflict, Unprocessable
from app.core.money import basis_points, scale
from app.core.security import utcnow
from app.modules.audit import service as audit
from app.modules.audit.service import Actor, RequestMeta
from app.modules.subscriptions.models import (
    LIVE_SUBSCRIPTION_STATUSES,
    BillingCycle,
    Invoice,
    InvoiceLine,
    InvoiceLineKind,
    InvoiceStatus,
    SubscriptionPlan,
    SubscriptionStatus,
    TenantSubscription,
)
from app.modules.tenants.models import Tenant, TenantSettings, TenantStatus

INVOICE_DUE_DAYS = 14
BILLING_SUSPENSION_REASONS = ("subscription_expired", "subscription_cancelled")


def add_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def period_end(start: datetime, cycle: BillingCycle) -> datetime:
    return add_months(start, 12 if cycle == BillingCycle.ANNUAL else 1)


def monthly_equivalent(plan: SubscriptionPlan, cycle: BillingCycle) -> Decimal:
    price = Decimal(plan.price_for(cycle))
    return price / 12 if cycle == BillingCycle.ANNUAL else price


@dataclass
class LineSpec:
    kind: InvoiceLineKind
    description: str
    amount_cents: int
    quantity: int = 1


async def _next_invoice_number(db: AsyncSession) -> str:
    n = (await db.execute(text("SELECT nextval('invoice_number_seq')"))).scalar_one()
    return f"INV-{utcnow():%Y}-{int(n):06d}"


async def create_invoice(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    subscription: TenantSubscription | None,
    currency: str,
    lines: list[LineSpec],
    period_start: datetime | None,
    period_end_: datetime | None,
    due_at: datetime | None = None,
    notes: str | None = None,
) -> Invoice:
    subtotal = sum(line.amount_cents for line in lines)
    tax_bps = get_settings().invoice_tax_rate_bps
    tax = max(0, basis_points(subtotal, tax_bps)) if subtotal > 0 else 0
    now = utcnow()
    invoice = Invoice(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        subscription_id=subscription.id if subscription else None,
        number=await _next_invoice_number(db),
        status=InvoiceStatus.OPEN if subtotal + tax > 0 else InvoiceStatus.PAID,
        currency=currency,
        subtotal_cents=subtotal,
        tax_cents=tax,
        total_cents=subtotal + tax,
        period_start=period_start,
        period_end=period_end_,
        issued_at=now,
        due_at=due_at or now + timedelta(days=INVOICE_DUE_DAYS),
        paid_at=now if subtotal + tax <= 0 else None,
        notes=notes,
    )
    invoice.lines = [
        InvoiceLine(
            tenant_id=tenant_id,
            position=i,
            kind=line.kind,
            description=line.description,
            quantity=line.quantity,
            unit_amount_cents=line.amount_cents // max(line.quantity, 1),
            amount_cents=line.amount_cents,
        )
        for i, line in enumerate(lines)
    ]
    db.add(invoice)
    await db.flush()
    return invoice


def _plan_line(plan: SubscriptionPlan, cycle: BillingCycle, start: datetime, end: datetime) -> LineSpec:
    return LineSpec(
        kind=InvoiceLineKind.PLAN,
        description=f"{plan.name} ({cycle.value}) {start:%Y-%m-%d} – {end:%Y-%m-%d}",
        amount_cents=plan.price_for(cycle),
    )


async def get_live(db: AsyncSession, tenant_id: uuid.UUID) -> TenantSubscription | None:
    return (
        await db.execute(
            select(TenantSubscription).where(
                TenantSubscription.tenant_id == tenant_id,
                TenantSubscription.status.in_(LIVE_SUBSCRIPTION_STATUSES),
            )
        )
    ).scalar_one_or_none()


async def start_subscription(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    plan: SubscriptionPlan,
    cycle: BillingCycle,
    with_trial: bool,
    actor: Actor,
    meta: RequestMeta | None = None,
) -> TenantSubscription:
    if not plan.is_active:
        raise Unprocessable("Plan is not available", code="plan_inactive")
    if await get_live(db, tenant_id) is not None:
        raise Conflict("Tenant already has an active subscription", code="subscription_exists")
    now = utcnow()
    trial = with_trial and plan.trial_days > 0
    if trial:
        end = now + timedelta(days=plan.trial_days)
        sub = TenantSubscription(
            tenant_id=tenant_id,
            plan_id=plan.id,
            billing_cycle=cycle,
            status=SubscriptionStatus.TRIALING,
            trial_ends_at=end,
            current_period_start=now,
            current_period_end=end,
        )
    else:
        end = period_end(now, cycle)
        sub = TenantSubscription(
            tenant_id=tenant_id,
            plan_id=plan.id,
            billing_cycle=cycle,
            status=SubscriptionStatus.ACTIVE,
            current_period_start=now,
            current_period_end=end,
        )
    db.add(sub)
    await db.flush()
    await db.refresh(sub)
    if not trial:
        await create_invoice(
            db,
            tenant_id=tenant_id,
            subscription=sub,
            currency=plan.currency,
            lines=[_plan_line(plan, cycle, now, end)],
            period_start=now,
            period_end_=end,
        )
    await audit.record(
        db,
        actor=actor,
        action="subscription.started",
        resource_type="tenant_subscription",
        resource_id=sub.id,
        tenant_id=tenant_id,
        changes={"plan": plan.code, "cycle": cycle, "status": sub.status},
        meta=meta,
    )
    return sub


async def change_plan(
    db: AsyncSession,
    sub: TenantSubscription,
    new_plan: SubscriptionPlan,
    new_cycle: BillingCycle,
    *,
    actor: Actor,
    meta: RequestMeta | None = None,
) -> tuple[TenantSubscription, Invoice | None, str]:
    """Upgrades apply now with proration; downgrades apply at the end of the period."""
    if sub.status not in LIVE_SUBSCRIPTION_STATUSES:
        raise Unprocessable("Subscription is not active", code="subscription_inactive")
    if not new_plan.is_active:
        raise Unprocessable("Plan is not available", code="plan_inactive")
    old_plan, old_cycle = sub.plan, sub.billing_cycle
    if old_plan.id == new_plan.id and old_cycle == new_cycle:
        raise Unprocessable("Subscription is already on this plan", code="no_change")

    now = utcnow()
    invoice: Invoice | None = None
    if sub.status == SubscriptionStatus.TRIALING:
        sub.plan_id, sub.billing_cycle = new_plan.id, new_cycle
        mode = "trial_switch"
    elif monthly_equivalent(new_plan, new_cycle) >= monthly_equivalent(old_plan, old_cycle):
        # Whole microseconds keep the fraction exact.
        span = (sub.current_period_end - sub.current_period_start) // timedelta(microseconds=1)
        remaining = max(0, (sub.current_period_end - now) // timedelta(microseconds=1))
        fraction = Decimal(remaining) / Decimal(span) if span > 0 else Decimal(0)
        credit = scale(old_plan.price_for(old_cycle), fraction)
        lines = [LineSpec(InvoiceLineKind.CREDIT, f"Unused time on {old_plan.name} ({old_cycle.value})", -credit)]
        if new_cycle != old_cycle:
            # Cycle change restarts the period now at the new plan's full price.
            start, end = now, period_end(now, new_cycle)
            lines.append(_plan_line(new_plan, new_cycle, start, end))
            sub.current_period_start, sub.current_period_end = start, end
        else:
            start, end = now, sub.current_period_end
            lines.append(
                LineSpec(
                    InvoiceLineKind.PRORATION,
                    f"{new_plan.name} ({new_cycle.value}) prorated {start:%Y-%m-%d} – {end:%Y-%m-%d}",
                    scale(new_plan.price_for(new_cycle), fraction),
                )
            )
        sub.plan_id, sub.billing_cycle = new_plan.id, new_cycle
        sub.pending_plan_id = sub.pending_billing_cycle = None
        invoice = await create_invoice(
            db,
            tenant_id=sub.tenant_id,
            subscription=sub,
            currency=new_plan.currency,
            lines=lines,
            period_start=start,
            period_end_=end,
            notes=f"Plan change {old_plan.code} → {new_plan.code}",
        )
        mode = "upgrade"
    else:
        sub.pending_plan_id, sub.pending_billing_cycle = new_plan.id, new_cycle
        mode = "downgrade_scheduled"

    await db.flush()
    await db.refresh(sub)
    await audit.record(
        db,
        actor=actor,
        action=f"subscription.{mode}",
        resource_type="tenant_subscription",
        resource_id=sub.id,
        tenant_id=sub.tenant_id,
        changes={"plan": [old_plan.code, new_plan.code], "cycle": [old_cycle, new_cycle]},
        meta=meta,
    )
    return sub, invoice, mode


async def cancel(
    db: AsyncSession, sub: TenantSubscription, *, immediately: bool, actor: Actor, meta: RequestMeta | None = None
) -> TenantSubscription:
    if sub.status not in LIVE_SUBSCRIPTION_STATUSES:
        raise Unprocessable("Subscription is not active", code="subscription_inactive")
    if immediately:
        sub.status = SubscriptionStatus.CANCELLED
        sub.cancelled_at = utcnow()
        await suspend_for_billing(db, sub.tenant_id, "subscription_cancelled", actor)
    else:
        sub.cancel_at_period_end = True
    await audit.record(
        db,
        actor=actor,
        action="subscription.cancelled",
        resource_type="tenant_subscription",
        resource_id=sub.id,
        tenant_id=sub.tenant_id,
        changes={"immediately": immediately},
        meta=meta,
    )
    await db.flush()
    return sub


async def suspend_for_billing(db: AsyncSession, tenant_id: uuid.UUID, reason: str, actor: Actor) -> None:
    from app.core.database import on_commit
    from app.core.tenancy import invalidate_tenant_cache

    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.status != TenantStatus.ACTIVE:
        return
    tenant.status = TenantStatus.SUSPENDED
    tenant.suspended_reason = reason
    tenant.suspended_at = utcnow()
    await audit.record(
        db,
        actor=actor,
        action="tenant.suspended",
        resource_type="tenant",
        resource_id=tenant_id,
        tenant_id=tenant_id,
        changes={"reason": reason},
    )

    async def _invalidate() -> None:
        await invalidate_tenant_cache(tenant_id)

    on_commit(db, _invalidate)


async def reactivate_after_payment(db: AsyncSession, tenant_id: uuid.UUID, actor: Actor) -> None:
    from app.core.database import on_commit
    from app.core.tenancy import invalidate_tenant_cache

    tenant = await db.get(Tenant, tenant_id)
    if tenant and tenant.status == TenantStatus.SUSPENDED and tenant.suspended_reason in BILLING_SUSPENSION_REASONS:
        tenant.status = TenantStatus.ACTIVE
        tenant.suspended_reason = tenant.suspended_at = None
        await audit.record(
            db,
            actor=actor,
            action="tenant.activated",
            resource_type="tenant",
            resource_id=tenant_id,
            tenant_id=tenant_id,
            changes={"reason": "payment_received"},
        )

        async def _invalidate() -> None:
            await invalidate_tenant_cache(tenant_id)

        on_commit(db, _invalidate)


async def mark_paid(
    db: AsyncSession, invoice: Invoice, *, reference: str, actor: Actor, meta: RequestMeta | None = None
) -> Invoice:
    if invoice.status != InvoiceStatus.OPEN:
        raise Unprocessable(f"Invoice is {invoice.status.value}", code="invoice_not_open")
    invoice.status = InvoiceStatus.PAID
    invoice.paid_at = utcnow()
    invoice.payment_reference = reference
    await db.flush()

    if invoice.subscription_id:
        sub = await db.get(TenantSubscription, invoice.subscription_id)
        if sub and sub.status in (SubscriptionStatus.PAST_DUE, SubscriptionStatus.EXPIRED):
            still_open = (
                await db.execute(
                    select(Invoice.id).where(
                        Invoice.subscription_id == sub.id,
                        Invoice.status == InvoiceStatus.OPEN,
                        Invoice.due_at <= utcnow(),
                    )
                )
            ).first()
            if not still_open:
                if sub.status == SubscriptionStatus.EXPIRED:
                    # Reinstate: a fresh period starts on payment.
                    sub.current_period_start = utcnow()
                    sub.current_period_end = period_end(sub.current_period_start, sub.billing_cycle)
                    sub.expired_at = None
                sub.status = SubscriptionStatus.ACTIVE
                await reactivate_after_payment(db, sub.tenant_id, actor)
    await audit.record(
        db,
        actor=actor,
        action="invoice.paid",
        resource_type="invoice",
        resource_id=invoice.id,
        tenant_id=invoice.tenant_id,
        changes={"reference": reference, "total_cents": invoice.total_cents},
        meta=meta,
    )
    return invoice


async def void_invoice(
    db: AsyncSession, invoice: Invoice, *, reason: str, actor: Actor, meta: RequestMeta | None = None
) -> Invoice:
    if invoice.status not in (InvoiceStatus.OPEN, InvoiceStatus.DRAFT):
        raise Unprocessable(f"Invoice is {invoice.status.value}", code="invoice_not_open")
    invoice.status = InvoiceStatus.VOID
    invoice.notes = f"{invoice.notes or ''}\nVoided: {reason}".strip()
    await audit.record(
        db,
        actor=actor,
        action="invoice.voided",
        resource_type="invoice",
        resource_id=invoice.id,
        tenant_id=invoice.tenant_id,
        changes={"reason": reason},
        meta=meta,
    )
    await db.flush()
    return invoice


# ---------------------------------------------------------------- scheduled processing


@dataclass
class BillingRunResult:
    trials_converted: int = 0
    renewed: int = 0
    cancelled: int = 0
    past_due: int = 0
    expired: int = 0


async def run_billing_cycle(db: AsyncSession, now: datetime | None = None) -> BillingRunResult:
    """Idempotent; safe to run as often as desired (Celery beat runs it hourly)."""
    now = now or utcnow()
    system = Actor.system()
    result = BillingRunResult()

    # 1. Trials that ended become past_due with an invoice for the first period.
    trials = (
        (
            await db.execute(
                select(TenantSubscription)
                .where(TenantSubscription.status == SubscriptionStatus.TRIALING, TenantSubscription.trial_ends_at <= now)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for sub in trials:
        start = sub.trial_ends_at or now
        end = period_end(start, sub.billing_cycle)
        sub.status = SubscriptionStatus.PAST_DUE
        sub.current_period_start, sub.current_period_end = start, end
        await create_invoice(
            db,
            tenant_id=sub.tenant_id,
            subscription=sub,
            currency=sub.plan.currency,
            lines=[_plan_line(sub.plan, sub.billing_cycle, start, end)],
            period_start=start,
            period_end_=end,
            due_at=start,
            notes="Trial ended",
        )
        await audit.record(
            db,
            actor=system,
            action="subscription.trial_ended",
            resource_type="tenant_subscription",
            resource_id=sub.id,
            tenant_id=sub.tenant_id,
        )
        result.trials_converted += 1

    # 2. Paid periods that ended renew (or cancel).
    ended = (
        (
            await db.execute(
                select(TenantSubscription)
                .where(TenantSubscription.status == SubscriptionStatus.ACTIVE, TenantSubscription.current_period_end <= now)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for sub in ended:
        if sub.cancel_at_period_end:
            sub.status = SubscriptionStatus.CANCELLED
            sub.cancelled_at = now
            await suspend_for_billing(db, sub.tenant_id, "subscription_cancelled", system)
            result.cancelled += 1
            continue
        if sub.pending_plan_id:
            sub.plan_id = sub.pending_plan_id
            sub.billing_cycle = sub.pending_billing_cycle or sub.billing_cycle
            sub.pending_plan_id = sub.pending_billing_cycle = None
            await db.flush()
            await db.refresh(sub)
        start = sub.current_period_end
        end = period_end(start, sub.billing_cycle)
        sub.current_period_start, sub.current_period_end = start, end
        await create_invoice(
            db,
            tenant_id=sub.tenant_id,
            subscription=sub,
            currency=sub.plan.currency,
            lines=[_plan_line(sub.plan, sub.billing_cycle, start, end)],
            period_start=start,
            period_end_=end,
        )
        await audit.record(
            db,
            actor=system,
            action="subscription.renewed",
            resource_type="tenant_subscription",
            resource_id=sub.id,
            tenant_id=sub.tenant_id,
        )
        result.renewed += 1

    # 3. Active subscriptions with an overdue invoice become past_due.
    overdue = (
        (
            await db.execute(
                select(TenantSubscription)
                .join(Invoice, Invoice.subscription_id == TenantSubscription.id)
                .where(
                    TenantSubscription.status == SubscriptionStatus.ACTIVE,
                    Invoice.status == InvoiceStatus.OPEN,
                    Invoice.due_at <= now,
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    for sub in overdue:
        sub.status = SubscriptionStatus.PAST_DUE
        result.past_due += 1

    # 4. Past-due beyond the tenant's grace period: expire and suspend.
    past_due = (
        (await db.execute(select(TenantSubscription).where(TenantSubscription.status == SubscriptionStatus.PAST_DUE)))
        .scalars()
        .all()
    )
    for sub in past_due:
        settings = await db.get(TenantSettings, sub.tenant_id)
        grace = timedelta(days=settings.suspension_grace_days if settings else 7)
        oldest_due = (
            await db.execute(
                select(Invoice.due_at)
                .where(Invoice.subscription_id == sub.id, Invoice.status == InvoiceStatus.OPEN)
                .order_by(Invoice.due_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        if oldest_due is None:
            sub.status = SubscriptionStatus.ACTIVE
            continue
        if oldest_due + grace <= now:
            sub.status = SubscriptionStatus.EXPIRED
            sub.expired_at = now
            await suspend_for_billing(db, sub.tenant_id, "subscription_expired", system)
            await audit.record(
                db,
                actor=system,
                action="subscription.expired",
                resource_type="tenant_subscription",
                resource_id=sub.id,
                tenant_id=sub.tenant_id,
            )
            result.expired += 1

    await db.flush()
    return result
