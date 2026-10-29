import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import func, or_, select

from app.core.deps import Meta, PlatformAdmin, PlatformDb, PlatformReader
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.security import utcnow
from app.core.tenancy import invalidate_tenant_cache
from app.modules.audit import service as audit
from app.modules.subscriptions.models import (
    LIVE_SUBSCRIPTION_STATUSES,
    SubscriptionPlan,
    TenantSubscription,
)
from app.modules.tenants import service
from app.modules.tenants.models import (
    FeatureFlag,
    SignupRequest,
    SignupStatus,
    Tenant,
    TenantBranding,
    TenantDomain,
    TenantFeatureFlag,
    TenantSettings,
    TenantStatus,
    TenantUsage,
)
from app.modules.tenants.schemas import (
    BrandingOut,
    BrandingUpdate,
    DomainCreate,
    DomainOut,
    FeatureFlagCreate,
    FeatureFlagOut,
    FeatureFlagUpdate,
    SettingsOut,
    SignupDecision,
    SignupRequestOut,
    StatusChange,
    TenantCreate,
    TenantDetail,
    TenantFlagState,
    TenantFlagsUpdate,
    TenantOut,
    TenantProvisioned,
    TenantUpdate,
    UsagePoint,
)
from app.modules.users.models import TenantUser
from app.shared.schemas import Message, Page, PageDep

router = APIRouter(prefix="/platform", tags=["platform: tenants"])


async def _get_tenant(db: PlatformDb, tenant_id: uuid.UUID) -> Tenant:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.deleted_at is not None:
        raise NotFound("Tenant not found")
    return tenant


async def _tenant_outs(db: PlatformDb, tenants: list[Tenant]) -> list[TenantOut]:
    ids = [t.id for t in tenants]
    if not ids:
        return []
    domains = dict(
        (
            await db.execute(
                select(TenantDomain.tenant_id, TenantDomain.domain).where(
                    TenantDomain.tenant_id.in_(ids), TenantDomain.is_primary.is_(True)
                )
            )
        )
        .tuples()
        .all()
    )
    subs = {
        s.tenant_id: s
        for s in (
            await db.execute(
                select(TenantSubscription).where(
                    TenantSubscription.tenant_id.in_(ids),
                    TenantSubscription.status.in_(LIVE_SUBSCRIPTION_STATUSES),
                )
            )
        ).scalars()
    }
    counts = dict(
        (
            await db.execute(
                select(TenantUser.tenant_id, func.count())
                .where(TenantUser.tenant_id.in_(ids), TenantUser.deleted_at.is_(None))
                .group_by(TenantUser.tenant_id)
            )
        )
        .tuples()
        .all()
    )
    out = []
    for t in tenants:
        item = TenantOut.model_validate(t)
        item.primary_domain = domains.get(t.id)
        sub = subs.get(t.id)
        if sub:
            item.plan_code, item.plan_name, item.subscription_status = sub.plan.code, sub.plan.name, sub.status.value
        item.user_count = int(counts.get(t.id, 0))
        out.append(item)
    return out


def _domain_out(row: TenantDomain) -> DomainOut:
    out = DomainOut.model_validate(row)
    if row.kind.value == "custom":
        out.instructions = service.verification_instructions(row)
    return out


# ---------------------------------------------------------------- tenants


