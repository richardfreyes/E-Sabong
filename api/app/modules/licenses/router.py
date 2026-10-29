import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import func, or_, select

from app.core.deps import Meta, PlatformAdmin, PlatformDb, PlatformReader
from app.core.errors import Conflict, NotFound, TooManyRequests, Unprocessable
from app.core.redis import get_redis
from app.modules.audit import service as audit
from app.modules.licenses import service
from app.modules.licenses.models import (
    DeploymentInstance,
    LicenseActivation,
    LicenseKey,
    LicenseStatus,
)
from app.modules.licenses.schemas import (
    ActivateRequest,
    ActivationOut,
    DeploymentCreate,
    DeploymentOut,
    DeploymentUpdate,
    HeartbeatRequest,
    LicenseCreate,
    LicenseIssued,
    LicenseOut,
    LicenseState,
    LicenseTokenResponse,
    LicenseUpdate,
    RevokeLicense,
)
from app.modules.tenants.models import Tenant
from app.shared.schemas import Page, PageDep

router = APIRouter(prefix="/platform", tags=["platform: licensing"])
licensing_router = APIRouter(prefix="/licensing", tags=["licensing"])
status_router = APIRouter(tags=["licensing"])


async def _license_outs(db: PlatformDb, rows: list[LicenseKey]) -> list[LicenseOut]:
    if not rows:
        return []
    names = dict(
        (await db.execute(select(Tenant.id, Tenant.name).where(Tenant.id.in_({r.tenant_id for r in rows})))).tuples().all()
    )
    counts = dict(
        (
            await db.execute(
                select(LicenseActivation.license_id, func.count())
                .where(LicenseActivation.license_id.in_([r.id for r in rows]), LicenseActivation.deactivated_at.is_(None))
                .group_by(LicenseActivation.license_id)
            )
        )
        .tuples()
        .all()
    )
    out = []
    for r in rows:
        item = LicenseOut.model_validate(r)
        item.status = service._effective_status(r)
        item.tenant_name = names.get(r.tenant_id)
        item.activation_count = int(counts.get(r.id, 0))
        out.append(item)
    return out


@router.get("/licenses", response_model=Page[LicenseOut])
async def list_licenses(
    _: PlatformReader,
    db: PlatformDb,
    params: PageDep,
    status_: Annotated[LicenseStatus | None, Query(alias="status")] = None,
    tenant_id: uuid.UUID | None = None,
) -> Page[LicenseOut]:
    stmt = select(LicenseKey).join(Tenant, Tenant.id == LicenseKey.tenant_id)
    if status_:
        stmt = stmt.where(LicenseKey.status == status_)
    if tenant_id:
        stmt = stmt.where(LicenseKey.tenant_id == tenant_id)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(LicenseKey.licensee_name.ilike(like), LicenseKey.key_prefix.ilike(like), Tenant.name.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(LicenseKey.created_at.desc()).offset(params.offset).limit(params.page_size)))
        .scalars()
        .all()
    )
    return Page(items=await _license_outs(db, list(rows)), total=total, page=params.page, page_size=params.page_size)


@router.post("/licenses", response_model=LicenseIssued, status_code=status.HTTP_201_CREATED)
async def issue_license(body: LicenseCreate, current: PlatformAdmin, db: PlatformDb, meta: Meta) -> LicenseIssued:
    lic, raw = await service.issue(db, body, actor=current.actor, meta=meta)
    [out] = await _license_outs(db, [lic])
    return LicenseIssued(license=out, license_key=raw)


@router.get("/licenses/{license_id}", response_model=LicenseOut)
async def get_license(license_id: uuid.UUID, _: PlatformReader, db: PlatformDb) -> LicenseOut:
    [out] = await _license_outs(db, [await service.get_license(db, license_id)])
    return out


