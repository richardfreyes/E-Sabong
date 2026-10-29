import uuid

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import Forbidden, NotFound, Unprocessable
from app.core.permissions import P
from app.core.security import utcnow
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.events.models import (
    EVENT_TRANSITIONS,
    PUBLISHED_EVENT_STATUSES,
    Event,
    EventStatus,
)
from app.modules.events.schemas import EventCreate, EventOut, EventUpdate
from app.modules.matches.models import Match, MatchStatus
from app.modules.realtime.publisher import publish_after_commit
from app.modules.subscriptions.limits import ensure_event_capacity
from app.shared.repository import get_scoped
from app.shared.schemas import PageParams, paginate

EDITABLE_FIELDS = ("name", "venue", "description", "starts_at", "ends_at")


async def get_event(db: AsyncSession, tenant_id: uuid.UUID, event_id: uuid.UUID, *, published_only: bool = False) -> Event:
    event = await get_scoped(db, Event, event_id, tenant_id)
    if event is None or (published_only and event.status not in PUBLISHED_EVENT_STATUSES):
        raise NotFound("Event not found")
    return event


async def to_out(db: AsyncSession, tenant_id: uuid.UUID, events: list[Event]) -> list[EventOut]:
    ids = [e.id for e in events]
    counts: dict[uuid.UUID, tuple[int, int]] = {}
    if ids:
        rows = await db.execute(
            select(
                Match.event_id,
                func.count(),
                func.sum(case((Match.status == MatchStatus.COMPLETED, 1), else_=0)),
            )
            .where(Match.tenant_id == tenant_id, Match.event_id.in_(ids), Match.deleted_at.is_(None))
            .group_by(Match.event_id)
        )
        counts = {eid: (int(n), int(done or 0)) for eid, n, done in rows.all()}
    out = []
    for e in events:
        item = EventOut.model_validate(e)
        item.match_count, item.completed_match_count = counts.get(e.id, (0, 0))
        out.append(item)
    return out


async def list_events(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    params: PageParams,
    *,
    statuses: list[EventStatus] | None = None,
    published_only: bool = False,
) -> tuple[list[EventOut], int]:
    stmt = select(Event).where(Event.tenant_id == tenant_id, Event.deleted_at.is_(None))
    if published_only:
        stmt = stmt.where(Event.status.in_(PUBLISHED_EVENT_STATUSES))
    if statuses:
        stmt = stmt.where(Event.status.in_(statuses))
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(Event.name.ilike(like), Event.venue.ilike(like)))
    stmt = stmt.order_by(
        params.order_by(
            {"starts_at": Event.starts_at, "name": Event.name, "created_at": Event.created_at}, Event.starts_at.desc()
        )
    )
    events, total = await paginate(db, stmt, params)
    return await to_out(db, tenant_id, events), total


async def create_event(db: AsyncSession, actor: CurrentUser, data: EventCreate, meta: RequestMeta) -> Event:
    tenant_id = actor.tenant.id
    await ensure_event_capacity(db, tenant_id)
    event = Event(tenant_id=tenant_id, created_by_id=actor.id, status=EventStatus.DRAFT, **data.model_dump())
    db.add(event)
    await db.flush()
    await db.refresh(event)
    await audit.record(
        db,
        actor=actor.actor,
        action="event.created",
        resource_type="event",
        resource_id=event.id,
        tenant_id=tenant_id,
        changes=data.model_dump(),
        meta=meta,
    )
    return event


async def update_event(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, data: EventUpdate, meta: RequestMeta) -> Event:
    event = await get_event(db, actor.tenant.id, event_id)
    if event.status in (EventStatus.COMPLETED, EventStatus.CANCELLED):
        raise Unprocessable(f"A {event.status.value} event cannot be edited", code="event_locked")
    fields = data.model_dump(exclude_unset=True)
    before = {k: getattr(event, k) for k in fields}
    for k, v in fields.items():
        if k == "name" and not v:
            continue
        setattr(event, k, v)
    if event.ends_at and event.ends_at < event.starts_at:
        raise Unprocessable("ends_at must be after starts_at", code="invalid_window")
    await db.flush()
    await db.refresh(event)
    await audit.record(
        db,
        actor=actor.actor,
        action="event.updated",
        resource_type="event",
        resource_id=event.id,
        tenant_id=actor.tenant.id,
        changes=audit.diff(before, {k: getattr(event, k) for k in fields}),
        meta=meta,
    )
    if event.status in PUBLISHED_EVENT_STATUSES:
        publish_after_commit(db, actor.tenant.id, "event.updated", {"id": event.id, "status": event.status})
    return event


