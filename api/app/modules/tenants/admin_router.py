import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy import select

from app.core.deps import CurrentUser, Meta, TenantDb, require_permissions
from app.core.errors import NotFound
from app.core.permissions import P
from app.core.redis import get_redis
from app.core.storage import delete_brand_asset, store_brand_asset
from app.modules.audit import service as audit
from app.modules.subscriptions.models import Invoice, TenantSubscription
from app.modules.subscriptions.schemas import InvoiceOut, SubscriptionOut
from app.modules.tenants import service
from app.modules.tenants.models import TenantBranding, TenantDomain, TenantSettings
from app.modules.tenants.schemas import (
    BrandingOut,
    BrandingUpdate,
    DomainCreate,
    DomainOut,
    SettingsOut,
    SettingsUpdate,
)
from app.shared.schemas import Message, Page, PageDep, paginate

router = APIRouter(prefix="/admin", tags=["admin: settings"])

SettingsManager = Annotated[CurrentUser, Depends(require_permissions(P.SETTINGS_MANAGE))]
BrandingManager = Annotated[CurrentUser, Depends(require_permissions(P.BRANDING_MANAGE))]
BillingViewer = Annotated[CurrentUser, Depends(require_permissions(P.BILLING_VIEW))]


async def _branding(db: TenantDb, tenant_id: uuid.UUID) -> TenantBranding:
    row = (await db.execute(select(TenantBranding).where(TenantBranding.tenant_id == tenant_id))).scalar_one_or_none()
    if row is None:
        raise NotFound("Branding not found")
    return row


async def _settings(db: TenantDb, tenant_id: uuid.UUID) -> TenantSettings:
    row = (await db.execute(select(TenantSettings).where(TenantSettings.tenant_id == tenant_id))).scalar_one_or_none()
    if row is None:
        raise NotFound("Settings not found")
    return row


@router.get("/settings", response_model=SettingsOut)
async def get_settings_(current: SettingsManager, db: TenantDb) -> SettingsOut:
    return SettingsOut.model_validate(await _settings(db, current.tenant.id))


@router.put("/settings", response_model=SettingsOut)
async def put_settings(body: SettingsUpdate, current: SettingsManager, db: TenantDb, meta: Meta) -> SettingsOut:
    row = await _settings(db, current.tenant.id)
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    before = {k: getattr(row, k) for k in fields}
    for k, v in fields.items():
        setattr(row, k, v)
    await audit.record(
        db,
        actor=current.actor,
        action="settings.updated",
        resource_type="tenant_settings",
        resource_id=current.tenant.id,
        tenant_id=current.tenant.id,
        changes=audit.diff(before, fields),
        meta=meta,
    )
    await db.flush()
    await get_redis().delete(f"tenant:modules:{current.tenant.id}")
    return SettingsOut.model_validate(row)


@router.get("/branding", response_model=BrandingOut)
async def get_branding(current: BrandingManager, db: TenantDb) -> BrandingOut:
    return BrandingOut.model_validate(await _branding(db, current.tenant.id))


@router.put("/branding", response_model=BrandingOut)
async def put_branding(body: BrandingUpdate, current: BrandingManager, db: TenantDb, meta: Meta) -> BrandingOut:
    row = await _branding(db, current.tenant.id)
    fields = body.model_dump(exclude_unset=True)
    before = {k: getattr(row, k) for k in fields}
    for k, v in fields.items():
        if k == "app_name" and not v:
            continue
        setattr(row, k, v)
    await audit.record(
        db,
        actor=current.actor,
        action="branding.updated",
        resource_type="tenant_branding",
        resource_id=current.tenant.id,
        tenant_id=current.tenant.id,
        changes=audit.diff(before, fields),
        meta=meta,
    )
    await db.flush()
    await db.refresh(row)
    return BrandingOut.model_validate(row)


AssetKind = Literal["logo", "favicon", "login_background"]
ASSET_COLUMNS = {"logo": "logo_url", "favicon": "favicon_url", "login_background": "login_background_url"}


