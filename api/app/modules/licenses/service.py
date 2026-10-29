import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound, Unprocessable
from app.core.redis import get_redis
from app.core.security import sha256_hex, utcnow
from app.modules.audit import service as audit
from app.modules.audit.service import Actor, RequestMeta
from app.modules.licenses import signing
from app.modules.licenses.models import (
    DeploymentInstance,
    DeploymentStatus,
    LicenseActivation,
    LicenseKey,
    LicenseStatus,
)
from app.modules.licenses.schemas import LicenseCreate, LicenseTokenResponse
from app.modules.tenants.models import Tenant

LICENSE_STATE_KEY = "license:state"


def _effective_status(lic: LicenseKey) -> LicenseStatus:
    if lic.status == LicenseStatus.REVOKED:
        return LicenseStatus.REVOKED
    if lic.expires_at is not None and lic.expires_at <= utcnow():
        return LicenseStatus.EXPIRED
    return lic.status


async def issue(db: AsyncSession, data: LicenseCreate, *, actor: Actor, meta: RequestMeta) -> tuple[LicenseKey, str]:
    tenant = await db.get(Tenant, data.tenant_id)
    if tenant is None or tenant.deleted_at is not None:
        raise Unprocessable("Tenant not found", code="invalid_tenant")
    raw = signing.generate_license_key()
    lic = LicenseKey(
        tenant_id=tenant.id,
        key_prefix=raw[:9],
        key_hash=sha256_hex(raw),
        status=LicenseStatus.ISSUED,
        issued_by_id=actor.id,
        **data.model_dump(exclude={"tenant_id"}),
    )
    db.add(lic)
    await db.flush()
    await db.refresh(lic)
    await audit.record(
        db,
        actor=actor,
        action="license.issued",
        resource_type="license_key",
        resource_id=lic.id,
        tenant_id=tenant.id,
        changes={"type": lic.license_type, "licensee": lic.licensee_name, "expires_at": lic.expires_at},
        meta=meta,
    )
    return lic, raw


async def revoke(db: AsyncSession, lic: LicenseKey, reason: str, *, actor: Actor, meta: RequestMeta) -> LicenseKey:
    if lic.status == LicenseStatus.REVOKED:
        return lic
    lic.status = LicenseStatus.REVOKED
    lic.revoked_at = utcnow()
    lic.revoked_reason = reason
    await audit.record(
        db,
        actor=actor,
        action="license.revoked",
        resource_type="license_key",
        resource_id=lic.id,
        tenant_id=lic.tenant_id,
        changes={"reason": reason},
        meta=meta,
    )
    await db.flush()
    return lic


def _token_for(lic: LicenseKey, tenant_slug: str, fingerprint: str) -> str:
    return signing.sign(
        {
            "lid": str(lic.id),
            "tenant": tenant_slug,
            "licensee": lic.licensee_name,
            "type": lic.license_type.value,
            "seats": lic.seats,
            "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
            "maintenance_until": lic.maintenance_until.isoformat() if lic.maintenance_until else None,
            "fingerprint": fingerprint,
            "issued_at": utcnow().isoformat(),
        }
    )


async def _by_key(db: AsyncSession, raw_key: str) -> LicenseKey:
    lic = (
        await db.execute(select(LicenseKey).where(LicenseKey.key_hash == sha256_hex(raw_key.strip())).with_for_update())
    ).scalar_one_or_none()
    if lic is None:
        raise Forbidden("Unknown license key", code="license_invalid")
    return lic


