import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.security import utcnow
from app.modules.audit import service as audit
from app.modules.audit.service import RequestMeta
from app.modules.events.models import PUBLISHED_EVENT_STATUSES, Event, EventStatus
from app.modules.events.schemas import MatchCreate, MatchOut, MatchUpdate, ResultOut
from app.modules.events.service import get_event
from app.modules.matches.models import MATCH_TRANSITIONS, Match, MatchStatus
from app.modules.realtime.publisher import publish_after_commit
from app.modules.results.models import MatchResult
from app.shared.repository import get_scoped


def realtime_payload(match: Match, result: MatchResult | None = None) -> dict:
    return {
        "id": match.id,
        "event_id": match.event_id,
        "match_number": match.match_number,
        "status": match.status,
        "meron_name": match.meron_name,
        "wala_name": match.wala_name,
        "result": {"outcome": result.outcome, "revision": result.revision} if result else None,
    }


async def get_match(
    db: AsyncSession, tenant_id: uuid.UUID, match_id: uuid.UUID, *, for_update: bool = False, published_only: bool = False
) -> Match:
    match = await get_scoped(db, Match, match_id, tenant_id, for_update=for_update)
    if match is None:
        raise NotFound("Match not found")
    if published_only:
        event = await get_scoped(db, Event, match.event_id, tenant_id)
        if event is None or event.status not in PUBLISHED_EVENT_STATUSES:
            raise NotFound("Match not found")
    return match


async def to_out(
    db: AsyncSession, tenant_id: uuid.UUID, matches: list[Match], *, with_event_names: bool = False
) -> list[MatchOut]:
    ids = [m.id for m in matches]
    results: dict[uuid.UUID, MatchResult] = {}
    if ids:
        rows = await db.execute(select(MatchResult).where(MatchResult.tenant_id == tenant_id, MatchResult.match_id.in_(ids)))
        results = {r.match_id: r for r in rows.scalars()}
    names: dict[uuid.UUID, str] = {}
    if with_event_names and matches:
        rows2 = await db.execute(
            select(Event.id, Event.name).where(Event.tenant_id == tenant_id, Event.id.in_({m.event_id for m in matches}))
        )
        names = dict(rows2.tuples().all())
    out = []
    for m in matches:
        item = MatchOut.model_validate(m)
        r = results.get(m.id)
        item.result = ResultOut.model_validate(r) if r else None
        item.event_name = names.get(m.event_id)
        out.append(item)
    return out


async def list_for_event(db: AsyncSession, tenant_id: uuid.UUID, event_id: uuid.UUID) -> list[Match]:
    rows = await db.execute(
        select(Match)
        .where(Match.tenant_id == tenant_id, Match.event_id == event_id, Match.deleted_at.is_(None))
        .order_by(Match.match_number)
    )
    return list(rows.scalars())


async def create_match(db: AsyncSession, actor: CurrentUser, event_id: uuid.UUID, data: MatchCreate, meta: RequestMeta) -> Match:
    tenant_id = actor.tenant.id
    event = await get_scoped(db, Event, event_id, tenant_id, for_update=True)  # serialises numbering
    if event is None:
        raise NotFound("Event not found")
    if event.status in (EventStatus.COMPLETED, EventStatus.CANCELLED):
        raise Unprocessable(f"Cannot add matches to a {event.status.value} event", code="event_locked")
    number = data.match_number
    if number is None:
        current_max = (
            await db.execute(
                select(func.max(Match.match_number)).where(
                    Match.tenant_id == tenant_id, Match.event_id == event.id, Match.deleted_at.is_(None)
                )
            )
        ).scalar_one()
        number = int(current_max or 0) + 1
    else:
        taken = (
            await db.execute(
                select(Match.id).where(
                    Match.tenant_id == tenant_id,
                    Match.event_id == event.id,
                    Match.match_number == number,
                    Match.deleted_at.is_(None),
                )
            )
        ).first()
        if taken:
            raise Conflict(f"Match #{number} already exists in this event", code="match_number_taken")
    match = Match(
        tenant_id=tenant_id,
        event_id=event.id,
        match_number=number,
        status=MatchStatus.SCHEDULED,
        **data.model_dump(exclude={"match_number"}),
    )
    db.add(match)
    await db.flush()
    await db.refresh(match)
    await audit.record(
        db,
        actor=actor.actor,
        action="match.created",
        resource_type="match",
        resource_id=match.id,
        tenant_id=tenant_id,
        changes={"event_id": event.id, "match_number": number, "meron": match.meron_name, "wala": match.wala_name},
        meta=meta,
    )
    if event.status in PUBLISHED_EVENT_STATUSES:
        publish_after_commit(db, tenant_id, "match.updated", realtime_payload(match))
    return match