@router.post("/branding/assets/{kind}", response_model=BrandingOut)
async def upload_asset(
    kind: AssetKind, current: BrandingManager, db: TenantDb, meta: Meta, file: Annotated[UploadFile, File()]
) -> BrandingOut:
    data = await file.read()
    url = store_brand_asset(current.tenant.id, kind, data)
    row = await _branding(db, current.tenant.id)
    column = ASSET_COLUMNS[kind]
    previous = getattr(row, column)
    setattr(row, column, url)
    await audit.record(
        db,
        actor=current.actor,
        action="branding.asset_uploaded",
        resource_type="tenant_branding",
        resource_id=current.tenant.id,
        tenant_id=current.tenant.id,
        changes={column: [previous, url]},
        meta=meta,
    )
    await db.flush()
    delete_brand_asset(previous)
    await db.refresh(row)
    return BrandingOut.model_validate(row)


@router.delete("/branding/assets/{kind}", response_model=BrandingOut)
async def delete_asset(kind: AssetKind, current: BrandingManager, db: TenantDb, meta: Meta) -> BrandingOut:
    row = await _branding(db, current.tenant.id)
    column = ASSET_COLUMNS[kind]
    previous = getattr(row, column)
    setattr(row, column, None)
    await audit.record(
        db,
        actor=current.actor,
        action="branding.asset_removed",
        resource_type="tenant_branding",
        resource_id=current.tenant.id,
        tenant_id=current.tenant.id,
        changes={column: [previous, None]},
        meta=meta,
    )
    await db.flush()
    delete_brand_asset(previous)
    await db.refresh(row)
    return BrandingOut.model_validate(row)


def _domain_out(row: TenantDomain) -> DomainOut:
    out = DomainOut.model_validate(row)
    if row.kind.value == "custom":
        out.instructions = service.verification_instructions(row)
    return out


@router.get("/domains", response_model=list[DomainOut])
async def list_domains(current: SettingsManager, db: TenantDb) -> list[DomainOut]:
    rows = (
        (
            await db.execute(
                select(TenantDomain).where(TenantDomain.tenant_id == current.tenant.id).order_by(TenantDomain.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_domain_out(r) for r in rows]


@router.post("/domains", response_model=DomainOut, status_code=status.HTTP_201_CREATED)
async def add_domain(body: DomainCreate, current: SettingsManager, db: TenantDb, meta: Meta) -> DomainOut:
    row = await service.add_custom_domain(db, current.tenant.id, body.domain, actor=current.actor, meta=meta)
    return _domain_out(row)


@router.post("/domains/{domain_id}/verify", response_model=DomainOut)
async def verify_domain(domain_id: uuid.UUID, current: SettingsManager, db: TenantDb, meta: Meta) -> DomainOut:
    row = (
        await db.execute(select(TenantDomain).where(TenantDomain.id == domain_id, TenantDomain.tenant_id == current.tenant.id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Domain not found")
    return _domain_out(await service.check_domain(db, row, actor=current.actor, meta=meta))


@router.delete("/domains/{domain_id}", response_model=Message)
async def delete_domain(domain_id: uuid.UUID, current: SettingsManager, db: TenantDb, meta: Meta) -> Message:
    await service.remove_domain(db, current.tenant.id, domain_id, actor=current.actor, meta=meta)
    return Message(message="Domain removed")


@router.get("/subscription", response_model=SubscriptionOut | None)
async def my_subscription(current: BillingViewer, db: TenantDb) -> SubscriptionOut | None:
    sub = (
        await db.execute(
            select(TenantSubscription)
            .where(TenantSubscription.tenant_id == current.tenant.id)
            .order_by(TenantSubscription.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return SubscriptionOut.model_validate(sub) if sub else None


@router.get("/invoices", response_model=Page[InvoiceOut])
async def my_invoices(current: BillingViewer, db: TenantDb, params: PageDep) -> Page[InvoiceOut]:
    stmt = select(Invoice).where(Invoice.tenant_id == current.tenant.id).order_by(Invoice.issued_at.desc())
    rows, total = await paginate(db, stmt, params)
    return Page(items=[InvoiceOut.model_validate(r) for r in rows], total=total, page=params.page, page_size=params.page_size)