@router.get("/tenants", response_model=Page[TenantOut])
async def list_tenants(
    _: PlatformReader,
    db: PlatformDb,
    params: PageDep,
    status_: Annotated[TenantStatus | None, Query(alias="status")] = None,
) -> Page[TenantOut]:
    stmt = select(Tenant).where(Tenant.deleted_at.is_(None))
    if status_:
        stmt = stmt.where(Tenant.status == status_)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(Tenant.name.ilike(like), Tenant.slug.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    stmt = stmt.order_by(
        params.order_by({"name": Tenant.name, "created_at": Tenant.created_at, "slug": Tenant.slug}, Tenant.created_at.desc())
    )
    rows = (await db.execute(stmt.offset(params.offset).limit(params.page_size))).scalars().all()
    return Page(items=await _tenant_outs(db, list(rows)), total=total, page=params.page, page_size=params.page_size)


@router.post("/tenants", response_model=TenantProvisioned, status_code=status.HTTP_201_CREATED)
async def create_tenant(body: TenantCreate, current: PlatformAdmin, db: PlatformDb, meta: Meta) -> TenantProvisioned:
    plan = await db.get(SubscriptionPlan, body.plan_id) if body.plan_id else None
    if body.plan_id and plan is None:
        raise Unprocessable("Plan not found", code="invalid_plan")
    result = await service.provision_tenant(
        db,
        name=body.name,
        slug=body.slug,
        plan=plan,
        billing_cycle=body.billing_cycle,
        admin_email=body.admin_email,
        admin_name=body.admin_name,
        admin_username=body.admin_username,
        with_trial=body.with_trial,
        deployment_mode=body.deployment_mode,
        actor=current.actor,
        meta=meta,
    )
    [out] = await _tenant_outs(db, [result.tenant])
    return TenantProvisioned(
        tenant=out,
        admin_username=result.admin.username,
        temporary_password=result.temporary_password,
        login_url=result.login_url,
    )


@router.get("/tenants/{tenant_id}", response_model=TenantDetail)
async def get_tenant(tenant_id: uuid.UUID, _: PlatformReader, db: PlatformDb) -> TenantDetail:
    tenant = await _get_tenant(db, tenant_id)
    [base] = await _tenant_outs(db, [tenant])
    detail = TenantDetail(**base.model_dump())
    branding = await db.get(TenantBranding, tenant_id)
    settings = await db.get(TenantSettings, tenant_id)
    detail.branding = BrandingOut.model_validate(branding) if branding else None
    detail.settings = SettingsOut.model_validate(settings) if settings else None
    rows = (
        (await db.execute(select(TenantDomain).where(TenantDomain.tenant_id == tenant_id).order_by(TenantDomain.created_at)))
        .scalars()
        .all()
    )
    detail.domains = [_domain_out(r) for r in rows]
    return detail


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: uuid.UUID, body: TenantUpdate, current: PlatformAdmin, db: PlatformDb, meta: Meta
) -> TenantOut:
    tenant = await _get_tenant(db, tenant_id)
    changes = {}
    if body.name and body.name != tenant.name:
        changes["name"] = [tenant.name, body.name]
        tenant.name = body.name
    if changes:
        await audit.record(
            db,
            actor=current.actor,
            action="tenant.updated",
            resource_type="tenant",
            resource_id=tenant.id,
            tenant_id=tenant.id,
            changes=changes,
            meta=meta,
        )
        await db.flush()
        await invalidate_tenant_cache(tenant.id)
    [out] = await _tenant_outs(db, [tenant])
    return out


@router.post("/tenants/{tenant_id}/suspend", response_model=TenantOut)
async def suspend_tenant(
    tenant_id: uuid.UUID, body: StatusChange, current: PlatformAdmin, db: PlatformDb, meta: Meta
) -> TenantOut:
    tenant = await _get_tenant(db, tenant_id)
    await service.set_status(db, tenant, TenantStatus.SUSPENDED, reason=body.reason, actor=current.actor, meta=meta)
    [out] = await _tenant_outs(db, [tenant])
    return out


@router.post("/tenants/{tenant_id}/activate", response_model=TenantOut)
async def activate_tenant(
    tenant_id: uuid.UUID, body: StatusChange, current: PlatformAdmin, db: PlatformDb, meta: Meta
) -> TenantOut:
    tenant = await _get_tenant(db, tenant_id)
    if tenant.status == TenantStatus.CANCELLED:
        raise Unprocessable("Cancelled tenants cannot be reactivated", code="tenant_cancelled")
    await service.set_status(db, tenant, TenantStatus.ACTIVE, reason=body.reason, actor=current.actor, meta=meta)
    [out] = await _tenant_outs(db, [tenant])
    return out


@router.delete("/tenants/{tenant_id}", response_model=Message)
async def delete_tenant(tenant_id: uuid.UUID, current: PlatformAdmin, db: PlatformDb, meta: Meta) -> Message:
    tenant = await _get_tenant(db, tenant_id)
    await service.set_status(db, tenant, TenantStatus.CANCELLED, reason="deleted", actor=current.actor, meta=meta)
    tenant.deleted_at = utcnow()
    return Message(message="Tenant cancelled and archived")


