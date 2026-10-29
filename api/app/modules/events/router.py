import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from app.core.deps import (
    CurrentUser,
    Meta,
    TenantDb,
    require_any_permission,
    require_module,
    require_permissions,
)
from app.core.permissions import P
from app.modules.events import service as events
from app.modules.events.models import EventStatus
from app.modules.events.schemas import (
    EventCreate,
    EventOut,
    EventUpdate,
    MatchCancel,
    MatchCreate,
    MatchOut,
    MatchUpdate,
    ResultCorrect,
    ResultDeclare,
    ResultHistoryOut,
)
from app.modules.matches import service as matches
from app.modules.results import service as results
from app.modules.users.models import TenantUser
from app.shared.schemas import Message, Page, PageDep

router = APIRouter(prefix="/admin", tags=["admin: events & matches"], dependencies=[Depends(require_module("events"))])

EventViewer = Annotated[CurrentUser, Depends(require_any_permission(P.EVENTS_VIEW, P.EVENTS_MANAGE, P.MATCHES_MANAGE))]
EventManager = Annotated[CurrentUser, Depends(require_permissions(P.EVENTS_MANAGE))]
MatchManager = Annotated[CurrentUser, Depends(require_permissions(P.MATCHES_MANAGE))]
ResultDeclarer = Annotated[CurrentUser, Depends(require_permissions(P.RESULTS_DECLARE))]
ResultCorrector = Annotated[CurrentUser, Depends(require_permissions(P.RESULTS_CORRECT))]
matches_module = [Depends(require_module("matches"))]


async def _event_out(db: TenantDb, current: CurrentUser, event) -> EventOut:  # type: ignore[no-untyped-def]
    [out] = await events.to_out(db, current.tenant.id, [event])
    return out


async def _match_out(db: TenantDb, current: CurrentUser, match) -> MatchOut:  # type: ignore[no-untyped-def]
    [out] = await matches.to_out(db, current.tenant.id, [match])
    return out


# ---------------------------------------------------------------- events


