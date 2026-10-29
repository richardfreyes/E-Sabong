"""Resolves the tenant for a request from its Host header (or dev-only header)."""

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass

from fastapi import Request
from sqlalchemy import func, or_, select

from app.core.config import get_settings
from app.core.database import platform_session
from app.core.redis import get_redis
from app.modules.tenants.models import DomainVerification, Tenant, TenantDomain, TenantStatus

CACHE_TTL = 60
NEGATIVE_TTL = 10


@dataclass(frozen=True)
class TenantContext:
    id: uuid.UUID
    slug: str
    name: str
    status: TenantStatus

    @property
    def is_operational(self) -> bool:
        return self.status == TenantStatus.ACTIVE

    def to_json(self) -> str:
        d = asdict(self)
        d["id"] = str(self.id)
        return json.dumps(d)

    @classmethod
    def from_json(cls, raw: str) -> "TenantContext":
        d = json.loads(raw)
        return cls(id=uuid.UUID(d["id"]), slug=d["slug"], name=d["name"], status=TenantStatus(d["status"]))


def request_host(request: Request) -> str:
    # Caddy preserves the original Host when proxying; X-Forwarded-Host is not trusted.
    host = request.headers.get("host") or ""
    return host.split(",")[0].strip().split(":")[0].lower().rstrip(".")


async def _lookup(host: str | None, slug: str | None) -> TenantContext | None:
    s = get_settings()
    async with platform_session() as db:
        stmt = select(Tenant).where(Tenant.deleted_at.is_(None))
        if slug is not None:
            stmt = stmt.where(Tenant.slug == slug)
        else:
            assert host is not None
            sub_slug = None
            suffix = "." + s.base_domain
            if host.endswith(suffix):
                candidate = host[: -len(suffix)]
                if candidate and "." not in candidate:
                    sub_slug = candidate
            domain_match = (
                select(TenantDomain.tenant_id)
                .where(
                    func.lower(TenantDomain.domain) == host,
                    TenantDomain.verification_status == DomainVerification.VERIFIED,
                )
                .scalar_subquery()
            )
            conditions = [Tenant.id == domain_match]
            if sub_slug:
                conditions.append(Tenant.slug == sub_slug)
            stmt = stmt.where(or_(*conditions))
        tenant = (await db.execute(stmt.limit(1))).scalar_one_or_none()
        if tenant is None:
            return None
        return TenantContext(id=tenant.id, slug=tenant.slug, name=tenant.name, status=tenant.status)


async def _cached(key: str, tenant_key_fn: Callable[[], Awaitable[TenantContext | None]]) -> TenantContext | None:
    redis = get_redis()
    raw = await redis.get(key)
    if raw == "none":
        return None
    if raw:
        return TenantContext.from_json(raw)
    ctx = await tenant_key_fn()
    if ctx is None:
        await redis.set(key, "none", ex=NEGATIVE_TTL)
        return None
    await redis.set(key, ctx.to_json(), ex=CACHE_TTL)
    await redis.sadd(f"tenant:cachekeys:{ctx.id}", key)
    await redis.expire(f"tenant:cachekeys:{ctx.id}", CACHE_TTL * 2)
    return ctx


async def resolve_tenant(request: Request, dev_slug: str | None = None) -> TenantContext | None:
    """``dev_slug`` carries the development-only tenant hint for WebSockets, which
    cannot send custom headers; it is ignored unless ALLOW_TENANT_HEADER is set."""
    s = get_settings()
    if s.deployment_mode == "dedicated":
        slug = s.dedicated_tenant_slug
        return await _cached(f"tenant:slug:{slug}", lambda: _lookup(None, slug))

    host = request_host(request)
    if s.allow_tenant_header:
        header_slug = (dev_slug or request.headers.get("x-tenant-slug", "")).strip().lower()
        if header_slug:
            return await _cached(f"tenant:slug:{header_slug}", lambda: _lookup(None, header_slug))
    if not host or host == s.platform_host:
        return None
    return await _cached(f"tenant:host:{host}", lambda: _lookup(host, None))


async def invalidate_tenant_cache(tenant_id: uuid.UUID, extra_hosts: list[str] | None = None) -> None:
    redis = get_redis()
    keys = set(await redis.smembers(f"tenant:cachekeys:{tenant_id}"))
    keys.update(f"tenant:host:{h.lower()}" for h in (extra_hosts or []))
    keys.add(f"tenant:modules:{tenant_id}")
    keys.add(f"tenant:cachekeys:{tenant_id}")
    if keys:
        await redis.delete(*keys)