# ---------------------------------------------------------------- domains


@router.get("/tenants/{tenant_id}/domains", response_model=list[DomainOut])
async def list_domains(tenant_id: uuid.UUID, _: PlatformReader, db: PlatformDb) -> list[DomainOut]:
    await _get_tenant(db, tenant_id)
    rows = (
        (await db.execute(select(TenantDomain).where(TenantDomain.tenant_id == tenant_id).order_by(TenantDomain.created_at)))
        .scalars()
        .all()
    )
    return [_domain_out(r) for r in rows]


@router.post("/tenants/{tenant_id}/domains", response_model=DomainOut, status_code=status.HTTP_201_CREATED)
async def add_domain(tenant_id: uuid.UUID, body: DomainCreate, current: PlatformAdmin, db: PlatformDb, meta: Meta) -> DomainOut:
    await _get_tenant(db, tenant_id)
    row = await service.add_custom_domain(db, tenant_id, body.domain, actor=current.actor, meta=meta)
    return _domain_out(row)


async def _get_domain(db: PlatformDb, domain_id: uuid.UUID) -> TenantDomain:
    row = await db.get(TenantDomain, domain_id)
    if row is None:
        raise NotFound("Domain not found")
    return row


@router.post("/domains/{domain_id}/verify", response_model=DomainOut)
async def verify_domain(domain_id: uuid.UUID, current: PlatformAdmin, db: PlatformDb, meta: Meta) -> DomainOut:
    row = await service.check_domain(db, await _get_domain(db, domain_id), actor=current.actor, meta=meta)
    return _domain_out(row)


@router.post("/domains/{domain_id}/primary", response_model=DomainOut)
async def make_primary(domain_id: uuid.UUID, current: PlatformAdmin, db: PlatformDb) -> DomainOut:
    row = await _get_domain(db, domain_id)
    await service.set_primary_domain(db, row.tenant_id, domain_id, actor=current.actor)
    return _domain_out(row)


@router.delete("/domains/{domain_id}", response_model=Message)
async def delete_domain(domain_id: uuid.UUID, current: PlatformAdmin, db: PlatformDb, meta: Meta) -> Message:
    row = await _get_domain(db, domain_id)
    await service.remove_domain(db, row.tenant_id, domain_id, actor=current.actor, meta=meta)
    return Message(message="Domain removed")


# ---------------------------------------------------------------- branding


@router.get("/tenants/{tenant_id}/branding", response_model=BrandingOut)
async def get_branding(tenant_id: uuid.UUID, _: PlatformReader, db: PlatformDb) -> BrandingOut:
    branding = await db.get(TenantBranding, tenant_id)
    if branding is None:
        raise NotFound("Tenant not found")
    return BrandingOut.model_validate(branding)


@router.put("/tenants/{tenant_id}/branding", response_model=BrandingOut)
async def put_branding(
    tenant_id: uuid.UUID, body: BrandingUpdate, current: PlatformAdmin, db: PlatformDb, meta: Meta
) -> BrandingOut:
    branding = await db.get(TenantBranding, tenant_id)
    if branding is None:
        raise NotFound("Tenant not found")
    fields = body.model_dump(exclude_unset=True)
    before = {k: getattr(branding, k) for k in fields}
    for k, v in fields.items():
        setattr(branding, k, v)
    await audit.record(
        db,
        actor=current.actor,
        action="branding.updated",
        resource_type="tenant_branding",
        resource_id=tenant_id,
        tenant_id=tenant_id,
        changes=audit.diff(before, fields),
        meta=meta,
    )
    await db.flush()
    await db.refresh(branding)
    return BrandingOut.model_validate(branding)


# ---------------------------------------------------------------- feature flags


@router.get("/feature-flags", response_model=list[FeatureFlagOut])
async def list_flags(_: PlatformReader, db: PlatformDb) -> list[FeatureFlagOut]:
    rows = (await db.execute(select(FeatureFlag).order_by(FeatureFlag.key))).scalars().all()
    return [FeatureFlagOut.model_validate(r) for r in rows]


