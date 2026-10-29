import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, or_, select

from app.core.deps import CurrentUser, PlatformDb, PlatformReader, TenantDb, require_permissions
from app.core.permissions import P
from app.modules.audit.models import ActorType, AuditLog
from app.modules.tenants.models import Tenant
from app.shared.schemas import Page, PageDep, PageParams, Schema, paginate

tenant_router = APIRouter(prefix="/admin", tags=["admin: audit"])
platform_router = APIRouter(prefix="/platform", tags=["platform: audit"])


class AuditOut(Schema):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    tenant_name: str | None = None
    actor_type: ActorType
    actor_id: uuid.UUID | None
    actor_label: str | None
    action: str
    resource_type: str
    resource_id: str | None
    changes: dict[str, Any]
    ip: str | None
    user_agent: str | None
    request_id: str | None
    created_at: datetime


def _filters(
    stmt: Select,  # type: ignore[type-arg]
    params: PageParams,
    action: str | None,
    resource_type: str | None,
    actor_id: uuid.UUID | None,
    since: datetime | None,
    until: datetime | None,
) -> Select:  # type: ignore[type-arg]
    if action:
        stmt = stmt.where(AuditLog.action.startswith(action))
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until:
        stmt = stmt.where(AuditLog.created_at < until)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(AuditLog.actor_label.ilike(like), AuditLog.action.ilike(like), AuditLog.resource_id.ilike(like)))
    return stmt.order_by(AuditLog.created_at.desc(), AuditLog.id)


@tenant_router.get("/audit-logs", response_model=Page[AuditOut])
async def tenant_audit(
    current: Annotated[CurrentUser, Depends(require_permissions(P.AUDIT_VIEW))],
    db: TenantDb,
    params: PageDep,
    action: Annotated[str | None, Query(max_length=64)] = None,
    resource_type: Annotated[str | None, Query(max_length=64)] = None,
    actor_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Page[AuditOut]:
    stmt = _filters(
        select(AuditLog).where(AuditLog.tenant_id == current.tenant.id), params, action, resource_type, actor_id, since, until
    )
    rows, total = await paginate(db, stmt, params)
    return Page(items=[AuditOut.model_validate(r) for r in rows], total=total, page=params.page, page_size=params.page_size)


@platform_router.get("/audit-logs", response_model=Page[AuditOut])
async def platform_audit(
    _: PlatformReader,
    db: PlatformDb,
    params: PageDep,
    tenant_id: uuid.UUID | None = None,
    platform_only: bool = False,
    actor_type: ActorType | None = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    resource_type: Annotated[str | None, Query(max_length=64)] = None,
    actor_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Page[AuditOut]:
    stmt = select(AuditLog)
    if platform_only:
        stmt = stmt.where(AuditLog.tenant_id.is_(None))
    elif tenant_id:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    if actor_type:
        stmt = stmt.where(AuditLog.actor_type == actor_type)
    stmt = _filters(stmt, params, action, resource_type, actor_id, since, until)
    rows, total = await paginate(db, stmt, params)
    names = {}
    tids = {r.tenant_id for r in rows if r.tenant_id}
    if tids:
        names = dict((await db.execute(select(Tenant.id, Tenant.name).where(Tenant.id.in_(tids)))).tuples().all())
    items = []
    for r in rows:
        item = AuditOut.model_validate(r)
        item.tenant_name = names.get(r.tenant_id) if r.tenant_id else None
        items.append(item)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)