@router.get("/events", response_model=Page[EventOut])
async def list_events(
    current: EventViewer,
    db: TenantDb,
    params: PageDep,
    status_: Annotated[list[EventStatus] | None, Query(alias="status")] = None,
) -> Page[EventOut]:
    items, total = await events.list_events(db, current.tenant.id, params, statuses=status_)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.post("/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
async def create_event(body: EventCreate, current: EventManager, db: TenantDb, meta: Meta) -> EventOut:
    return await _event_out(db, current, await events.create_event(db, current, body, meta))


@router.get("/events/{event_id}", response_model=EventOut)
async def get_event(event_id: uuid.UUID, current: EventViewer, db: TenantDb) -> EventOut:
    return await _event_out(db, current, await events.get_event(db, current.tenant.id, event_id))


@router.patch("/events/{event_id}", response_model=EventOut)
async def update_event(event_id: uuid.UUID, body: EventUpdate, current: EventManager, db: TenantDb, meta: Meta) -> EventOut:
    return await _event_out(db, current, await events.update_event(db, current, event_id, body, meta))


@router.delete("/events/{event_id}", response_model=Message)
async def delete_event(event_id: uuid.UUID, current: EventManager, db: TenantDb, meta: Meta) -> Message:
    await events.soft_delete(db, current, event_id, meta)
    return Message(message="Event deleted")


def _event_action(target: EventStatus, doc: str):  # type: ignore[no-untyped-def]
    async def handler(event_id: uuid.UUID, current: EventManager, db: TenantDb, meta: Meta) -> EventOut:
        return await _event_out(db, current, await events.transition(db, current, event_id, target, meta))

    handler.__doc__ = doc
    return handler


router.post("/events/{event_id}/publish", response_model=EventOut, name="publish_event")(
    _event_action(EventStatus.SCHEDULED, "Publish a draft event to the player portal.")
)
router.post("/events/{event_id}/unpublish", response_model=EventOut, name="unpublish_event")(
    _event_action(EventStatus.DRAFT, "Return a scheduled event to draft.")
)
router.post("/events/{event_id}/start", response_model=EventOut, name="start_event")(
    _event_action(EventStatus.LIVE, "Mark the event as live so its matches can start.")
)
router.post("/events/{event_id}/complete", response_model=EventOut, name="complete_event")(
    _event_action(EventStatus.COMPLETED, "Complete the event once every match is finished or cancelled.")
)
router.post("/events/{event_id}/cancel", response_model=EventOut, name="cancel_event")(
    _event_action(EventStatus.CANCELLED, "Cancel the event and all open matches.")
)


# ---------------------------------------------------------------- matches


@router.get("/events/{event_id}/matches", response_model=list[MatchOut], dependencies=matches_module)
async def list_matches(event_id: uuid.UUID, current: EventViewer, db: TenantDb) -> list[MatchOut]:
    await events.get_event(db, current.tenant.id, event_id)
    return await matches.to_out(db, current.tenant.id, await matches.list_for_event(db, current.tenant.id, event_id))


@router.post(
    "/events/{event_id}/matches", response_model=MatchOut, status_code=status.HTTP_201_CREATED, dependencies=matches_module
)
async def create_match(event_id: uuid.UUID, body: MatchCreate, current: MatchManager, db: TenantDb, meta: Meta) -> MatchOut:
    return await _match_out(db, current, await matches.create_match(db, current, event_id, body, meta))


@router.get("/matches/{match_id}", response_model=MatchOut, dependencies=matches_module)
async def get_match(match_id: uuid.UUID, current: EventViewer, db: TenantDb) -> MatchOut:
    return await _match_out(db, current, await matches.get_match(db, current.tenant.id, match_id))


@router.patch("/matches/{match_id}", response_model=MatchOut, dependencies=matches_module)
async def update_match(match_id: uuid.UUID, body: MatchUpdate, current: MatchManager, db: TenantDb, meta: Meta) -> MatchOut:
    return await _match_out(db, current, await matches.update_match(db, current, match_id, body, meta))


@router.delete("/matches/{match_id}", response_model=Message, dependencies=matches_module)
async def delete_match(match_id: uuid.UUID, current: MatchManager, db: TenantDb, meta: Meta) -> Message:
    await matches.soft_delete(db, current, match_id, meta)
    return Message(message="Match deleted")


@router.post("/matches/{match_id}/start", response_model=MatchOut, dependencies=matches_module)
async def start_match(match_id: uuid.UUID, current: MatchManager, db: TenantDb, meta: Meta) -> MatchOut:
    return await _match_out(db, current, await matches.start_match(db, current, match_id, meta))


@router.post("/matches/{match_id}/cancel", response_model=MatchOut, dependencies=matches_module)
async def cancel_match(match_id: uuid.UUID, body: MatchCancel, current: MatchManager, db: TenantDb, meta: Meta) -> MatchOut:
    return await _match_out(db, current, await matches.cancel_match(db, current, match_id, body.reason, meta))


# ---------------------------------------------------------------- results


@router.post(
    "/matches/{match_id}/result", response_model=MatchOut, status_code=status.HTTP_201_CREATED, dependencies=matches_module
)
async def declare_result(match_id: uuid.UUID, body: ResultDeclare, current: ResultDeclarer, db: TenantDb, meta: Meta) -> MatchOut:
    match, _ = await results.declare(db, current, match_id, body.outcome, body.notes, meta)
    return await _match_out(db, current, match)


@router.put("/matches/{match_id}/result", response_model=MatchOut, dependencies=matches_module)
async def correct_result(
    match_id: uuid.UUID, body: ResultCorrect, current: ResultCorrector, db: TenantDb, meta: Meta
) -> MatchOut:
    match, _ = await results.correct(db, current, match_id, body.outcome, body.reason, body.notes, meta)
    return await _match_out(db, current, match)


@router.get("/matches/{match_id}/result/history", response_model=list[ResultHistoryOut], dependencies=matches_module)
async def result_history(match_id: uuid.UUID, current: EventViewer, db: TenantDb) -> list[ResultHistoryOut]:
    await matches.get_match(db, current.tenant.id, match_id)
    rows = await results.history(db, current.tenant.id, match_id)
    names = (
        dict(
            (
                await db.execute(
                    select(TenantUser.id, TenantUser.username).where(
                        TenantUser.tenant_id == current.tenant.id, TenantUser.id.in_({r.changed_by_id for r in rows})
                    )
                )
            )
            .tuples()
            .all()
        )
        if rows
        else {}
    )
    out = []
    for r in rows:
        item = ResultHistoryOut.model_validate(r)
        item.changed_by_username = names.get(r.changed_by_id)
        out.append(item)
    return out