@router.post("/feature-flags", response_model=FeatureFlagOut, status_code=status.HTTP_201_CREATED)
async def create_flag(body: FeatureFlagCreate, current: PlatformAdmin, db: PlatformDb, meta: Meta) -> FeatureFlagOut:
    if (await db.execute(select(FeatureFlag.id).where(FeatureFlag.key == body.key))).first():
        raise Conflict("A flag with this key already exists", code="flag_exists")
    flag = FeatureFlag(**body.model_dump())
    db.add(flag)
    await db.flush()
    await db.refresh(flag)
    await audit.record(
        db,
        actor=current.actor,
        action="feature_flag.created",
        resource_type="feature_flag",
        resource_id=flag.id,
        changes=body.model_dump(),
        meta=meta,
    )
    return FeatureFlagOut.model_validate(flag)


@router.patch("/feature-flags/{flag_id}", response_model=FeatureFlagOut)
async def update_flag(
    flag_id: uuid.UUID, body: FeatureFlagUpdate, current: PlatformAdmin, db: PlatformDb, meta: Meta
) -> FeatureFlagOut:
    flag = await db.get(FeatureFlag, flag_id)
    if flag is None:
        raise NotFound("Flag not found")
    fields = body.model_dump(exclude_unset=True)
    before = {k: getattr(flag, k) for k in fields}
    for k, v in fields.items():
        setattr(flag, k, v)
    await audit.record(
        db,
        actor=current.actor,
        action="feature_flag.updated",
        resource_type="feature_flag",
        resource_id=flag.id,
        changes=audit.diff(before, fields),
        meta=meta,
    )
    await db.flush()
    await db.refresh(flag)
    return FeatureFlagOut.model_validate(flag)


@router.get("/tenants/{tenant_id}/feature-flags", response_model=list[TenantFlagState])
async def tenant_flags(tenant_id: uuid.UUID, _: PlatformReader, db: PlatformDb) -> list[TenantFlagState]:
    await _get_tenant(db, tenant_id)
    flags = (await db.execute(select(FeatureFlag).order_by(FeatureFlag.key))).scalars().all()
    overrides = {
        o.flag_id: o.enabled
        for o in (await db.execute(select(TenantFeatureFlag).where(TenantFeatureFlag.tenant_id == tenant_id))).scalars()
    }
    return [
        TenantFlagState(
            key=f.key,
            description=f.description,
            default_enabled=f.default_enabled,
            override=overrides.get(f.id),
            effective=overrides.get(f.id, f.default_enabled),
        )
        for f in flags
    ]


@router.put("/tenants/{tenant_id}/feature-flags", response_model=list[TenantFlagState])
async def put_tenant_flags(
    tenant_id: uuid.UUID, body: TenantFlagsUpdate, current: PlatformAdmin, db: PlatformDb, meta: Meta
) -> list[TenantFlagState]:
    await _get_tenant(db, tenant_id)
    flags = {f.key: f for f in (await db.execute(select(FeatureFlag))).scalars()}
    unknown = set(body.overrides) - set(flags)
    if unknown:
        raise Unprocessable("Unknown flags", code="invalid_flag", details={"unknown": sorted(unknown)})
    existing = {
        o.flag_id: o
        for o in (await db.execute(select(TenantFeatureFlag).where(TenantFeatureFlag.tenant_id == tenant_id))).scalars()
    }
    for key, value in body.overrides.items():
        flag = flags[key]
        row = existing.get(flag.id)
        if value is None:
            if row:
                await db.delete(row)
        elif row:
            row.enabled = value
        else:
            db.add(TenantFeatureFlag(tenant_id=tenant_id, flag_id=flag.id, enabled=value))
    await audit.record(
        db,
        actor=current.actor,
        action="feature_flags.updated",
        resource_type="tenant",
        resource_id=tenant_id,
        tenant_id=tenant_id,
        changes=body.overrides,
        meta=meta,
    )
    await db.flush()
    return await tenant_flags(tenant_id, current, db)


# ---------------------------------------------------------------- usage


