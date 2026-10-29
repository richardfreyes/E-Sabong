from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select

from app.core.deps import CurrentUser, TenantDb, require_permissions
from app.core.errors import Forbidden
from app.core.permissions import P
from app.core.security import utcnow
from app.modules.audit.models import AuditLog
from app.modules.demo_points import service as points
from app.modules.events.models import Event, EventStatus
from app.modules.matches.models import Match, MatchStatus
from app.modules.reports import service
from app.modules.tenants.service import effective_flags, effective_modules
from app.modules.users.models import TenantUser
from app.shared.schemas import Schema

router = APIRouter(prefix="/admin", tags=["admin: reports"])
Reporter = Annotated[CurrentUser, Depends(require_permissions(P.REPORTS_VIEW))]
Format = Annotated[Literal["json", "csv"], Query(alias="format")]


async def _ensure_csv_allowed(db: TenantDb, tenant_id) -> None:  # type: ignore[no-untyped-def]
    if not (await effective_flags(db, tenant_id)).get("csv_exports", True):
        raise Forbidden("CSV export is not enabled for this workspace", code="feature_disabled", details={"flag": "csv_exports"})


def _csv(name: str, payload: dict) -> Response:
    return Response(
        content=service.to_csv(payload["rows"]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}-{payload["from"]}-{payload["to"]}.csv"'},
    )


@router.get("/reports/activity")
async def activity_report(
    current: Reporter,
    db: TenantDb,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    fmt: Format = "json",
):  # type: ignore[no-untyped-def]
    if fmt == "csv":
        await _ensure_csv_allowed(db, current.tenant.id)
    payload = await service.activity(db, current, from_, to)
    return _csv("activity", payload) if fmt == "csv" else payload


@router.get("/reports/points")
async def points_report(
    current: Reporter,
    db: TenantDb,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    fmt: Format = "json",
):  # type: ignore[no-untyped-def]
    if fmt == "csv":
        await _ensure_csv_allowed(db, current.tenant.id)
    payload = await service.points_report(db, current, from_, to)
    return _csv("points", payload) if fmt == "csv" else payload


@router.get("/reports/matches")
async def matches_report(
    current: Reporter,
    db: TenantDb,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    fmt: Format = "json",
):  # type: ignore[no-untyped-def]
    if fmt == "csv":
        await _ensure_csv_allowed(db, current.tenant.id)
    payload = await service.matches_report(db, current, from_, to)
    return _csv("matches", payload) if fmt == "csv" else payload


class DashboardOut(Schema):
    users_by_type: dict[str, int] | None
    active_users_7d: int | None
    events_by_status: dict[str, int] | None
    matches_in_progress: int | None
    results_7d: int | None
    points: dict | None
    activity: list[dict] | None
    recent_audit: list[dict] | None
    modules: list[str]


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    current: Annotated[CurrentUser, Depends(require_permissions(P.DASHBOARD_VIEW))], db: TenantDb
) -> DashboardOut:
    tenant_id = current.tenant.id
    modules = sorted(await effective_modules(tenant_id))
    scope = service.scoped_user_ids(current)
    since_7d = utcnow() - timedelta(days=7)

    users_q = select(TenantUser.account_type, func.count()).where(
        TenantUser.tenant_id == tenant_id, TenantUser.deleted_at.is_(None)
    )
    active_q = (
        select(func.count())
        .select_from(TenantUser)
        .where(TenantUser.tenant_id == tenant_id, TenantUser.deleted_at.is_(None), TenantUser.last_login_at >= since_7d)
    )
    if scope is not None:
        users_q = users_q.where(TenantUser.id.in_(scope))
        active_q = active_q.where(TenantUser.id.in_(scope))
    users_by_type = {t.value: int(n) for t, n in (await db.execute(users_q.group_by(TenantUser.account_type))).tuples().all()}
    active = int((await db.execute(active_q)).scalar_one())

    events_by_status = matches_live = results_7d = None
    if "events" in modules and current.has_any(P.EVENTS_VIEW, P.EVENTS_MANAGE):
        events_by_status = {
            s.value: int(n)
            for s, n in (
                await db.execute(
                    select(Event.status, func.count())
                    .where(Event.tenant_id == tenant_id, Event.deleted_at.is_(None))
                    .group_by(Event.status)
                )
            )
            .tuples()
            .all()
        }
        for s in EventStatus:
            events_by_status.setdefault(s.value, 0)
        matches_live = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Match)
                    .where(Match.tenant_id == tenant_id, Match.status == MatchStatus.IN_PROGRESS, Match.deleted_at.is_(None))
                )
            ).scalar_one()
        )
        results_7d = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Match)
                    .where(Match.tenant_id == tenant_id, Match.status == MatchStatus.COMPLETED, Match.ended_at >= since_7d)
                )
            ).scalar_one()
        )

    points_summary = None
    if "points" in modules and current.has_any(P.POINTS_VIEW_ALL, P.POINTS_VIEW_DOWNLINE):
        points_summary = (await points.summary(db, current)).model_dump()

    activity = None
    if current.has(P.REPORTS_VIEW):
        activity = (await service.activity(db, current, None, None))["rows"][-14:]

    recent = None
    if current.has(P.AUDIT_VIEW):
        rows = (
            (
                await db.execute(
                    select(AuditLog).where(AuditLog.tenant_id == tenant_id).order_by(AuditLog.created_at.desc()).limit(8)
                )
            )
            .scalars()
            .all()
        )
        recent = [
            {
                "id": str(r.id),
                "action": r.action,
                "actor": r.actor_label,
                "resource_type": r.resource_type,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]

    return DashboardOut(
        users_by_type=users_by_type,
        active_users_7d=active,
        events_by_status=events_by_status,
        matches_in_progress=matches_live,
        results_7d=results_7d,
        points=points_summary,
        activity=activity,
        recent_audit=recent,
        modules=modules,
    )
