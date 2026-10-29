"""Account hierarchy: master agents -> agents -> players.

Every query filters on ``tenant_id`` explicitly; RLS is the second line of defence.
"""

import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.users.models import TenantUser

MAX_DEPTH = 8  # hierarchy rules cap real depth at 3; this bounds the ancestor walk


def downline_ids_select(tenant_id: uuid.UUID, root_id: uuid.UUID) -> Select:  # type: ignore[type-arg]
    """Ids of every non-deleted account below ``root_id`` (excluding root)."""
    base = (
        select(TenantUser.id)
        .where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.parent_id == root_id,
            TenantUser.deleted_at.is_(None),
        )
        .cte("downline", recursive=True)
    )
    child = aliased(TenantUser)
    cte = base.union_all(
        select(child.id).join(base, child.parent_id == base.c.id).where(child.tenant_id == tenant_id, child.deleted_at.is_(None))
    )
    return select(cte.c.id)


async def is_in_downline(db: AsyncSession, tenant_id: uuid.UUID, ancestor_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    current: uuid.UUID | None = user_id
    for _ in range(MAX_DEPTH):
        if current is None:
            return False
        parent = (
            await db.execute(select(TenantUser.parent_id).where(TenantUser.tenant_id == tenant_id, TenantUser.id == current))
        ).scalar_one_or_none()
        if parent == ancestor_id:
            return True
        current = parent
    return False


async def child_counts(db: AsyncSession, tenant_id: uuid.UUID, parent_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not parent_ids:
        return {}
    rows = await db.execute(
        select(TenantUser.parent_id, func.count())
        .where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.parent_id.in_(parent_ids),
            TenantUser.deleted_at.is_(None),
        )
        .group_by(TenantUser.parent_id)
    )
    return {pid: int(n) for pid, n in rows.all()}
