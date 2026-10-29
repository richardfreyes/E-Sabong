import uuid
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from app.core.config import get_settings
from app.core.money import SUPPORTED_CURRENCIES
from app.modules.subscriptions.models import (
    BillingCycle,
    InvoiceLineKind,
    InvoiceStatus,
    PlanKind,
    SubscriptionStatus,
)
from app.modules.tenants.models import ALL_MODULES
from app.shared.schemas import Schema

KNOWN_LIMITS = {"max_users", "max_events_per_month", "max_admins"}


class PlanOut(Schema):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    kind: PlanKind
    price_monthly_cents: int
    price_annual_cents: int
    currency: str
    trial_days: int
    limits: dict[str, Any]
    modules: list[str]
    is_active: bool
    sort_order: int


class _PlanFields(Schema):
    @field_validator("modules", check_fields=False)
    @classmethod
    def _modules(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        unknown = set(v) - set(ALL_MODULES)
        if unknown:
            raise ValueError(f"Unknown modules: {', '.join(sorted(unknown))}")
        return sorted(set(v))

    @field_validator("limits", check_fields=False)
    @classmethod
    def _limits(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return v
        unknown = set(v) - KNOWN_LIMITS
        if unknown:
            raise ValueError(f"Unknown limits: {', '.join(sorted(unknown))}")
        for key, value in v.items():
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(f"{key} must be a non-negative integer or null")
        return v


class PlanCreate(_PlanFields):
    code: str = Field(min_length=2, max_length=48, pattern=r"^[a-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    kind: PlanKind = PlanKind.SAAS
    price_monthly_cents: int = Field(ge=0)
    price_annual_cents: int = Field(ge=0)
    currency: str = Field(
        default_factory=lambda: get_settings().default_currency,
        pattern=r"^[A-Z]{3}$",
        description=f"ISO 4217 code, one of {', '.join(SUPPORTED_CURRENCIES)}; defaults to the platform DEFAULT_CURRENCY",
        examples=["PHP"],
    )
    trial_days: int = Field(default=14, ge=0, le=365)
    limits: dict[str, Any] = Field(default_factory=dict)
    modules: list[str] = Field(default_factory=lambda: list(ALL_MODULES))
    is_active: bool = True
    sort_order: int = 0

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported currency; expected one of: {', '.join(SUPPORTED_CURRENCIES)}")
        return v


class PlanUpdate(_PlanFields):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    price_monthly_cents: int | None = Field(default=None, ge=0)
    price_annual_cents: int | None = Field(default=None, ge=0)
    trial_days: int | None = Field(default=None, ge=0, le=365)
    limits: dict[str, Any] | None = None
    modules: list[str] | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class SubscriptionOut(Schema):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str | None = None
    tenant_slug: str | None = None
    plan: PlanOut
    pending_plan: PlanOut | None
    billing_cycle: BillingCycle
    pending_billing_cycle: BillingCycle | None
    status: SubscriptionStatus
    trial_ends_at: datetime | None
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    cancelled_at: datetime | None
    expired_at: datetime | None
    created_at: datetime


class SubscriptionStart(Schema):
    plan_id: uuid.UUID
    billing_cycle: BillingCycle = BillingCycle.MONTHLY
    with_trial: bool = False


class ChangePlan(Schema):
    plan_id: uuid.UUID
    billing_cycle: BillingCycle


class CancelSubscription(Schema):
    immediately: bool = False


class InvoiceLineOut(Schema):
    id: uuid.UUID
    kind: InvoiceLineKind
    description: str
    quantity: int
    unit_amount_cents: int
    amount_cents: int


class InvoiceOut(Schema):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str | None = None
    subscription_id: uuid.UUID | None
    number: str
    status: InvoiceStatus
    currency: str
    subtotal_cents: int
    tax_cents: int
    total_cents: int
    period_start: datetime | None
    period_end: datetime | None
    issued_at: datetime | None
    due_at: datetime | None
    paid_at: datetime | None
    payment_reference: str | None
    notes: str | None
    lines: list[InvoiceLineOut] = []


class MarkPaid(Schema):
    payment_reference: str = Field(min_length=1, max_length=120)


class VoidInvoice(Schema):
    reason: str = Field(min_length=1, max_length=300)


class PlanChangeResult(Schema):
    subscription: SubscriptionOut
    invoice: InvoiceOut | None
    mode: str
