from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select

from app.core.deps import Meta, PlatformDb, current_tenant_optional
from app.core.errors import NotFound, TooManyRequests, Unprocessable
from app.core.redis import get_redis
from app.core.tenancy import TenantContext
from app.modules.subscriptions.models import PlanKind, SubscriptionPlan
from app.modules.subscriptions.schemas import PlanOut
from app.modules.tenants import service
from app.modules.tenants.models import SignupRequest, TenantBranding, TenantSettings
from app.modules.tenants.schemas import (
    BrandingOut,
    PublicTenantConfig,
    SignupRequestCreate,
    SignupRequestOut,
)

router = APIRouter(prefix="/public", tags=["public"])
internal_router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


@router.get("/tenant", response_model=PublicTenantConfig)
async def public_tenant(
    response: Response,
    db: PlatformDb,
    tenant: Annotated[TenantContext | None, Depends(current_tenant_optional)],
) -> PublicTenantConfig:
    """Branding and public configuration for the tenant serving this host.

    Answers for suspended tenants too, so the portal can render a notice.
    """
    if tenant is None:
        raise NotFound("No tenant is configured for this host", code="tenant_not_found")
    branding = await db.get(TenantBranding, tenant.id)
    settings = await db.get(TenantSettings, tenant.id)
    assert branding is not None and settings is not None
    response.headers["Cache-Control"] = "public, max-age=60"
    return PublicTenantConfig(
        id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        status=tenant.status,
        branding=BrandingOut.model_validate(branding),
        modules=sorted(await service.effective_modules(tenant.id)),
        feature_flags=await service.effective_flags(db, tenant.id),
        allow_self_registration=settings.allow_self_registration,
        timezone=settings.timezone,
        locale=settings.locale,
    )


@router.get("/plans", response_model=list[PlanOut])
async def public_plans(db: PlatformDb) -> list[PlanOut]:
    plans = (
        (
            await db.execute(
                select(SubscriptionPlan)
                .where(SubscriptionPlan.is_active.is_(True), SubscriptionPlan.kind == PlanKind.SAAS)
                .order_by(SubscriptionPlan.sort_order, SubscriptionPlan.price_monthly_cents)
            )
        )
        .scalars()
        .all()
    )
    return [PlanOut.model_validate(p) for p in plans]


@router.get("/slug-availability")
async def slug_availability(db: PlatformDb, slug: Annotated[str, Query(min_length=1, max_length=63)]) -> dict:
    try:
        normalized = service.validate_slug(slug)
    except Unprocessable as exc:
        return {"slug": slug, "available": False, "reason": exc.message}
    pending = (
        await db.execute(
            select(SignupRequest.id).where(SignupRequest.desired_slug == normalized, SignupRequest.status == "pending")
        )
    ).first()
    available = await service.slug_available(db, normalized) and pending is None
    return {"slug": normalized, "available": available, "reason": None if available else "Already taken"}


@router.post("/signup-requests", response_model=SignupRequestOut, status_code=status.HTTP_201_CREATED)
async def create_signup_request(body: SignupRequestCreate, db: PlatformDb, meta: Meta) -> SignupRequestOut:
    redis = get_redis()
    key = f"ratelimit:signup:{meta.ip}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 3600)
    if count > 5:
        raise TooManyRequests("Too many requests. Try again later.")
    slug = service.validate_slug(body.desired_slug)
    if not await service.slug_available(db, slug):
        raise Unprocessable("That workspace address is already taken", code="slug_taken")
    plan = await db.get(SubscriptionPlan, body.plan_id)
    if plan is None or not plan.is_active or plan.kind != PlanKind.SAAS:
        raise Unprocessable("Choose an available plan", code="invalid_plan")
    row = SignupRequest(**body.model_dump(exclude={"desired_slug"}), desired_slug=slug)
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return SignupRequestOut.model_validate(row)


@internal_router.get("/tls-ask")
async def tls_ask(domain: Annotated[str, Query(max_length=253)]) -> Response:
    """Caddy on-demand TLS gate: 200 issues a certificate, anything else refuses."""
    allowed = await service.is_domain_allowed_for_tls(domain)
    return Response(status_code=200 if allowed else 404)
