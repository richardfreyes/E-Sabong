"""Non-monetary fight predictions.

A player marks MERON, WALA or DRAW while a fight's prediction window is open. Outcomes are
derived from the official record only (never from a pending declaration) by ``resolve``, which
every path that changes an official outcome calls inside its own transaction. Nothing here
reads or writes demonstration points.
"""

import uuid

from sqlalchemy import String, case, cast, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import Conflict, Forbidden, NotFound
from app.modules.declarator.models import ACTIVE_STATUSES, ResultDeclaration
from app.modules.events.models import Event, EventStatus
from app.modules.matches.models import Match, MatchStatus
from app.modules.predictions.models import Prediction, PredictionSide, PredictionStatus
from app.modules.predictions.schemas import PredictionOut
from app.modules.realtime.publisher import publish_after_commit
from app.modules.results.models import MatchResult
from app.modules.users.models import AccountType
from app.shared.schemas import Page, PageParams

OPEN_EVENT_STATUSES = (EventStatus.SCHEDULED, EventStatus.LIVE)
OPEN_MATCH_STATUSES = (MatchStatus.SCHEDULED, MatchStatus.IN_PROGRESS)


def _proposal_pending(tenant_id: uuid.UUID):  # type: ignore[no-untyped-def]
    return exists(
        select(ResultDeclaration.id).where(
            ResultDeclaration.tenant_id == tenant_id,
            ResultDeclaration.match_id == Match.id,
            ResultDeclaration.status.in_(ACTIVE_STATUSES),
        )
    )


def window_open_clause(tenant_id: uuid.UUID):  # type: ignore[no-untyped-def]
    """The window closes once a result is proposed or recorded, or the fight or event ends."""
    return (
        Event.status.in_(OPEN_EVENT_STATUSES)
        & Event.deleted_at.is_(None)
        & Match.status.in_(OPEN_MATCH_STATUSES)
        & Match.deleted_at.is_(None)
        & ~_proposal_pending(tenant_id)
    )


async def predict(db: AsyncSession, actor: CurrentUser, match_id: uuid.UUID, side: PredictionSide) -> PredictionOut:
    tenant_id = actor.tenant.id
    if actor.user.account_type != AccountType.PLAYER:
        raise Forbidden("Only player accounts can make predictions", code="players_only")
    # Same lock order as result declaration and event cancellation (event, then fight), but shared:
    # predictions run concurrently with each other and wait for, or hold off, any state change.
    row = (
        await db.execute(
            select(Match.id, Match.event_id).where(Match.tenant_id == tenant_id, Match.id == match_id, Match.deleted_at.is_(None))
        )
    ).first()
    if row is None:
        raise NotFound("Match not found")
    await db.execute(select(Event.id).where(Event.tenant_id == tenant_id, Event.id == row.event_id).with_for_update(read=True))
    await db.execute(select(Match.id).where(Match.tenant_id == tenant_id, Match.id == match_id).with_for_update(read=True))
    state = (
        await db.execute(
            select(Event.status, window_open_clause(tenant_id))
            .join(Match, Match.event_id == Event.id)
            .where(Event.tenant_id == tenant_id, Match.tenant_id == tenant_id, Match.id == match_id)
        )
    ).one()
    if state[0] not in (*OPEN_EVENT_STATUSES, EventStatus.COMPLETED):
        raise NotFound("Match not found")  # Draft or cancelled events are not visible to players.
    if not state[1]:
        raise Conflict("Predictions for this fight are closed", code="prediction_closed")

    stmt = insert(Prediction).values(
        tenant_id=tenant_id, user_id=actor.id, match_id=match_id, side=side, status=PredictionStatus.PENDING, changes=0
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_predictions_player_fight",
        set_={
            "side": stmt.excluded.side,
            "changes": Prediction.changes + case((Prediction.side != stmt.excluded.side, 1), else_=0),
            "updated_at": func.now(),
        },
        where=Prediction.status == PredictionStatus.PENDING,
    ).returning(Prediction.id)
    prediction_id = (await db.execute(stmt)).scalar_one_or_none()
    if prediction_id is None:
        raise Conflict("Predictions for this fight are closed", code="prediction_closed")
    [out] = (await _page(db, actor, select_ids=[prediction_id])).items
    publish_after_commit(
        db,
        tenant_id,
        "prediction.updated",
        {"id": out.id, "match_id": out.match_id, "side": out.side, "status": out.status},
        user_id=actor.id,
    )
    return out