async def update_match(db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, data: MatchUpdate, meta: RequestMeta) -> Match:
    match = await get_match(db, actor.tenant.id, match_id, for_update=True)
    if match.status != MatchStatus.SCHEDULED:
        raise Unprocessable("Only scheduled matches can be edited", code="match_locked")
    fields = data.model_dump(exclude_unset=True)
    before = {k: getattr(match, k) for k in fields}
    for k, v in fields.items():
        if k in ("meron_name", "wala_name") and not v:
            continue
        setattr(match, k, v)
    await db.flush()
    await db.refresh(match)
    await audit.record(
        db,
        actor=actor.actor,
        action="match.updated",
        resource_type="match",
        resource_id=match.id,
        tenant_id=actor.tenant.id,
        changes=audit.diff(before, {k: getattr(match, k) for k in fields}),
        meta=meta,
    )
    publish_after_commit(db, actor.tenant.id, "match.updated", realtime_payload(match))
    return match


async def _transition(db: AsyncSession, match: Match, target: MatchStatus) -> MatchStatus:
    if target not in MATCH_TRANSITIONS[match.status]:
        raise Unprocessable(
            f"Cannot move a match from {match.status.value} to {target.value}",
            code="invalid_transition",
            details={"from": match.status.value, "to": target.value},
        )
    old = match.status
    match.status = target
    return old


async def start_match(db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, meta: RequestMeta) -> Match:
    tenant_id = actor.tenant.id
    candidate = await get_match(db, tenant_id, match_id)
    await get_scoped(db, Event, candidate.event_id, tenant_id, for_update=True)
    match = await get_match(db, tenant_id, match_id, for_update=True)
    event = await get_event(db, tenant_id, match.event_id)
    if event.status != EventStatus.LIVE:
        raise Unprocessable("Start the event before starting its matches", code="event_not_live")
    in_progress = (
        await db.execute(
            select(Match.id).where(
                Match.tenant_id == tenant_id,
                Match.event_id == event.id,
                Match.status == MatchStatus.IN_PROGRESS,
                Match.id != match.id,
                Match.deleted_at.is_(None),
            )
        )
    ).first()
    if in_progress:
        raise Conflict("Another match in this event is already in progress", code="match_in_progress")
    old = await _transition(db, match, MatchStatus.IN_PROGRESS)
    match.started_at = utcnow()
    await db.flush()
    await db.refresh(match)
    await audit.record(
        db,
        actor=actor.actor,
        action="match.started",
        resource_type="match",
        resource_id=match.id,
        tenant_id=tenant_id,
        changes={"status": [old, match.status]},
        meta=meta,
    )
    publish_after_commit(db, tenant_id, "match.updated", realtime_payload(match))
    return match


async def cancel_match(
    db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, reason: str, meta: RequestMeta, *, _workflow: bool = False
) -> Match:
    tenant_id = actor.tenant.id
    candidate = await get_match(db, tenant_id, match_id)
    await get_scoped(db, Event, candidate.event_id, tenant_id, for_update=True)
    match = await get_match(db, tenant_id, match_id, for_update=True)
    if not _workflow:
        from app.modules.results.service import assert_unmanaged

        await assert_unmanaged(db, tenant_id, match.id)
    old = await _transition(db, match, MatchStatus.CANCELLED)
    match.cancel_reason = reason
    match.ended_at = utcnow()
    await db.flush()
    await db.refresh(match)
    await audit.record(
        db,
        actor=actor.actor,
        action="match.cancelled",
        resource_type="match",
        resource_id=match.id,
        tenant_id=tenant_id,
        changes={"status": [old, match.status], "reason": reason},
        meta=meta,
    )
    publish_after_commit(db, tenant_id, "match.updated", realtime_payload(match))
    return match


async def soft_delete(db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, meta: RequestMeta) -> None:
    match = await get_match(db, actor.tenant.id, match_id, for_update=True)
    if match.status not in (MatchStatus.SCHEDULED, MatchStatus.CANCELLED):
        raise Unprocessable("Only scheduled or cancelled matches can be deleted", code="match_locked")
    match.deleted_at = utcnow()
    await audit.record(
        db,
        actor=actor.actor,
        action="match.deleted",
        resource_type="match",
        resource_id=match.id,
        tenant_id=actor.tenant.id,
        changes={"match_number": match.match_number},
        meta=meta,
    )
