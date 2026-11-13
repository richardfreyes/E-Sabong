"""Project official arena facts into isolated tenant books; never copy pools or wallets.

Owner changes and their projections commit together. Each tenant's existing settlement
engine calculates payouts and commissions from only its own books and immutable bet snapshots.
"""

import uuid

from sqlalchemy import select

from app.core.security import utcnow
from app.modules.audit.service import RequestMeta
from app.modules.betting import service as betting
from app.modules.betting.models import BettingSettings, BookState, FightBook
from app.modules.events.models import Event
from app.modules.matches.models import Match, MatchStatus
from app.modules.matches.service import realtime_payload
from app.modules.realtime.publisher import publish_after_commit
from app.modules.results import service as results
from app.modules.results.models import MatchResult
from app.modules.tenants.models import Tenant
from app.modules.tenants.rental_router import control_actor


def projection_id(tenant_id: uuid.UUID, source_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(tenant_id, str(source_id))


async def synchronize(db, config, owner, meta: RequestMeta, *, only_tenant: uuid.UUID | None = None):
    if not config.shared_domain_enabled or not config.global_event_id:
        return
    source = await db.get(Event, config.global_event_id, with_for_update=True)
    if source is None:
        return
    fights = list(
        (
            await db.scalars(
                select(Match).where(Match.event_id == source.id, Match.deleted_at.is_(None)).order_by(Match.match_number)
            )
        ).all()
    )
    tenants = list(
        (
            await db.scalars(select(Tenant).where(Tenant.deleted_at.is_(None), Tenant.id != source.tenant_id).order_by(Tenant.id))
        ).all()
    )
    for tenant in tenants:
        if only_tenant and tenant.id != only_tenant:
            continue
        actor = await control_actor(db, owner, tenant.id)
        eid = projection_id(tenant.id, source.id)
        event = await db.get(Event, eid, with_for_update=True)
        changed = event is None
        if event is None:
            event = Event(id=eid, tenant_id=tenant.id, name=source.name, starts_at=source.starts_at, status=source.status)
            db.add(event)
            await db.flush()
        for key in ("name", "venue", "description", "starts_at", "ends_at", "status"):
            changed |= getattr(event, key) != getattr(source, key)
            setattr(event, key, getattr(source, key))
        if changed:
            publish_after_commit(db, tenant.id, "event.updated", {"id": str(eid), "name": event.name, "status": event.status})
        settings = await db.get(BettingSettings, tenant.id)
        for official in fights:
            mid = projection_id(tenant.id, official.id)
            match = await db.get(Match, mid, with_for_update=True)
            fresh = match is None
            if fresh:
                match = Match(
                    id=mid,
                    tenant_id=tenant.id,
                    event_id=eid,
                    match_number=official.match_number,
                    meron_name=official.meron_name,
                    wala_name=official.wala_name,
                    status=MatchStatus.SCHEDULED,
                )
                db.add(match)
                await db.flush()
            changed = fresh
            for key in ("match_number", "meron_name", "wala_name", "meron_details", "wala_details", "scheduled_at", "started_at"):
                changed |= getattr(match, key) != getattr(official, key)
                setattr(match, key, getattr(official, key))
            book = await db.scalar(
                select(FightBook).where(FightBook.tenant_id == tenant.id, FightBook.match_id == mid).with_for_update()
            )
            source_book = await db.scalar(
                select(FightBook).where(FightBook.tenant_id == source.tenant_id, FightBook.match_id == official.id)
            )
            result = await db.scalar(select(MatchResult).where(MatchResult.match_id == official.id))
            own_result = await db.scalar(select(MatchResult).where(MatchResult.match_id == mid))
            if result:
                if own_result is None:
                    match.status = MatchStatus.IN_PROGRESS
                    await db.flush()
                    await results.declare(db, actor, mid, result.outcome, result.notes, meta, _workflow=True)
                elif own_result.outcome != result.outcome or own_result.revision != result.revision:
                    await results.correct(
                        db, actor, mid, result.outcome, "OWNER official result correction", result.notes, meta, _workflow=True
                    )
            elif official.status == MatchStatus.CANCELLED:
                if match.status != MatchStatus.CANCELLED:
                    match.status = MatchStatus.CANCELLED
                    match.cancel_reason, match.ended_at = official.cancel_reason, official.ended_at or utcnow()
                    await db.flush()
                    await betting.settle(db, actor, mid, meta)
                    changed = True
            else:
                changed |= match.status != official.status
                match.status = official.status
                await db.flush()
                if source_book and source_book.state == BookState.OPEN and settings and settings.enabled and book is None:
                    await betting.open_betting(db, actor, mid, meta, by="owner_global")
                elif source_book and source_book.state != BookState.OPEN and book and book.state == BookState.OPEN:
                    await betting.close_betting(db, actor, mid, meta)
            if changed:
                publish_after_commit(db, tenant.id, "match.updated", realtime_payload(match, own_result))
        await db.flush()