async def resolve(
    db: AsyncSession, tenant_id: uuid.UUID, *, match_ids: list[uuid.UUID] | None = None, event_id: uuid.UUID | None = None
) -> int:
    """Recompute outcomes from the official record: WIN/LOSE against MERON, WALA or DRAW, VOID for
    a cancelled, deleted or no-contest fight, PENDING while no result is official. Idempotent, so
    corrections simply re-run it."""
    official = (
        select(cast(MatchResult.outcome, String))
        .where(MatchResult.tenant_id == tenant_id, MatchResult.match_id == Prediction.match_id)
        .correlate(Prediction)
        .scalar_subquery()
    )
    outcome = case(
        (or_(Match.status == MatchStatus.CANCELLED, Match.deleted_at.is_not(None)), PredictionStatus.VOID.value),
        (official.is_(None), PredictionStatus.PENDING.value),
        (official == "no_contest", PredictionStatus.VOID.value),
        (official == cast(Prediction.side, String), PredictionStatus.WIN.value),
        else_=PredictionStatus.LOSE.value,
    )
    decided = case(
        (outcome == PredictionStatus.PENDING.value, None),
        (outcome == cast(Prediction.status, String), Prediction.decided_at),
        else_=func.now(),
    )
    stmt = update(Prediction).where(
        Prediction.tenant_id == tenant_id, Match.tenant_id == tenant_id, Match.id == Prediction.match_id
    )
    if match_ids is not None:
        stmt = stmt.where(Prediction.match_id.in_(match_ids))
    if event_id is not None:
        stmt = stmt.where(Match.event_id == event_id)
    result = await db.execute(
        stmt.values(status=outcome, decided_at=decided, updated_at=func.now()).execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


async def history(
    db: AsyncSession,
    actor: CurrentUser,
    params: PageParams,
    *,
    status: PredictionStatus | None = None,
    side: PredictionSide | None = None,
    event_id: uuid.UUID | None = None,
    match_id: uuid.UUID | None = None,
) -> Page[PredictionOut]:
    return await _page(db, actor, params=params, status=status, side=side, event_id=event_id, match_id=match_id)


async def _page(
    db: AsyncSession,
    actor: CurrentUser,
    *,
    params: PageParams | None = None,
    select_ids: list[uuid.UUID] | None = None,
    status: PredictionStatus | None = None,
    side: PredictionSide | None = None,
    event_id: uuid.UUID | None = None,
    match_id: uuid.UUID | None = None,
) -> Page[PredictionOut]:
    tenant_id = actor.tenant.id
    stmt = (
        select(Prediction, Match, Event.name, MatchResult.outcome, window_open_clause(tenant_id))
        .join(Match, (Match.id == Prediction.match_id) & (Match.tenant_id == tenant_id))
        .join(Event, (Event.id == Match.event_id) & (Event.tenant_id == tenant_id))
        .outerjoin(MatchResult, (MatchResult.match_id == Match.id) & (MatchResult.tenant_id == tenant_id))
        # Only ever the caller's own predictions.
        .where(Prediction.tenant_id == tenant_id, Prediction.user_id == actor.id)
    )
    if select_ids is not None:
        stmt = stmt.where(Prediction.id.in_(select_ids))
    if status:
        stmt = stmt.where(Prediction.status == status)
    if side:
        stmt = stmt.where(Prediction.side == side)
    if event_id:
        stmt = stmt.where(Match.event_id == event_id)
    if match_id:
        stmt = stmt.where(Prediction.match_id == match_id)
    if params and params.q:
        like = f"%{params.q}%"
        terms = [Match.meron_name.ilike(like), Match.wala_name.ilike(like), Event.name.ilike(like)]
        number = params.q.removeprefix("#")
        if number.isascii() and number.isdigit() and len(number) <= 9:
            terms.append(Match.match_number == int(number))
        stmt = stmt.where(or_(*terms))
    page, size = (params.page, params.page_size) if params else (1, max(len(select_ids or []), 1))
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (
        await db.execute(stmt.order_by(Prediction.created_at.desc(), Prediction.id.desc()).offset((page - 1) * size).limit(size))
    ).all()
    items = [
        PredictionOut(
            id=p.id,
            match_id=m.id,
            event_id=m.event_id,
            event_name=event_name,
            match_number=m.match_number,
            meron_name=m.meron_name,
            wala_name=m.wala_name,
            match_status=m.status,
            official_outcome=official,
            side=p.side,
            status=p.status,
            open=bool(window) and p.status == PredictionStatus.PENDING,
            changes=p.changes,
            created_at=p.created_at,
            updated_at=p.updated_at,
            decided_at=p.decided_at,
        )
        for p, m, event_name, official, window in rows
    ]
    return Page(items=items, total=int(total), page=page, page_size=size)
