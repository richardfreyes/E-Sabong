import asyncio
import re
import uuid
from dataclasses import dataclass

import dns.exception
import dns.resolver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import on_commit, platform_session
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.redis import get_redis
from app.core.security import new_opaque_token, utcnow
from app.core.tenancy import invalidate_tenant_cache
from app.modules.audit import service as audit
from app.modules.audit.service import Actor, RequestMeta
from app.modules.demo_points.models import AccountKind, PointAccount
from app.modules.subscriptions import service as billing
from app.modules.subscriptions.models import BillingCycle, SubscriptionPlan
from app.modules.tenants.models import (
    ALL_MODULES,
    DeploymentMode,
    DomainKind,
    DomainVerification,
    FeatureFlag,
    Tenant,
    TenantBranding,
    TenantDomain,
    TenantFeatureFlag,
    TenantSettings,
    TenantStatus,
)
from app.modules.users import service as users_service
from app.modules.users.models import AccountType, TenantUser
from app.modules.users.schemas import UserCreate

RESERVED_SLUGS = frozenset(
    {"admin", "api", "app", "docs", "help", "mail", "media", "owner", "platform", "static", "status", "support", "www"}
)
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?!-)(?:[a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
VERIFY_RECORD_PREFIX = "_esabong-verify"
MODULES_CACHE_TTL = 60


# ---------------------------------------------------------------- modules and flags


async def effective_modules(tenant_id: uuid.UUID) -> frozenset[str]:
    """Modules the tenant enabled AND its plan includes (licensed installs get all)."""
    redis = get_redis()
    key = f"tenant:modules:{tenant_id}"
    cached = await redis.get(key)
    if cached is not None:
        return frozenset(filter(None, cached.split(",")))
    async with platform_session() as db:
        tenant = await db.get(Tenant, tenant_id)
        settings = await db.get(TenantSettings, tenant_id)
        enabled = set(settings.enabled_modules if settings else ALL_MODULES)
        if tenant is not None and tenant.deployment_mode == DeploymentMode.DEDICATED:
            licensed = set(ALL_MODULES)
        else:
            sub = await billing.get_live(db, tenant_id)
            licensed = set(sub.plan.modules) if sub else set()
    modules = frozenset(enabled & licensed)
    await redis.set(key, ",".join(sorted(modules)), ex=MODULES_CACHE_TTL)
    return modules


async def effective_flags(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, bool]:
    flags = (await db.execute(select(FeatureFlag))).scalars().all()
    overrides = {
        o.flag_id: o.enabled
        for o in (await db.execute(select(TenantFeatureFlag).where(TenantFeatureFlag.tenant_id == tenant_id))).scalars()
    }
    return {f.key: overrides.get(f.id, f.default_enabled) for f in flags}


# ---------------------------------------------------------------- provisioning


def validate_slug(slug: str) -> str:
    slug = slug.strip().lower()
    if not SLUG_RE.match(slug):
        raise Unprocessable("Slug must be 3-63 characters: lowercase letters, digits and hyphens", code="invalid_slug")
    if slug in RESERVED_SLUGS:
        raise Unprocessable("This slug is reserved", code="invalid_slug")
    return slug


async def slug_available(db: AsyncSession, slug: str) -> bool:
    exists = (await db.execute(select(Tenant.id).where(Tenant.slug == slug, Tenant.deleted_at.is_(None)))).first()
    return exists is None


@dataclass
class ProvisionResult:
    tenant: Tenant
    admin: TenantUser
    temporary_password: str | None
    login_url: str


def tenant_base_url(slug: str) -> str:
    s = get_settings()
    return f"{s.public_scheme}://{slug}.{s.base_domain}"


async def provision_tenant(
    db: AsyncSession,
    *,
    name: str,
    slug: str,
    plan: SubscriptionPlan | None,
    billing_cycle: BillingCycle,
    admin_email: str,
    admin_name: str,
    admin_username: str = "admin",
    with_trial: bool = True,
    deployment_mode: DeploymentMode = DeploymentMode.SHARED,
    actor: Actor,
    meta: RequestMeta | None = None,
) -> ProvisionResult:
    """Create a fully usable tenant in one transaction (platform session)."""
    slug = validate_slug(slug)
    if not await slug_available(db, slug):
        raise Conflict("That workspace address is already taken", code="slug_taken")
    if deployment_mode == DeploymentMode.SHARED and plan is None:
        raise Unprocessable("A plan is required for shared tenants", code="plan_required")

    tenant = Tenant(id=uuid.uuid4(), slug=slug, name=name, status=TenantStatus.ACTIVE, deployment_mode=deployment_mode)
    db.add(tenant)
    await db.flush()

    s = get_settings()
    db.add(
        TenantDomain(
            tenant_id=tenant.id,
            domain=f"{slug}.{s.base_domain}",
            kind=DomainKind.SUBDOMAIN,
            is_primary=True,
            verification_status=DomainVerification.VERIFIED,
            verification_token=new_opaque_token(24),
            verified_at=utcnow(),
        )
    )
    db.add(TenantBranding(tenant_id=tenant.id, app_name=name, contact_email=admin_email))
    db.add(
        TenantSettings(
            tenant_id=tenant.id,
            enabled_modules=sorted(set(plan.modules) & set(ALL_MODULES)) if plan else list(ALL_MODULES),
        )
    )
    db.add(PointAccount(tenant_id=tenant.id, kind=AccountKind.ISSUANCE, user_id=None, balance=0))
    await users_service.seed_system_roles(db, tenant.id)
    await db.flush()

    admin, temporary = await users_service.create_user(
        db,
        tenant_id=tenant.id,
        data=UserCreate(username=admin_username, email=admin_email, full_name=admin_name, account_type=AccountType.ADMIN),
        actor=None,
        audit_actor=actor,
        meta=meta or RequestMeta(),
        enforce_limits=False,
    )

    if plan is not None:
        await billing.start_subscription(
            db, tenant_id=tenant.id, plan=plan, cycle=billing_cycle, with_trial=with_trial, actor=actor, meta=meta
        )

    await audit.record(
        db,
        actor=actor,
        action="tenant.provisioned",
        resource_type="tenant",
        resource_id=tenant.id,
        tenant_id=tenant.id,
        changes={"slug": slug, "name": name, "plan": plan.code if plan else None, "mode": deployment_mode},
        meta=meta,
    )
    login_url = f"{tenant_base_url(slug)}/admin/"
    if temporary:
        _queue_welcome_email(db, admin_email, admin_name, name, login_url, admin_username, temporary)
    return ProvisionResult(tenant=tenant, admin=admin, temporary_password=temporary, login_url=login_url)


def _queue_welcome_email(db: AsyncSession, to: str, name: str, workspace: str, url: str, username: str, password: str) -> None:
    async def _send() -> None:
        from app.workers.tasks.email import send_email

        send_email.delay(
            to,
            f"Your {workspace} workspace is ready",
            (
                f"Hello {name},\n\nYour workspace is ready at {url}\n\n"
                f"Username: {username}\nTemporary password: {password}\n\n"
                "You will be asked to choose a new password when you first sign in.\n"
            ),
        )

    on_commit(db, _send)


# ---------------------------------------------------------------- status


async def set_status(
    db: AsyncSession, tenant: Tenant, status: TenantStatus, *, reason: str | None, actor: Actor, meta: RequestMeta | None
) -> Tenant:
    if tenant.status == status:
        return tenant
    old = tenant.status
    tenant.status = status
    if status == TenantStatus.SUSPENDED:
        tenant.suspended_reason = reason or "suspended_by_platform"
        tenant.suspended_at = utcnow()
    elif status == TenantStatus.ACTIVE:
        tenant.suspended_reason = None
        tenant.suspended_at = None
    await audit.record(
        db,
        actor=actor,
        action=f"tenant.{status.value}",
        resource_type="tenant",
        resource_id=tenant.id,
        tenant_id=tenant.id,
        changes={"status": [old, status], "reason": reason},
        meta=meta,
    )
    tenant_id = tenant.id

    async def _invalidate() -> None:
        await invalidate_tenant_cache(tenant_id)

    on_commit(db, _invalidate)
    await db.flush()
    return tenant


# ---------------------------------------------------------------- domains


def normalize_domain(domain: str) -> str:
    d = domain.strip().lower().rstrip(".")
    if d.startswith(("http://", "https://")):
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0]
    if not DOMAIN_RE.match(d):
        raise Unprocessable("Enter a valid domain name, e.g. play.example.com", code="invalid_domain")
    s = get_settings()
    if d == s.base_domain or d.endswith("." + s.base_domain) or d == s.platform_host:
        raise Unprocessable("Platform domains cannot be added as custom domains", code="invalid_domain")
    return d


async def add_custom_domain(
    db: AsyncSession, tenant_id: uuid.UUID, domain: str, *, actor: Actor, meta: RequestMeta | None
) -> TenantDomain:
    d = normalize_domain(domain)
    # Global uniqueness check needs to see every tenant; the unique index enforces it
    # regardless, this lookup only produces a friendly error.
    async with platform_session() as pdb:
        taken = (await pdb.execute(select(TenantDomain.id).where(func.lower(TenantDomain.domain) == d))).first()
    if taken:
        raise Conflict("This domain is already registered", code="domain_taken")
    row = TenantDomain(
        tenant_id=tenant_id,
        domain=d,
        kind=DomainKind.CUSTOM,
        is_primary=False,
        verification_status=DomainVerification.PENDING,
        verification_token=new_opaque_token(24),
    )
    db.add(row)
    await db.flush()
    await audit.record(
        db,
        actor=actor,
        action="domain.added",
        resource_type="tenant_domain",
        resource_id=row.id,
        tenant_id=tenant_id,
        changes={"domain": d},
        meta=meta,
    )
    return row


def verification_instructions(row: TenantDomain) -> dict[str, str]:
    s = get_settings()
    return {
        "txt_name": f"{VERIFY_RECORD_PREFIX}.{row.domain}",
        "txt_value": row.verification_token,
        "cname_name": row.domain,
        "cname_value": s.base_domain,
    }


def _lookup_txt(name: str) -> list[str]:
    try:
        answers = dns.resolver.resolve(name, "TXT", lifetime=5)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        return []
    values = []
    for rdata in answers:
        values.append(b"".join(rdata.strings).decode(errors="ignore"))
    return values


async def check_domain(db: AsyncSession, row: TenantDomain, *, actor: Actor, meta: RequestMeta | None) -> TenantDomain:
    """Verify ownership through a DNS TXT record containing the verification token."""
    if row.kind != DomainKind.CUSTOM:
        return row
    name = f"{VERIFY_RECORD_PREFIX}.{row.domain}"
    values = await asyncio.to_thread(_lookup_txt, name)
    row.last_checked_at = utcnow()
    if row.verification_token in values:
        if row.verification_status != DomainVerification.VERIFIED:
            row.verification_status = DomainVerification.VERIFIED
            row.verified_at = utcnow()
            await audit.record(
                db,
                actor=actor,
                action="domain.verified",
                resource_type="tenant_domain",
                resource_id=row.id,
                tenant_id=row.tenant_id,
                changes={"domain": row.domain},
                meta=meta,
            )
            tenant_id, domain = row.tenant_id, row.domain

            async def _invalidate() -> None:
                await invalidate_tenant_cache(tenant_id, [domain])

            on_commit(db, _invalidate)
    elif row.verification_status != DomainVerification.VERIFIED:
        row.verification_status = DomainVerification.FAILED
    await db.flush()
    return row


async def remove_domain(
    db: AsyncSession, tenant_id: uuid.UUID, domain_id: uuid.UUID, *, actor: Actor, meta: RequestMeta | None
) -> None:
    row = (
        await db.execute(select(TenantDomain).where(TenantDomain.id == domain_id, TenantDomain.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Domain not found")
    if row.kind == DomainKind.SUBDOMAIN:
        raise Unprocessable("The platform subdomain cannot be removed", code="domain_protected")
    domain = row.domain
    await db.delete(row)
    await audit.record(
        db,
        actor=actor,
        action="domain.removed",
        resource_type="tenant_domain",
        resource_id=domain_id,
        tenant_id=tenant_id,
        changes={"domain": domain},
        meta=meta,
    )

    async def _invalidate() -> None:
        await invalidate_tenant_cache(tenant_id, [domain])

    on_commit(db, _invalidate)


async def set_primary_domain(db: AsyncSession, tenant_id: uuid.UUID, domain_id: uuid.UUID, *, actor: Actor) -> None:
    rows = (await db.execute(select(TenantDomain).where(TenantDomain.tenant_id == tenant_id))).scalars().all()
    target = next((r for r in rows if r.id == domain_id), None)
    if target is None:
        raise NotFound("Domain not found")
    if target.verification_status != DomainVerification.VERIFIED:
        raise Unprocessable("Verify the domain before making it primary", code="domain_unverified")
    for r in rows:
        if r.is_primary and r.id != domain_id:
            r.is_primary = False
    await db.flush()
    target.is_primary = True
    await audit.record(
        db,
        actor=actor,
        action="domain.primary_set",
        resource_type="tenant_domain",
        resource_id=domain_id,
        tenant_id=tenant_id,
        changes={"domain": target.domain},
    )
    await db.flush()


async def is_domain_allowed_for_tls(domain: str) -> bool:
    """Caddy on-demand TLS 'ask' hook: issue certificates only for known hosts."""
    d = domain.strip().lower().rstrip(".")
    s = get_settings()
    if d == s.platform_host:
        return True
    async with platform_session() as db:
        if d.endswith("." + s.base_domain):
            slug = d[: -len(s.base_domain) - 1]
            if "." not in slug and not await slug_available(db, slug):
                return True
        row = (
            await db.execute(
                select(TenantDomain.id).where(
                    func.lower(TenantDomain.domain) == d,
                    TenantDomain.verification_status == DomainVerification.VERIFIED,
                )
            )
        ).first()
        return row is not None