async def transition(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, target: EventStatus, meta: RequestMeta) -> Event:
    tenant_id = actor.tenant.id
    event = await get_scoped(db, Event, event_id, tenant_id, for_update=True)
    if event is None:
        raise NotFound("Event not found")
    if target not in EVENT_TRANSITIONS[event.status]:
        raise Unprocessable(
            f"Cannot move an event from {event.status.value} to {target.value}",
            code="invalid_transition",
            details={"from": event.status.value, "to": target.value},
        )
    if target == EventStatus.COMPLETED:
        open_matches = (
            await db.execute(
                select(func.count())
                .select_from(Match)
                .where(
                    Match.tenant_id == tenant_id,
                    Match.event_id == event.id,
                    Match.deleted_at.is_(None),
                    Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.IN_PROGRESS]),
                )
            )
        ).scalar_one()
        if open_matches:
            raise Unprocessable(
                f"{open_matches} match(es) are still scheduled or in progress",
                code="matches_open",
                details={"open_matches": int(open_matches)},
            )
        event.ends_at = event.ends_at or max(utcnow(), event.starts_at)
    if target == EventStatus.CANCELLED:
        verified = (
            await db.execute(
                select(Event.result_verification_required).where(
                    Event.tenant_id == tenant_id,
                    Event.id == event.id,
                )
            )
        ).scalar_one()
        if verified and not actor.has(P.RESULTS_APPROVE):
            raise Forbidden("An authorized result approver must cancel a verified event", code="approval_required")
        from app.modules.declarator.models import ACTIVE_STATUSES, ResultDeclaration

        pending = (
            await db.execute(
                select(ResultDeclaration.id)
                .join(Match, Match.id == ResultDeclaration.match_id)
                .where(
                    ResultDeclaration.tenant_id == tenant_id,
                    Match.event_id == event.id,
                    ResultDeclaration.status.in_(ACTIVE_STATUSES),
                )
                .limit(1)
            )
        ).first()
        if pending:
            from app.core.errors import Conflict

            raise Conflict("Return or finalize pending declarations before cancelling this event", code="verification_required")
        await db.execute(
            update(Match)
            .where(
                Match.tenant_id == tenant_id,
                Match.event_id == event.id,
                Match.deleted_at.is_(None),
                Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.IN_PROGRESS]),
            )
            .values(status=MatchStatus.CANCELLED, cancel_reason="Event cancelled", ended_at=utcnow())
        )
        from app.modules.predictions.service import resolve

        await resolve(db, tenant_id, event_id=event.id)
    old = event.status
    event.status = target
    await db.flush()
    await db.refresh(event)
    await audit.record(
        db,
        actor=actor.actor,
        action=f"event.{target.value}",
        resource_type="event",
        resource_id=event.id,
        tenant_id=tenant_id,
        changes={"status": [old, target]},
        meta=meta,
    )
    publish_after_commit(db, tenant_id, "event.updated", {"id": event.id, "status": target, "name": event.name})
    return event


async def soft_delete(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, meta: RequestMeta) -> None:
    event = await get_event(db, actor.tenant.id, event_id)
    if event.status not in (EventStatus.DRAFT, EventStatus.CANCELLED):
        raise Unprocessable("Only draft or cancelled events can be deleted", code="event_locked")
    from app.core.errors import Conflict
    from app.modules.declarator.models import ResultDeclaration

    recorded = (
        await db.execute(
            select(ResultDeclaration.id)
            .join(Match, Match.id == ResultDeclaration.match_id)
            .where(
                ResultDeclaration.tenant_id == actor.tenant.id,
                Match.event_id == event.id,
            )
            .limit(1)
        )
    ).first()
    if recorded:
        raise Conflict("Events with declaration audit history cannot be deleted", code="verification_required")
    now = utcnow()
    event.deleted_at = now
    await db.execute(
        update(Match)
        .where(Match.tenant_id == actor.tenant.id, Match.event_id == event.id, Match.deleted_at.is_(None))
        .values(deleted_at=now)
    )
    await audit.record(
        db,
        actor=actor.actor,
        action="event.deleted",
        resource_type="event",
        resource_id=event.id,
        tenant_id=actor.tenant.id,
        changes={"name": event.name},
        meta=meta,
    )
