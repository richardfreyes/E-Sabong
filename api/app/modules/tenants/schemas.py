import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import EmailStr, Field, StringConstraints, field_validator

from app.modules.subscriptions.models import BillingCycle
from app.modules.tenants.models import (
    ALL_MODULES,
    DeploymentMode,
    DomainKind,
    DomainVerification,
    SignupStatus,
    TenantStatus,
)
from app.shared.schemas import Schema

HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class BrandingOut(Schema):
    app_name: str
    logo_url: str | None
    favicon_url: str | None
    primary_color: str
    secondary_color: str
    background_color: str
    surface_color: str
    login_title: str | None
    login_subtitle: str | None
    login_background_url: str | None
    contact_email: str | None
    contact_phone: str | None
    contact_address: str | None
    footer_text: str | None


class BrandingUpdate(Schema):
    app_name: str | None = Field(default=None, min_length=1, max_length=120)
    primary_color: HexColor | None = None
    secondary_color: HexColor | None = None
    background_color: HexColor | None = None
    surface_color: HexColor | None = None
    login_title: str | None = Field(default=None, max_length=160)
    login_subtitle: str | None = Field(default=None, max_length=300)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=40)
    contact_address: str | None = Field(default=None, max_length=300)
    footer_text: str | None = Field(default=None, max_length=300)


class SettingsOut(Schema):
    timezone: str
    locale: str
    allow_self_registration: bool
    enabled_modules: list[str]
    suspension_grace_days: int


class SettingsUpdate(Schema):
    timezone: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=16)
    allow_self_registration: bool | None = None
    enabled_modules: list[str] | None = None

    @field_validator("enabled_modules")
    @classmethod
    def _modules(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        unknown = set(v) - set(ALL_MODULES)
        if unknown:
            raise ValueError(f"Unknown modules: {', '.join(sorted(unknown))}")
        return sorted(set(v))

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown timezone") from exc
        return v


class DomainOut(Schema):
    id: uuid.UUID
    domain: str
    kind: DomainKind
    is_primary: bool
    verification_status: DomainVerification
    verified_at: datetime | None
    last_checked_at: datetime | None
    instructions: dict[str, str] | None = None


class DomainCreate(Schema):
    domain: str = Field(min_length=4, max_length=253)


class PublicTenantConfig(Schema):
    id: uuid.UUID
    slug: str
    name: str
    status: TenantStatus
    branding: BrandingOut
    modules: list[str]
    feature_flags: dict[str, bool]
    allow_self_registration: bool
    timezone: str
    locale: str


class TenantOut(Schema):
    id: uuid.UUID
    slug: str
    name: str
    status: TenantStatus
    deployment_mode: DeploymentMode
    suspended_reason: str | None
    suspended_at: datetime | None
    created_at: datetime
    primary_domain: str | None = None
    plan_code: str | None = None
    plan_name: str | None = None
    subscription_status: str | None = None
    user_count: int | None = None


class TenantDetail(TenantOut):
    branding: BrandingOut | None = None
    settings: SettingsOut | None = None
    domains: list[DomainOut] = []


class TenantCreate(Schema):
    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(min_length=3, max_length=63)
    plan_id: uuid.UUID | None = None
    billing_cycle: BillingCycle = BillingCycle.MONTHLY
    with_trial: bool = True
    deployment_mode: DeploymentMode = DeploymentMode.SHARED
    admin_email: EmailStr
    admin_name: str = Field(min_length=1, max_length=200)
    admin_username: str = Field(default="admin", min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")


class TenantProvisioned(Schema):
    tenant: TenantOut
    admin_username: str
    temporary_password: str | None
    login_url: str


class TenantUpdate(Schema):
    name: str | None = Field(default=None, min_length=2, max_length=200)


class StatusChange(Schema):
    reason: str | None = Field(default=None, max_length=500)


class FeatureFlagOut(Schema):
    id: uuid.UUID
    key: str
    description: str | None
    default_enabled: bool


class FeatureFlagCreate(Schema):
    key: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_.\-]+$")
    description: str | None = Field(default=None, max_length=300)
    default_enabled: bool = False


class FeatureFlagUpdate(Schema):
    description: str | None = Field(default=None, max_length=300)
    default_enabled: bool | None = None


class TenantFlagState(Schema):
    key: str
    description: str | None
    default_enabled: bool
    override: bool | None
    effective: bool


class TenantFlagsUpdate(Schema):
    overrides: dict[str, bool | None] = Field(description="Flag key -> true/false to override, null to fall back to the default")


class UsagePoint(Schema):
    metric: str
    period_date: date
    value: int


class SignupRequestCreate(Schema):
    company_name: str = Field(min_length=2, max_length=200)
    contact_name: str = Field(min_length=1, max_length=200)
    contact_email: EmailStr
    contact_phone: str | None = Field(default=None, max_length=40)
    desired_slug: str = Field(min_length=3, max_length=63)
    plan_id: uuid.UUID
    billing_cycle: BillingCycle = BillingCycle.MONTHLY
    message: str | None = Field(default=None, max_length=2000)


class SignupRequestOut(Schema):
    id: uuid.UUID
    company_name: str
    contact_name: str
    contact_email: str
    contact_phone: str | None
    desired_slug: str
    plan_id: uuid.UUID
    billing_cycle: str
    message: str | None
    status: SignupStatus
    reviewed_at: datetime | None
    review_note: str | None
    tenant_id: uuid.UUID | None
    created_at: datetime


class SignupDecision(Schema):
    note: str | None = Field(default=None, max_length=500)
    slug: str | None = Field(default=None, min_length=3, max_length=63, description="Override desired slug")
    with_trial: bool = True
