import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.models import Base, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum


class PlanKind(StrEnum):
    SAAS = "saas"
    MAINTENANCE = "maintenance"  # support/updates for dedicated licenses


class BillingCycle(StrEnum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


class SubscriptionStatus(StrEnum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


LIVE_SUBSCRIPTION_STATUSES = (
    SubscriptionStatus.TRIALING,
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE,
)


class InvoiceStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    VOID = "void"


class InvoiceLineKind(StrEnum):
    PLAN = "plan"
    PRORATION = "proration"
    CREDIT = "credit"


class SubscriptionPlan(Base, UUIDPrimaryKey, Timestamps):
    __tablename__ = "subscription_plans"
    __table_args__ = (
        CheckConstraint("price_monthly_cents >= 0 AND price_annual_cents >= 0", name="prices"),
        CheckConstraint("trial_days >= 0", name="trial_days"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_code"),
    )

    code: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    kind: Mapped[PlanKind] = mapped_column(str_enum(PlanKind), nullable=False, default=PlanKind.SAAS)
    price_monthly_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_annual_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Set explicitly from settings.default_currency on create; the server default only covers raw SQL inserts.
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="PHP")
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    # e.g. {"max_users": 500, "max_events_per_month": 30}; null/absent = unlimited
    limits: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    modules: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def price_for(self, cycle: BillingCycle) -> int:
        return self.price_annual_cents if cycle == BillingCycle.ANNUAL else self.price_monthly_cents


class TenantSubscription(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "tenant_subscriptions"
    __table_args__ = (
        # At most one live subscription per tenant.
        Index(
            "uq_tenant_subscriptions_live",
            "tenant_id",
            unique=True,
            postgresql_where=text("status IN ('trialing', 'active', 'past_due')"),
        ),
        Index("ix_tenant_subscriptions_period_end", "status", "current_period_end"),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subscription_plans.id", ondelete="RESTRICT"), nullable=False)
    billing_cycle: Mapped[BillingCycle] = mapped_column(str_enum(BillingCycle), nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(str_enum(SubscriptionStatus), nullable=False)
    trial_ends_at: Mapped[datetime | None]
    current_period_start: Mapped[datetime] = mapped_column(nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(nullable=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pending_plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subscription_plans.id", ondelete="RESTRICT"))
    pending_billing_cycle: Mapped[BillingCycle | None] = mapped_column(str_enum(BillingCycle, name="pending_billing_cycle"))
    cancelled_at: Mapped[datetime | None]
    expired_at: Mapped[datetime | None]

    plan: Mapped[SubscriptionPlan] = relationship(foreign_keys=[plan_id], lazy="selectin")
    pending_plan: Mapped[SubscriptionPlan | None] = relationship(foreign_keys=[pending_plan_id], lazy="selectin")


class Invoice(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint("total_cents = subtotal_cents + tax_cents", name="total_consistent"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_code"),
        Index("ix_invoices_tenant_issued", "tenant_id", "issued_at"),
        Index("ix_invoices_status_due", "status", "due_at"),
    )

    subscription_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant_subscriptions.id", ondelete="SET NULL"))
    number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    status: Mapped[InvoiceStatus] = mapped_column(str_enum(InvoiceStatus), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period_start: Mapped[datetime | None]
    period_end: Mapped[datetime | None]
    issued_at: Mapped[datetime | None]
    due_at: Mapped[datetime | None]
    paid_at: Mapped[datetime | None]
    payment_reference: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(String(500))

    lines: Mapped[list["InvoiceLine"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="InvoiceLine.position"
    )


class InvoiceLine(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "invoice_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kind: Mapped[InvoiceLineKind] = mapped_column(str_enum(InvoiceLineKind), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