@router.get("/licenses/{license_id}/activations", response_model=list[ActivationOut])
async def license_activations(license_id: uuid.UUID, _: PlatformReader, db: PlatformDb) -> list[ActivationOut]:
    await service.get_license(db, license_id)
    rows = (
        (
            await db.execute(
                select(LicenseActivation)
                .where(LicenseActivation.license_id == license_id)
                .order_by(LicenseActivation.activated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [ActivationOut.model_validate(r) for r in rows]


@router.patch("/licenses/{license_id}", response_model=LicenseOut)
async def update_license(
    license_id: uuid.UUID, body: LicenseUpdate, current: PlatformAdmin, db: PlatformDb, meta: Meta
) -> LicenseOut:
    lic = await service.get_license(db, license_id)
    if lic.status == LicenseStatus.REVOKED:
        raise Unprocessable("Revoked licenses cannot be changed", code="license_revoked")
    fields = body.model_dump(exclude_unset=True)
    if "expires_at" in fields and lic.license_type.value == "perpetual" and fields["expires_at"] is not None:
        raise Unprocessable("Perpetual licenses cannot expire", code="invalid_expiry")
    before = {k: getattr(lic, k) for k in fields}
    for k, v in fields.items():
        setattr(lic, k, v)
    if lic.status == LicenseStatus.EXPIRED and (lic.expires_at is None or lic.expires_at > service.utcnow()):
        lic.status = LicenseStatus.ACTIVE
    await audit.record(
        db,
        actor=current.actor,
        action="license.updated",
        resource_type="license_key",
        resource_id=lic.id,
        tenant_id=lic.tenant_id,
        changes=audit.diff(before, fields),
        meta=meta,
    )
    await db.flush()
    [out] = await _license_outs(db, [lic])
    return out


@router.post("/licenses/{license_id}/revoke", response_model=LicenseOut)
async def revoke_license(
    license_id: uuid.UUID, body: RevokeLicense, current: PlatformAdmin, db: PlatformDb, meta: Meta
) -> LicenseOut:
    lic = await service.revoke(db, await service.get_license(db, license_id), body.reason, actor=current.actor, meta=meta)
    [out] = await _license_outs(db, [lic])
    return out


# ---------------------------------------------------------------- deployments


async def _deployment_outs(db: PlatformDb, rows: list[DeploymentInstance]) -> list[DeploymentOut]:
    if not rows:
        return []
    names = dict(
        (await db.execute(select(Tenant.id, Tenant.name).where(Tenant.id.in_({r.tenant_id for r in rows})))).tuples().all()
    )
    out = []
    for r in rows:
        item = DeploymentOut.model_validate(r)
        item.tenant_name = names.get(r.tenant_id)
        out.append(item)
    return out


async def _check_license_link(db: PlatformDb, tenant_id: uuid.UUID, license_id: uuid.UUID | None) -> None:
    if license_id is None:
        return
    lic = await db.get(LicenseKey, license_id)
    if lic is None or lic.tenant_id != tenant_id:
        raise Unprocessable("License not found for this tenant", code="invalid_license")


@router.get("/deployments", response_model=Page[DeploymentOut])
async def list_deployments(
    _: PlatformReader, db: PlatformDb, params: PageDep, tenant_id: uuid.UUID | None = None
) -> Page[DeploymentOut]:
    stmt = select(DeploymentInstance)
    if tenant_id:
        stmt = stmt.where(DeploymentInstance.tenant_id == tenant_id)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(DeploymentInstance.name.ilike(like), DeploymentInstance.hostname.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(DeploymentInstance.created_at.desc()).offset(params.offset).limit(params.page_size)))
        .scalars()
        .all()
    )
    return Page(items=await _deployment_outs(db, list(rows)), total=total, page=params.page, page_size=params.page_size)


@router.post("/deployments", response_model=DeploymentOut, status_code=status.HTTP_201_CREATED)
async def create_deployment(body: DeploymentCreate, current: PlatformAdmin, db: PlatformDb, meta: Meta) -> DeploymentOut:
    tenant = await db.get(Tenant, body.tenant_id)
    if tenant is None or tenant.deleted_at is not None:
        raise Unprocessable("Tenant not found", code="invalid_tenant")
    await _check_license_link(db, body.tenant_id, body.license_id)
    hostname = body.hostname.strip().lower()
    if (await db.execute(select(DeploymentInstance.id).where(DeploymentInstance.hostname == hostname))).first():
        raise Conflict("A deployment with this hostname exists", code="hostname_taken")
    dep = DeploymentInstance(**body.model_dump(exclude={"hostname"}), hostname=hostname)
    db.add(dep)
    await db.flush()
    await db.refresh(dep)
    await audit.record(
        db,
        actor=current.actor,
        action="deployment.created",
        resource_type="deployment_instance",
        resource_id=dep.id,
        tenant_id=dep.tenant_id,
        changes={"hostname": hostname, "name": dep.name},
        meta=meta,
    )
    [out] = await _deployment_outs(db, [dep])
    return out


@router.patch("/deployments/{deployment_id}", response_model=DeploymentOut)
async def update_deployment(
    deployment_id: uuid.UUID, body: DeploymentUpdate, current: PlatformAdmin, db: PlatformDb, meta: Meta
) -> DeploymentOut:
    dep = await db.get(DeploymentInstance, deployment_id)
    if dep is None:
        raise NotFound("Deployment not found")
    fields = body.model_dump(exclude_unset=True)
    if "license_id" in fields:
        await _check_license_link(db, dep.tenant_id, fields["license_id"])
    before = {k: getattr(dep, k) for k in fields}
    for k, v in fields.items():
        if k == "name" and not v:
            continue
        setattr(dep, k, v)
    await audit.record(
        db,
        actor=current.actor,
        action="deployment.updated",
        resource_type="deployment_instance",
        resource_id=dep.id,
        tenant_id=dep.tenant_id,
        changes=audit.diff(before, fields),
        meta=meta,
    )
    await db.flush()
    await db.refresh(dep)
    [out] = await _deployment_outs(db, [dep])
    return out


# ---------------------------------------------------------------- instance-facing endpoints


async def _rate_limit(ip: str | None) -> None:
    redis = get_redis()
    key = f"ratelimit:licensing:{ip}"
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, 60)
    if n > 30:
        raise TooManyRequests("Too many licensing requests")


@licensing_router.post("/activate", response_model=LicenseTokenResponse)
async def activate(body: ActivateRequest, db: PlatformDb, meta: Meta) -> LicenseTokenResponse:
    """Called by a dedicated installation to bind a license key to an instance."""
    await _rate_limit(meta.ip)
    return await service.activate(
        db, raw_key=body.license_key, fingerprint=body.instance_fingerprint, hostname=body.hostname, app_version=body.app_version
    )


@licensing_router.post("/heartbeat", response_model=LicenseTokenResponse)
async def heartbeat(body: HeartbeatRequest, db: PlatformDb, meta: Meta) -> LicenseTokenResponse:
    """Periodic check-in; returns a refreshed token, or no token once revoked or expired."""
    await _rate_limit(meta.ip)
    return await service.heartbeat(
        db, raw_key=body.license_key, fingerprint=body.instance_fingerprint, hostname=body.hostname, app_version=body.app_version
    )


@status_router.get("/license/status", response_model=LicenseState)
async def license_status() -> LicenseState:
    """License state of this installation (always valid in shared SaaS mode)."""
    return LicenseState(**await service.current_license_state())
