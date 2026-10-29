import uuid
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound

M = TypeVar("M")


async def get_scoped(
    db: AsyncSession,
    model: type[M],
    obj_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    include_deleted: bool = False,
    for_update: bool = False,
) -> M | None:
    """Fetch a tenant-owned row by id, filtering on tenant explicitly (RLS also applies)."""
    m: Any = model
    stmt = select(m).where(m.id == obj_id, m.tenant_id == tenant_id)
    if not include_deleted and hasattr(m, "deleted_at"):
        stmt = stmt.where(m.deleted_at.is_(None))
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_scoped_or_404(
    db: AsyncSession,
    model: type[M],
    obj_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    label: str | None = None,
    for_update: bool = False,
) -> M:
    obj = await get_scoped(db, model, obj_id, tenant_id, for_update=for_update)
    if obj is None:
        raise NotFound(f"{label or model.__name__} not found")
    return obj
