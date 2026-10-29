import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.models import Base, SoftDelete, TenantScoped, Timestamps, UUIDPrimaryKey
from app.shared.types import str_enum


class TenantStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class DeploymentMode(StrEnum):
    SHARED = "shared"
    DEDICATED = "dedicated"


class DomainKind(StrEnum):
    SUBDOMAIN = "subdomain"
    CUSTOM = "custom"


class DomainVerification(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"


class SignupStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


HEX_COLOR = "^#[0-9a-fA-F]{6}$"

ALL_MODULES = ("events", "matches", "points", "reports", "notifications")


class Tenant(Base, UUIDPrimaryKey, Timestamps, SoftDelete):
    __tablename__ = "tenants"
    __table_args__ = (
        Index("uq_tenants_slug_live", "slug", unique=True, postgresql_where=text("deleted_at IS NULL")),
        CheckConstraint("slug ~ '^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$'", name="slug_format"),
    )

    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[TenantStatus] = mapped_column(str_enum(TenantStatus), nullable=False, default=TenantStatus.ACTIVE)
    deployment_mode: Mapped[DeploymentMode] = mapped_column(
        str_enum(DeploymentMode), nullable=False, default=DeploymentMode.SHARED
    )
    suspended_reason: Mapped[str | None] = mapped_column(String(500))
    suspended_at: Mapped[datetime | None]

    branding: Mapped["TenantBranding"] = relationship(back_populates="tenant", uselist=False, lazy="raise")
    settings: Mapped["TenantSettings"] = relationship(back_populates="tenant", uselist=False, lazy="raise")
    domains: Mapped[list["TenantDomain"]] = relationship(back_populates="tenant", lazy="raise")


class TenantDomain(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "tenant_domains"
    __table_args__ = (
        Index("uq_tenant_domains_domain", func.lower(text("domain")), unique=True),
        Index(
            "uq_tenant_domains_primary",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    kind: Mapped[DomainKind] = mapped_column(str_enum(DomainKind), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_status: Mapped[DomainVerification] = mapped_column(
        str_enum(DomainVerification), nullable=False, default=DomainVerification.PENDING
    )
    verification_token: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime | None]
    last_checked_at: Mapped[datetime | None]

    tenant: Mapped[Tenant] = relationship(back_populates="domains", lazy="raise")


class TenantBranding(Base, Timestamps):
    __tablename__ = "tenant_branding"
    __table_args__ = tuple(
        CheckConstraint(f"{col} ~ '{HEX_COLOR}'", name=f"{col}_hex")
        for col in ("primary_color", "secondary_color", "background_color", "surface_color")
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    app_name: Mapped[str] = mapped_column(String(120), nullable=False)
    logo_url: Mapped[str | None] = mapped_column(String(500))
    favicon_url: Mapped[str | None] = mapped_column(String(500))
    primary_color: Mapped[str] = mapped_column(String(7), nullable=False, default="#B3261E")
    secondary_color: Mapped[str] = mapped_column(String(7), nullable=False, default="#1D4ED8")
    background_color: Mapped[str] = mapped_column(String(7), nullable=False, default="#FFFBFF")
    surface_color: Mapped[str] = mapped_column(String(7), nullable=False, default="#FFFFFF")
    login_title: Mapped[str | None] = mapped_column(String(160))
    login_subtitle: Mapped[str | None] = mapped_column(String(300))
    login_background_url: Mapped[str | None] = mapped_column(String(500))
    contact_email: Mapped[str | None] = mapped_column(String(254))
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    contact_address: Mapped[str | None] = mapped_column(String(300))
    footer_text: Mapped[str | None] = mapped_column(String(300))

    tenant: Mapped[Tenant] = relationship(back_populates="branding", lazy="raise")


class TenantSettings(Base, Timestamps):
    __tablename__ = "tenant_settings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Manila")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    allow_self_registration: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled_modules: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False, default=lambda: list(ALL_MODULES))
    suspension_grace_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)

    tenant: Mapped[Tenant] = relationship(back_populates="settings", lazy="raise")


class FeatureFlag(Base, UUIDPrimaryKey, Timestamps):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(300))
    default_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TenantFeatureFlag(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "tenant_feature_flags"
    __table_args__ = (Index("uq_tenant_feature_flags", "tenant_id", "flag_id", unique=True),)

    flag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feature_flags.id", ondelete="CASCADE"), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)

    flag: Mapped[FeatureFlag] = relationship(lazy="selectin")


class TenantUsage(Base, UUIDPrimaryKey, Timestamps, TenantScoped):
    __tablename__ = "tenant_usage"
    __table_args__ = (Index("uq_tenant_usage_metric_day", "tenant_id", "metric", "period_date", unique=True),)

    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    period_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class SignupRequest(Base, UUIDPrimaryKey, Timestamps):
    __tablename__ = "signup_requests"
    __table_args__ = (Index("ix_signup_requests_status_created", "status", "created_at"),)

    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(254), nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    desired_slug: Mapped[str] = mapped_column(String(63), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subscription_plans.id", ondelete="RESTRICT"), nullable=False)
    billing_cycle: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[SignupStatus] = mapped_column(str_enum(SignupStatus), nullable=False, default=SignupStatus.PENDING)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("platform_users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None]
    review_note: Mapped[str | None] = mapped_column(String(500))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"))
