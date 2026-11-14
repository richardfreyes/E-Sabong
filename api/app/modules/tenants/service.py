import asyncio
import ipaddress
import re
import socket
import ssl
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

import dns.exception
import dns.resolver
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import on_commit, platform_session
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.redis import get_redis
from app.core.security import new_opaque_token, utcnow
from app.core.tenancy import invalidate_tenant_cache
from app.modules.audit import service as audit
from app.modules.audit.service import Actor, RequestMeta
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
    {
        "admin",
        "api",
        "app",
        "docs",
        "help",
        "mail",
        "media",
        "owner",
        "platform",
        "staging",
        "static",
        "status",
        "support",
        "uat",
        "www",
    }
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
    login_identifier: str | None = None


def tenant_base_url(slug: str) -> str:
    s = get_settings()
    if s.root_tenant_slug and slug == s.root_tenant_slug:
        return f"{s.public_scheme}://{s.base_domain}"
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
    from app.modules.tenants.rental import RentalSettings

    rental = await db.get(RentalSettings, 1, with_for_update=True)
    if not (rental and rental.shared_domain_enabled):
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
    login_url = (
        f"{s.public_scheme}://{s.base_domain}/admin/"
        if rental and rental.shared_domain_enabled
        else f"{tenant_base_url(slug)}/admin/"
    )
    if rental and rental.shared_domain_enabled and rental.global_event_id:
        from types import SimpleNamespace

        from app.modules.tenants.global_arena import synchronize

        await synchronize(db, rental, SimpleNamespace(id=actor.id, actor=actor), meta or RequestMeta())
    login_identifier = f"{admin_username}@{slug}" if rental and rental.shared_domain_enabled else admin_username
    if temporary:
        _queue_welcome_email(db, admin_email, admin_name, name, login_url, login_identifier, temporary)
    return ProvisionResult(
        tenant=tenant, admin=admin, temporary_password=temporary, login_url=login_url, login_identifier=login_identifier
    )


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
    raw = domain.strip().lower()
    try:
        parsed = urlsplit(raw if "://" in raw else "https://" + raw)
    except ValueError:
        raise Unprocessable("Enter a valid domain name", code="invalid_domain") from None
    d = (parsed.hostname or "").rstrip(".")
    if (
        parsed.scheme not in ("http", "https")
        or parsed.netloc.rstrip(".") != d
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or not DOMAIN_RE.fullmatch(d)
        or any(label.startswith("-") or label.endswith("-") for label in d.split("."))
    ):
        raise Unprocessable("Enter a domain without a port, path or credentials", code="invalid_domain")
    s = get_settings()
    if d == s.base_domain or d.endswith("." + s.base_domain) or d == s.platform_host:
        raise Unprocessable("Platform domains cannot be added as custom domains", code="invalid_domain")
    return d


async def _flush_domain(db: AsyncSession) -> None:
    try:
        await db.flush()
    except IntegrityError as exc:
        if "uq_tenant_domains_domain" in str(exc):
            raise Conflict("This domain is already registered", code="domain_taken") from None
        raise


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
    await _flush_domain(db)
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
        "a_value": s.custom_domain_gateway_ips or "Use the public A/AAAA addresses of " + s.base_domain,
        "note": "Use CNAME for a subdomain, or A/AAAA at the apex. Keep the TXT record. Disable proxying while verifying.",
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


def _addresses(host: str) -> set[str]:
    result = set()
    for kind in ("A", "AAAA"):
        try:
            result.update(str(r) for r in dns.resolver.resolve(host, kind, lifetime=3))
        except (dns.exception.DNSException, OSError):
            pass
    return result


def _routing_addresses(domain: str) -> set[str]:
    s = get_settings()
    expected = (
        {v.strip() for v in s.custom_domain_gateway_ips.split(",") if v.strip()}
        if s.custom_domain_gateway_ips
        else _addresses(s.base_domain)
    )
    actual = _addresses(domain)
    # Reject private destinations and DNS rebinding; the TLS probe uses these exact IPs.
    if not actual or len(actual) > 8 or not actual <= expected or not all(ipaddress.ip_address(v).is_global for v in actual):
        return set()
    return actual


def _probe_tls(domain: str, addresses: set[str]) -> bool:
    context = ssl.create_default_context()
    try:
        for address in addresses:
            with socket.create_connection((address, 443), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname=domain):
                    pass  # Validates the trust chain, expiry and requested hostname.
        return True
    except (OSError, ssl.SSLError):
        return False