async def activate(
    db: AsyncSession, *, raw_key: str, fingerprint: str, hostname: str | None, app_version: str | None
) -> LicenseTokenResponse:
    lic = await _by_key(db, raw_key)
    status = _effective_status(lic)
    if status in (LicenseStatus.REVOKED, LicenseStatus.EXPIRED):
        lic.status = status
        raise Forbidden(f"License is {status.value}", code=f"license_{status.value}")
    existing = (
        await db.execute(
            select(LicenseActivation).where(
                LicenseActivation.license_id == lic.id, LicenseActivation.instance_fingerprint == fingerprint
            )
        )
    ).scalar_one_or_none()
    now = utcnow()
    if existing is None:
        active = (
            await db.execute(
                select(func.count())
                .select_from(LicenseActivation)
                .where(LicenseActivation.license_id == lic.id, LicenseActivation.deactivated_at.is_(None))
            )
        ).scalar_one()
        if active >= lic.max_activations:
            raise Forbidden("Activation limit reached for this license", code="license_activation_limit")
        db.add(
            LicenseActivation(
                license_id=lic.id,
                instance_fingerprint=fingerprint,
                hostname=hostname,
                app_version=app_version,
                activated_at=now,
                last_seen_at=now,
            )
        )
    else:
        existing.deactivated_at = None
        existing.last_seen_at = now
        existing.hostname = hostname or existing.hostname
        existing.app_version = app_version or existing.app_version
    lic.status = LicenseStatus.ACTIVE
    tenant = await db.get(Tenant, lic.tenant_id)
    assert tenant is not None
    await _touch_deployment(db, lic, hostname, app_version)
    await audit.record(
        db,
        actor=Actor.system(),
        action="license.activated",
        resource_type="license_key",
        resource_id=lic.id,
        tenant_id=lic.tenant_id,
        changes={"fingerprint": fingerprint, "hostname": hostname},
    )
    await db.flush()
    return LicenseTokenResponse(
        status=lic.status,
        license_token=_token_for(lic, tenant.slug, fingerprint),
        expires_at=lic.expires_at,
        maintenance_until=lic.maintenance_until,
    )


async def heartbeat(
    db: AsyncSession, *, raw_key: str, fingerprint: str, hostname: str | None, app_version: str | None
) -> LicenseTokenResponse:
    lic = await _by_key(db, raw_key)
    status = _effective_status(lic)
    activation = (
        await db.execute(
            select(LicenseActivation).where(
                LicenseActivation.license_id == lic.id,
                LicenseActivation.instance_fingerprint == fingerprint,
                LicenseActivation.deactivated_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if activation is None:
        raise Forbidden("This instance is not activated", code="license_not_activated")
    activation.last_seen_at = utcnow()
    activation.app_version = app_version or activation.app_version
    await _touch_deployment(db, lic, hostname, app_version)
    if status != lic.status:
        lic.status = status
    await db.flush()
    if status in (LicenseStatus.REVOKED, LicenseStatus.EXPIRED):
        return LicenseTokenResponse(
            status=status, license_token=None, expires_at=lic.expires_at, maintenance_until=lic.maintenance_until
        )
    tenant = await db.get(Tenant, lic.tenant_id)
    assert tenant is not None
    return LicenseTokenResponse(
        status=status,
        license_token=_token_for(lic, tenant.slug, fingerprint),
        expires_at=lic.expires_at,
        maintenance_until=lic.maintenance_until,
    )


async def _touch_deployment(db: AsyncSession, lic: LicenseKey, hostname: str | None, app_version: str | None) -> None:
    dep = (await db.execute(select(DeploymentInstance).where(DeploymentInstance.license_id == lic.id))).scalars().first()
    if dep is None:
        return
    dep.last_heartbeat_at = utcnow()
    dep.app_version = app_version or dep.app_version
    if dep.status == DeploymentStatus.PROVISIONING:
        dep.status = DeploymentStatus.ACTIVE


async def get_license(db: AsyncSession, license_id: uuid.UUID) -> LicenseKey:
    lic = await db.get(LicenseKey, license_id)
    if lic is None:
        raise NotFound("License not found")
    return lic


# ---------------------------------------------------------------- dedicated-mode enforcement


async def current_license_state() -> dict:
    """License validity for a dedicated install, cached in Redis.

    Combines the offline signature check of LICENSE_TOKEN with the last
    heartbeat verdict (a revoked license stops working at the next heartbeat).
    """
    s = get_settings()
    if s.deployment_mode != "dedicated":
        return {"mode": "shared", "valid": True, "reason": None}
    redis = get_redis()
    if await redis.get("license:revoked"):
        return {"mode": "dedicated", "valid": False, "reason": "License has been revoked"}
    token = await redis.get("license:token") or s.license_token
    if not token:
        return {"mode": "dedicated", "valid": False, "reason": "No license token configured"}
    try:
        lic = signing.verify(token)
    except signing.LicenseError as exc:
        return {"mode": "dedicated", "valid": False, "reason": exc.message}
    if lic.tenant_slug != s.dedicated_tenant_slug:
        return {"mode": "dedicated", "valid": False, "reason": "License was issued for a different workspace"}
    if lic.expired:
        return {
            "mode": "dedicated",
            "valid": False,
            "reason": "License has expired",
            "licensee": lic.licensee,
            "expires_at": lic.expires_at,
        }
    return {
        "mode": "dedicated",
        "valid": True,
        "reason": None,
        "licensee": lic.licensee,
        "expires_at": lic.expires_at,
        "maintenance_until": lic.maintenance_until,
    }


def parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