@router.get("/tenants/{tenant_id}/usage", response_model=list[UsagePoint])
async def tenant_usage(
    tenant_id: uuid.UUID, _: PlatformReader, db: PlatformDb, days: Annotated[int, Query(ge=1, le=366)] = 30
) -> list[UsagePoint]:
    await _get_tenant(db, tenant_id)
    since = (utcnow() - timedelta(days=days)).date()
    rows = (
        (
            await db.execute(
                select(TenantUsage)
                .where(TenantUsage.tenant_id == tenant_id, TenantUsage.period_date >= since)
                .order_by(TenantUsage.period_date, TenantUsage.metric)
            )
        )
        .scalars()
        .all()
    )
    return [UsagePoint.model_validate(r) for r in rows]


# ---------------------------------------------------------------- signup requests


@router.get("/signup-requests", response_model=Page[SignupRequestOut])
async def list_signups(
    _: PlatformReader,
    db: PlatformDb,
    params: PageDep,
    status_: Annotated[SignupStatus | None, Query(alias="status")] = None,
) -> Page[SignupRequestOut]:
    stmt = select(SignupRequest)
    if status_:
        stmt = stmt.where(SignupRequest.status == status_)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(SignupRequest.company_name.ilike(like), SignupRequest.contact_email.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(SignupRequest.created_at.desc()).offset(params.offset).limit(params.page_size)))
        .scalars()
        .all()
    )
    return Page(
        items=[SignupRequestOut.model_validate(r) for r in rows], total=total, page=params.page, page_size=params.page_size
    )


@router.post("/signup-requests/{request_id}/approve", response_model=TenantProvisioned)
async def approve_signup(
    request_id: uuid.UUID, body: SignupDecision, current: PlatformAdmin, db: PlatformDb, meta: Meta
) -> TenantProvisioned:
    req = (await db.execute(select(SignupRequest).where(SignupRequest.id == request_id).with_for_update())).scalar_one_or_none()
    if req is None:
        raise NotFound("Signup request not found")
    if req.status != SignupStatus.PENDING:
        raise Conflict(f"Request is already {req.status.value}", code="already_reviewed")
    plan = await db.get(SubscriptionPlan, req.plan_id)
    result = await service.provision_tenant(
        db,
        name=req.company_name,
        slug=body.slug or req.desired_slug,
        plan=plan,
        billing_cycle=req.billing_cycle,  # type: ignore[arg-type]
        admin_email=req.contact_email,
        admin_name=req.contact_name,
        with_trial=body.with_trial,
        actor=current.actor,
        meta=meta,
    )
    req.status = SignupStatus.APPROVED
    req.reviewed_by = current.id
    req.reviewed_at = utcnow()
    req.review_note = body.note
    req.tenant_id = result.tenant.id
    await audit.record(
        db,
        actor=current.actor,
        action="signup.approved",
        resource_type="signup_request",
        resource_id=req.id,
        tenant_id=result.tenant.id,
        meta=meta,
    )
    [out] = await _tenant_outs(db, [result.tenant])
    return TenantProvisioned(
        tenant=out,
        admin_username=result.admin.username,
        temporary_password=result.temporary_password,
        login_url=result.login_url,
    )


@router.post("/signup-requests/{request_id}/reject", response_model=SignupRequestOut)
async def reject_signup(
    request_id: uuid.UUID, body: SignupDecision, current: PlatformAdmin, db: PlatformDb, meta: Meta
) -> SignupRequestOut:
    req = await db.get(SignupRequest, request_id)
    if req is None:
        raise NotFound("Signup request not found")
    if req.status != SignupStatus.PENDING:
        raise Conflict(f"Request is already {req.status.value}", code="already_reviewed")
    req.status = SignupStatus.REJECTED
    req.reviewed_by = current.id
    req.reviewed_at = utcnow()
    req.review_note = body.note
    await audit.record(
        db,
        actor=current.actor,
        action="signup.rejected",
        resource_type="signup_request",
        resource_id=req.id,
        changes={"note": body.note},
        meta=meta,
    )
    await db.flush()
    await db.refresh(req)
    return SignupRequestOut.model_validate(req)