async def check_domain(db: AsyncSession, row: TenantDomain, *, actor: Actor, meta: RequestMeta | None) -> TenantDomain:
    """Require ownership and gateway routing; observe TLS separately from DNS."""
    await db.refresh(row, with_for_update=True)
    if row.kind != DomainKind.CUSTOM:
        return row
    was_verified = row.verification_status == DomainVerification.VERIFIED
    values = await asyncio.to_thread(_lookup_txt, f"{VERIFY_RECORD_PREFIX}.{row.domain}")
    addresses = await asyncio.to_thread(_routing_addresses, row.domain)
    row.last_checked_at = utcnow()
    row.connection_error = None
    if row.verification_token not in values or not addresses:
        row.verification_status = DomainVerification.FAILED
        row.ssl_status = "pending"
        row.is_primary = False
        row.connection_error = (
            "Ownership TXT record is missing"
            if row.verification_token not in values
            else "A/AAAA records must point only to this environment's public gateway"
        )
    else:
        row.verification_status = DomainVerification.VERIFIED
        row.verified_at = row.verified_at or utcnow()
        if not was_verified:
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
        # First commit ownership before Caddy's ask callback can approve the handshake.
        if was_verified and row.enabled:
            ok = await asyncio.to_thread(_probe_tls, row.domain, addresses)
            row.ssl_status = "active" if ok else "failed"
            row.ssl_checked_at = utcnow()
            if not ok:
                row.connection_error = "HTTPS certificate check failed; check gateway ports 80/443, CAA and Caddy logs"
        else:
            row.ssl_status = "pending"
    tenant_id, domain = row.tenant_id, row.domain

    async def invalidate() -> None:
        await invalidate_tenant_cache(tenant_id, [domain])

    on_commit(db, invalidate)
    await db.flush()
    return row


async def update_domain(
    db: AsyncSession, row: TenantDomain, *, domain: str | None, enabled: bool | None, actor: Actor, meta: RequestMeta | None
) -> TenantDomain:
    await db.refresh(row, with_for_update=True)
    if row.kind != DomainKind.CUSTOM:
        raise Unprocessable("The default domain cannot be changed", code="domain_protected")
    old = row.domain
    if domain is not None:
        value = normalize_domain(domain)
        if value != old:
            async with platform_session() as pdb:
                taken = await pdb.scalar(select(TenantDomain.id).where(func.lower(TenantDomain.domain) == value))
            if taken:
                raise Conflict("This domain is already registered", code="domain_taken")
            row.domain = value
            row.verification_token = new_opaque_token(24)
            row.verification_status = DomainVerification.PENDING
            row.verified_at = row.last_checked_at = row.ssl_checked_at = None
            row.connection_error = None
            row.ssl_status = "pending"
            row.is_primary = False
    if enabled is not None:
        row.enabled = enabled
        if not enabled:
            row.is_primary = False
    await _flush_domain(db)
    await audit.record(
        db,
        actor=actor,
        action="domain.updated",
        resource_type="tenant_domain",
        resource_id=row.id,
        tenant_id=row.tenant_id,
        changes={"domain": row.domain, "enabled": row.enabled},
        meta=meta,
    )
    tenant_id, new = row.tenant_id, row.domain

    async def invalidate() -> None:
        await invalidate_tenant_cache(tenant_id, [old, new])

    on_commit(db, invalidate)
    return row


def domain_output(row: TenantDomain):
    from app.modules.tenants.schemas import DomainOut

    out = DomainOut.model_validate(row)
    out.default_domain = f"{get_settings().public_scheme}://{get_settings().base_domain}"
    out.connection_status = (
        "disabled"
        if not row.enabled
        else "failed"
        if row.connection_error
        else "active"
        if row.verification_status == DomainVerification.VERIFIED and row.ssl_status == "active"
        else "verification_required"
        if row.verification_status != DomainVerification.VERIFIED
        else "pending"
    )
    if row.kind == DomainKind.CUSTOM:
        out.instructions = verification_instructions(row)
    return out


async def remove_domain(
    db: AsyncSession, tenant_id: uuid.UUID, domain_id: uuid.UUID, *, actor: Actor, meta: RequestMeta | None
) -> None:
    row = (
        await db.execute(
            select(TenantDomain).where(TenantDomain.id == domain_id, TenantDomain.tenant_id == tenant_id).with_for_update()
        )
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
    rows = (await db.execute(select(TenantDomain).where(TenantDomain.tenant_id == tenant_id).with_for_update())).scalars().all()
    target = next((r for r in rows if r.id == domain_id), None)
    if target is None:
        raise NotFound("Domain not found")
    if not target.enabled or target.verification_status != DomainVerification.VERIFIED:
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
        if d == s.base_domain:
            from app.modules.tenants.rental import RentalSettings

            rental = await db.get(RentalSettings, 1)
            if rental and rental.shared_domain_enabled:
                return True
            if s.root_tenant_slug:
                return not await slug_available(db, s.root_tenant_slug)
        if d.endswith("." + s.base_domain):
            slug = d[: -len(s.base_domain) - 1]
            if "." not in slug and not await slug_available(db, slug):
                return True
        row = (
            await db.execute(
                select(TenantDomain.id)
                .join(Tenant, Tenant.id == TenantDomain.tenant_id)
                .where(
                    Tenant.deleted_at.is_(None),
                    Tenant.status == TenantStatus.ACTIVE,
                    func.lower(TenantDomain.domain) == d,
                    TenantDomain.verification_status == DomainVerification.VERIFIED,
                    TenantDomain.enabled.is_(True),
                )
            )
        ).first()
        return row is not None
