"""Shared tenant branding uploads for Owner and tenant Admin settings."""

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import on_commit
from app.core.storage import delete_brand_asset, store_brand_asset
from app.modules.audit import service as audit
from app.modules.audit.service import Actor, RequestMeta
from app.modules.tenants.models import TenantBranding

AssetKind = Literal["logo", "favicon", "login_background"]
ASSET_COLUMNS = {"logo": "logo_url", "favicon": "favicon_url", "login_background": "login_background_url"}


async def set_asset(
    db: AsyncSession,
    row: TenantBranding,
    kind: AssetKind,
    data: bytes | None,
    *,
    actor: Actor,
    meta: RequestMeta,
) -> TenantBranding:
    # Owner and tenant Admin edits serialize against the same branding row.
    await db.refresh(row, with_for_update=True)
    column = ASSET_COLUMNS[kind]
    previous = getattr(row, column)
    url = store_brand_asset(row.tenant_id, kind, data) if data is not None else None
    setattr(row, column, url)
    try:
        await audit.record(
            db,
            actor=actor,
            action="branding.asset_uploaded" if data is not None else "branding.asset_removed",
            resource_type="tenant_branding",
            resource_id=row.tenant_id,
            tenant_id=row.tenant_id,
            changes={column: [previous, url]},
            meta=meta,
        )
        await db.flush()
    except BaseException:
        if url:
            delete_brand_asset(url)
        raise

    async def cleanup() -> None:
        delete_brand_asset(previous)

    # A failed database transaction must not remove the logo still in use.
    on_commit(db, cleanup)
    return row
