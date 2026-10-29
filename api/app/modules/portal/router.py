"""End-user portal: read-only views of published events, official results,
the user's own demonstration points, activity and notifications."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select

from app.core.deps import CurrentUserDep, TenantDb, require_module
from app.modules.audit.models import AuditLog
from app.modules.demo_points import service as points
from app.modules.demo_points.schemas import POINTS_DISCLAIMER, AccountOut, LedgerEntryOut
from app.modules.events import service as events
from app.modules.events.models import PUBLISHED_EVENT_STATUSES, Event, EventStatus
from app.modules.events.schemas import EventOut, MatchOut
from app.modules.matches import service as matches
from app.modules.matches.models import Match, MatchStatus
from app.modules.notifications import service as notifications
from app.modules.notifications.models import Notification
from app.modules.results.models import MatchResult
from app.modules.tenants.service import effective_modules
from app.shared.schemas import Message, Page, PageDep, Schema, paginate

router = APIRouter(prefix="/portal", tags=["portal"])
events_module = [Depends(require_module("events"))]


class NotificationOut(Schema):
    id: uuid.UUID
    type: str
    title: str
    body: str | None
    data: dict
    read_at: datetime | None
    created_at: datetime


class ActivityOut(Schema):
    id: uuid.UUID
    action: str
    resource_type: str
    ip: str | None
    created_at: datetime


class PortalDashboard(Schema):
    balance: int | None
    points_disclaimer: str = POINTS_DISCLAIMER
    unread_notifications: int
    live_matches: list[MatchOut]
    upcoming_events: list[EventOut]
    latest_results: list[MatchOut]


async def _published_event_ids(tenant_id: uuid.UUID):  # type: ignore[no-untyped-def]
    return select(Event.id).where(
        Event.tenant_id == tenant_id, Event.deleted_at.is_(None), Event.status.in_(PUBLISHED_EVENT_STATUSES)
    )


@router.get("/dashboard", response_model=PortalDashboard)
async def dashboard(current: CurrentUserDep, db: TenantDb) -> PortalDashboard:
    tenant_id = current.tenant.id
    modules = await effective_modules(tenant_id)
    balance = None
    if "points" in modules:
        balance = (await points.user_account(db, tenant_id, current.id)).balance
    live: list[MatchOut] = []
    upcoming: list[EventOut] = []
    latest: list[MatchOut] = []
    if "events" in modules:
        published = await _published_event_ids(tenant_id)
        live_rows = (
            (
                await db.execute(
                    select(Match)
                    .where(
                        Match.tenant_id == tenant_id,
                        Match.deleted_at.is_(None),
                        Match.status == MatchStatus.IN_PROGRESS,
                        Match.event_id.in_(published),
                    )
                    .order_by(Match.started_at.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        live = await matches.to_out(db, tenant_id, list(live_rows), with_event_names=True)
        upcoming_rows = (
            (
                await db.execute(
                    select(Event)
                    .where(
                        Event.tenant_id == tenant_id,
                        Event.deleted_at.is_(None),
                        Event.status.in_([EventStatus.SCHEDULED, EventStatus.LIVE]),
                    )
                    .order_by(Event.starts_at)
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        upcoming = await events.to_out(db, tenant_id, list(upcoming_rows))
        latest_rows = (
            (
                await db.execute(
                    select(Match)
                    .join(MatchResult, MatchResult.match_id == Match.id)
                    .where(Match.tenant_id == tenant_id, Match.deleted_at.is_(None), Match.event_id.in_(published))
                    .order_by(MatchResult.declared_at.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        latest = await matches.to_out(db, tenant_id, list(latest_rows), with_event_names=True)
    return PortalDashboard(
        balance=balance,
        unread_notifications=await notifications.unread_count(db, tenant_id, current.id),
        live_matches=live,
        upcoming_events=upcoming,
        latest_results=latest,
    )


@router.get("/events", response_model=Page[EventOut], dependencies=events_module)
async def list_events(
    current: CurrentUserDep,
    db: TenantDb,
    params: PageDep,
    status_: Annotated[list[EventStatus] | None, Query(alias="status")] = None,
) -> Page[EventOut]:
    statuses = [s for s in (status_ or []) if s in PUBLISHED_EVENT_STATUSES] or None
    items, total = await events.list_events(db, current.tenant.id, params, statuses=statuses, published_only=True)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.get("/events/{event_id}", response_model=EventOut, dependencies=events_module)
async def get_event(event_id: uuid.UUID, current: CurrentUserDep, db: TenantDb) -> EventOut:
    event = await events.get_event(db, current.tenant.id, event_id, published_only=True)
    [out] = await events.to_out(db, current.tenant.id, [event])
    return out


@router.get("/events/{event_id}/matches", response_model=list[MatchOut], dependencies=events_module)
async def event_matches(event_id: uuid.UUID, current: CurrentUserDep, db: TenantDb) -> list[MatchOut]:
    await events.get_event(db, current.tenant.id, event_id, published_only=True)
    return await matches.to_out(db, current.tenant.id, await matches.list_for_event(db, current.tenant.id, event_id))


@router.get("/matches/{match_id}", response_model=MatchOut, dependencies=events_module)
async def get_match(match_id: uuid.UUID, current: CurrentUserDep, db: TenantDb) -> MatchOut:
    match = await matches.get_match(db, current.tenant.id, match_id, published_only=True)
    [out] = await matches.to_out(db, current.tenant.id, [match], with_event_names=True)
    return out


@router.get("/results", response_model=Page[MatchOut], dependencies=events_module)
async def results(current: CurrentUserDep, db: TenantDb, params: PageDep, event_id: uuid.UUID | None = None) -> Page[MatchOut]:
    tenant_id = current.tenant.id
    stmt = (
        select(Match)
        .join(MatchResult, MatchResult.match_id == Match.id)
        .where(
            Match.tenant_id == tenant_id,
            Match.deleted_at.is_(None),
            Match.event_id.in_(await _published_event_ids(tenant_id)),
        )
        .order_by(MatchResult.declared_at.desc())
    )
    if event_id:
        stmt = stmt.where(Match.event_id == event_id)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(Match.meron_name.ilike(like), Match.wala_name.ilike(like)))
    rows, total = await paginate(db, stmt, params)
    items = await matches.to_out(db, tenant_id, rows, with_event_names=True)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.get("/points", response_model=AccountOut, dependencies=[Depends(require_module("points"))])
async def my_points(current: CurrentUserDep, db: TenantDb) -> AccountOut:
    acct = await points.user_account(db, current.tenant.id, current.id)
    return AccountOut(user_id=current.id, username=current.user.username, full_name=current.user.full_name, balance=acct.balance)


@router.get("/points/history", response_model=Page[LedgerEntryOut], dependencies=[Depends(require_module("points"))])
async def my_points_history(current: CurrentUserDep, db: TenantDb, params: PageDep) -> Page[LedgerEntryOut]:
    acct = await points.user_account(db, current.tenant.id, current.id)
    items, total = await points.statement(db, current.tenant.id, acct, params)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.get("/activity", response_model=Page[ActivityOut])
async def my_activity(current: CurrentUserDep, db: TenantDb, params: PageDep) -> Page[ActivityOut]:
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.tenant_id == current.tenant.id,
            or_(AuditLog.actor_id == current.id, AuditLog.resource_id == str(current.id)),
        )
        .order_by(AuditLog.created_at.desc())
    )
    rows, total = await paginate(db, stmt, params)
    return Page(items=[ActivityOut.model_validate(r) for r in rows], total=total, page=params.page, page_size=params.page_size)


@router.get("/notifications", response_model=Page[NotificationOut])
async def my_notifications(
    current: CurrentUserDep, db: TenantDb, params: PageDep, unread_only: bool = False
) -> Page[NotificationOut]:
    stmt = select(Notification).where(Notification.tenant_id == current.tenant.id, Notification.user_id == current.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows, total = await paginate(db, stmt.order_by(Notification.created_at.desc()), params)
    return Page(
        items=[NotificationOut.model_validate(r) for r in rows], total=total, page=params.page, page_size=params.page_size
    )


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
async def read_notification(notification_id: uuid.UUID, current: CurrentUserDep, db: TenantDb) -> NotificationOut:
    return NotificationOut.model_validate(await notifications.mark_read(db, current.tenant.id, current.id, notification_id))


@router.post("/notifications/read-all", response_model=Message)
async def read_all(current: CurrentUserDep, db: TenantDb) -> Message:
    n = await notifications.mark_all_read(db, current.tenant.id, current.id)
    return Message(message=f"{n} notification(s) marked as read")
